# Data Transmission Logic

This document provides a detailed explanation of how the Network Monitor Agent handles data transmission, particularly the distinction between normal metrics and alerts.

## Core Principles

1. **All data is stored locally FIRST** before any transmission attempt
2. **Alerts are prioritized** over regular metrics
3. **WARNING/CRITICAL alerts are sent immediately**
4. **Normal metrics are batched** for efficiency
5. **Failed transmissions are retried** without data loss
6. **Offline operation is fully supported**

---

## Data Flow Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    COLLECTION PHASE                          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
           ┌────────────────────────────────┐
           │  1. Collect System Metrics     │
           │     (CPU, Memory, Disk, etc.)  │
           └────────────────┬───────────────┘
                            │
                            ▼
           ┌────────────────────────────────┐
           │  2. Store ALL Metrics          │
           │     in SQLite (sent=0)         │
           └────────────────┬───────────────┘
                            │
                            ▼
           ┌────────────────────────────────┐
           │  3. Evaluate Alert Thresholds  │
           └────────────────┬───────────────┘
                            │
                   ┌────────┴────────┐
                   │                 │
           No Alert│                 │Alert Generated
                   │                 │
                   ▼                 ▼
        ┌──────────────┐   ┌─────────────────────┐
        │  Continue    │   │  4. Store Alert     │
        │  Next Cycle  │   │     (sent=0)        │
        └──────────────┘   └──────────┬──────────┘
                                      │
                            ┌─────────┴─────────┐
                            │ Check Severity    │
                            └─────────┬─────────┘
                                      │
                          ┌───────────┴───────────┐
                          │                       │
                    WARNING/CRITICAL             INFO
                          │                       │
                          ▼                       ▼
           ┌──────────────────────────┐  ┌───────────────┐
           │  5. IMMEDIATE SEND       │  │  Queue for    │
           │     (async goroutine)    │  │  Batch Send   │
           └──────────────────────────┘  └───────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   TRANSMISSION PHASE                         │
└─────────────────────────────────────────────────────────────┘

    Every send_interval (60s):

           ┌────────────────────────────────┐
           │  1. Query Unsent Alerts        │
           │     ORDER BY timestamp, severity│
           └────────────────┬───────────────┘
                            │
                            ▼
           ┌────────────────────────────────┐
           │  2. Send Alerts to Server      │
           │     (Priority Transmission)    │
           └────────────────┬───────────────┘
                            │
                   ┌────────┴────────┐
                   │                 │
               Success            Failure
                   │                 │
                   ▼                 ▼
        ┌──────────────────┐  ┌──────────────┐
        │ Mark sent=1      │  │ Log error    │
        │ Set sent_at      │  │ Keep sent=0  │
        │                  │  │ Retry later  │
        └────────┬─────────┘  └──────────────┘
                 │
                 ▼
           ┌────────────────────────────────┐
           │  3. Query Unsent Metrics       │
           │     ORDER BY timestamp         │
           │     LIMIT batch_size (100)     │
           └────────────────┬───────────────┘
                            │
                            ▼
           ┌────────────────────────────────┐
           │  4. Send Metrics Batch         │
           │     (Normal Transmission)      │
           └────────────────┬───────────────┘
                            │
                   ┌────────┴────────┐
                   │                 │
               Success            Failure
                   │                 │
                   ▼                 ▼
        ┌──────────────────┐  ┌──────────────┐
        │ Mark sent=1      │  │ Log error    │
        │ Set sent_at      │  │ Keep sent=0  │
        │                  │  │ Retry later  │
        └──────────────────┘  └──────────────┘
```

---

## Two Types of Data Transmission

### 1. Normal Metrics (Batched)

**Characteristics:**
- Collected every `collect_interval` (default: 30s)
- Stored immediately in SQLite
- **NOT sent immediately**
- Sent in batches at `send_interval` (default: 60s)
- Batch size: configurable (default: 100 metrics)
- Oldest metrics sent first (FIFO)

**Why batched?**
- Reduces HTTP request overhead
- More efficient network usage
- Lower load on server
- Acceptable delay for non-critical data

**Example Timeline:**
```
00:00 - Collect metrics → Store (sent=0)
00:30 - Collect metrics → Store (sent=0)
01:00 - Send batch [00:00 + 00:30 metrics] → Mark sent=1
01:30 - Collect metrics → Store (sent=0)
02:00 - Send batch [01:30 metrics] → Mark sent=1
```

**Code Location:** `internal/agent/agent.go` - `senderLoop()`

```go
func (a *Agent) senderLoop() {
    ticker := time.NewTicker(a.config.Agent.SendInterval)
    for {
        select {
        case <-ticker.C:
            a.processQueue()  // Sends batched metrics
        }
    }
}
```

---

### 2. Alerts (Immediate)

**Characteristics:**
- Generated when thresholds exceeded
- Stored immediately in SQLite
- **WARNING/CRITICAL sent IMMEDIATELY**
- Does not wait for batch send interval
- Sent in separate HTTP request
- Bypasses normal queuing

**Why immediate?**
- Time-sensitive information
- Requires urgent attention
- Cannot wait for next batch cycle
- May indicate imminent system failure

**Example Timeline:**
```
00:00 - Collect metrics → CPU=45% → No alert
00:30 - Collect metrics → CPU=97% → CRITICAL ALERT
00:30 - ⚡ IMMEDIATE SEND (async) → Server receives instantly
01:00 - Normal batch send (includes other metrics)
```

**Code Location:** `internal/agent/agent.go` - `collectAndProcess()`

```go
func (a *Agent) collectAndProcess() {
    // ... collect metrics ...

    // Evaluate alerts
    generatedAlerts, _ := a.alertEngine.EvaluateMetrics(metrics)

    // Send critical/warning alerts immediately
    if len(generatedAlerts) > 0 {
        var immediateAlerts []*storage.Alert
        for _, alert := range generatedAlerts {
            if alerts.ShouldSendImmediately(alert) {
                immediateAlerts = append(immediateAlerts, alert)
            }
        }

        if len(immediateAlerts) > 0 {
            // Send in separate goroutine (non-blocking)
            go func() {
                a.sender.SendAlerts(immediateAlerts)
            }()
        }
    }
}
```

**Alert Severity Logic:**

```go
// internal/alerts/alerts.go
func ShouldSendImmediately(alert *storage.Alert) bool {
    return alert.Severity == SeverityWarning ||
           alert.Severity == SeverityCritical
}
```

---

## Database States

Every metric and alert has a `sent` flag:

```sql
-- Unsent (waiting to be transmitted)
sent = 0, sent_at = NULL

