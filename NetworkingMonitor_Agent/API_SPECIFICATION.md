# Server API Specification

This document specifies the REST API endpoints that the monitoring server must implement to receive data from the Network Monitor Agent.

## Base URL

```
https://your-monitoring-server.com/api/v1
```

## Authentication

### Agent ID Header

All requests include the agent's unique identifier in the HTTP header:

```
X-Agent-ID: hostname-1234567890
```

### Optional: API Key Authentication

For enhanced security, you may require an API key:

```
Authorization: Bearer <api-key>
```

Configure in agent's `config.yaml`:
```yaml
server:
  url: "https://your-server.com/api/v1"
  api_key: "your-api-key-here"
```

---

## Endpoints

### 1. POST /metrics

Receives batched system metrics from agents.

#### Request Headers
```
Content-Type: application/json
User-Agent: NetworkMonitorAgent/1.0
X-Agent-ID: <agent-id>
```

#### Request Body

```json
{
  "agent_id": "hostname-1234567890",
  "timestamp": "2024-01-20T10:30:00Z",
  "metrics": [
    {
      "id": 123,
      "timestamp": "2024-01-20T10:29:30Z",
      "metric_type": "cpu.usage",
      "value": 45.2,
      "unit": "percent",
      "metadata": null,
      "created_at": "2024-01-20T10:29:30Z"
    },
    {
      "id": 124,
      "timestamp": "2024-01-20T10:29:30Z",
      "metric_type": "memory.usage",
      "value": 62.5,
      "unit": "percent",
      "metadata": {
        "total": 16000000000,
        "used": 10000000000,
        "available": 6000000000
      },
      "created_at": "2024-01-20T10:29:30Z"
    },
    {
      "id": 125,
      "timestamp": "2024-01-20T10:29:30Z",
      "metric_type": "disk.usage",
      "value": 75.3,
      "unit": "percent",
      "metadata": {
        "device": "/dev/sda1",
        "mountpoint": "/",
        "total": 500000000000,
        "used": 376500000000,
        "free": 123500000000
      },
      "created_at": "2024-01-20T10:29:30Z"
    }
  ]
}
```

#### Metric Types

The agent sends these metric types:

| Metric Type          | Description                    | Unit    |
|---------------------|--------------------------------|---------|
| `cpu.usage`         | CPU usage percentage           | percent |
| `cpu.load1`         | 1-minute load average          | load    |
| `cpu.load5`         | 5-minute load average          | load    |
| `cpu.load15`        | 15-minute load average         | load    |
| `memory.usage`      | Memory usage percentage        | percent |
| `memory.swap`       | Swap usage percentage          | percent |
| `disk.usage`        | Disk usage percentage          | percent |
| `network.bytes_sent`| Network bytes sent (cumulative)| bytes   |
| `network.bytes_recv`| Network bytes received         | bytes   |
| `temperature`       | Temperature reading            | celsius |
| `system.uptime`     | System uptime                  | seconds |

#### Response

**Success (200 OK):**
```json
{
  "success": true,
  "message": "Received 3 metrics",
  "accepted": 3,
  "rejected": 0
}
```

**Error (400 Bad Request):**
```json
{
  "success": false,
  "error": "Invalid metric format",
  "message": "Metric value must be a number"
}
```

**Error (500 Internal Server Error):**
```json
{
  "success": false,
  "error": "Database error",
  "message": "Failed to store metrics"
}
```

#### Notes

- Metrics are sent in batches (default: 100 per request)
- Oldest metrics sent first
- Agent retries on failure (3 attempts by default)
- Batch size configurable in agent config

---

### 2. POST /alerts

Receives alerts from agents (sent immediately when generated).

#### Request Headers
```
Content-Type: application/json
User-Agent: NetworkMonitorAgent/1.0
X-Agent-ID: <agent-id>
```

#### Request Body

```json
{
  "agent_id": "hostname-1234567890",
  "timestamp": "2024-01-20T10:30:15Z",
  "alerts": [
    {
      "id": 42,
      "timestamp": "2024-01-20T10:30:10Z",
      "severity": "CRITICAL",
      "metric_type": "cpu",
      "value": 96.5,
      "threshold": 95.0,
      "message": "cpu CRITICAL: 96.50% (threshold: 95.00%)",
      "retry_count": 0,
      "created_at": "2024-01-20T10:30:10Z"
    },
    {
      "id": 43,
      "timestamp": "2024-01-20T10:30:10Z",
      "severity": "WARNING",
      "metric_type": "memory",
      "value": 87.2,
      "threshold": 85.0,
      "message": "memory WARNING: 87.20% (threshold: 85.00%)",
      "retry_count": 0,
      "created_at": "2024-01-20T10:30:10Z"
    }
  ]
}
```

#### Alert Severities

| Severity   | Description                          | Immediate Send |
|-----------|--------------------------------------|----------------|
| `INFO`    | Informational only                   | No             |
| `WARNING` | Threshold exceeded, needs attention  | Yes            |
| `CRITICAL`| Critical threshold, immediate action | Yes            |

#### Response

**Success (200 OK):**
```json
{
  "success": true,
  "message": "Received 2 alerts",
  "accepted": 2,
  "rejected": 0
}
```

**Error (400 Bad Request):**
```json
{
  "success": false,
  "error": "Invalid alert format",
  "message": "Alert severity must be INFO, WARNING, or CRITICAL"
}
```

#### Notes

- Alerts sent **immediately** when generated
- Not batched with normal metrics
- Agent retries failed sends
- `retry_count` indicates number of previous failed attempts

---

### 3. GET /agents/:agent_id/status (Optional)

Allows agent to check its registration status or fetch updated configuration.

