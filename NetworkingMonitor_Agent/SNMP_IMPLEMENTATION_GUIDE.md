# SNMP Implementation Guide

This guide explains how to add SNMP functionality to the Network Monitor Agent.

## Overview

### Two SNMP Modes

1. **SNMP Manager** - Query SNMP-enabled devices (routers, switches, printers)
2. **SNMP Agent** - Expose collected metrics via SNMP to other monitoring systems

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Network Monitor Agent (Enhanced)                │
│                                                              │
│  ┌──────────────┐     ┌──────────────┐                     │
│  │   System     │     │    SNMP      │                     │
│  │  Collector   │     │   Manager    │                     │
│  │ (CPU, RAM)   │     │  (Devices)   │                     │
│  └──────┬───────┘     └──────┬───────┘                     │
│         │                     │                              │
│         └─────────┬───────────┘                             │
│                   ▼                                          │
│         ┌──────────────────┐                                │
│         │   SQLite DB      │                                │
│         │  - System Metrics│                                │
│         │  - SNMP Metrics  │                                │
│         └─────────┬────────┘                                │
│                   │                                          │
│                   ▼                                          │
│         ┌──────────────────┐                                │
│         │  Alert Engine    │                                │
│         └─────────┬────────┘                                │
│                   │                                          │
│                   ▼                                          │
│         ┌──────────────────┐                                │
│         │     Sender       │                                │
│         └─────────┬────────┘                                │
│                   │                                          │
│  ┌────────────────┼────────────────┐                       │
│  │                │                 │                       │
│  ▼                ▼                 ▼                       │
│ HTTP         SNMP Agent     Monitoring Server              │
│ API          (Port 161)                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1: Add SNMP Dependencies

### Update go.mod

Add the SNMP library:

```go
require (
    // ... existing dependencies ...
    github.com/gosnmp/gosnmp v1.37.0
)
```

### Install Dependencies

```bash
go get github.com/gosnmp/gosnmp
go mod tidy
```

---

## Step 2: Update Configuration

### Add SNMP Configuration to config.yaml

```yaml
# Server settings
server:
  url: "http://localhost:8080/api/v1"
  timeout: 30s

# Agent settings
agent:
  collect_interval: 60s
  send_interval: 300s

# SNMP Manager settings (query devices)
snmp_manager:
  enabled: true
  poll_interval: 120s  # How often to poll SNMP devices
  devices:
    - name: "core-router"
      host: "192.168.1.1"
      port: 161
      community: "public"
      version: "2c"  # Options: 1, 2c, 3
      timeout: 5s
      retries: 3
      oids:
        - oid: "1.3.6.1.2.1.1.3.0"        # sysUpTime
          name: "uptime"
          type: "counter"
        - oid: "1.3.6.1.2.1.2.2.1.10"     # ifInOctets (interface traffic)
          name: "interface_in_bytes"
          type: "counter"
        - oid: "1.3.6.1.2.1.2.2.1.16"     # ifOutOctets
          name: "interface_out_bytes"
          type: "counter"

    - name: "office-printer"
      host: "192.168.1.100"
      port: 161
      community: "public"
      version: "2c"
      timeout: 5s
      oids:
        - oid: "1.3.6.1.2.1.43.10.2.1.4.1.1"  # Toner level
          name: "toner_black"
          type: "gauge"

# SNMP Agent settings (expose metrics via SNMP)
snmp_agent:
  enabled: false
  listen_address: "0.0.0.0:161"
  community: "public"
  # Expose collected metrics as SNMP OIDs

# Database settings
database:
  path: "./agent.db"
  retention_days: 30

# Alert thresholds
alerts:
  cpu:
    warning: 80.0
    critical: 95.0
  # Add SNMP device alerts
  snmp:
    interface_utilization:
      warning: 80.0
      critical: 95.0
    printer_toner:
      warning: 20.0  # Toner below 20%
      critical: 10.0
```

---

## Step 3: Update Database Schema

### Add SNMP Metrics Table

Add to `internal/storage/schema.go`:

```sql
-- SNMP metrics table
CREATE TABLE IF NOT EXISTS snmp_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    device_name TEXT NOT NULL,
    device_host TEXT NOT NULL,
    oid TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    value_type TEXT,  -- counter, gauge, string
    metadata TEXT,
    sent INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_snmp_metrics_timestamp ON snmp_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_device ON snmp_metrics(device_name);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_sent ON snmp_metrics(sent);

-- SNMP devices table (track device info)
CREATE TABLE IF NOT EXISTS snmp_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL UNIQUE,
    device_host TEXT NOT NULL,
    community TEXT NOT NULL,
    version TEXT NOT NULL,
    last_poll_success INTEGER,
    last_poll_time INTEGER,
    total_polls INTEGER DEFAULT 0,
    failed_polls INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

---

## Step 4: Create SNMP Configuration Module

Create `internal/config/snmp_config.go`:

```go
package config

