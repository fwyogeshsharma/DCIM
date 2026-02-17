# Agent Monitoring & Alert System Documentation

**Version:** 1.0.0
**Date:** 2026-02-17
**Features:** Graceful Shutdown Detection & Hang Detection

---

## Table of Contents
1. [Overview](#overview)
2. [Alert Types & Severities](#alert-types--severities)
3. [Database Schema](#database-schema)
4. [API Endpoints](#api-endpoints)
5. [Configuration](#configuration)
6. [Alert Messages Reference](#alert-messages-reference)
7. [Agent Status States](#agent-status-states)
8. [Detection Algorithms](#detection-algorithms)
9. [Testing Scenarios](#testing-scenarios)
10. [Troubleshooting](#troubleshooting)

---

## Overview

The DCIM system implements advanced agent monitoring with intelligent alert generation to distinguish between different types of agent failures:

### Phase 1: Graceful vs Unexpected Shutdown Detection
- Distinguishes between planned shutdowns (Ctrl+C) and unexpected failures (crash/kill)
- Creates INFO alerts for graceful shutdowns
- Creates CRITICAL alerts for unexpected shutdowns
- Tracks shutdown events in database for historical analysis

### Phase 2: Hang Detection
- Monitors agent response times and performance degradation
- Detects slow responses and potential system hangs
- Creates WARNING alerts for performance degradation
- Creates CRITICAL alerts for suspected hangs
- Automatically tracks and resets performance metrics

---

## Alert Types & Severities

### Alert Classification

| Metric Type | Severity | Description | Auto-Resolve |
|-------------|----------|-------------|--------------|
| `agent_shutdown_graceful` | **INFO** | Agent shutdown gracefully (user-initiated) | No |
| `agent_shutdown_error` | **CRITICAL** | Agent shutdown with error condition | No |
| `agent_offline` | **CRITICAL** | Agent went offline unexpectedly | Yes (when online) |
| `agent_degraded` | **WARNING** | Agent responding slowly (performance issue) | Yes (when recovered) |
| `agent_hanging` | **CRITICAL** | Agent appears to be hanging/frozen | Yes (when recovered) |

### Severity Levels Explained

#### INFO
- **Purpose**: Informational only, no action required
- **Use Case**: Normal operational events (graceful shutdown)
- **Response**: Monitor for patterns, no immediate action needed

#### WARNING
- **Purpose**: Indicates potential issues requiring attention
- **Use Case**: Performance degradation, slow responses
- **Response**: Investigate root cause, monitor for escalation

#### CRITICAL
- **Purpose**: Requires immediate attention
- **Use Case**: Unexpected failures, hangs, crashes
- **Response**: Immediate investigation and remediation required

---

## Database Schema

### Migration 005: Agent Shutdown Tracking

```sql
-- Table: agent_shutdown_events
CREATE TABLE IF NOT EXISTS agent_shutdown_events (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    shutdown_type TEXT NOT NULL CHECK (shutdown_type IN ('graceful', 'unexpected', 'error')),
    shutdown_time TIMESTAMP NOT NULL,
    reason TEXT,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_shutdown_events_agent ON agent_shutdown_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_shutdown_events_server ON agent_shutdown_events(server_id);
CREATE INDEX IF NOT EXISTS idx_shutdown_events_time ON agent_shutdown_events(shutdown_time DESC);
CREATE INDEX IF NOT EXISTS idx_shutdown_events_type ON agent_shutdown_events(shutdown_type);
```

### Migration 006: Agent Hang Detection

```sql
-- Added columns to agents table:
ALTER TABLE agents ADD COLUMN last_response_time INTERVAL;        -- Time between heartbeats
ALTER TABLE agents ADD COLUMN avg_response_time INTERVAL;         -- Rolling average
ALTER TABLE agents ADD COLUMN consecutive_slow_count INTEGER DEFAULT 0;
ALTER TABLE agents ADD COLUMN previous_seen TIMESTAMP;            -- Previous last_seen
```

**Field Descriptions:**
- `last_response_time`: Actual interval between last two heartbeats
- `avg_response_time`: Exponential moving average (EMA) of response times (70% old, 30% new)
- `consecutive_slow_count`: Number of consecutive slow responses (reset on normal response)
- `previous_seen`: Previous `last_seen` timestamp for interval calculation

---

## API Endpoints

### POST /api/v1/agent/shutdown

Agent graceful shutdown notification endpoint.

**Request Body:**
```json
{
    "agent_id": "JPR_MP_AGENT_WIN_HP_AS_01",
    "shutdown_type": "graceful",  // "graceful" or "error"
    "reason": "User-initiated shutdown",
    "metadata": {
        "signal": "SIGTERM",
        "exit_code": "0"
    }
}
```

**Response:**
```json
{
    "success": true,
    "message": "Shutdown notification received"
}
```

**Behavior:**
1. Records shutdown event in `agent_shutdown_events` table
2. Updates agent status to `offline`
3. Creates alert based on shutdown_type:
   - `graceful` → INFO alert
   - `error` → CRITICAL alert

**Alert Messages:**
- Graceful: "Agent shutdown gracefully: [reason]"
- Error: "Agent shutdown with error: [reason]"

---

## Configuration

### Server Configuration (config.yaml)

```yaml
agents:
  connection:
    # Agent heartbeat timeout - mark agent offline if no data for this duration
    heartbeat_timeout: 60s

    # Agent identification method
    identification_method: "agent_id"
```

**Hang Detection Thresholds (Auto-calculated):**
- Expected Interval: `heartbeat_timeout / 2` (e.g., 30s for 60s timeout)
- Slow Threshold: `1.5x avg_response_time`
- Degraded Threshold: 3 consecutive slow responses
- Hanging Threshold: 5 consecutive slow responses

**Monitor Check Interval:**
- Default: 1 minute
- Configured in: `internal/server/agent_monitor.go`

---

## Alert Messages Reference

### 1. Graceful Shutdown (INFO)

**Metric Type:** `agent_shutdown_graceful`
**Severity:** INFO
**Trigger:** Agent sends POST to `/api/v1/agent/shutdown` with `shutdown_type: "graceful"`

**Message Format:**
```
Agent shutdown gracefully
Agent shutdown gracefully: User-initiated shutdown
Agent shutdown gracefully: System maintenance scheduled
```

**Database Fields:**
```json
{
    "agent_id": "JPR_MP_AGENT_WIN_HP_AS_01",
    "timestamp": "2026-02-17T10:30:00Z",
    "severity": "INFO",
    "metric_type": "agent_shutdown_graceful",
    "value": 0,
    "threshold": 0,
    "message": "Agent shutdown gracefully: User-initiated shutdown",
    "resolved": false
}
```

---

### 2. Error Shutdown (CRITICAL)

**Metric Type:** `agent_shutdown_error`
**Severity:** CRITICAL
**Trigger:** Agent sends POST to `/api/v1/agent/shutdown` with `shutdown_type: "error"`

**Message Format:**
```
Agent shutdown with error
Agent shutdown with error: Database connection failed
Agent shutdown with error: Fatal exception in metrics collector
```

**Database Fields:**
```json
{
    "agent_id": "JPR_MP_AGENT_WIN_HP_AS_01",
    "timestamp": "2026-02-17T10:30:00Z",
    "severity": "CRITICAL",
    "metric_type": "agent_shutdown_error",
    "value": 0,
    "threshold": 0,
    "message": "Agent shutdown with error: Database connection failed",
    "resolved": false
}
```

---

### 3. Unexpected Offline (CRITICAL)

**Metric Type:** `agent_offline`
**Severity:** CRITICAL
**Trigger:** No heartbeat within timeout AND no shutdown notification received

**Message Format:**
```
Agent went offline unexpectedly - no data received for 1m0s
Agent went offline unexpectedly - no data received for 5m0s
```

**Database Fields:**
```json
{
    "agent_id": "JPR_MP_AGENT_WIN_HP_AS_01",
    "timestamp": "2026-02-17T10:30:00Z",
    "severity": "CRITICAL",
    "metric_type": "agent_offline",
    "value": 0,
    "threshold": 0,
    "message": "Agent went offline unexpectedly - no data received for 1m0s",
    "resolved": false
}
```

**Possible Causes:**
- Agent process killed (kill -9, Task Manager end process)
- System crash/reboot
- Network connectivity loss
- Power failure
- Agent deadlock/freeze

---

### 4. Degraded Performance (WARNING)

**Metric Type:** `agent_degraded`
**Severity:** WARNING
**Trigger:** 3 consecutive responses slower than 1.5x average response time

**Message Format:**
```
Agent is responding slowly (3 consecutive slow responses, last: 45s)
Agent is responding slowly (4 consecutive slow responses, last: 1m2s)
```

**Database Fields:**
```json
{
    "agent_id": "JPR_MP_AGENT_WIN_HP_AS_01",
    "timestamp": "2026-02-17T10:30:00Z",
    "severity": "WARNING",
    "metric_type": "agent_degraded",
    "value": 3.0,
    "threshold": 3.0,
    "message": "Agent is responding slowly (3 consecutive slow responses, last: 45s)",
    "resolved": false
}
```

**Possible Causes:**
- High CPU utilization
- Memory pressure
- Disk I/O bottleneck
- Network latency
- Resource contention

---

### 5. Agent Hanging (CRITICAL)

**Metric Type:** `agent_hanging`
**Severity:** CRITICAL
**Trigger:** 5+ consecutive responses critically slower than average

**Message Format:**
```
Agent appears to be hanging (5 consecutive slow responses, last: 2m15s) - may need restart
Agent appears to be hanging (7 consecutive slow responses, last: 3m45s) - may need restart
```

**Database Fields:**
```json
{
    "agent_id": "JPR_MP_AGENT_WIN_HP_AS_01",
    "timestamp": "2026-02-17T10:30:00Z",
    "severity": "CRITICAL",
    "metric_type": "agent_hanging",
    "value": 5.0,
    "threshold": 5.0,
    "message": "Agent appears to be hanging (5 consecutive slow responses, last: 2m15s) - may need restart",
    "resolved": false
}
```

**Possible Causes:**
- Deadlock in agent code
- Infinite loop or blocking operation
- Severe memory leak
- Disk full (swap exhausted)
- Database lock/contention
- External service timeout

---

## Agent Status States

### Status Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent Lifecycle                          │
└─────────────────────────────────────────────────────────────┘

    pending ──registration──> online
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
                 ▼               ▼               ▼
            degraded        graceful        unexpected
         (3+ slow)         shutdown         shutdown
                 │               │               │
                 ▼               ▼               ▼
            hanging          offline          offline
         (5+ slow)        (INFO alert)    (CRITICAL alert)
                 │               │               │
                 └───────────────┴───────────────┘
                                 │
                         recovery/reconnect
                                 │
                                 ▼
                              online
```

### Status Values

| Status | Description | Alert Severity | Transition From |
|--------|-------------|----------------|-----------------|
| `pending` | Agent registered but not approved | - | Initial registration |
| `online` | Agent operating normally | - | pending, offline, degraded |
| `degraded` | Agent responding slowly | WARNING | online |
| `offline` | Agent not responding | INFO/CRITICAL | online, degraded, graceful shutdown |

**Note:** There is no separate "hanging" status - hanging agents remain in `degraded` status with a CRITICAL alert.

---

## Detection Algorithms

### 1. Response Time Calculation

**UpdateAgentLastSeen() Logic:**

```go
// Calculate interval since last heartbeat
if !agent.LastSeen.IsZero() {
    interval := now.Sub(agent.LastSeen)
    responseTime = &interval
    previousSeen = &agent.LastSeen
}

// Exponential Moving Average (EMA)
// new_avg = 0.7 * old_avg + 0.3 * new_value
if responseTime != nil && agent.AvgResponseTime != nil {
    newAvg := time.Duration(float64(*agent.AvgResponseTime)*0.7 + float64(*responseTime)*0.3)
    avgResponseTime = &newAvg
}
```

**Why EMA?**
- Smooths out temporary spikes
- Gives more weight to recent history (30%)
- Adapts to changing baseline performance

---

### 2. Slow Response Detection

**Slow Threshold Calculation:**
```go
// Consider "slow" if current response is > 1.5x average
slowThreshold := time.Duration(float64(*avgResponseTime) * 1.5)
if *responseTime > slowThreshold {
    consecutiveSlowCount++
} else {
    consecutiveSlowCount = 0  // Reset on normal response
}
```

**Example:**
- Average response time: 30s
- Slow threshold: 45s (1.5 × 30s)
- Current response: 50s → **Slow** (increment counter)
- Next response: 35s → **Normal** (reset counter to 0)

---

### 3. Degraded/Hanging Detection

**Monitor Check (Every 1 Minute):**

```go
expectedInterval := heartbeatTimeout / 2  // e.g., 30s for 60s timeout
slowThreshold := expectedInterval * 1.5   // e.g., 45s

// Check agent performance
if !isOffline && agent.LastResponseTime > slowThreshold {
    if agent.ConsecutiveSlowCount >= 5 {
        // CRITICAL: Agent hanging
        createAlert("agent_hanging", "CRITICAL", ...)
        status = "degraded"
    } else if agent.ConsecutiveSlowCount >= 3 {
        // WARNING: Agent degraded
        createAlert("agent_degraded", "WARNING", ...)
        status = "degraded"
    }
}

// Recovery
if agent.Status == "degraded" && agent.ConsecutiveSlowCount < 3 {
    status = "online"
    consecutiveSlowCount = 0
}
```

---

### 4. Shutdown vs Offline Detection

**When Agent Goes Offline:**

```go
// Check for recent graceful shutdown notification
shutdownCheckWindow := heartbeatTimeout + 1*time.Minute
recentShutdown := db.GetRecentShutdownEvent(agentID, shutdownCheckWindow)

if recentShutdown != nil {
    if recentShutdown.ShutdownType == "graceful" {
        // Already created INFO alert via shutdown endpoint
        severity = "INFO"
        metricType = "agent_shutdown_graceful"
    } else {
        // Already created CRITICAL alert via shutdown endpoint
        severity = "CRITICAL"
        metricType = "agent_shutdown_error"
    }
} else {
    // No shutdown notification - unexpected offline
    severity = "CRITICAL"
    metricType = "agent_offline"
    message = "Agent went offline unexpectedly - no data received for " + timeout
}
```

**Grace Period:** Heartbeat timeout + 1 minute allows for network delays

---

## Testing Scenarios

### Test 1: Graceful Shutdown (INFO Alert)

**Steps:**
1. Start agent normally
2. Press Ctrl+C to stop agent
3. Agent sends shutdown notification before stopping

**Expected Results:**
- Agent status: `offline`
- Alert created: `agent_shutdown_graceful` (INFO)
- Message: "Agent shutdown gracefully: User-initiated shutdown"
- Database: Shutdown event recorded with `shutdown_type = 'graceful'`

**Database Query to Verify:**
```sql
-- Check alert
SELECT * FROM alerts
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
  AND metric_type = 'agent_shutdown_graceful'
ORDER BY timestamp DESC LIMIT 1;

-- Check shutdown event
SELECT * FROM agent_shutdown_events
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
ORDER BY shutdown_time DESC LIMIT 1;
```

---

### Test 2: Unexpected Shutdown (CRITICAL Alert)

**Steps:**
1. Start agent normally
2. Kill agent process forcefully: `taskkill /F /IM dcim-agent.exe` (Windows) or `kill -9 <pid>` (Linux)
3. Wait for heartbeat timeout (default: 60s)

**Expected Results:**
- Agent status: `offline`
- Alert created: `agent_offline` (CRITICAL)
- Message: "Agent went offline unexpectedly - no data received for 1m0s"
- No shutdown event in database

**Database Query to Verify:**
```sql
-- Check alert
SELECT * FROM alerts
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
  AND metric_type = 'agent_offline'
ORDER BY timestamp DESC LIMIT 1;

-- Verify no recent shutdown event
SELECT * FROM agent_shutdown_events
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
  AND shutdown_time > NOW() - INTERVAL '2 minutes';
```

---

### Test 3: Performance Degradation (WARNING Alert)

**Simulation Method:**
Modify agent code to add artificial delay:

```go
// In agent's senderLoop() or collectionLoop()
time.Sleep(45 * time.Second)  // Simulate slow performance
```

**Steps:**
1. Start agent with delay modification
2. Monitor for 3-4 send cycles
3. Observe consecutive slow count incrementing

**Expected Results:**
- After 3 slow responses:
  - Agent status: `degraded`
  - Alert created: `agent_degraded` (WARNING)
  - Message: "Agent is responding slowly (3 consecutive slow responses, last: 45s)"
  - `consecutive_slow_count = 3`

**Database Query to Verify:**
```sql
-- Check agent metrics
SELECT agent_id, status, consecutive_slow_count,
       last_response_time, avg_response_time, last_seen
FROM agents
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01';

-- Check alert
SELECT * FROM alerts
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
  AND metric_type = 'agent_degraded'
ORDER BY timestamp DESC LIMIT 1;
```

---

### Test 4: Agent Hanging (CRITICAL Alert)

**Simulation Method:**
Same as Test 3, but wait for 5+ slow responses:

```go
time.Sleep(90 * time.Second)  // Simulate very slow/hanging
```

**Steps:**
1. Start agent with heavy delay
2. Monitor for 5-6 send cycles
3. Observe transition from degraded to hanging

**Expected Results:**
- After 5 slow responses:
  - Agent status: `degraded`
  - Alert created: `agent_hanging` (CRITICAL)
  - Message: "Agent appears to be hanging (5 consecutive slow responses, last: 1m30s) - may need restart"
  - `consecutive_slow_count = 5`

**Database Query to Verify:**
```sql
-- Check agent metrics
SELECT agent_id, status, consecutive_slow_count,
       last_response_time, avg_response_time
FROM agents
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01';

-- Check alert
SELECT * FROM alerts
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
  AND metric_type = 'agent_hanging'
ORDER BY timestamp DESC LIMIT 1;
```

---

### Test 5: Recovery from Degraded (Auto-Recovery)

**Steps:**
1. Start with degraded agent (from Test 3)
2. Remove artificial delay
3. Restart agent or wait for performance to normalize
4. Monitor for 1-2 normal send cycles

**Expected Results:**
- Agent status: `online`
- `consecutive_slow_count = 0`
- Previous degraded/hanging alerts remain in database (historical record)

**Database Query to Verify:**
```sql
-- Check agent recovered
SELECT agent_id, status, consecutive_slow_count,
       last_response_time, avg_response_time
FROM agents
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01';

-- Alert history shows progression
SELECT timestamp, severity, metric_type, message
FROM alerts
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
ORDER BY timestamp DESC LIMIT 10;
```

---

## Troubleshooting

### Issue 1: Graceful Shutdown Alert Not Created

**Symptoms:**
- Agent stops with Ctrl+C
- No INFO alert in database
- Agent marked offline but no shutdown event

**Possible Causes:**
1. Agent shutdown notification failed to send
2. Network connectivity lost before notification sent
3. Server not running or endpoint unavailable
4. Agent code not calling `SendShutdownNotification()`

**Diagnosis:**
```bash
# Check agent logs
grep "Shutdown notification" /var/log/dcim-agent/agent.log

# Check server logs
grep "shutdown notification received" /var/log/dcim-server/server.log

# Check network connectivity
curl -k https://server:8443/api/v1/agent/shutdown
```

**Solution:**
- Verify agent has `SendShutdownNotification()` call in `Stop()` method
- Check server endpoint is registered and accessible
- Ensure certificates are valid for mTLS
- Increase shutdown timeout if notification is being cut off

---

### Issue 2: False Hang Detection (Too Sensitive)

**Symptoms:**
- Normal agents marked as degraded/hanging
- Frequent WARNING/CRITICAL alerts
- High consecutive_slow_count on healthy agents

**Possible Causes:**
1. Network latency between agent and server
2. Heartbeat timeout too aggressive
3. Slow threshold (1.5x) too tight
4. Server processing delays

**Diagnosis:**
```sql
-- Check response time distribution
SELECT agent_id,
       AVG(EXTRACT(EPOCH FROM last_response_time)) as avg_response_sec,
       MIN(EXTRACT(EPOCH FROM last_response_time)) as min_response_sec,
       MAX(EXTRACT(EPOCH FROM last_response_time)) as max_response_sec
FROM agents
GROUP BY agent_id;

-- Check recent alerts
SELECT agent_id, COUNT(*) as alert_count
FROM alerts
WHERE metric_type IN ('agent_degraded', 'agent_hanging')
  AND timestamp > NOW() - INTERVAL '1 hour'
GROUP BY agent_id
ORDER BY alert_count DESC;
```

**Solution:**
```yaml
# Increase heartbeat timeout in config.yaml
agents:
  connection:
    heartbeat_timeout: 120s  # Increase from 60s

# Or modify slow threshold in agent_monitor.go
slowThreshold := time.Duration(float64(expectedInterval) * 2.0)  // More tolerant
```

---

### Issue 3: Hang Detection Not Triggering

**Symptoms:**
- Agent clearly slow/frozen
- No degraded/hanging alerts created
- consecutive_slow_count not incrementing

**Possible Causes:**
1. Agent completely offline (not sending any data)
2. Response time calculation not working
3. avg_response_time not initialized

**Diagnosis:**
```sql
-- Check agent metrics
SELECT agent_id, status,
       last_response_time,
       avg_response_time,
       consecutive_slow_count,
       last_seen,
       previous_seen
FROM agents
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01';
```

**Solution:**
- Ensure agent is sending data (even if slow)
- Check that `UpdateAgentLastSeen()` is being called
- Verify `previous_seen` is being populated
- Check server logs for calculation errors

---

### Issue 4: Agent Stays in Degraded Status

**Symptoms:**
- Agent performance normalized
- consecutive_slow_count = 0
- Status still shows `degraded`

**Possible Causes:**
1. Monitor not detecting recovery
2. Status update failing in database
3. Logic bug in `handleAgentRecovered()`

**Diagnosis:**
```sql
-- Check current state
SELECT agent_id, status, consecutive_slow_count, last_response_time
FROM agents
WHERE status = 'degraded';

-- Check monitor logs
grep "recovered to normal" /var/log/dcim-server/server.log
```

**Solution:**
- Manual status reset:
```sql
UPDATE agents
SET status = 'online', consecutive_slow_count = 0
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01';
```
- Restart server to reinitialize monitor
- Check for database transaction issues

---

### Issue 5: Duplicate Shutdown Alerts

**Symptoms:**
- Multiple alerts for same shutdown event
- Both INFO and CRITICAL alerts created

**Possible Causes:**
1. Agent sends shutdown notification multiple times
2. Monitor creates offline alert despite shutdown notification
3. Timing issue between notification and monitor check

**Diagnosis:**
```sql
-- Check for duplicate alerts
SELECT timestamp, severity, metric_type, message
FROM alerts
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
  AND timestamp > NOW() - INTERVAL '5 minutes'
ORDER BY timestamp DESC;

-- Check shutdown events
SELECT * FROM agent_shutdown_events
WHERE agent_id = 'JPR_MP_AGENT_WIN_HP_AS_01'
ORDER BY shutdown_time DESC LIMIT 5;
```

**Solution:**
- Check agent shutdown logic for retry loops
- Verify monitor checks for recent shutdown before creating offline alert
- Ensure grace period (heartbeat_timeout + 1 minute) is sufficient

---

## Best Practices

### 1. Alert Response Workflow

**INFO Alerts (Graceful Shutdown):**
- ✅ Review for patterns (scheduled maintenance, frequent restarts)
- ✅ No immediate action required
- ✅ Update runbooks if pattern detected

**WARNING Alerts (Degraded Performance):**
- ⚠️ Monitor for escalation to hanging
- ⚠️ Check system resources (CPU, memory, disk)
- ⚠️ Review agent logs for errors
- ⚠️ Consider scaling if multiple agents affected

**CRITICAL Alerts (Offline/Hanging):**
- 🚨 Immediate investigation required
- 🚨 Check agent process status
- 🚨 Review system logs for crashes
- 🚨 Restart agent if hung
- 🚨 Escalate if persistent

### 2. Monitoring Recommendations

**Dashboard Metrics:**
- Current online/offline/degraded agent counts
- Alert count by severity (last 1h, 24h, 7d)
- Average response times per agent
- Consecutive slow count distribution
- Shutdown event frequency

**Alerting Integration:**
- Send CRITICAL alerts to on-call team
- Send WARNING alerts to monitoring channel
- Batch INFO alerts for daily digest

### 3. Tuning Guidelines

**Initial Deployment:**
- Use default thresholds (60s heartbeat, 3/5 slow counts)
- Monitor for 1 week to establish baseline
- Collect response time statistics

**After Baseline Established:**
- Adjust heartbeat_timeout based on network latency
- Tune slow threshold multiplier (1.5x) if too sensitive
- Adjust degraded/hanging thresholds based on alert frequency

**Production Optimization:**
- Different thresholds for different agent groups
- Geographic-specific timeouts for WAN agents
- Custom thresholds for resource-constrained devices

---

## Migration History

| Migration | Date | Description |
|-----------|------|-------------|
| 001 | Initial | Base schema - agents, metrics, alerts tables |
| 002 | - | Server tracking - multi-server support |
| 003 | - | Alert resolution - resolved_at timestamps |
| 004 | 2026-02-13 | Agent server_id - link agents to servers |
| 005 | 2026-02-17 | **Shutdown tracking - graceful vs unexpected** |
| 006 | 2026-02-17 | **Hang detection - performance monitoring** |

---

## Appendix

### A. Database Schema Quick Reference

**Shutdown Events:**
```sql
SELECT * FROM agent_shutdown_events LIMIT 1;
```

**Agent Performance Metrics:**
```sql
SELECT agent_id, status,
       EXTRACT(EPOCH FROM last_response_time) as response_sec,
       EXTRACT(EPOCH FROM avg_response_time) as avg_response_sec,
       consecutive_slow_count
FROM agents;
```

**Alert Summary:**
```sql
SELECT metric_type, severity, COUNT(*) as count
FROM alerts
WHERE timestamp > NOW() - INTERVAL '24 hours'
GROUP BY metric_type, severity
ORDER BY severity, count DESC;
```

### B. Log File Locations

**Server:**
- Windows: `.\logs\dcim_server.log`
- Linux: `/var/log/dcim-server/dcim_server.log`

**Agent:**
- Windows: `.\logs\dcim_agent.log`
- Linux: `/var/log/dcim-agent/dcim_agent.log`

### C. Related Files

**Server:**
- `internal/server/agent_monitor.go` - Main monitoring logic
- `internal/database/database.go` - Database operations
- `internal/models/models.go` - Data structures
- `migrations/005_agent_shutdown_tracking.sql`
- `migrations/006_agent_hang_detection.sql`

**Agent:**
- `internal/agent/agent.go` - Shutdown notification
- `internal/sender/sender.go` - HTTP client

---

## Support & Contact

For issues or questions regarding the agent monitoring system:
- Review this documentation
- Check server and agent logs
- Query database for alert history
- Consult troubleshooting section

**Version Control:**
- Document Version: 1.0.0
- Last Updated: 2026-02-17
- Next Review: 2026-03-17

---

*End of Document*
