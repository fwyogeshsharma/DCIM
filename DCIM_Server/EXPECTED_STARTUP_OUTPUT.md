# Expected Server Startup Console Output

## With Cooling API Implemented

When you start the DCIM Server, you should see the following output:

```
================================================================================
  DCIM Server - Data Center Infrastructure Monitor
================================================================================

[SERVER] Loaded cooling configuration from cooling_config.yaml

License Information:
  Company: Faber Labs
  Max Agents: 100
  Max SNMP Devices: 50
  Expires: 2027-12-31 (650 days)

Server Configuration:
  Address: 0.0.0.0:8443
  TLS Enabled: true
  Client Authentication: require_and_verify
  Database: sqlite

API Endpoints:
  POST /api/v1/metrics
  GET  /api/v1/metrics
  POST /api/v1/alerts
  GET  /api/v1/alerts
  POST /api/v1/snmp-metrics
  GET  /api/v1/snmp-metrics
  POST /api/v1/cooling-metrics          ← NEW ENDPOINT
  GET  /api/v1/agent-status-history
  POST /api/v1/register
  GET  /api/v1/agents
  GET  /api/v1/agents/{id}/metrics
  GET  /api/v1/events
  GET  /health

================================================================================

[SERVER] Server starting with mTLS on https://0.0.0.0:8443
```

---

## Key Changes

### 1. Cooling Configuration Loaded
```
[SERVER] Loaded cooling configuration from cooling_config.yaml
```
This appears early in the startup sequence, confirming that:
- ✅ cooling_config.yaml was found
- ✅ Configuration was parsed successfully
- ✅ All thresholds are valid

### 2. New API Endpoint Listed
```
  POST /api/v1/cooling-metrics          ← NEW
```
This shows the cooling metrics endpoint is registered and ready to receive requests.

---

## If Cooling Config Not Found

If `cooling_config.yaml` is missing or invalid, you'll see:

```
[SERVER] Warning: Failed to load cooling configuration: cooling_config.yaml not found
[SERVER] Cooling metrics API will be available but alerts may not function correctly
```

**Action Required:**
1. Verify `cooling_config.yaml` exists in DCIM_Server root directory
2. Check YAML syntax is valid
3. Restart server

---

## When Cooling Metrics Are Received

When simulator sends data, you'll see:

```
[SERVER] -> POST /api/v1/cooling-metrics (Agent: System_Sim_1, IP: 192.168.1.100)
[SERVER] Stored 11 cooling metrics from agent System_Sim_1
[SERVER] Generated 2 cooling alerts from agent System_Sim_1
[SERVER] <- POST /api/v1/cooling-metrics completed in 45ms
```

---

## Full Startup Sequence

```
2026-02-11 10:00:00 [SERVER] ================================================================================
2026-02-11 10:00:00 [SERVER]   DCIM Server - Data Center Infrastructure Monitor
2026-02-11 10:00:00 [SERVER] ================================================================================
2026-02-11 10:00:00 [SERVER]
2026-02-11 10:00:00 [SERVER] Loaded cooling configuration from cooling_config.yaml
2026-02-11 10:00:00 [SERVER] License Information:
2026-02-11 10:00:00 [SERVER]   Company: Faber Labs
2026-02-11 10:00:00 [SERVER]   Max Agents: 100
2026-02-11 10:00:00 [SERVER]   Max SNMP Devices: 50
2026-02-11 10:00:00 [SERVER]   Expires: 2027-12-31 (650 days)
2026-02-11 10:00:00 [SERVER]
2026-02-11 10:00:00 [SERVER] Server Configuration:
2026-02-11 10:00:00 [SERVER]   Address: 0.0.0.0:8443
2026-02-11 10:00:00 [SERVER]   TLS Enabled: true
2026-02-11 10:00:00 [SERVER]   Client Authentication: require_and_verify
2026-02-11 10:00:00 [SERVER]   Database: sqlite
2026-02-11 10:00:00 [SERVER]
2026-02-11 10:00:00 [SERVER] API Endpoints:
2026-02-11 10:00:00 [SERVER]   POST /api/v1/metrics
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/metrics
2026-02-11 10:00:00 [SERVER]   POST /api/v1/alerts
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/alerts
2026-02-11 10:00:00 [SERVER]   POST /api/v1/snmp-metrics
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/snmp-metrics
2026-02-11 10:00:00 [SERVER]   POST /api/v1/cooling-metrics
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/agent-status-history
2026-02-11 10:00:00 [SERVER]   POST /api/v1/register
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/agents
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/agents/{id}/metrics
2026-02-11 10:00:00 [SERVER]   GET  /api/v1/events
2026-02-11 10:00:00 [SERVER]   GET  /health
2026-02-11 10:00:00 [SERVER]
2026-02-11 10:00:00 [SERVER] ================================================================================
2026-02-11 10:00:00 [SERVER]
2026-02-11 10:00:00 [SERVER] TLS configured: ClientAuth=require_and_verify, MinVersion=1.2
2026-02-11 10:00:00 [SERVER] Server starting with mTLS on https://0.0.0.0:8443
```

