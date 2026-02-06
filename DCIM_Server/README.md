# DCIM Server - Data Center Infrastructure Monitoring Server

Enterprise-grade central monitoring server for receiving and storing data from Network Monitor Agents.

## Features

- **mTLS Authentication** - Secure mutual TLS authentication with client certificates
- **Multi-Agent Support** - Monitor multiple agents simultaneously with license-based limits
- **Database Storage** - Store metrics, alerts, and SNMP data (SQLite, PostgreSQL, MySQL)
- **License Management** - Flexible licensing system with agent and device limits
- **Auto-Registration** - Automatic agent registration with optional approval workflow
- **Data Retention** - Configurable retention policies for different data types
- **RESTful API** - Clean API for receiving agent data
- **Health Monitoring** - Built-in health check endpoint
- **Hostname/IP Support** - Connect using hostname or IP address
- **Encrypted Communication** - All data transmitted over encrypted TLS channels

## Quick Start

### 1. Generate Certificates

Generate mTLS certificates using the built-in script (with automatic OpenSSL config fix):

```powershell
# Run certificate generation script
.\scripts\generate-certs.ps1
```

The script will:
- ✅ Automatically fix OpenSSL configuration issues
- ✅ Generate CA, server, and client certificates
- ✅ Verify all files were created successfully
- ✅ Provide clear error messages if anything fails

This creates:
- `certs/ca.crt` and `certs/ca.key` - Certificate Authority
- `certs/server.crt` and `certs/server.key` - Server certificate
- `certs/client.crt` and `certs/client.key` - Initial client certificate

**Generate additional client certificates for more agents:**
```powershell
.\scripts\generate-client-cert.ps1 -AgentName "agent-02"

# Creates certificates in: certs/agents/agent-02/
```

### 2. Generate License (Optional)

```powershell
# Generate a license for 100 agents, valid for 1 year
go run . -generate-license -license-company "Your Company" -license-email "admin@company.com" -license-agents 100 -license-snmp 500 -license-years 1
```

### 3. Configure Server

Edit `config.yaml` to customize settings:

```yaml
server:
  address: "0.0.0.0"
  port: 8443

tls:
  enabled: true
  server_cert_path: "./certs/server.crt"
  server_key_path: "./certs/server.key"
  ca_cert_path: "./certs/ca.crt"
  client_auth: "require_and_verify"

database:
  type: "sqlite"
  sqlite:
    path: "./data/dcim_server.db"

license:
  mode: "file"
  file_path: "./license.json"
  enforce: true
```

### 4. Build Server

```powershell
# Install dependencies
go mod download

# Build for Windows
go build -o dcim-server.exe .

# Build for Linux
$env:GOOS="linux"; $env:GOARCH="amd64"; go build -o dcim-server .

# Or use build script for all platforms
.\build.ps1 -Target dist -Version "1.0.0" # Build for all platforms
.\build.ps1                    # Build for all platforms
.\build.ps1 -Platform windows  # Windows only
.\build.ps1 -Platform linux    # Linux only
.\build.ps1 -Platform macos    # macOS only
```

**Build script output**: Creates platform-specific builds in `build/` directory with config templates and README files.

### 5. Run Server

```powershell
# Run directly
.\dcim-server.exe -config config.yaml

# Or with Go
go run . -config config.yaml
```

## Configuration

### Server Settings

```yaml
server:
  address: "0.0.0.0"           # Listen on all interfaces
  port: 8443                   # HTTPS port
  read_timeout: 30s
  write_timeout: 30s
  idle_timeout: 120s
  max_body_size: 10485760      # 10MB
```

### TLS Configuration

```yaml
tls:
  enabled: true
  server_cert_path: "./certs/server.crt"
  server_key_path: "./certs/server.key"
  ca_cert_path: "./certs/ca.crt"
  client_auth: "require_and_verify"  # Options: none, request, require, verify_if_given, require_and_verify
  min_tls_version: "1.2"             # Options: 1.2, 1.3
```

