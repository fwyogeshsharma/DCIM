# Architecture Documentation

## System Overview

The Network Monitor Agent is a lightweight, cross-platform system monitoring solution designed for production environments. It follows an offline-first architecture with reliable data transmission and immediate alert delivery.

## Design Principles

1. **Offline-First**: Agent operates independently of server availability
2. **Data Durability**: All data persisted locally before transmission
3. **Alert Priority**: Critical alerts bypass normal queuing
4. **Fail-Safe**: Automatic retry and recovery mechanisms
5. **Platform Native**: Runs as OS-native service on all platforms
6. **Zero Dependencies**: Single binary with embedded database

---

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────┐
│                        Network Monitor Agent                    │
│                                                                 │
│  ┌──────────────┐     ┌─────────────┐     ┌───────────────┐  │
│  │              │     │             │     │               │  │
│  │   Collector  │────▶│   Storage   │────▶│ Alert Engine  │  │
│  │  (Metrics)   │     │  (SQLite)   │     │  (Thresholds) │  │
│  │              │     │             │     │               │  │
│  └──────────────┘     └─────────────┘     └───────┬───────┘  │
│         │                     │                    │          │
│         │                     │                    │          │
│         ▼                     ▼                    ▼          │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │                   Transmission Queue                      │ │
│  │                                                           │ │
│  │  ┌─────────────────┐          ┌──────────────────────┐  │ │
│  │  │ Alert Queue     │          │  Metrics Queue       │  │ │
│  │  │ (Immediate)     │          │  (Batched)           │  │ │
│  │  │ Priority: HIGH  │          │  Priority: NORMAL    │  │ │
│  │  └─────────────────┘          └──────────────────────┘  │ │
│  │                                                           │ │
│  └──────────────────────────┬────────────────────────────────┘ │
│                             │                                  │
│                             ▼                                  │
│                    ┌─────────────────┐                         │
│                    │     Sender      │                         │
│                    │ (HTTP Client)   │                         │
│                    └────────┬────────┘                         │
└─────────────────────────────┼──────────────────────────────────┘
                              │
                              │ HTTPS
                              ▼
                    ┌──────────────────┐
                    │ Monitoring Server│
                    │   REST API       │
                    └──────────────────┘
```

---

## Component Details

### 1. Collector

**Responsibility**: Gather system metrics from OS

**Implementation**: `internal/collector/collector.go`

**Metrics Collected**:
- CPU usage (percentage and load averages)
- Memory (RAM and swap usage)
- Disk (usage per partition)
- Network (bytes and packets in/out)
- Temperature (if available)
- System uptime

**Technology**: Uses `gopsutil` library for cross-platform metrics

**Collection Flow**:
```
Timer (30s) → Collect() → Parse Metrics → Store in SQLite
```

**Key Methods**:
- `Collect()`: Gathers all metrics
- `Store()`: Persists metrics to database
- `CollectSystemInfo()`: One-time system metadata collection

---

### 2. Storage Layer

**Responsibility**: Persistent local data storage

**Implementation**: `internal/storage/storage.go`

**Technology**: Embedded SQLite with WAL mode

**Schema**:

```sql
metrics (
    id, timestamp, metric_type, value, unit, metadata,
    sent, created_at, sent_at
)

alerts (
    id, timestamp, severity, metric_type, value, threshold,
    message, sent, created_at, sent_at, retry_count
)

system_info (
    agent_id, hostname, os, platform, cpu_model, cpu_cores,
    total_memory, updated_at
)

