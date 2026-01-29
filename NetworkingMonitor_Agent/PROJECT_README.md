# Network Monitor Agent

Enterprise-grade network monitoring agent with mTLS security for collecting system metrics, alerts, and SNMP data.

## Overview

The Network Monitor Agent is a cross-platform monitoring solution that collects system metrics (CPU, memory, disk, network, temperature) and SNMP device metrics, then securely transmits them to a central monitoring server using mutual TLS (mTLS) authentication.

## Key Features

- **System Metrics Collection**: CPU, memory, disk, network, temperature
- **SNMP Monitoring**: Monitor remote devices via SNMP v2c/v3
- **Alert Engine**: Threshold-based alerts (WARNING, CRITICAL)
- **Local Storage**: SQLite database with 30-day retention
- **Secure Communication**: mTLS (mutual TLS) authentication
- **Cross-Platform**: Windows, Linux, macOS support
- **Service Installation**: Run as Windows Service or Linux daemon

## Technology Stack

### Language & Runtime
- **Go 1.21+**: Main programming language
- **Go Modules**: Dependency management

### Core Libraries
- `github.com/shirou/gopsutil/v3`: System metrics collection
- `github.com/gosnmp/gosnmp`: SNMP device monitoring
- `github.com/mattn/go-sqlite3`: SQLite database driver
- `github.com/kardianos/service`: Cross-platform service management
- `gopkg.in/yaml.v3`: Configuration file parsing

### Database
- **SQLite 3**: Embedded database
- **WAL Mode**: Write-Ahead Logging for better concurrency
- **Retention**: 30-day automatic cleanup

## Database Schema

### Tables

**metrics** - System metrics data
- id (INTEGER PRIMARY KEY)
- timestamp (DATETIME)
- metric_type (TEXT): cpu_usage, memory_usage, disk_usage, etc.
- value (REAL): Metric value
- unit (TEXT): %, MB, GB, °C, etc.
- metadata (TEXT): JSON additional data
- sent (BOOLEAN): Transmission status
- created_at (DATETIME)

**alerts** - System alerts
- id (INTEGER PRIMARY KEY)
- timestamp (DATETIME)
- severity (TEXT): INFO, WARNING, CRITICAL
- metric_type (TEXT)
- value (REAL)
- threshold (REAL)
- message (TEXT)
- retry_count (INTEGER)
- sent (BOOLEAN)
- created_at (DATETIME)

**system_info** - System information
- id (INTEGER PRIMARY KEY)
- hostname (TEXT)
- os (TEXT)
- platform (TEXT)
- cpu_cores (INTEGER)
- total_memory (INTEGER)
- updated_at (DATETIME)

**transmission_log** - Transmission audit log
- id (INTEGER PRIMARY KEY)
- timestamp (DATETIME)
- data_type (TEXT): metric, alert, snmp_metric
- data_id (INTEGER)
- status (TEXT): success, failed
- error_message (TEXT)

**snmp_metrics** - SNMP device metrics
- id (INTEGER PRIMARY KEY)
- timestamp (DATETIME)
- device_name (TEXT)
- device_host (TEXT)
- oid (TEXT)
- metric_name (TEXT)
- value (REAL)
- value_type (TEXT)
- metadata (TEXT)
- sent (BOOLEAN)
- created_at (DATETIME)

**snmp_devices** - SNMP device registry
- id (INTEGER PRIMARY KEY)
- name (TEXT UNIQUE)
- host (TEXT)
- port (INTEGER)
- community (TEXT)
- version (TEXT)
- enabled (BOOLEAN)
- last_poll (DATETIME)
- created_at (DATETIME)

## Project Structure

