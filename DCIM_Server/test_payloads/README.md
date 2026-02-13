# Cooling System Test Payloads

## Test Scenarios

### 01_normal_operation.json
**Scenario:** All systems operating normally
- Pump ON, CPU ON, Condenser ON
- Inlet: 8°C, 50 PSI
- Outlet: 45°C, 48 PSI
- No leaks
- **Expected:** No alerts, 11 metrics stored

---

### 02_cpu_overheating.json
**Scenario:** CPU overheating
- Outlet temperature: 65°C (> 50°C threshold)
- **Expected Alerts:**
  - CRITICAL: "CPU overheating: Outlet temperature 65.0°C exceeds normal maximum 50.0°C"

---

### 03_condenser_failure.json
**Scenario:** Condenser running but inlet temperature too high
- Condenser ON but inlet: 15°C (> 10°C threshold)
- **Expected Alerts:**
  - CRITICAL: "Condenser failure: Inlet temperature 15.0°C exceeds maximum 10.0°C with condenser ON"
  - WARNING: "CPU overheating: Outlet temperature 55.0°C exceeds normal maximum 50.0°C"

---

### 04_leak_detected.json
**Scenario:** Leak detected with low pressure
- Inlet leak flag: true
- Inlet pressure: 25 PSI (< 30 PSI leakage threshold)
- **Expected Alerts:**
  - CRITICAL: "LEAK DETECTED at inlet - immediate inspection required"
  - CRITICAL: "Critical low pressure detected: inlet pressure 25.0 PSI is below leakage threshold 30.0 PSI - leak suspected"

---

### 05_no_cooling.json
**Scenario:** CPU ON but no temperature rise (thermal contact lost)
- CPU ON
- Inlet: 8°C, Outlet: 9°C (ΔT = 1°C, within tolerance)
- **Expected Alerts:**
  - CRITICAL: "CPU not cooling: Temperature difference only 1.0°C (inlet: 8.0°C, outlet: 9.0°C) - CPU may not be working or thermal contact lost"

---

### 06_low_pressure.json
**Scenario:** Low pressure indicating potential leak
- Inlet: 28 PSI (< 30 PSI leakage threshold)
- Outlet: 26 PSI (< 30 PSI leakage threshold)
- **Expected Alerts:**
  - CRITICAL: "Critical low pressure detected: inlet pressure 28.0 PSI is below leakage threshold 30.0 PSI - leak suspected"
  - CRITICAL: "Critical low pressure detected: outlet pressure 26.0 PSI is below leakage threshold 30.0 PSI - leak suspected"
  - WARNING: "Abnormal pressure: inlet pressure 28.0 PSI outside normal range 40.0-60.0 PSI"
  - WARNING: "Abnormal pressure: outlet pressure 26.0 PSI outside normal range 40.0-60.0 PSI"

---

### 07_pump_off.json
**Scenario:** All components OFF (normal shutdown state)
- Pump OFF, CPU OFF, Condenser OFF
- Pressure: 0 PSI
- Temperature equal at inlet and outlet
- **Expected:** No alerts (this is normal OFF state)

---

### 08_sensor_error.json
**Scenario:** Sensor malfunction (outlet colder than inlet with CPU ON)
- Inlet: 50°C, Outlet: 45°C
- CPU ON (should heat water, not cool it)
- **Expected Alerts:**
  - CRITICAL: "Sensor error: Outlet temperature 45.0°C is LESS than inlet 50.0°C with CPU ON - physically impossible"
  - WARNING: "CPU overheating: Outlet temperature 45.0°C" (False positive due to sensor error)

---

## How to Test

### Using cURL (with mTLS)

```bash
# Test normal operation
curl -X POST https://localhost:8443/api/v1/cooling-metrics \
  --cacert ../certs/ca.crt \
  --cert ../certs/agents/System_Sim_1/client.crt \
  --key ../certs/agents/System_Sim_1/client.key \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: System_Sim_1" \
  -d @01_normal_operation.json

# Test CPU overheating
curl -X POST https://localhost:8443/api/v1/cooling-metrics \
  --cacert ../certs/ca.crt \
  --cert ../certs/agents/System_Sim_1/client.crt \
  --key ../certs/agents/System_Sim_1/client.key \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: System_Sim_1" \
  -d @02_cpu_overheating.json

# ... repeat for other test files
```

### Using PowerShell

```powershell
# Test normal operation
$headers = @{
    "Content-Type" = "application/json"
    "X-Agent-ID" = "System_Sim_1"
}

$body = Get-Content "01_normal_operation.json" -Raw

Invoke-RestMethod -Uri "https://localhost:8443/api/v1/cooling-metrics" `
    -Method POST `
    -Headers $headers `
    -Body $body `
    -Certificate (Get-PfxCertificate -FilePath "..\certs\agents\System_Sim_1\client.pfx")
```

### Using Postman

1. Import test payloads as request bodies
2. Configure mTLS certificates in Postman settings
3. Set headers:
   - `Content-Type`: `application/json`
   - `X-Agent-ID`: `System_Sim_1`
4. Send POST requests to `https://localhost:8443/api/v1/cooling-metrics`

---

## Expected Database Changes

### After Running All Tests

**Agents Table:**
- 1 new agent: `System_Sim_1` (group: "cooling_systems")

**Metrics Table:**
- 88 new rows (11 metrics × 8 test payloads)

**Alerts Table:**
- Varies by test scenario (see each test description above)
- Approximately 10-15 alerts total across all tests

---

## Verification Queries

### Check Metrics Inserted
```sql
SELECT metric_type, COUNT(*) as count
FROM metrics
WHERE agent_id = 'System_Sim_1'
  AND metric_type LIKE 'cooling.%'
GROUP BY metric_type
ORDER BY metric_type;
```

### Check Alerts Generated
```sql
SELECT severity, metric_type, message
FROM alerts
WHERE agent_id = 'System_Sim_1'
ORDER BY created_at DESC;
```

### Check Agent Registration
```sql
SELECT agent_id, hostname, status, group, approved
FROM agents
WHERE agent_id = 'System_Sim_1';
```

---

## Notes

- Timestamps in test files are static (2026-02-11)
- Update timestamps if testing time-based validations
- Agent auto-registers on first request
- Subsequent requests update `last_seen` timestamp
- All tests use same `agent_id` for simplicity
- To test multiple agents, duplicate files and change `agent_id` and `agent_name`