-- Successfully sent
sent = 1, sent_at = <timestamp>
```

### Querying Unsent Data

**Alerts (Priority):**
```sql
SELECT * FROM alerts
WHERE sent = 0
ORDER BY timestamp ASC, severity DESC
LIMIT ?
```

**Metrics (Batched):**
```sql
SELECT * FROM metrics
WHERE sent = 0
ORDER BY timestamp ASC
LIMIT ?
```

**Why this order?**
- Alerts: Oldest first + highest severity first
- Metrics: Oldest first (maintain time series integrity)

---

## Transmission Priority

When `processQueue()` runs:

```
Priority 1: Unsent Alerts
    ↓
Priority 2: Unsent Metrics
```

**Code:**
```go
func (s *Sender) ProcessQueue(batchSize int) error {
    // PRIORITY 1: Send unsent alerts first
    alerts, _ := s.storage.GetUnsentAlerts(batchSize)
    if len(alerts) > 0 {
        s.SendAlerts(alerts)
    }

    // PRIORITY 2: Send normal metrics
    metrics, _ := s.storage.GetUnsentMetrics(batchSize)
    if len(metrics) > 0 {
        s.SendMetrics(metrics)
    }

    return nil
}
```

**Why this order?**
- Alerts are time-sensitive
- If network is slow, alerts go first
- Ensures critical information always prioritized

---

## Offline Operation

### Scenario: Server Unreachable

```
Time    Action                          Database State
-----   -----------------------------   --------------------------
00:00   Collect metrics                 100 metrics, sent=0
00:30   Collect metrics                 200 metrics, sent=0
01:00   Try send → SERVER DOWN          200 metrics, sent=0 (retry)
01:30   Collect metrics                 300 metrics, sent=0
02:00   Try send → SERVER DOWN          300 metrics, sent=0 (retry)
02:30   Collect metrics                 400 metrics, sent=0
03:00   Try send → SERVER BACK UP! ✓
        - Send 100 alerts (if any)
        - Send 100 oldest metrics        300 metrics, sent=0
03:30   Collect metrics                 400 metrics, sent=0
04:00   Send next batch                 300 metrics, sent=0
        ...continues until caught up...
```

**Key Points:**
1. Agent **never stops collecting** during outages
2. Data **accumulates in local database**
3. When server returns, **backlog is processed in order**
4. **Alerts always sent before metrics**
5. **No data is lost**

### Recovery Process

```go
// On startup or after reconnection
func (a *Agent) senderLoop() {
    // Immediately process any backlog
    a.processQueue()

    // Then continue normal schedule
    ticker := time.NewTicker(a.config.Agent.SendInterval)
    for {
        select {
        case <-ticker.C:
            a.processQueue()
        }
    }
}
```

---

## Retry Logic

### Per-Request Retries

When sending data, the agent retries failed requests:

```go
func (s *Sender) SendAlerts(alerts []*Alert) error {
    for attempt := 0; attempt <= s.config.RetryAttempts; attempt++ {
        if attempt > 0 {
            time.Sleep(s.config.RetryDelay)  // Wait before retry
        }

        resp, err := s.sendRequest(url, data)
        if err == nil && resp.Success {
            // Mark as sent
            s.storage.MarkAlertsSent(ids)
            return nil
        }
    }

    // All retries failed - keep in database
    return errors.New("failed after retries")
}
```

**Configuration:**
```yaml
server:
  retry_attempts: 3    # Try 3 times
  retry_delay: 5s      # Wait 5 seconds between retries