transmission_log (
    id, timestamp, record_type, record_id, status, error_message
)
```

**Key Features**:
- WAL (Write-Ahead Logging) for concurrent access
- Automatic cleanup based on retention policy
- Transaction support for batch operations
- Audit trail via transmission_log

**Key Methods**:
- `SaveMetric()`: Store individual metric
- `SaveAlert()`: Store generated alert
- `GetUnsentAlerts()`: Retrieve alerts for transmission
- `GetUnsentMetrics()`: Retrieve metrics for transmission
- `MarkMetricsSent()` / `MarkAlertsSent()`: Update sent status
- `CleanupOldData()`: Remove old data based on retention

---

### 3. Alert Engine

**Responsibility**: Evaluate metrics against thresholds

**Implementation**: `internal/alerts/alerts.go`

**Alert Levels**:
- `INFO`: Informational (not used currently)
- `WARNING`: Threshold exceeded, needs attention
- `CRITICAL`: Critical threshold, immediate action required

**Evaluation Logic**:
```go
if value >= critical_threshold {
    severity = CRITICAL
} else if value >= warning_threshold {
    severity = WARNING
}
```

**Thresholds** (configurable):
- CPU: Warning 80%, Critical 95%
- Memory: Warning 85%, Critical 95%
- Disk: Warning 85%, Critical 95%
- Temperature: Warning 75°C, Critical 85°C

**Key Methods**:
- `EvaluateMetrics()`: Check all metrics against thresholds
- `checkThreshold()`: Individual threshold check
- `ShouldSendImmediately()`: Determine if alert needs immediate send

**Alert Flow**:
```
Metrics → Evaluate → Alert Generated → Store → Immediate Send (if WARNING/CRITICAL)
```

---

### 4. Sender

**Responsibility**: Transmit data to monitoring server

**Implementation**: `internal/sender/sender.go`

**Communication**:
- Protocol: HTTPS
- Format: JSON
- Authentication: Agent ID in header

**Transmission Priority**:

1. **Alerts** (Immediate)
   - Sent as soon as generated
   - Separate goroutine to not block collection
   - Retry with configurable attempts

2. **Metrics** (Batched)
   - Collected in batches (default: 100)
   - Sent at regular intervals (default: 60s)
   - Oldest data sent first

**Retry Logic**:
```
Attempt 1 → Wait 5s → Attempt 2 → Wait 5s → Attempt 3 → Fail & Log
```

Failed sends remain in database for next cycle.

**Key Methods**:
- `SendAlerts()`: Immediate alert transmission
- `SendMetrics()`: Batch metric transmission
- `ProcessQueue()`: Process unsent data (alerts first, then metrics)
- `sendRequest()`: Low-level HTTP request handler

**HTTP Headers**:
```
Content-Type: application/json
User-Agent: NetworkMonitorAgent/1.0
X-Agent-ID: <unique-agent-id>
```

---

### 5. Agent Orchestrator

**Responsibility**: Coordinate all components

**Implementation**: `internal/agent/agent.go`

**Goroutines**:

1. **Collection Loop**
   - Timer: `collect_interval` (default: 30s)
   - Collects metrics
   - Stores in database
   - Evaluates alerts
   - Triggers immediate send for critical alerts

2. **Sender Loop**
   - Timer: `send_interval` (default: 60s)
   - Processes unsent alerts (priority)
   - Processes unsent metrics (batched)
   - Retries failed transmissions

3. **Cleanup Loop**
   - Timer: 24 hours
   - Removes old data beyond retention period
   - Optimizes database

**Lifecycle**:
```
Start() → Initialize Components → Launch Goroutines → Run
Stop()  → Signal Stop → Wait for Goroutines → Close Resources
```

**Crash Recovery**:
- Service manager auto-restarts agent
- Agent reads unsent data from database
- Resumes transmission from where it left off

---

## Data Flow

### Normal Operation

```
┌─────────────────────────────────────────────────────────────┐
│ COLLECTION CYCLE (every 30s)                                 │
├─────────────────────────────────────────────────────────────┤
│ 1. Collect system metrics                                    │
│ 2. Store metrics in SQLite (sent=0)                          │
│ 3. Evaluate against thresholds                               │
│ 4. If alert generated:                                       │
│    a. Store alert (sent=0)                                   │
│    b. If WARNING/CRITICAL → Trigger immediate send           │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│ SEND CYCLE (every 60s)                                       │
├─────────────────────────────────────────────────────────────┤
│ 1. Query unsent alerts (ORDER BY timestamp, severity)        │
│ 2. Send alerts to server                                     │
│ 3. Mark sent alerts (sent=1, sent_at=now)                    │
│ 4. Query unsent metrics (ORDER BY timestamp, LIMIT 100)      │
│ 5. Send metrics batch to server                              │
│ 6. Mark sent metrics (sent=1, sent_at=now)                   │
└─────────────────────────────────────────────────────────────┘
```

### Offline Operation

```
┌─────────────────────────────────────────────────────────────┐
│ SERVER UNREACHABLE                                           │
├─────────────────────────────────────────────────────────────┤
│ 1. Collection continues normally                             │
│ 2. Metrics accumulate in database (sent=0)                   │
│ 3. Alerts accumulate in database (sent=0)                    │
│ 4. Send attempts fail → Logged → Data retained              │
│ 5. Agent continues collecting                                │
└─────────────────────────────────────────────────────────────┘
                                │
                                │ (Server comes back online)
                                ▼
┌─────────────────────────────────────────────────────────────┐
│ RECOVERY MODE                                                │
├─────────────────────────────────────────────────────────────┤
│ 1. Next send cycle detects unsent data                       │
│ 2. Send unsent alerts FIRST (oldest first)                   │
│ 3. Send unsent metrics in batches                            │
│ 4. Continue until all backlog cleared                        │
│ 5. Resume normal operation                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Service Integration

### Windows Service

**Mechanism**: `kardianos/service` library + Windows Service Manager

**Service Properties**:
- Name: `NetworkMonitorAgent`
- Start Type: Automatic
- Recovery: Restart on failure
- Runs as: SYSTEM account

