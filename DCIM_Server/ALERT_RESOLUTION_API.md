# Alert Resolution API - Track Who Fixed What

**Date:** 2026-02-12
**Status:** ✅ IMPLEMENTED AND READY

---

## Overview

New API endpoints to mark alerts as resolved with full audit trail:
- **WHO** resolved the alert
- **WHEN** it was resolved
- **WHAT** fix was applied
- **NOTES** about the resolution

---

## Database Schema Updates

### New Columns in `alerts` Table

```sql
-- Added columns:
resolved_by         TEXT      -- Username/operator who resolved it
resolution_action   TEXT      -- What fix was applied
resolution_notes    TEXT      -- Additional comments/notes
```

### Complete Alert Record Now Includes

```sql
SELECT
    id,
    agent_id,
    metric_type,
    severity,
    message,
    occurrence_count,         -- How many times alert occurred
    first_seen,               -- When alert first appeared
    last_seen,                -- When alert last occurred
    resolved,                 -- Is it resolved?
    resolved_at,              -- When was it resolved?
    resolved_by,              -- WHO resolved it? ✨ NEW
    resolution_action,        -- WHAT fix was applied? ✨ NEW
    resolution_notes,         -- Additional comments ✨ NEW
    created_at,
    updated_at
FROM alerts;
```

---

## API Endpoints

### 1. Get Single Alert

**Endpoint:** `GET /api/v1/alerts/{id}`

**Description:** Retrieve detailed information about a specific alert

**Example Request:**
```bash
curl -X GET https://localhost:8443/api/v1/alerts/123 \
  --cacert certs/ca.crt \
  --cert certs/agents/Aman-PC-UI/client.crt \
  --key certs/agents/Aman-PC-UI/client.key
```

**Response:**
```json
{
  "success": true,
  "data": {
    "id": 123,
    "server_id": "Faber-a1b2c3d4",
    "agent_id": "System_Sim_1",
    "timestamp": "2026-02-12T10:00:00Z",
    "severity": "CRITICAL",
    "metric_type": "cooling.primary_loop.condenser_1.efficiency",
    "value": 1.0,
    "threshold": 2.0,
    "message": "Condenser failure: Inlet temp equals outlet temp",
    "occurrence_count": 15,
    "first_seen": "2026-02-12T09:45:00Z",
    "last_seen": "2026-02-12T10:00:00Z",
    "resolved": false,
    "created_at": "2026-02-12T09:45:00Z",
    "updated_at": "2026-02-12T10:00:00Z"
  }
}
```

---

### 2. Resolve Alert

**Endpoint:** `PUT /api/v1/alerts/{id}/resolve`

**Description:** Mark an alert as resolved with resolution details

**Request Body:**
```json
{
  "resolved_by": "john.doe@company.com",          // REQUIRED
  "resolution_action": "Replaced condenser pump", // REQUIRED
  "resolution_notes": "Pump motor was seized. Replaced with spare unit. System tested and operating normally."
}
```

**Example Request:**
```bash
curl -X PUT https://localhost:8443/api/v1/alerts/123/resolve \
  --cacert certs/ca.crt \
  --cert certs/agents/Aman-PC-UI/client.crt \
  --key certs/agents/Aman-PC-UI/client.key \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Replaced condenser pump",
    "resolution_notes": "Pump motor was seized. Replaced with spare unit."
  }'
```

**Success Response (200 OK):**
```json
{
  "success": true,
  "message": "Alert 123 resolved successfully",
  "data": {
    "id": 123,
    "agent_id": "System_Sim_1",
    "severity": "CRITICAL",
    "message": "Condenser failure: Inlet temp equals outlet temp",
    "occurrence_count": 15,
    "first_seen": "2026-02-12T09:45:00Z",
    "last_seen": "2026-02-12T10:00:00Z",
    "resolved": true,
    "resolved_at": "2026-02-12T10:15:00Z",
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Replaced condenser pump",
    "resolution_notes": "Pump motor was seized. Replaced with spare unit.",
    "updated_at": "2026-02-12T10:15:00Z"
  }
}
```