---

## Verification Checklist

After starting the server, verify:

- [ ] Cooling configuration loaded message appears
- [ ] `/api/v1/cooling-metrics` listed in API endpoints
- [ ] No error messages about cooling_config.yaml
- [ ] Server starts successfully on port 8443

If all checks pass, the cooling API is ready to receive simulator data! ✅

---

## Troubleshooting Console Output

### Error: "cooling_config.yaml not found"
```
[SERVER] Warning: Failed to load cooling configuration: cooling_config.yaml not found
```
**Solution:**
```bash
# Verify file exists in DCIM_Server root
ls DCIM_Server/cooling_config.yaml

# If missing, copy from backup or recreate
```

### Error: "failed to parse cooling config"
```
[SERVER] Warning: Failed to load cooling configuration: yaml: line 5: mapping values are not allowed in this context
```
**Solution:**
- YAML syntax error - check indentation
- Ensure colons have space after them: `key: value` not `key:value`
- No tabs allowed, use spaces only

### Error: "invalid cooling config"
```
[SERVER] Warning: Failed to load cooling configuration: invalid cooling config: outlet_max_normal must be greater than inlet_max_condenser_on
```
**Solution:**
- Configuration validation failed
- Fix threshold values to be logically consistent
- Example: outlet_max should be > inlet_max

---

## Normal Operation Logs

When cooling metrics are received and processed:

```
2026-02-11 10:15:00 [SERVER] -> POST /api/v1/cooling-metrics (Agent: System_Sim_1, IP: 192.168.1.100)
2026-02-11 10:15:00 [SERVER] Stored 11 cooling metrics from agent System_Sim_1
2026-02-11 10:15:00 [SERVER] <- POST /api/v1/cooling-metrics completed in 42ms

2026-02-11 10:15:10 [SERVER] -> POST /api/v1/cooling-metrics (Agent: System_Sim_1, IP: 192.168.1.100)
2026-02-11 10:15:10 [SERVER] Stored 11 cooling metrics from agent System_Sim_1
2026-02-11 10:15:10 [SERVER] Generated 1 cooling alerts from agent System_Sim_1
2026-02-11 10:15:10 [SERVER] <- POST /api/v1/cooling-metrics completed in 45ms
```

---

## Alert Generation Logs

When alerts are triggered:

```
2026-02-11 10:20:00 [SERVER] -> POST /api/v1/cooling-metrics (Agent: System_Sim_1, IP: 192.168.1.100)
2026-02-11 10:20:00 [SERVER] Stored 11 cooling metrics from agent System_Sim_1
2026-02-11 10:20:00 [SERVER] Generated 3 cooling alerts from agent System_Sim_1
2026-02-11 10:20:00 [SERVER] <- POST /api/v1/cooling-metrics completed in 52ms
```

To see alert details, query database:
```sql
SELECT severity, message FROM alerts
WHERE agent_id = 'System_Sim_1'
ORDER BY created_at DESC LIMIT 5;
```

---

## Summary

✅ **Cooling configuration loads on startup**
✅ **Endpoint displayed in API list**
✅ **Detailed logging for debugging**
✅ **Warning messages if config missing**
✅ **Performance metrics (request duration)**

The console output provides full visibility into the cooling system monitoring! 🚀
