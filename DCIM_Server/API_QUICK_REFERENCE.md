# DCIM Server API - Quick Reference

**Base URL:** `https://localhost:8443/api/v1`
**Auth:** mTLS (client certificate required)

---

## Quick Links

| Category | Endpoint | Method | Purpose |
|----------|----------|--------|---------|
| **Metrics** | `/metrics` | POST | Submit metrics |
| | `/metrics` | GET | Retrieve metrics |
| **Alerts** | `/alerts` | POST | Submit alerts |
| | `/alerts` | GET | List alerts |
| | `/alerts/{id}` | GET | Get alert details |
| | `/alerts/{id}/resolve` | PUT | Resolve alert ⭐ |
| **Cooling** | `/cooling-metrics` | POST | Submit cooling data |
| **SNMP** | `/snmp-metrics` | POST | Submit SNMP data |
| | `/snmp-metrics` | GET | Retrieve SNMP data |
| **Agents** | `/register` | POST | Register agent |
| | `/agents` | GET | List agents |
| | `/agents/{id}/metrics` | GET | Agent metrics |
| **Status** | `/agent-status-history` | GET | Status history |
| **Events** | `/events` | GET | All events |
| **Health** | `/health` | GET | Health check (no auth) |

---

## Common Examples

### Submit Metrics
```bash
curl -X POST https://localhost:8443/api/v1/metrics \
  --cacert ca.crt --cert client.crt --key client.key \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-001",
    "timestamp": "2026-02-13T12:00:00Z",
    "metrics": [
      {"metric_type": "cpu.usage", "value": 75.5, "unit": "%"}
    ]
  }'
```

### Get Unresolved Alerts
```bash
curl -X GET "https://localhost:8443/api/v1/alerts?resolved=false" \
  --cacert ca.crt --cert client.crt --key client.key
```

### Resolve Alert ⭐
```bash
curl -X PUT https://localhost:8443/api/v1/alerts/123/resolve \
  --cacert ca.crt --cert client.crt --key client.key \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Restarted service",
    "resolution_notes": "Fixed the issue"
  }'
```

### Health Check
```bash
curl https://localhost:8443/health
```

---

## Response Format

### Success
```json
{
  "success": true,
  "message": "Operation completed",
  "data": {...}
}
```

### Error
```json
{
  "success": false,
  "error": "Error message"
}
```

---

## HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK |
| 400 | Bad Request |
| 401 | Unauthorized |
| 404 | Not Found |
| 409 | Conflict |
| 500 | Server Error |

---

## Query Parameters

### Metrics & Alerts
- `agent_id` - Filter by agent
- `start_time` - Start time (ISO8601)
- `end_time` - End time (ISO8601)
- `limit` - Max results (default: 100)

### Alerts Specific
- `severity` - CRITICAL, WARNING, INFO
- `resolved` - true/false
- `metric_type` - Filter by type

---

## Python Quick Start

```python
import requests
from datetime import datetime

URL = "https://localhost:8443/api/v1"
CERTS = ("client.crt", "client.key")
CA = "ca.crt"

# Submit metrics
data = {
    "agent_id": "agent-001",
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "metrics": [
        {"metric_type": "cpu.usage", "value": 75.5, "unit": "%"}
    ]
}

response = requests.post(
    f"{URL}/metrics",
    json=data,
    cert=CERTS,
    verify=CA
)

print(response.json())

# Resolve alert
resolve_data = {
    "resolved_by": "user@company.com",
    "resolution_action": "Fixed the issue"
}

response = requests.put(
    f"{URL}/alerts/123/resolve",
    json=resolve_data,
    cert=CERTS,
    verify=CA
)

print(response.json())
```

---

## New in v2.0 ⭐

### Alert Resolution API
- `GET /api/v1/alerts/{id}` - Get alert details
- `PUT /api/v1/alerts/{id}/resolve` - Resolve with audit trail

**Tracks:**
- WHO resolved (`resolved_by`)
- WHAT fix (`resolution_action`)
- Additional notes (`resolution_notes`)
- WHEN resolved (`resolved_at`)

### Server Tracking
- All data tagged with `server_id`
- Multi-server deployment support

### Alert Deduplication
- `occurrence_count` - How many times
- `first_seen` - When first occurred
- `last_seen` - When last occurred

---

**Full Documentation:** See `API_DOCUMENTATION.md`