**Lifecycle**:
```
Service Manager → Start() → Run() → Agent goroutines
Service Manager → Stop()  → Cleanup → Exit
```

---

### Linux systemd

**Service Unit**: `/etc/systemd/system/network-monitor-agent.service`

**Type**: `simple` (foreground process)

**Features**:
- Auto-restart on failure (`Restart=always`)
- 10-second restart delay (`RestartSec=10`)
- Journal logging
- Security hardening (NoNewPrivileges, PrivateTmp, ProtectSystem)

**Lifecycle**:
```
systemd → ExecStart → Agent process → Goroutines
systemd → SIGTERM → Stop() → Graceful shutdown
```

---

### macOS launchd

**Plist**: `/Library/LaunchDaemons/com.faber.network-monitor-agent.plist`

**Features**:
- Launch at boot (`RunAtLoad`)
- Keep alive (`KeepAlive`)
- Auto-restart on crash
- Throttle interval: 10s

**Lifecycle**:
```
launchd → Launch → Agent process → Goroutines
launchd → Signal → Stop() → Graceful shutdown
```

---

## Configuration Management

**Format**: YAML

**Loading**: `internal/config/config.go`

**Validation**: Defaults set for missing values

**Hot Reload**: Not supported (requires service restart)

**Sensitive Data**: Stored in config file, protected by OS permissions

---

## Security Architecture

### Network Security
- HTTPS only (TLS 1.2+)
- Server certificate validation
- Optional: mTLS for mutual authentication

### Local Security
- Config file: 600 permissions (owner read/write only)
- Database file: Restricted to agent user
- Logs: Restricted to agent user

### Privilege Model
- Runs as privileged user (root/SYSTEM) for full system metrics access
- No network listening (outbound only)
- No user input (config file only)

---

## Performance Characteristics

### Resource Usage

**CPU**: < 1% average
- Brief spike during collection (1-2s)
- Minimal during idle

**Memory**: 20-50 MB RSS
- SQLite page cache: configurable
- Go runtime overhead: ~10 MB
- Metrics buffer: minimal

**Disk I/O**:
- Write: Every 30s (metrics)
- Read: Every 60s (unsent data query)
- WAL checkpoint: periodic

**Network**:
- Outbound only
- 10-50 KB/min typical
- Batch size controls traffic

### Scalability

**Single Agent**:
- Handles 1000s of metrics/hour
- Database grows ~10 MB/day (typical)
- Retention policy prevents unbounded growth

**Server Load** (per 1000 agents):
- 100,000 metrics/min
- 1,000 potential alerts/min
- 1-5 GB/day storage

---

## Error Handling

### Collection Errors
- Log error
- Skip current metric
- Continue with other metrics
- Do not crash

### Storage Errors
- Log error
- Retry transaction
- If persistent: Alert and continue

### Transmission Errors
- Log error
- Increment retry count
- Data remains in database
- Next cycle retries

### Service Errors
- Auto-restart via service manager
- Resume from database state
- No data loss

---

## Testing Strategy

### Unit Tests
- Each package independently testable
- Mock database for storage tests
- Mock HTTP for sender tests

### Integration Tests
- End-to-end metric collection
- Database persistence
- Alert generation

### Manual Tests
- Service installation
- Crash recovery
- Offline operation
- Server unavailability

---

## Future Enhancements

Potential improvements:

1. **Compression**: Gzip HTTP payloads
2. **Encryption**: Encrypt local database
3. **Plugins**: Support for custom collectors
4. **Hot Reload**: Config changes without restart
5. **Adaptive Intervals**: Adjust collection based on load
6. **Push Metrics**: Expose local metrics endpoint
7. **Self-Monitoring**: Expose agent health metrics

---

## Troubleshooting Guide

### High Memory Usage
- Check database size
- Reduce retention period
- Lower batch size

### High Disk Usage
- Database growing too large
- Increase cleanup frequency
- Reduce retention period

### Missing Metrics
- Check collector errors in logs
- Verify gopsutil compatibility
- Check system permissions

### Delayed Alerts
- Check sender logs
- Verify network connectivity
- Check server response times

---

## Technical Decisions & Trade-offs

### Why SQLite?
**Pros**: Embedded, no external dependencies, reliable, proven
**Cons**: Single-writer limit (acceptable for our use case)

### Why Go?
**Pros**: Cross-platform, single binary, great concurrency, small footprint
**Cons**: Not as lightweight as C (acceptable trade-off)

### Why Service Libraries?
**Pros**: Handles platform differences, battle-tested
**Cons**: Adds dependency (acceptable, well-maintained)

### Why HTTP/JSON?
**Pros**: Universal, easy to debug, firewall-friendly
**Cons**: More verbose than binary protocols (acceptable for metrics volume)

---

This architecture provides a robust, production-ready monitoring solution that prioritizes reliability, data durability, and operational simplicity.