### Database Configuration

**SQLite (Default):**
```yaml
database:
  type: "sqlite"
  sqlite:
    path: "./data/dcim_server.db"
    max_open_conns: 25
    max_idle_conns: 5
```

**PostgreSQL:**
```yaml
database:
  type: "postgres"
  postgres:
    host: "localhost"
    port: 5432
    user: "dcim_user"
    password: "secure_password"
    database: "dcim_db"
    sslmode: "require"
```

**MySQL:**
```yaml
database:
  type: "mysql"
  mysql:
    host: "localhost"
    port: 3306
    user: "dcim_user"
    password: "secure_password"
    database: "dcim_db"
```

### Agent Management

```yaml
agents:
  connection:
    heartbeat_timeout: 300s              # Mark agent offline after 5 minutes
    identification_method: "certificate_cn"  # Options: certificate_cn, agent_id, both

  registration:
    auto_register: true                  # Automatically register new agents
    require_approval: false              # Require manual approval for new agents
    default_group: "default"

  validation:
    reject_old_metrics: 24h              # Reject metrics older than 24 hours
    reject_future_metrics: 5m            # Reject metrics from future (clock skew)
    max_metrics_per_batch: 1000
    max_alerts_per_batch: 100
```

### License Management

```yaml
license:
  mode: "file"                    # Options: file, database, disabled
  file_path: "./license.json"
  enforce: true                   # Enforce license limits
  grace_period_days: 7
  check_interval: 1h
```

### Data Retention

```yaml
database:
  retention:
    metrics_days: 90              # Keep metrics for 90 days (0 = forever)
    alerts_days: 365              # Keep alerts for 1 year
    agent_status_days: 30         # Keep agent status history for 30 days
    cleanup_interval: 24h         # Run cleanup daily
```

## API Endpoints

### Metrics

**POST /api/v1/metrics**

Receive system metrics from agents.

```json
{
  "agent_id": "agent-001",
  "timestamp": "2024-02-03T10:00:00Z",
  "metrics": [
    {
      "timestamp": "2024-02-03T10:00:00Z",
      "metric_type": "cpu_usage",
      "value": 45.2,
      "unit": "percent",
      "metadata": {}
    }
  ]
}
```

### Alerts

**POST /api/v1/alerts**

Receive alerts from agents.

```json
{
  "agent_id": "agent-001",
  "timestamp": "2024-02-03T10:00:00Z",
  "alerts": [
    {
      "timestamp": "2024-02-03T10:00:00Z",
      "severity": "WARNING",
      "metric_type": "cpu_usage",
      "value": 85.0,
      "threshold": 80.0,
      "message": "CPU usage above threshold",
      "retry_count": 0
    }
  ]
}
```

### SNMP Metrics

**POST /api/v1/snmp-metrics**

Receive SNMP device metrics from agents.

```json
{
  "agent_id": "agent-001",
  "timestamp": "2024-02-03T10:00:00Z",
  "snmp_metrics": [
    {
      "timestamp": "2024-02-03T10:00:00Z",
      "device_name": "switch-01",
      "device_host": "192.168.1.1",
      "oid": "1.3.6.1.2.1.1.3.0",
      "metric_name": "system_uptime",
      "value": 1234567,
      "value_type": "counter"
    }
  ]
}
```

### Agent Registration

**POST /api/v1/register**

Manually register an agent.

```json
{
  "agent_id": "agent-001",
  "hostname": "server-01",
  "ip_address": "192.168.1.100",
  "metadata": {
    "location": "datacenter-1",
    "environment": "production"
  }
}
```

### Health Check

**GET /health**

Check server health and status.