import "time"

type SNMPManagerConfig struct {
    Enabled      bool                `yaml:"enabled"`
    PollInterval time.Duration       `yaml:"poll_interval"`
    Devices      []SNMPDeviceConfig  `yaml:"devices"`
}

type SNMPDeviceConfig struct {
    Name      string              `yaml:"name"`
    Host      string              `yaml:"host"`
    Port      uint16              `yaml:"port"`
    Community string              `yaml:"community"`
    Version   string              `yaml:"version"`  // "1", "2c", "3"
    Timeout   time.Duration       `yaml:"timeout"`
    Retries   int                 `yaml:"retries"`
    OIDs      []SNMPOIDConfig     `yaml:"oids"`
}

type SNMPOIDConfig struct {
    OID  string `yaml:"oid"`
    Name string `yaml:"name"`
    Type string `yaml:"type"`  // counter, gauge, string
}

type SNMPAgentConfig struct {
    Enabled       bool   `yaml:"enabled"`
    ListenAddress string `yaml:"listen_address"`
    Community     string `yaml:"community"`
}
```

Update `internal/config/config.go` to include SNMP configs:

```go
type Config struct {
    Server      ServerConfig      `yaml:"server"`
    Agent       AgentConfig       `yaml:"agent"`
    SNMPManager SNMPManagerConfig `yaml:"snmp_manager"`
    SNMPAgent   SNMPAgentConfig   `yaml:"snmp_agent"`
    Database    DatabaseConfig    `yaml:"database"`
    Alerts      AlertsConfig      `yaml:"alerts"`
    Logging     LoggingConfig     `yaml:"logging"`
}
```

---

## Step 5: Create SNMP Manager Module

Create `internal/snmp/manager.go`:

```go
package snmp

import (
    "fmt"
    "time"

    "github.com/faber/network-monitor-agent/internal/config"
    "github.com/faber/network-monitor-agent/internal/logger"
    "github.com/faber/network-monitor-agent/internal/storage"
    "github.com/gosnmp/gosnmp"
)

type Manager struct {
    config  config.SNMPManagerConfig
    storage *storage.Storage
    logger  *logger.Logger
}

func NewManager(cfg config.SNMPManagerConfig, store *storage.Storage, log *logger.Logger) *Manager {
    return &Manager{
        config:  cfg,
        storage: store,
        logger:  log,
    }
}

// PollDevices queries all configured SNMP devices
func (m *Manager) PollDevices() error {
    m.logger.Infof("Polling %d SNMP devices", len(m.config.Devices))

    for _, deviceCfg := range m.config.Devices {
        if err := m.pollDevice(deviceCfg); err != nil {
            m.logger.Errorf("Failed to poll device %s: %v", deviceCfg.Name, err)
            continue
        }
    }

    return nil
}

func (m *Manager) pollDevice(deviceCfg config.SNMPDeviceConfig) error {
    // Create SNMP connection
    snmpClient := &gosnmp.GoSNMP{
        Target:    deviceCfg.Host,
        Port:      deviceCfg.Port,
        Community: deviceCfg.Community,
        Version:   m.parseVersion(deviceCfg.Version),
        Timeout:   deviceCfg.Timeout,
        Retries:   deviceCfg.Retries,
    }

    err := snmpClient.Connect()
    if err != nil {
        return fmt.Errorf("connect to device: %w", err)
    }
    defer snmpClient.Conn.Close()

    timestamp := time.Now()

    // Query each OID
    for _, oidCfg := range deviceCfg.OIDs {
        result, err := snmpClient.Get([]string{oidCfg.OID})
        if err != nil {
            m.logger.Warnf("Failed to get OID %s from %s: %v", oidCfg.OID, deviceCfg.Name, err)
            continue
        }

        for _, variable := range result.Variables {
            value := m.convertValue(variable)

            // Store in database
            metric := &storage.SNMPMetric{
                Timestamp:  timestamp,
                DeviceName: deviceCfg.Name,
                DeviceHost: deviceCfg.Host,
                OID:        oidCfg.OID,
                MetricName: oidCfg.Name,
                Value:      value,
                ValueType:  oidCfg.Type,
                CreatedAt:  time.Now(),
            }

            if err := m.storage.SaveSNMPMetric(metric); err != nil {
                m.logger.Errorf("Failed to save SNMP metric: %v", err)
            } else {
                m.logger.Debugf("Saved SNMP metric: %s.%s = %.2f", deviceCfg.Name, oidCfg.Name, value)
            }
        }
    }

    return nil
}

