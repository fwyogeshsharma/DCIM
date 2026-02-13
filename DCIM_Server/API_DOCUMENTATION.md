# DCIM Server API Documentation

**Version:** 2.0
**Last Updated:** 2026-02-13
**Base URL:** `https://localhost:8443/api/v1`
**Authentication:** mTLS (Mutual TLS)

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Error Handling](#error-handling)
4. [Rate Limiting](#rate-limiting)
5. [Metrics APIs](#metrics-apis)
6. [Alerts APIs](#alerts-apis)
7. [Cooling System API](#cooling-system-api)
8. [SNMP Monitoring APIs](#snmp-monitoring-apis)
9. [Agent Management APIs](#agent-management-apis)
10. [Agent Status History API](#agent-status-history-api)
11. [Events API](#events-api)
12. [Health Check API](#health-check-api)
13. [Code Examples](#code-examples)
14. [Best Practices](#best-practices)
15. [Changelog](#changelog)

---

## Overview

The DCIM (Data Center Infrastructure Monitoring) Server API provides endpoints for:

- **Monitoring:** Collect metrics from agents (CPU, memory, disk, network)
- **Alerting:** Receive and manage alerts with resolution tracking
- **Cooling Systems:** Monitor liquid cooling infrastructure
- **SNMP:** Collect metrics from network devices
- **Agent Management:** Register and manage monitoring agents

### Key Features

- ✅ **mTLS Authentication** - Enterprise-grade security
- ✅ **Server Tracking** - Multi-server deployment support
- ✅ **Alert Deduplication** - Intelligent alert aggregation
- ✅ **Resolution Audit Trail** - Track who fixed what
- ✅ **Real-time Data** - Sub-second metric collection
- ✅ **RESTful Design** - Standard HTTP methods

---

## Authentication

All API endpoints (except `/health`) require **Mutual TLS (mTLS)** authentication.

### Required Certificates

1. **CA Certificate** (`ca.crt`) - Certificate Authority
2. **Client Certificate** (`client.crt`) - Agent/client identity
3. **Client Private Key** (`client.key`) - Client private key

### Certificate Generation

```powershell
# Generate certificates
cd DCIM_Server/scripts
.\generate-certs.ps1

# Certificates created in:
# - DCIM_Server/certs/ca.crt
# - DCIM_Server/certs/agents/{agent-name}/client.crt
# - DCIM_Server/certs/agents/{agent-name}/client.key
```

### Authentication Example

```bash
curl -X GET https://localhost:8443/api/v1/agents \
  --cacert certs/ca.crt \
  --cert certs/agents/agent-001/client.crt \
  --key certs/agents/agent-001/client.key
```

---

## Error Handling

### Standard Error Response

```json
{
  "success": false,
  "error": "Error message describing what went wrong"
}
```

### HTTP Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 201 | Created | Resource created successfully |
| 400 | Bad Request | Invalid request format or parameters |
| 401 | Unauthorized | Invalid or missing authentication |
| 404 | Not Found | Resource not found |
| 409 | Conflict | Resource conflict (e.g., already resolved) |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |
| 503 | Service Unavailable | Server temporarily unavailable |

### Common Error Examples

**Invalid JSON:**
```json
{
  "success": false,
  "error": "Invalid JSON: unexpected end of JSON input"
}
```

**Missing Required Field:**
```json
{
  "success": false,
  "error": "resolved_by is required"
}
```

**Resource Not Found:**
```json
{
  "success": false,
  "error": "Alert not found"
}
```

---

## Rate Limiting

**Default Limits:**
- 12 requests per minute per agent
- Burst: 20 requests

**Rate Limit Headers:**
```
X-RateLimit-Limit: 12
X-RateLimit-Remaining: 10
X-RateLimit-Reset: 1676284800
```

**Rate Limit Exceeded Response:**
```json
{
  "success": false,
  "error": "Rate limit exceeded. Try again in 60 seconds."
}
```

---

## Metrics APIs

### POST /api/v1/metrics

Submit monitoring metrics from agents.

**Request:**
```json
{
  "agent_id": "agent-001",
  "agent_name": "Server-01",
  "timestamp": "2026-02-13T12:00:00Z",
  "metrics": [
    {
      "metric_type": "cpu.usage",
      "value": 75.5,
      "unit": "%",
      "metadata": "{\"core_count\": 8}"
    },
    {
      "metric_type": "memory.used",
      "value": 8192,
      "unit": "MB"
    },
    {
      "metric_type": "disk.used",
      "value": 450,
      "unit": "GB"
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Stored 3 metrics from agent agent-001"
}
```

**Metric Types:**
| Type | Description | Unit |
|------|-------------|------|
| `cpu.usage` | CPU utilization | % |
| `memory.used` | Memory used | MB/GB |
| `memory.available` | Memory available | MB/GB |
| `disk.used` | Disk space used | GB/TB |
| `disk.free` | Disk space free | GB/TB |
| `network.bytes_sent` | Network bytes sent | bytes |
| `network.bytes_received` | Network bytes received | bytes |

---

### GET /api/v1/metrics

Retrieve stored metrics.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | string | No | Filter by agent ID |
| `metric_type` | string | No | Filter by metric type |
| `start_time` | ISO8601 | No | Start of time range |
| `end_time` | ISO8601 | No | End of time range |
| `limit` | integer | No | Max results (default: 100, max: 1000) |

**Request:**
```bash
GET /api/v1/metrics?agent_id=agent-001&metric_type=cpu.usage&limit=10
```

**Response (200 OK):**
```json
{
  "success": true,
  "count": 10,
  "data": [
    {
      "id": 1234,
      "server_id": "Faber-520b9334",
      "agent_id": "agent-001",
      "timestamp": "2026-02-13T12:00:00Z",
      "metric_type": "cpu.usage",
      "value": 75.5,
      "unit": "%",
      "created_at": "2026-02-13T12:00:01Z"
    }
  ]
}
```

---

## Alerts APIs

### POST /api/v1/alerts

Submit alerts when monitoring thresholds are exceeded.

**Request:**
```json
{
  "agent_id": "agent-001",
  "agent_name": "Server-01",
  "timestamp": "2026-02-13T12:00:00Z",
  "alerts": [
    {
      "severity": "CRITICAL",
      "metric_type": "cpu.usage",
      "value": 95.5,
      "threshold": 90.0,
      "message": "CPU usage critical: 95.5% exceeds threshold 90%"
    },
    {
      "severity": "WARNING",
      "metric_type": "memory.used",
      "value": 14336,
      "threshold": 12288,
      "message": "Memory usage high: 14GB exceeds threshold 12GB"
    }
  ]
}
```

**Severity Levels:**
- `CRITICAL` - Immediate action required
- `WARNING` - Attention needed
- `INFO` - Informational

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Stored 2 alerts from agent agent-001"
}
```

**Alert Deduplication:**
If the same alert (same agent_id, metric_type, severity, unresolved) is submitted multiple times:
- `occurrence_count` is incremented
- `last_seen` timestamp is updated
- No duplicate rows created

---

### GET /api/v1/alerts

Retrieve alerts.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | string | No | Filter by agent ID |
| `severity` | string | No | Filter by severity (CRITICAL, WARNING, INFO) |
| `resolved` | boolean | No | Filter by resolution status |
| `metric_type` | string | No | Filter by metric type |
| `limit` | integer | No | Max results (default: 100) |

**Request:**
```bash
GET /api/v1/alerts?agent_id=agent-001&resolved=false&severity=CRITICAL
```

**Response (200 OK):**
```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "id": 123,
      "server_id": "Faber-520b9334",
      "agent_id": "agent-001",
      "timestamp": "2026-02-13T12:00:00Z",
      "severity": "CRITICAL",
      "metric_type": "cpu.usage",
      "value": 95.5,
      "threshold": 90.0,
      "message": "CPU usage critical: 95.5% exceeds threshold 90%",
      "occurrence_count": 15,
      "first_seen": "2026-02-13T11:00:00Z",
      "last_seen": "2026-02-13T12:00:00Z",
      "resolved": false,
      "created_at": "2026-02-13T11:00:00Z",
      "updated_at": "2026-02-13T12:00:00Z"
    }
  ]
}
```

---

### GET /api/v1/alerts/{id}

Get details of a specific alert.

**Request:**
```bash
GET /api/v1/alerts/123
```

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "id": 123,
    "server_id": "Faber-520b9334",
    "agent_id": "agent-001",
    "timestamp": "2026-02-13T12:00:00Z",
    "severity": "CRITICAL",
    "metric_type": "cpu.usage",
    "value": 95.5,
    "threshold": 90.0,
    "message": "CPU usage critical: 95.5% exceeds threshold 90%",
    "occurrence_count": 15,
    "first_seen": "2026-02-13T11:00:00Z",
    "last_seen": "2026-02-13T12:00:00Z",
    "resolved": false,
    "resolved_at": null,
    "resolved_by": null,
    "resolution_action": null,
    "resolution_notes": null,
    "created_at": "2026-02-13T11:00:00Z",
    "updated_at": "2026-02-13T12:00:00Z"
  }
}
```

**Response (404 Not Found):**
```json
{
  "success": false,
  "error": "Alert not found"
}
```

---

### PUT /api/v1/alerts/{id}/resolve

Mark an alert as resolved with audit trail.

**Request:**
```json
{
  "resolved_by": "john.doe@company.com",
  "resolution_action": "Replaced condenser pump",
  "resolution_notes": "Pump motor was seized. Replaced with spare unit from inventory. System tested - temperatures normal."
}
```

**Required Fields:**
- `resolved_by` - Username, email, or operator ID
- `resolution_action` - Description of fix applied

**Optional Fields:**
- `resolution_notes` - Additional details, observations

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Alert 123 resolved successfully",
  "data": {
    "id": 123,
    "agent_id": "agent-001",
    "severity": "CRITICAL",
    "message": "CPU usage critical: 95.5% exceeds threshold 90%",
    "occurrence_count": 15,
    "first_seen": "2026-02-13T11:00:00Z",
    "last_seen": "2026-02-13T12:00:00Z",
    "resolved": true,
    "resolved_at": "2026-02-13T12:15:00Z",
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Replaced condenser pump",
    "resolution_notes": "Pump motor was seized. Replaced with spare unit.",
    "updated_at": "2026-02-13T12:15:00Z"
  }
}
```

**Response (404 Not Found):**
```json
{
  "success": false,
  "error": "Alert not found"
}
```

**Response (409 Conflict):**
```json
{
  "success": false,
  "error": "Alert is already resolved"
}
```

**Response (400 Bad Request):**
```json
{
  "success": false,
  "error": "resolved_by is required"
}
```

---

## Cooling System API

### POST /api/v1/cooling-metrics

Submit liquid cooling system data with graph-based structure.

**Request:**
```json
{
  "agent_id": "System_Sim_1",
  "agent_name": "Cooling Simulator",
  "timestamp": "2026-02-13T12:00:00Z",
  "loops": [
    {
      "loop_id": "primary_loop",
      "type": "primary",
      "components": {
        "pumps": [
          {
            "id": "pump_1",
            "type": "pump",
            "properties": {
              "status": "ON",
              "rpm": 3000,
              "power_consumption_w": 150
            }
          }
        ],
        "condensers": [
          {
            "id": "condenser_1",
            "type": "condenser",
            "properties": {
              "status": "ON",
              "fan_speed_rpm": 1200
            }
          }
        ],
        "servers": [
          {
            "id": "server_1",
            "type": "server",
            "properties": {
              "status": "ON",
              "heat_load_kw": 15.5
            }
          }
        ],
        "connections": [
          {
            "from": "pump_1",
            "from_port": "OUTLET",
            "to": "server_1",
            "to_port": "INLET"
          }
        ],
        "sensors": [
          {
            "id": "sensor_1",
            "type": "TEMPERATURE",
            "attached_to": "pump_1",
            "position": "OUTLET",
            "value": 8.0,
            "unit": "°C",
            "status": "NORMAL"
          },
          {
            "id": "sensor_2",
            "type": "FLOW_RATE",
            "attached_to": "pump_1",
            "position": "OUTLET",
            "value": 10.5,
            "unit": "L/min",
            "status": "NORMAL"
          }
        ]
      }
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Processed cooling metrics successfully",
  "metrics_created": 25,
  "alerts_created": 2
}
```

**Server-Side Alert Calculation:**
The server automatically analyzes cooling data and creates alerts for:
- Condenser efficiency issues
- Pump failures
- Flow rate problems
- Temperature anomalies
- Server overheating

**Configuration:**
Thresholds defined in `cooling_config.yaml`

---

## SNMP Monitoring APIs

### POST /api/v1/snmp-metrics

Submit SNMP metrics from network devices.

**Request:**
```json
{
  "agent_id": "agent-001",
  "agent_name": "Network Monitor",
  "timestamp": "2026-02-13T12:00:00Z",
  "snmp_metrics": [
    {
      "device_name": "switch-01",
      "device_host": "192.168.1.10",
      "oid": "1.3.6.1.2.1.1.3.0",
      "metric_name": "sysUpTime",
      "value": 123456789,
      "value_type": "counter",
      "metadata": "{\"location\": \"rack-a1\"}"
    },
    {
      "device_name": "router-01",
      "device_host": "192.168.1.1",
      "oid": "1.3.6.1.2.1.2.2.1.10.1",
      "metric_name": "ifInOctets",
      "value": 987654321,
      "value_type": "counter"
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Stored 2 SNMP metrics from agent agent-001"
}
```

---

### GET /api/v1/snmp-metrics

Retrieve SNMP metrics.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | string | No | Filter by agent ID |
| `device_host` | string | No | Filter by device IP/hostname |
| `metric_name` | string | No | Filter by metric name |
| `start_time` | ISO8601 | No | Start of time range |
| `end_time` | ISO8601 | No | End of time range |
| `limit` | integer | No | Max results (default: 100) |

**Response (200 OK):**
```json
{
  "success": true,
  "count": 10,
  "data": [
    {
      "id": 456,
      "server_id": "Faber-520b9334",
      "agent_id": "agent-001",
      "timestamp": "2026-02-13T12:00:00Z",
      "device_name": "switch-01",
      "device_host": "192.168.1.10",
      "oid": "1.3.6.1.2.1.1.3.0",
      "metric_name": "sysUpTime",
      "value": 123456789,
      "value_type": "counter",
      "created_at": "2026-02-13T12:00:01Z"
    }
  ]
}
```

---

## Agent Management APIs

### POST /api/v1/register

Register a new agent with the server.

**Request:**
```json
{
  "agent_id": "agent-001",
  "hostname": "server-01.company.com",
  "ip_address": "192.168.1.100",
  "version": "1.0.0"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Agent registered successfully",
  "data": {
    "agent_id": "agent-001",
    "approved": true,
    "status": "active"
  }
}
```

**Auto-Registration:**
If `agents.registration.auto_register: true` in config, agents are automatically approved.

---

### GET /api/v1/agents

List all registered agents.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `status` | string | No | Filter by status (active, inactive, pending) |
| `limit` | integer | No | Max results (default: 100) |

**Response (200 OK):**
```json
{
  "success": true,
  "count": 3,
  "data": [
    {
      "id": 1,
      "agent_id": "agent-001",
      "hostname": "server-01.company.com",
      "ip_address": "192.168.1.100",
      "status": "active",
      "last_seen": "2026-02-13T12:00:00Z",
      "first_seen": "2026-02-01T08:00:00Z",
      "total_metrics": 15000,
      "total_alerts": 25,
      "created_at": "2026-02-01T08:00:00Z"
    }
  ]
}
```

---

### GET /api/v1/agents/{id}/metrics

Get metrics for a specific agent.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `metric_type` | string | No | Filter by metric type |
| `start_time` | ISO8601 | No | Start of time range |
| `end_time` | ISO8601 | No | End of time range |
| `limit` | integer | No | Max results (default: 100) |

**Request:**
```bash
GET /api/v1/agents/agent-001/metrics?metric_type=cpu.usage&limit=100
```

**Response (200 OK):**
```json
{
  "success": true,
  "agent_id": "agent-001",
  "count": 100,
  "data": [
    {
      "timestamp": "2026-02-13T12:00:00Z",
      "metric_type": "cpu.usage",
      "value": 75.5,
      "unit": "%"
    }
  ]
}
```

---

## Agent Status History API

### GET /api/v1/agent-status-history

Retrieve agent status change history (online/offline transitions).

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | string | No | Filter by agent ID |
| `start_time` | ISO8601 | No | Start of time range |
| `end_time` | ISO8601 | No | End of time range |
| `limit` | integer | No | Max results (default: 100) |

**Response (200 OK):**
```json
{
  "success": true,
  "count": 10,
  "data": [
    {
      "id": 789,
      "server_id": "Faber-520b9334",
      "agent_id": "agent-001",
      "status": "online",
      "timestamp": "2026-02-13T12:00:00Z",
      "created_at": "2026-02-13T12:00:01Z"
    },
    {
      "id": 788,
      "server_id": "Faber-520b9334",
      "agent_id": "agent-001",
      "status": "offline",
      "timestamp": "2026-02-13T11:00:00Z",
      "created_at": "2026-02-13T11:00:01Z"
    }
  ]
}
```

**Use Cases:**
- Uptime reports
- Availability monitoring
- Maintenance windows tracking

---

## Events API

### GET /api/v1/events

Retrieve system events (combined alerts + status changes).

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | string | No | Filter by agent ID |
| `event_type` | string | No | Filter by type (alert, status_change) |
| `start_time` | ISO8601 | No | Start of time range |
| `end_time` | ISO8601 | No | End of time range |
| `limit` | integer | No | Max results (default: 100) |

**Response (200 OK):**
```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "timestamp": "2026-02-13T12:00:00Z",
      "event_type": "alert",
      "agent_id": "agent-001",
      "severity": "CRITICAL",
      "message": "CPU usage critical: 95.5% exceeds threshold 90%"
    },
    {
      "timestamp": "2026-02-13T11:00:00Z",
      "event_type": "status_change",
      "agent_id": "agent-001",
      "status": "offline",
      "message": "Agent went offline"
    }
  ]
}
```

**Use Cases:**
- Event timeline
- Audit logs
- Incident correlation

---

## Health Check API

### GET /health

Server health check endpoint (no authentication required).

**Request:**
```bash
GET https://localhost:8443/health
```

**Response (200 OK):**
```json
{
  "status": "ok",
  "database": "connected",
  "server_id": "Faber-520b9334",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "timestamp": "2026-02-13T12:00:00Z"
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "error",
  "database": "disconnected",
  "error": "Database connection failed"
}
```

**Use Cases:**
- Load balancer health checks
- Uptime monitoring
- Kubernetes liveness/readiness probes

---

## Code Examples

### Python

#### Submit Metrics
```python
import requests
import json
from datetime import datetime

# Configuration
URL = "https://localhost:8443/api/v1/metrics"
CERT_DIR = "./certs/agents/agent-001"

# Prepare data
data = {
    "agent_id": "agent-001",
    "agent_name": "Server-01",
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "metrics": [
        {
            "metric_type": "cpu.usage",
            "value": 75.5,
            "unit": "%"
        },
        {
            "metric_type": "memory.used",
            "value": 8192,
            "unit": "MB"
        }
    ]
}

# Send request
response = requests.post(
    URL,
    json=data,
    cert=(f"{CERT_DIR}/client.crt", f"{CERT_DIR}/client.key"),
    verify="./certs/ca.crt"
)

print(f"Status: {response.status_code}")
print(json.dumps(response.json(), indent=2))
```

#### Resolve Alert
```python
import requests

# Configuration
ALERT_ID = 123
URL = f"https://localhost:8443/api/v1/alerts/{ALERT_ID}/resolve"
CERT_DIR = "./certs/agents/agent-001"

# Resolution data
data = {
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Restarted service",
    "resolution_notes": "CPU spike caused by runaway process. Killed process and restarted service."
}

# Send request
response = requests.put(
    URL,
    json=data,
    cert=(f"{CERT_DIR}/client.crt", f"{CERT_DIR}/client.key"),
    verify="./certs/ca.crt"
)

print(f"Status: {response.status_code}")
print(response.json())
```

---

### Go

#### Submit Metrics
```go
package main

import (
    "bytes"
    "crypto/tls"
    "crypto/x509"
    "encoding/json"
    "fmt"
    "io/ioutil"
    "net/http"
    "time"
)

type Metric struct {
    MetricType string  `json:"metric_type"`
    Value      float64 `json:"value"`
    Unit       string  `json:"unit"`
}

type MetricsRequest struct {
    AgentID   string    `json:"agent_id"`
    AgentName string    `json:"agent_name"`
    Timestamp time.Time `json:"timestamp"`
    Metrics   []Metric  `json:"metrics"`
}

func main() {
    // Load certificates
    cert, _ := tls.LoadX509KeyPair(
        "certs/agents/agent-001/client.crt",
        "certs/agents/agent-001/client.key",
    )

    caCert, _ := ioutil.ReadFile("certs/ca.crt")
    caCertPool := x509.NewCertPool()
    caCertPool.AppendCertsFromPEM(caCert)

    // Configure TLS
    tlsConfig := &tls.Config{
        Certificates: []tls.Certificate{cert},
        RootCAs:      caCertPool,
    }

    client := &http.Client{
        Transport: &http.Transport{TLSClientConfig: tlsConfig},
    }

    // Prepare data
    data := MetricsRequest{
        AgentID:   "agent-001",
        AgentName: "Server-01",
        Timestamp: time.Now().UTC(),
        Metrics: []Metric{
            {MetricType: "cpu.usage", Value: 75.5, Unit: "%"},
            {MetricType: "memory.used", Value: 8192, Unit: "MB"},
        },
    }

    jsonData, _ := json.Marshal(data)

    // Send request
    resp, _ := client.Post(
        "https://localhost:8443/api/v1/metrics",
        "application/json",
        bytes.NewBuffer(jsonData),
    )

    defer resp.Body.Close()
    body, _ := ioutil.ReadAll(resp.Body)

    fmt.Printf("Status: %d\n", resp.StatusCode)
    fmt.Printf("Response: %s\n", body)
}
```

---

### PowerShell

#### Submit Metrics
```powershell
# Configuration
$URL = "https://localhost:8443/api/v1/metrics"
$CertDir = ".\certs\agents\agent-001"

# Prepare data
$data = @{
    agent_id = "agent-001"
    agent_name = "Server-01"
    timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    metrics = @(
        @{
            metric_type = "cpu.usage"
            value = 75.5
            unit = "%"
        },
        @{
            metric_type = "memory.used"
            value = 8192
            unit = "MB"
        }
    )
} | ConvertTo-Json -Depth 10

# Send request
$response = Invoke-RestMethod `
    -Uri $URL `
    -Method Post `
    -Body $data `
    -ContentType "application/json" `
    -Certificate (Get-PfxCertificate "$CertDir\client.pfx")

Write-Host "Response:" -ForegroundColor Green
$response | ConvertTo-Json
```

---

### cURL

#### Get Alerts
```bash
curl -X GET "https://localhost:8443/api/v1/alerts?resolved=false&severity=CRITICAL" \
  --cacert certs/ca.crt \
  --cert certs/agents/agent-001/client.crt \
  --key certs/agents/agent-001/client.key \
  | jq .
```

#### Resolve Alert
```bash
curl -X PUT "https://localhost:8443/api/v1/alerts/123/resolve" \
  --cacert certs/ca.crt \
  --cert certs/agents/agent-001/client.crt \
  --key certs/agents/agent-001/client.key \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Restarted service",
    "resolution_notes": "CPU spike resolved"
  }' \
  | jq .
```

---

## Best Practices

### 1. Error Handling

Always check the `success` field in responses:

```python
response = requests.post(url, json=data, cert=certs)

if response.json().get("success"):
    print("Success!")
else:
    print(f"Error: {response.json().get('error')}")
```

### 2. Timestamps

Always use UTC timestamps in ISO8601 format:

```python
from datetime import datetime

timestamp = datetime.utcnow().isoformat() + "Z"
# Example: "2026-02-13T12:00:00Z"
```

### 3. Batch Submissions

Submit metrics and alerts in batches for efficiency:

```json
{
  "metrics": [
    {...},
    {...},
    {...}  // Up to 1000 metrics per batch
  ]
}
```

### 4. Certificate Management

- Store certificates securely
- Use separate certificates for each agent
- Rotate certificates regularly
- Never commit certificates to version control

### 5. Rate Limiting

Respect rate limits:
- Submit metrics at regular intervals (e.g., every 60 seconds)
- Don't retry immediately on rate limit errors
- Use exponential backoff for retries

### 6. Connection Pooling

Reuse HTTP connections for better performance:

```python
session = requests.Session()
session.cert = (cert_path, key_path)
session.verify = ca_path

# Reuse session for multiple requests
session.post(url1, json=data1)
session.post(url2, json=data2)
```

### 7. Alert Resolution

Always provide meaningful resolution information:

```json
{
  "resolved_by": "john.doe@company.com",  // Who fixed it
  "resolution_action": "Replaced failed disk",  // What was done
  "resolution_notes": "Disk /dev/sda failed SMART test. Replaced with new 2TB SSD."  // Details
}
```

---

## Changelog

### Version 2.0 (2026-02-13)

**Added:**
- ✅ `GET /api/v1/alerts/{id}` - Get single alert
- ✅ `PUT /api/v1/alerts/{id}/resolve` - Resolve alert with audit trail
- ✅ Server tracking - All data tagged with `server_id`
- ✅ Alert deduplication - `occurrence_count`, `first_seen`, `last_seen`
- ✅ Automatic database migrations

**Changed:**
- Cooling metrics API now calculates alerts server-side
- Alert schema updated with resolution tracking fields

### Version 1.0 (2026-02-01)

**Initial Release:**
- Metrics submission and retrieval
- Alert submission and retrieval
- SNMP metrics
- Agent management
- Health check endpoint

---

## Support

### Documentation
- **API Reference:** This document
- **Migration Guide:** `MIGRATIONS.md`
- **Alert Resolution Guide:** `ALERT_RESOLUTION_API.md`
- **Cooling API Guide:** Available in source code

### Contact
- **GitHub Issues:** https://github.com/faberlabs/dcim-server/issues
- **Email:** support@faberlabs.com

### Additional Resources
- **Server Configuration:** See `config.yaml`
- **Cooling Configuration:** See `cooling_config.yaml`
- **Certificate Generation:** See `scripts/generate-certs.ps1`

---

**Last Updated:** 2026-02-13
**Document Version:** 2.0
**API Version:** v1