Response:
```json
{
  "status": "ok",
  "timestamp": "2024-02-03T10:00:00Z",
  "service": "DCIM Server",
  "version": "1.0.0",
  "uptime": 3600000000000,
  "total_agents": 10,
  "online_agents": 8,
  "details": {
    "license": {
      "company": "Your Company",
      "max_agents": 100,
      "max_snmp_devices": 500,
      "expires_at": "2025-02-03T00:00:00Z",
      "expires_in_days": 365
    }
  }
}
```

## Connecting Agents

### Configure Agent

Edit the agent's `config.yaml`:

```yaml
server:
  url: "https://dcim-server.company.com:8443/api/v1"  # Use hostname or IP
  timeout: 30s
  retry_attempts: 3

  tls:
    enabled: true
    client_cert_path: "./certs/client.crt"
    client_key_path: "./certs/client.key"
    ca_cert_path: "./certs/ca.crt"
    insecure_skip_verify: false
```

### Using Hostname

```yaml
server:
  url: "https://dcim-server.example.com:8443/api/v1"
```

Make sure the hostname resolves correctly:
- Add DNS record, or
- Add entry to hosts file:
  - Windows: `C:\Windows\System32\drivers\etc\hosts`
  - Linux: `/etc/hosts`

```
192.168.1.50  dcim-server.example.com
```

### Using IP Address

```yaml
server:
  url: "https://192.168.1.50:8443/api/v1"
```

## License File Format

Sample `license.json`:

```json
{
  "license_key": "DCIM-YourCompany-1234567890",
  "company_name": "Your Company",
  "email": "admin@company.com",
  "max_agents": 100,
  "max_snmp_devices": 500,
  "features": [
    "basic_monitoring",
    "alerting",
    "snmp_monitoring",
    "advanced_analytics",
    "dashboard"
  ],
  "issued_at": "2024-02-03T00:00:00Z",
  "expires_at": "2025-02-03T00:00:00Z",
  "signature": "a1b2c3d4e5f6..."
}
```

## Database Schema

### Agents Table

Stores registered agents and their status.

Fields:
- `id` - Auto-increment primary key
- `agent_id` - Unique agent identifier
- `certificate_cn` - Client certificate Common Name
- `hostname` - Agent hostname
- `ip_address` - Agent IP address
- `status` - online, offline, pending
- `group_name` - Agent group
- `last_seen` - Last contact timestamp
- `approved` - Approval status
- `total_metrics` - Total metrics received
- `total_alerts` - Total alerts received

### Metrics Table

Stores system metrics from agents.

Fields:
- `id` - Auto-increment primary key
- `agent_id` - Agent identifier
- `timestamp` - Metric timestamp
- `metric_type` - Type of metric (cpu_usage, memory_usage, etc.)
- `value` - Metric value
- `unit` - Measurement unit
- `metadata` - Additional JSON metadata

### Alerts Table

Stores alerts from agents.

Fields:
- `id` - Auto-increment primary key
- `agent_id` - Agent identifier
- `timestamp` - Alert timestamp
- `severity` - INFO, WARNING, CRITICAL
- `metric_type` - Related metric type
- `value` - Current value
- `threshold` - Threshold value
- `message` - Alert message
- `resolved` - Resolution status

### SNMP Metrics Table

Stores SNMP device metrics.

Fields:
- `id` - Auto-increment primary key
- `agent_id` - Agent identifier
- `timestamp` - Metric timestamp
- `device_name` - SNMP device name
- `device_host` - SNMP device address
- `oid` - SNMP OID
- `metric_name` - Metric name
- `value` - Metric value
- `value_type` - gauge, counter, string

## Security Best Practices

1. **Use Strong Certificates**
   - Generate certificates with at least 2048-bit keys
   - Use proper certificate validity periods
   - Rotate certificates regularly

2. **Network Security**
   - Use firewall rules to restrict access
   - Only allow connections from known agent IPs
   - Use VPN for remote agents

3. **License Protection**
   - Keep license file secure
   - Don't share license files
   - Monitor license usage

4. **Database Security**
   - Use strong database passwords
   - Enable database encryption
   - Regular backups