func (m *Manager) parseVersion(version string) gosnmp.SnmpVersion {
    switch version {
    case "1":
        return gosnmp.Version1
    case "2c":
        return gosnmp.Version2c
    case "3":
        return gosnmp.Version3
    default:
        return gosnmp.Version2c
    }
}

func (m *Manager) convertValue(variable gosnmp.SnmpPDU) float64 {
    switch variable.Type {
    case gosnmp.Counter32, gosnmp.Counter64:
        return float64(gosnmp.ToBigInt(variable.Value).Uint64())
    case gosnmp.Gauge32:
        return float64(gosnmp.ToBigInt(variable.Value).Uint64())
    case gosnmp.Integer:
        return float64(gosnmp.ToBigInt(variable.Value).Int64())
    default:
        return 0
    }
}
```

---

## Step 6: Update Storage Module

Add SNMP methods to `internal/storage/storage.go`:

```go
type SNMPMetric struct {
    ID         int64
    Timestamp  time.Time
    DeviceName string
    DeviceHost string
    OID        string
    MetricName string
    Value      float64
    ValueType  string
    Metadata   map[string]interface{}
    Sent       bool
    CreatedAt  time.Time
    SentAt     *time.Time
}

func (s *Storage) SaveSNMPMetric(m *SNMPMetric) error {
    metadata, _ := json.Marshal(m.Metadata)

    result, err := s.db.Exec(`
        INSERT INTO snmp_metrics (timestamp, device_name, device_host, oid, metric_name, value, value_type, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `, m.Timestamp.Unix(), m.DeviceName, m.DeviceHost, m.OID, m.MetricName, m.Value, m.ValueType, string(metadata), time.Now().Unix())

    if err != nil {
        return fmt.Errorf("insert SNMP metric: %w", err)
    }

    m.ID, _ = result.LastInsertId()
    return nil
}

func (s *Storage) GetUnsentSNMPMetrics(limit int) ([]*SNMPMetric, error) {
    rows, err := s.db.Query(`
        SELECT id, timestamp, device_name, device_host, oid, metric_name, value, value_type, metadata, created_at
        FROM snmp_metrics
        WHERE sent = 0
        ORDER BY timestamp ASC
        LIMIT ?
    `, limit)
    if err != nil {
        return nil, fmt.Errorf("query unsent SNMP metrics: %w", err)
    }
    defer rows.Close()

    var metrics []*SNMPMetric
    for rows.Next() {
        var m SNMPMetric
        var ts, createdAt int64
        var metadata string

        err := rows.Scan(&m.ID, &ts, &m.DeviceName, &m.DeviceHost, &m.OID, &m.MetricName, &m.Value, &m.ValueType, &metadata, &createdAt)
        if err != nil {
            return nil, fmt.Errorf("scan SNMP metric: %w", err)
        }

        m.Timestamp = time.Unix(ts, 0)
        m.CreatedAt = time.Unix(createdAt, 0)
        json.Unmarshal([]byte(metadata), &m.Metadata)

        metrics = append(metrics, &m)
    }

    return metrics, nil
}

func (s *Storage) MarkSNMPMetricsSent(ids []int64) error {
    if len(ids) == 0 {
        return nil
    }

    query := "UPDATE snmp_metrics SET sent = 1, sent_at = ? WHERE id IN ("
    args := []interface{}{time.Now().Unix()}
    for i, id := range ids {
        if i > 0 {
            query += ","
        }
        query += "?"
        args = append(args, id)
    }
    query += ")"

    _, err := s.db.Exec(query, args...)
    return err
}
```

---

## Step 7: Integrate with Main Agent

Update `internal/agent/agent.go`:

```go
import (
    // ... existing imports ...
    "github.com/faber/network-monitor-agent/internal/snmp"
)

type Agent struct {
    config       *config.Config
    logger       *logger.Logger
    storage      *storage.Storage
    collector    *collector.Collector
    snmpManager  *snmp.Manager  // NEW
    alertEngine  *alerts.AlertEngine
    sender       *sender.Sender
    stopCh       chan struct{}
    wg           sync.WaitGroup
}