```

**Retry Timeline:**
```
Attempt 1 → FAIL
Wait 5s
Attempt 2 → FAIL
Wait 5s
Attempt 3 → FAIL
→ Give up, data stays in DB for next cycle
```

### Cross-Cycle Retries

Even if immediate retries fail:
- Data remains `sent=0` in database
- Next `processQueue()` cycle will try again
- Continues until successful or retention period expires

---

## Example Scenarios

### Scenario 1: Normal Operation

```
Config:
  collect_interval: 30s
  send_interval: 60s
  cpu.warning: 80%

Timeline:
00:00 - Collect: CPU=45%, Memory=60%, Disk=50%
        → Store 3 metrics (sent=0)
        → No alerts

00:30 - Collect: CPU=48%, Memory=62%, Disk=51%
        → Store 3 metrics (sent=0)
        → No alerts

01:00 - Send Cycle
        → Query unsent: 6 metrics
        → POST /api/v1/metrics (batch of 6)
        → Success → Mark sent=1

01:30 - Collect: CPU=50%, Memory=65%, Disk=52%
        → Store 3 metrics (sent=0)
        → No alerts
```

**Total HTTP Requests:** 1 (batched 6 metrics)

---

### Scenario 2: Alert Triggered

```
Config:
  collect_interval: 30s
  send_interval: 60s
  cpu.warning: 80%

Timeline:
00:00 - Collect: CPU=45%, Memory=60%, Disk=50%
        → Store 3 metrics (sent=0)
        → No alerts

00:30 - Collect: CPU=85%, Memory=62%, Disk=51%
        → Store 3 metrics (sent=0)
        → ALERT: CPU WARNING 85% > 80%
        → Store alert (sent=0)
        → ⚡ IMMEDIATE SEND (async)
           POST /api/v1/alerts
           → Success → Mark alert sent=1

01:00 - Send Cycle
        → Query unsent alerts: 0 (already sent)
        → Query unsent metrics: 6 metrics
        → POST /api/v1/metrics (batch of 6)
        → Success → Mark sent=1
```

**Total HTTP Requests:** 2
- 1 immediate (alert)
- 1 batched (metrics)

---

### Scenario 3: Server Offline

```
Config:
  collect_interval: 30s
  send_interval: 60s

Timeline:
00:00 - Collect: 3 metrics → Store (sent=0)
00:30 - Collect: 3 metrics → Store (sent=0)
01:00 - Send Cycle
        → POST /api/v1/metrics
        → ❌ CONNECTION REFUSED
        → Retry 1 → ❌ FAIL
        → Retry 2 → ❌ FAIL
        → Retry 3 → ❌ FAIL
        → Log error, data stays sent=0

01:30 - Collect: 3 metrics → Store (sent=0)
        → Now have 9 unsent metrics

02:00 - Send Cycle
        → POST /api/v1/metrics
        → ❌ SERVER STILL DOWN
        → Now have 9 unsent metrics

02:30 - Collect: 3 metrics → Store (sent=0)
        → Now have 12 unsent metrics

03:00 - SERVER BACK ONLINE ✓
        Send Cycle
        → Query unsent: 12 metrics
        → POST /api/v1/metrics (batch of 12)
        → ✅ SUCCESS
        → Mark all 12 sent=1

03:30 - Back to normal operation
```

**Result:** All data preserved and eventually transmitted

---

## Performance Considerations

### Network Efficiency

**Without Batching:**
- 1 metric every 30s
- 2 metrics/minute
- 120 HTTP requests/hour
- High overhead

**With Batching:**
- 100 metrics every 60s
- Send 1x per minute
- 60 HTTP requests/hour
- 50% reduction in requests

### Database Growth

**Metrics:**
- 10 metrics/collection
- Collect every 30s
- 1,200 metrics/hour
- 28,800 metrics/day
- ~10 MB/day (typical)

**Retention:**
```yaml
database:
  retention_days: 30
```
- Sent data older than 30 days is deleted
- Unsent data is NEVER deleted (unless manually removed)

---

## Configuration Tuning

### For High-Frequency Monitoring

```yaml
agent:
  collect_interval: 10s   # More frequent
  send_interval: 30s      # Send more often
  batch_size: 50          # Smaller batches
```

### For Low-Bandwidth Environments

```yaml
agent:
  collect_interval: 60s   # Less frequent
  send_interval: 300s     # Send less often (5 min)
  batch_size: 200         # Larger batches
```

### For Alert-Heavy Environments

```yaml
alerts:
  # Higher thresholds = fewer alerts
  cpu:
    warning: 90.0
    critical: 98.0
```

---

## Summary

**Normal Metrics:**
- ✅ Stored locally first
- ✅ Sent in batches
- ✅ Efficient for bulk data
- ✅ Acceptable latency

**Alerts:**
- ✅ Stored locally first
- ✅ Sent immediately (WARNING/CRITICAL)
- ✅ Bypass batching
- ✅ Time-sensitive delivery

**Both:**
- ✅ Survive server outages
- ✅ Automatic retry
- ✅ No data loss
- ✅ Ordered transmission (oldest first)

This design ensures reliable, efficient data transmission while prioritizing critical alerts for immediate delivery.