5. **Access Control**
   - Enable agent approval workflow
   - Regularly review registered agents
   - Monitor for unauthorized access attempts

## Monitoring and Maintenance

### Check Server Status

```powershell
# Health check
curl.exe -k https://localhost:8443/health

# View logs
Get-Content logs\dcim_server.log -Tail 50 -Wait
```

### Database Maintenance

```powershell
# Manually trigger cleanup
# (Or wait for scheduled cleanup)

# Backup SQLite database
Copy-Item data\dcim_server.db data\dcim_server.db.backup
```

### License Monitoring

The server automatically checks license validity every hour and logs warnings when:
- License expires in 30 days or less
- License has expired
- License is in grace period

## Troubleshooting

### Agent Connection Issues

**Problem:** Agent cannot connect to server

**Solutions:**
1. Check server is running: `netstat -an | findstr 8443`
2. Verify certificates are valid
3. Check firewall rules
4. Verify hostname/IP resolution
5. Check agent configuration

### Certificate Errors

**Problem:** Certificate verification failed

**Solutions:**
1. Ensure CA certificate matches
2. Check certificate expiry dates
3. Verify certificate Common Names
4. Regenerate certificates if needed

### License Issues

**Problem:** License limit reached

**Solutions:**
1. Check current agent count
2. Deactivate unused agents
3. Upgrade license
4. Generate new license with higher limits

### Database Issues

**Problem:** Database errors or slowness

**Solutions:**
1. Check disk space
2. Run cleanup to remove old data
3. Increase connection pool size
4. Consider migrating to PostgreSQL/MySQL for larger deployments

## Performance Tuning

### For High-Volume Deployments

```yaml
performance:
  workers:
    metric_processors: 20      # Increase worker pools
    alert_processors: 10
    database_writers: 10

  buffers:
    metrics: 5000              # Increase buffer sizes
    alerts: 500

  batch:
    metrics_batch_size: 1000   # Larger batch inserts
```

### Database Optimization

**SQLite:**
- Increase max_open_conns for concurrent writes
- Use WAL mode for better concurrency
- Consider PostgreSQL for > 50 agents

**PostgreSQL:**
- Enable connection pooling
- Add indexes for common queries
- Regular VACUUM operations

## Command-Line Options

```
Usage: dcim-server [options]

Options:
  -config string
        Path to configuration file (default "config.yaml")
  -version
        Show version information
  -generate-license
        Generate a sample license file
  -license-output string
        Output path for generated license (default "license.json")
  -license-company string
        Company name for license (default "Example Company")
  -license-email string
        Email for license (default "admin@example.com")
  -license-agents int
        Maximum agents for license (default 100)
  -license-snmp int
        Maximum SNMP devices for license (default 500)
  -license-years int
        License validity in years (default 1)
```

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     DCIM Server                          │
│                                                          │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │   mTLS     │  │   License    │  │    Database     │ │
│  │   Auth     │──│   Manager    │──│    (SQLite/     │ │
│  │            │  │              │  │    PostgreSQL)  │ │
│  └────────────┘  └──────────────┘  └─────────────────┘ │
│         │                │                  │           │
│  ┌──────▼────────────────▼──────────────────▼────────┐ │
│  │              API Handlers                         │ │
│  │  /metrics  /alerts  /snmp-metrics  /register     │ │
│  └───────────────────────────────────────────────────┘ │
│                                                          │
└──────────────────────────────────────────────────────────┘
                         ▲
                         │ mTLS
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼────┐      ┌────▼────┐     ┌────▼────┐
   │ Agent 1 │      │ Agent 2 │     │ Agent N │
   └─────────┘      └─────────┘     └─────────┘
```

## Support

For issues, questions, or feature requests, contact:
- Email: support@faberlabs.com
- GitHub: https://github.com/faberlabs/dcim-server

## License

Copyright (c) 2024 Faber Labs. All rights reserved.