func New(cfg *config.Config, log *logger.Logger) (*Agent, error) {
    // ... existing code ...

    // Create SNMP manager if enabled
    var snmpMgr *snmp.Manager
    if cfg.SNMPManager.Enabled {
        snmpMgr = snmp.NewManager(cfg.SNMPManager, store, log)
        log.Infof("SNMP Manager enabled with %d devices", len(cfg.SNMPManager.Devices))
    }

    agent := &Agent{
        config:      cfg,
        logger:      log,
        storage:     store,
        collector:   col,
        snmpManager: snmpMgr,  // NEW
        alertEngine: alertEng,
        sender:      snd,
        stopCh:      make(chan struct{}),
    }

    return agent, nil
}

func (a *Agent) Run() {
    // ... existing code ...

    // Start SNMP polling loop if enabled
    if a.snmpManager != nil {
        a.wg.Add(1)
        go a.snmpPollLoop()
    }

    // ... rest of code ...
}

// snmpPollLoop periodically polls SNMP devices
func (a *Agent) snmpPollLoop() {
    defer a.wg.Done()

    ticker := time.NewTicker(a.config.SNMPManager.PollInterval)
    defer ticker.Stop()

    // Poll immediately on startup
    a.snmpManager.PollDevices()

    for {
        select {
        case <-ticker.C:
            a.snmpManager.PollDevices()
        case <-a.stopCh:
            return
        }
    }
}
```

---

## Step 8: Update Sender to Send SNMP Metrics

Update `internal/sender/sender.go`:

```go
// ProcessQueue now also sends SNMP metrics
func (s *Sender) ProcessQueue(batchSize int) error {
    // Priority 1: Send unsent alerts
    // ... existing code ...

    // Priority 2: Send normal metrics
    // ... existing code ...

    // Priority 3: Send SNMP metrics
    snmpMetrics, err := s.storage.GetUnsentSNMPMetrics(batchSize)
    if err != nil {
        return fmt.Errorf("get unsent SNMP metrics: %w", err)
    }

    if len(snmpMetrics) > 0 {
        s.logger.Infof("Processing %d unsent SNMP metrics", len(snmpMetrics))
        if err := s.SendSNMPMetrics(snmpMetrics); err != nil {
            s.logger.Errorf("Failed to send SNMP metrics: %v", err)
            return err
        }
    }

    return nil
}

type SNMPMetricsPayload struct {
    AgentID   string                  `json:"agent_id"`
    Timestamp time.Time               `json:"timestamp"`
    Metrics   []*storage.SNMPMetric   `json:"snmp_metrics"`
}

func (s *Sender) SendSNMPMetrics(metrics []*storage.SNMPMetric) error {
    if len(metrics) == 0 {
        return nil
    }

    payload := SNMPMetricsPayload{
        AgentID:   s.agentID,
        Timestamp: time.Now(),
        Metrics:   metrics,
    }

    data, err := json.Marshal(payload)
    if err != nil {
        return fmt.Errorf("marshal SNMP metrics payload: %w", err)
    }

    url := s.config.URL + "/snmp-metrics"
    resp, err := s.sendRequest("POST", url, data)
    if err == nil && resp.Success {
        var ids []int64
        for _, metric := range metrics {
            ids = append(ids, metric.ID)
        }
        s.storage.MarkSNMPMetricsSent(ids)
        s.logger.Infof("Sent %d SNMP metrics successfully", len(metrics))
        return nil
    }

    return fmt.Errorf("failed to send SNMP metrics: %w", err)
}
```

---

## Step 9: Common SNMP OIDs Reference

### System Information
```yaml
oids:
  - oid: "1.3.6.1.2.1.1.1.0"       # sysDescr (System description)
    name: "system_description"
    type: "string"

  - oid: "1.3.6.1.2.1.1.3.0"       # sysUpTime (Uptime in timeticks)
    name: "uptime"
    type: "counter"

  - oid: "1.3.6.1.2.1.1.5.0"       # sysName (System name)
    name: "system_name"
    type: "string"
```

### Interface Statistics
```yaml
oids:
  - oid: "1.3.6.1.2.1.2.2.1.10.1"  # ifInOctets.1 (Bytes in on interface 1)
    name: "interface1_in_bytes"
    type: "counter"

  - oid: "1.3.6.1.2.1.2.2.1.16.1"  # ifOutOctets.1 (Bytes out on interface 1)
    name: "interface1_out_bytes"
    type: "counter"

  - oid: "1.3.6.1.2.1.2.2.1.8.1"   # ifOperStatus.1 (Interface status)
    name: "interface1_status"
    type: "gauge"