```
NetworkingMonitor_Client/
├── cmd/
│   └── agent/
│       └── main.go                 # Application entry point
├── internal/
│   ├── agent/
│   │   └── agent.go                # Main agent logic
│   ├── collector/
│   │   └── collector.go            # System metrics collector
│   ├── config/
│   │   └── config.go               # Configuration management
│   ├── logger/
│   │   └── logger.go               # Logging functionality
│   ├── sender/
│   │   └── sender.go               # Metrics transmission (mTLS)
│   ├── storage/
│   │   └── storage.go              # SQLite database operations
│   └── snmp/
│       └── manager.go              # SNMP device management
├── examples/
│   └── test-server/
│       └── main-mtls.go            # mTLS test server
├── scripts/
│   ├── generate-certs.ps1          # Windows cert generation
│   ├── generate-certs.sh           # Linux/macOS cert generation
│   ├── install-windows.bat         # Windows service installer
│   ├── uninstall-windows.bat       # Windows service uninstaller
│   ├── install-linux.sh            # Linux service installer
│   └── uninstall-linux.sh          # Linux service uninstaller
├── certs/                          # SSL/TLS certificates
│   ├── ca.crt                      # Certificate Authority
│   ├── ca.key                      # CA private key
│   ├── server.crt                  # Server certificate
│   ├── server.key                  # Server private key
│   ├── client.crt                  # Client certificate
│   └── client.key                  # Client private key
├── config.yaml                     # Agent configuration
├── agent.db                        # SQLite database (runtime)
├── agent.log                       # Log file (runtime)
├── go.mod                          # Go module definition
├── go.sum                          # Go dependencies checksums
└── network-monitor-agent.exe       # Compiled binary (Windows)
```

## Configuration

**config.yaml** - Main configuration file

```yaml
server:
  url: "https://localhost:8443/api/v1"
  timeout: 30s
  retry_attempts: 3
  retry_delay: 5s
  tls:
    enabled: true
    client_cert_path: "./certs/client.crt"
    client_key_path: "./certs/client.key"
    ca_cert_path: "./certs/ca.crt"
    insecure_skip_verify: false
    min_tls_version: "1.2"

agent:
  id: ""                            # Auto-generated if empty
  name: ""                          # Hostname if empty
  collect_interval: 30s             # Metrics collection frequency
  send_interval: 120s               # Transmission frequency
  batch_size: 100                   # Max metrics per batch

snmp_manager:
  enabled: true
  poll_interval: 30s
  devices:
    - name: "device1"
      host: "192.168.1.10"
      port: 161
      community: "public"
      version: "2c"
      timeout: 5s
      retries: 3
      oids:
        - oid: "1.3.6.1.2.1.1.3.0"
          name: "system_uptime"
          type: "counter"

database:
  path: "./agent.db"
  retention_days: 30

alerts:
  cpu:
    warning: 80.0
    critical: 95.0
  memory:
    warning: 85.0
    critical: 95.0
  disk:
    warning: 85.0
    critical: 95.0
  temperature:
    warning: 75.0
    critical: 85.0

logging:
  level: "info"                     # debug, info, warn, error
  file: "./agent.log"
  max_size_mb: 100
  max_backups: 5
```

## Security

### mTLS (Mutual TLS) Authentication

The agent uses mutual TLS for secure communication:

1. **Certificate Authority (CA)**
   - Self-signed root CA
   - Signs both server and client certificates
   - 10-year validity

2. **Server Certificate**
   - Used by monitoring server
   - Includes Subject Alternative Names (SANs)
   - DNS: localhost, IP: 127.0.0.1, ::1
   - 1-year validity

3. **Client Certificate**
   - Unique per agent
   - Includes SAN with hostname
   - Used for client authentication
   - 1-year validity

### Certificate Requirements

**Critical**: Certificates MUST include Subject Alternative Names (SANs)
- Go 1.15+ deprecated Common Name (CN) for hostname verification
- Go 1.17+ requires SANs - CN-only certificates will fail
- CA certificate must have `CA:TRUE` extension

### TLS Configuration

- Minimum TLS version: 1.2
- Client certificate required
- Server certificate verified against CA
- Hostname verification enabled

## How It Works

### 1. Metrics Collection