#### Request Headers
```
X-Agent-ID: <agent-id>
```

#### Response

**Success (200 OK):**
```json
{
  "success": true,
  "agent_id": "hostname-1234567890",
  "registered": true,
  "last_seen": "2024-01-20T10:29:45Z",
  "config_version": "1.0"
}
```

---

### 4. POST /agents/register (Optional)

Allows agent to register itself on first startup.

#### Request Body

```json
{
  "agent_id": "hostname-1234567890",
  "hostname": "webserver-01",
  "os": "linux",
  "platform": "ubuntu",
  "architecture": "x86_64",
  "cpu_model": "Intel Xeon E5-2670",
  "cpu_cores": 8,
  "total_memory": 16000000000
}
```

#### Response

**Success (200 OK):**
```json
{
  "success": true,
  "message": "Agent registered successfully",
  "agent_id": "hostname-1234567890"
}
```

---

## Data Models

### Metric Object

```typescript
{
  id: number,              // Agent's local DB ID
  timestamp: string,       // ISO 8601 timestamp of collection
  metric_type: string,     // Type of metric (see table above)
  value: number,           // Metric value
  unit: string,            // Unit of measurement
  metadata: object | null, // Additional context (optional)
  created_at: string       // When metric was created in agent DB
}
```

### Alert Object

```typescript
{
  id: number,              // Agent's local DB ID
  timestamp: string,       // ISO 8601 timestamp of alert
  severity: string,        // INFO | WARNING | CRITICAL
  metric_type: string,     // Metric that triggered alert
  value: number,           // Current value
  threshold: number,       // Threshold that was exceeded
  message: string,         // Human-readable alert message
  retry_count: number,     // Number of previous send attempts
  created_at: string       // When alert was created in agent DB
}
```

---

## Error Handling

### Client Errors (4xx)

The server should return 4xx errors for invalid requests:

- **400 Bad Request**: Invalid JSON, missing fields, invalid values
- **401 Unauthorized**: Invalid API key or authentication failure
- **403 Forbidden**: Agent not authorized
- **429 Too Many Requests**: Rate limit exceeded

### Server Errors (5xx)

The server should return 5xx errors for server-side issues:

- **500 Internal Server Error**: Database error, processing error
- **503 Service Unavailable**: Server overloaded or maintenance

### Agent Retry Behavior

On receiving errors:
- **4xx errors** (except 429): Agent logs error, does NOT retry
- **429 errors**: Agent retries with exponential backoff
- **5xx errors**: Agent retries (3 attempts by default)
- **Network errors**: Agent retries

---

## Rate Limiting

Recommended rate limits:

- **Per Agent**: 100 requests/minute
- **Per Endpoint**: Varies by usage
  - `/metrics`: 1-2 requests/minute per agent
  - `/alerts`: Burst-friendly (up to 10/minute)

Return `429 Too Many Requests` with:
```json
{
  "success": false,
  "error": "Rate limit exceeded",
  "message": "Try again in 30 seconds",
  "retry_after": 30
}
```

---

## Security Recommendations

### Transport Security
- **HTTPS Only**: Require TLS 1.2+
- **Certificate Validation**: Agents verify server certificates
- **Optional mTLS**: Mutual TLS for high-security environments

### Authentication
- **API Keys**: Require API key in Authorization header
- **Agent Registration**: Require approval for new agents
- **Key Rotation**: Support periodic key rotation

### Input Validation
- **JSON Schema Validation**: Validate all incoming payloads
- **Metric Value Limits**: Reject unrealistic values
- **Timestamp Validation**: Reject timestamps too far in past/future

### DoS Protection
- **Rate Limiting**: Implement per-agent rate limits
- **Request Size Limits**: Max 5 MB per request
- **Timeout**: Reject requests taking > 30 seconds

---

## Testing the API

### cURL Examples

**Send Metrics:**
```bash
curl -X POST https://your-server.com/api/v1/metrics \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: test-agent-123" \
  -d '{
    "agent_id": "test-agent-123",
    "timestamp": "2024-01-20T10:30:00Z",
    "metrics": [
      {
        "timestamp": "2024-01-20T10:29:30Z",
        "metric_type": "cpu.usage",
        "value": 45.2,
        "unit": "percent"
      }
    ]
  }'
```

**Send Alert:**
```bash
curl -X POST https://your-server.com/api/v1/alerts \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: test-agent-123" \
  -d '{
    "agent_id": "test-agent-123",
    "timestamp": "2024-01-20T10:30:00Z",
    "alerts": [
      {
        "timestamp": "2024-01-20T10:30:00Z",
        "severity": "CRITICAL",
        "metric_type": "cpu",
        "value": 96.5,
        "threshold": 95.0,
        "message": "cpu CRITICAL: 96.50%"
      }
    ]
  }'
```

---

## Implementation Checklist

When implementing the server:

- [ ] Implement `/metrics` endpoint
- [ ] Implement `/alerts` endpoint
- [ ] Validate JSON payloads
- [ ] Store metrics in time-series database
- [ ] Store alerts with priority flags
- [ ] Implement authentication
- [ ] Add rate limiting
- [ ] Configure HTTPS
- [ ] Add logging and monitoring
- [ ] Test with actual agent
- [ ] Document any custom extensions

---

## Optional Extensions

### Webhook Notifications
Server can forward critical alerts to external systems (Slack, PagerDuty, etc.)

### Agent Configuration Push
Server can push updated configurations to agents via a config endpoint

### Historical Data Query
Agents can query their own historical data from server

### Heartbeat Monitoring
Server tracks agent heartbeats and alerts on missing agents

---

For a reference implementation, see `examples/test-server/` in the agent repository.