```

### CPU and Memory (if supported)
```yaml
oids:
  - oid: "1.3.6.1.4.1.2021.11.9.0"   # ssCpuIdle
    name: "cpu_idle"
    type: "gauge"

  - oid: "1.3.6.1.4.1.2021.4.11.0"  # memTotalReal
    name: "memory_total"
    type: "gauge"
```

### Printer Specific
```yaml
oids:
  - oid: "1.3.6.1.2.1.43.10.2.1.4.1.1"  # Toner black
    name: "toner_black"
    type: "gauge"

  - oid: "1.3.6.1.2.1.43.10.2.1.5.1.1"  # Toner max capacity
    name: "toner_black_max"
    type: "gauge"
```

---

## Step 10: Testing SNMP Manager

### Test Configuration

Create `config-snmp-test.yaml`:

```yaml
server:
  url: "http://localhost:8080/api/v1"

agent:
  collect_interval: 60s
  send_interval: 300s

snmp_manager:
  enabled: true
  poll_interval: 30s  # Poll every 30 seconds for testing
  devices:
    - name: "test-device"
      host: "192.168.1.1"  # Change to your device IP
      port: 161
      community: "public"
      version: "2c"
      timeout: 5s
      retries: 2
      oids:
        - oid: "1.3.6.1.2.1.1.3.0"
          name: "uptime"
          type: "counter"
        - oid: "1.3.6.1.2.1.1.5.0"
          name: "hostname"
          type: "string"

database:
  path: "./agent.db"

logging:
  level: "debug"
  file: "./agent.log"
```

### Run Test

```bash
# Build with SNMP support
go build -o network-monitor-agent.exe .

# Run with test config
.\network-monitor-agent.exe -config config-snmp-test.yaml

# Watch logs
Get-Content agent.log -Wait -Tail 20
```

### Verify SNMP Data

```powershell
# Check database for SNMP metrics
sqlite3 agent.db "SELECT * FROM snmp_metrics LIMIT 10;"

# Or use PowerShell
# (Requires SQLite PowerShell module or export data)
```

---

## Step 11: Server API Update

Your server needs a new endpoint for SNMP metrics:

### POST /api/v1/snmp-metrics

**Request:**
```json
{
  "agent_id": "hostname-123456",
  "timestamp": "2026-01-20T10:30:00Z",
  "snmp_metrics": [
    {
      "timestamp": "2026-01-20T10:29:30Z",
      "device_name": "core-router",
      "device_host": "192.168.1.1",
      "oid": "1.3.6.1.2.1.1.3.0",
      "metric_name": "uptime",
      "value": 1234567.0,
      "value_type": "counter"
    }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "message": "Received 1 SNMP metrics"
}
```

---

## Summary: Implementation Steps

### Phase 1: Basic SNMP Manager (Query Devices)
1. ✅ Add gosnmp dependency
2. ✅ Update config.yaml with SNMP devices
3. ✅ Update database schema (add snmp_metrics table)
4. ✅ Create SNMP manager module
5. ✅ Integrate with main agent
6. ✅ Update sender to send SNMP data
7. ✅ Test with real SNMP device

### Phase 2: SNMP Agent (Expose Metrics)
8. Create SNMP agent module
9. Expose collected metrics as SNMP OIDs
10. Configure MIB definitions
11. Test with SNMP tools (snmpwalk, snmpget)

### Phase 3: Advanced Features
12. Add SNMP traps support
13. Add SNMP v3 authentication
14. Add SNMP discovery (auto-discover devices)
15. Add MIB browser/compiler

---

## Quick Start Command

```bash
# 1. Update dependencies
go get github.com/gosnmp/gosnmp
go mod tidy

# 2. Configure SNMP devices in config.yaml

# 3. Rebuild
go build -o network-monitor-agent.exe .

# 4. Test
.\network-monitor-agent.exe -config config.yaml
```

---

## Troubleshooting

### SNMP Connection Failures

**Check:**
- Device IP is reachable: `ping 192.168.1.1`
- SNMP port is open: `Test-NetConnection -ComputerName 192.168.1.1 -Port 161`
- Community string is correct
- SNMP is enabled on the device

### No Data Returned

**Check:**
- OID is valid for the device
- Use `snmpwalk` to test: `snmpwalk -v2c -c public 192.168.1.1`
- Check device SNMP configuration

### Permission Issues

**Windows:**
- SNMP queries typically don't require admin rights
- But installing as service does

---

**Next:** Would you like me to implement Phase 1 (SNMP Manager) in the actual code?