**Error Response (404 Not Found):**
```json
{
  "success": false,
  "error": "Alert not found"
}
```

**Error Response (409 Conflict):**
```json
{
  "success": false,
  "error": "Alert is already resolved"
}
```

**Error Response (400 Bad Request):**
```json
{
  "success": false,
  "error": "resolved_by is required"
}
```

---

## Field Descriptions

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `resolved_by` | String | Username, email, or operator ID who resolved the alert | `"john.doe@company.com"` |
| `resolution_action` | String | What fix was applied | `"Replaced condenser pump"` |

### Optional Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `resolution_notes` | String | Additional details, observations, or comments | `"Pump motor was seized..."` |

---

## Usage Examples

### Example 1: Fix Condenser Failure

**Alert:**
```
Condenser failure: Inlet temp 60°C equals outlet temp 59°C
```

**Resolution:**
```bash
curl -X PUT https://localhost:8443/api/v1/alerts/123/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "jane.smith@company.com",
    "resolution_action": "Cleaned condenser coils and restarted system",
    "resolution_notes": "Found coils blocked by dust. Performed full cleaning. Temperatures now normal (inlet 8°C, outlet 45°C)."
  }'
```

---

### Example 2: Fix Low Pressure Alert

**Alert:**
```
Critical low pressure: 25 PSI below threshold 40 PSI - leak suspected
```

**Resolution:**
```bash
curl -X PUT https://localhost:8443/api/v1/alerts/124/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "maintenance-team",
    "resolution_action": "Repaired leak at pipe junction",
    "resolution_notes": "Found small crack at junction between server outlet and condenser inlet. Applied epoxy seal and refilled coolant. Pressure restored to 50 PSI."
  }'
```

---

### Example 3: False Positive

**Alert:**
```
Server overheating: 65°C exceeds maximum 60°C
```

**Resolution:**
```bash
curl -X PUT https://localhost:8443/api/v1/alerts/125/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "john.doe@company.com",
    "resolution_action": "False alarm - sensor calibration issue",
    "resolution_notes": "Recalibrated temperature sensor. Actual temperature was 58°C. Updated sensor offset in config."
  }'
```

---

## Python Test Script

```python
#!/usr/bin/env python3
"""Test Alert Resolution API"""

import requests
import json
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://localhost:8443/api/v1/alerts"
CERT_DIR = r"C:\Anupam\Faber\Projects\DCIM\DCIM_Server\certs\agents\Aman-PC-UI"

# Step 1: Get alert details
alert_id = 1
print(f"Getting alert {alert_id}...")

response = requests.get(
    f"{URL}/{alert_id}",
    cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
    verify=False
)

print(f"Status: {response.status_code}")
print(json.dumps(response.json(), indent=2))
print()

# Step 2: Resolve the alert
print(f"Resolving alert {alert_id}...")

resolve_data = {
    "resolved_by": "john.doe@company.com",
    "resolution_action": "Replaced condenser pump",
    "resolution_notes": "Pump motor was seized. Replaced with spare unit from inventory. System tested - temperatures normal."
}

response = requests.put(
    f"{URL}/{alert_id}/resolve",
    json=resolve_data,
    cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
    verify=False
)

print(f"Status: {response.status_code}")
print(json.dumps(response.json(), indent=2))
```

---

## Database Queries for Reporting

### Get All Resolved Alerts with Resolution Details

```sql
SELECT
    id,
    agent_id,
    metric_type,
    severity,
    occurrence_count,
    resolved_at,
    resolved_by,
    resolution_action,
    resolution_notes,
    ROUND(EXTRACT(EPOCH FROM (resolved_at - first_seen)) / 60, 2) AS resolution_time_minutes
FROM alerts
WHERE resolved = true
ORDER BY resolved_at DESC
LIMIT 50;
```