```
┌─────────────────┐
│  Collector      │
│  - CPU          │
│  - Memory       │──┐
│  - Disk         │  │
│  - Network      │  │
│  - Temperature  │  │
└─────────────────┘  │
                     │
┌─────────────────┐  │
│  SNMP Manager   │  │
│  - Poll devices │──┤
│  - Read OIDs    │  │
└─────────────────┘  │
                     ▼
              ┌──────────────┐
              │  SQLite DB   │
              │  - Store     │
              │  - Buffer    │
              └──────────────┘
```

### 2. Alert Processing

```
Metric Value
     │
     ▼
┌─────────────┐
│ Threshold   │
│ Check       │
└─────────────┘
     │
     ├─── Normal ──────► Continue
     │
     └─── Alert ───────► Store in DB
                              │
                              ▼
                         Send Immediately
                         (bypass queue)
```

### 3. Data Transmission

```
┌──────────────┐
│  Send Queue  │
│  Processor   │
└──────────────┘
     │
     ├─── Priority 1: Alerts
     ├─── Priority 2: Metrics
     └─── Priority 3: SNMP Metrics
          │
          ▼
    ┌────────────┐
    │  mTLS      │
    │  Sender    │──► HTTPS POST ──► Server
    └────────────┘
          │
          ├─── Success ──► Mark as sent
          └─── Failure ──► Retry (3 attempts)
```

## API Endpoints

The agent sends data to these server endpoints:

**POST /api/v1/metrics**
```json
{
  "agent_id": "string",
  "timestamp": "2026-01-29T12:00:00Z",
  "metrics": [
    {
      "id": 1,
      "timestamp": "2026-01-29T12:00:00Z",
      "metric_type": "cpu_usage",
      "value": 45.5,
      "unit": "%",
      "metadata": {},
      "created_at": "2026-01-29T12:00:00Z"
    }
  ]
}
```

**POST /api/v1/alerts**
```json
{
  "agent_id": "string",
  "timestamp": "2026-01-29T12:00:00Z",
  "alerts": [
    {
      "id": 1,
      "timestamp": "2026-01-29T12:00:00Z",
      "severity": "WARNING",
      "metric_type": "memory_usage",
      "value": 92.0,
      "threshold": 85.0,
      "message": "Memory usage exceeded warning threshold",
      "retry_count": 0,
      "created_at": "2026-01-29T12:00:00Z"
    }
  ]
}
```

**POST /api/v1/snmp-metrics**
```json
{
  "agent_id": "string",
  "timestamp": "2026-01-29T12:00:00Z",
  "snmp_metrics": [
    {
      "id": 1,
      "timestamp": "2026-01-29T12:00:00Z",
      "device_name": "router1",
      "device_host": "192.168.1.1",
      "oid": "1.3.6.1.2.1.1.3.0",
      "metric_name": "uptime",
      "value": 12345,
      "value_type": "counter",
      "metadata": {},
      "created_at": "2026-01-29T12:00:00Z"
    }
  ]
}
```

## Performance

- **Metrics Collection**: 30-second intervals (configurable)
- **Data Transmission**: 2-minute batches (configurable)
- **Database**: SQLite with WAL mode for concurrent access
- **Memory Usage**: ~50-100 MB typical
- **CPU Usage**: <1% idle, 2-5% during collection
- **Disk Usage**: ~10-50 MB database (30-day retention)

## System Requirements

### Agent Machine
- **OS**: Windows 10+, Linux (kernel 3.10+), macOS 10.14+
- **CPU**: 1 core minimum
- **RAM**: 512 MB minimum
- **Disk**: 100 MB minimum
- **Network**: Outbound HTTPS (port 8443 or custom)

### Monitored Devices (SNMP)
- **SNMP**: v2c or v3 support
- **Network**: Accessible from agent machine
- **MIBs**: Standard MIB-II support

## License

Internal use - Network Monitor Agent
Version 1.0.0

---

**Documentation Updated**: January 29, 2026