### Get Alerts Resolved by Specific User

```sql
SELECT
    id,
    agent_id,
    severity,
    message,
    resolved_at,
    resolution_action
FROM alerts
WHERE resolved_by = 'john.doe@company.com'
ORDER BY resolved_at DESC;
```

### Get Average Resolution Time by User

```sql
SELECT
    resolved_by,
    COUNT(*) as alerts_resolved,
    ROUND(AVG(EXTRACT(EPOCH FROM (resolved_at - first_seen)) / 60), 2) AS avg_resolution_minutes
FROM alerts
WHERE resolved = true
GROUP BY resolved_by
ORDER BY alerts_resolved DESC;
```

### Get Most Common Resolution Actions

```sql
SELECT
    resolution_action,
    COUNT(*) as count
FROM alerts
WHERE resolved = true
GROUP BY resolution_action
ORDER BY count DESC
LIMIT 10;
```

### Get Unresolved Alerts (Need Attention)

```sql
SELECT
    id,
    agent_id,
    severity,
    metric_type,
    message,
    occurrence_count,
    first_seen,
    last_seen,
    ROUND(EXTRACT(EPOCH FROM (NOW() - first_seen)) / 60, 2) AS age_minutes
FROM alerts
WHERE resolved = false
ORDER BY severity DESC, first_seen ASC;
```

---

## Benefits

### Complete Audit Trail
- ✅ Know **WHO** resolved each alert
- ✅ Know **WHEN** it was resolved
- ✅ Know **WHAT** fix was applied
- ✅ Track **HOW LONG** alerts persisted before resolution

### Accountability
- ✅ Track operator performance
- ✅ Identify recurring issues
- ✅ Build knowledge base of resolutions

### Reporting
- ✅ Generate resolution time reports
- ✅ Identify most common fixes
- ✅ Track team workload
- ✅ Compliance and audit requirements

---

## Integration with UI

The UI developer should add:

1. **Alert Detail View**
   - Show resolution status
   - Display resolved_by, resolution_action, resolution_notes
   - Show time to resolution

2. **Resolve Alert Dialog**
   - Form with:
     - Resolved By (auto-fill from logged-in user)
     - Resolution Action (text field)
     - Resolution Notes (textarea)
   - Submit button calls PUT /api/v1/alerts/{id}/resolve

3. **Resolution History**
   - Table showing all resolved alerts
   - Filter by user, date range, action type

4. **Dashboard Metrics**
   - Average resolution time
   - Alerts resolved today/this week
   - Top resolvers (leaderboard)

---

## Files Modified

- ✅ `internal/database/database.go`
  - Added columns to alerts table schema
  - Added `ResolveAlert()` function
  - Added `GetAlertByID()` function

- ✅ `internal/server/alert_resolution.go` (NEW)
  - Added `handleResolveAlert()` - PUT /alerts/{id}/resolve
  - Added `handleGetAlert()` - GET /alerts/{id}
  - Added `handleAlertsWithID()` - Router

- ✅ `internal/server/server.go`
  - Registered `/alerts/` route

---

## Testing Checklist

- [ ] Get single alert (GET /alerts/{id})
- [ ] Resolve alert with all fields
- [ ] Try resolving already resolved alert (should get 409 error)
- [ ] Try resolving non-existent alert (should get 404 error)
- [ ] Try resolving without resolved_by (should get 400 error)
- [ ] Try resolving without resolution_action (should get 400 error)
- [ ] Verify database shows resolution details
- [ ] Check updated_at timestamp updates
- [ ] Query resolved alerts by user
- [ ] Calculate resolution time metrics

---

## Next Steps

1. ✅ Schema updated
2. ✅ API implemented
3. ✅ Code compiles
4. 🧪 Test with real alerts
5. 📊 Build resolution reports
6. 🎨 Update UI (separate developer)

---

**Status:** Ready for Testing
**API Version:** 2.0
**Backward Compatible:** Yes (new columns default to NULL for existing data)
