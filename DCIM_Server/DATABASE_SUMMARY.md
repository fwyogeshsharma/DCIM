# DCIM Server Database Summary

**Export Date:** February 9, 2026 18:51:23
**Database Type:** SQLite
**Database Location:** `./data/dcim_server.db`

---

## 📋 Tables Overview

| Table | Rows | Description |
|-------|------|-------------|
| **agents** | 1 | Registered monitoring agents |
| **metrics** | 200 | System metrics collected from agents |
| **snmp_metrics** | 200 | SNMP device metrics |
| **alerts** | 4 | Triggered alerts and warnings |
| **aggregated_metrics** | 0 | Time-aggregated metrics (empty) |
| **agent_status_history** | 0 | Agent status change history (empty) |
| **licenses** | 0 | License information (empty) |
| **sqlite_sequence** | 4 | SQLite internal auto-increment tracking |

**Total Tables:** 8
**Total Data Rows:** 405 (excluding internal tables)

---

## 🖥️ Agents (1 agent)

### Agent: Faber

| Field | Value |
|-------|-------|
| **Agent ID** | Faber |
| **Hostname** | Faber |
| **IP Address** | [::1] (localhost IPv6) |
| **Status** | 🟢 Online |
| **Group** | default |
| **Approved** | ✅ Yes |
| **Total Metrics** | 0 (reported) |
| **Total Alerts** | 0 (reported) |
| **First Seen** | 2026-02-03 14:51:29 |
| **Last Seen** | 2026-02-03 14:53:29 |
| **Registered** | 2026-02-03 14:51:29 |

**Note:** This agent has been offline since Feb 3, 2026. The "online" status may be stale.

---

## 📊 Metrics (200 rows)

The database contains **200 system metrics** collected from agents.

### Sample Metrics:

**Network Interface Metrics:**
- Interface traffic (bytes sent/received)
- Packet drops
- Network errors
- Multiple network interfaces monitored

**Metric Types Found:**
- Network bandwidth (bytes)
- Interface statistics (drops, errors)
- Various system counters

**Time Range:**
- Oldest: 2026-02-02 13:06:39
- All metrics appear to be from the same time period
- Created in database: 2026-02-03 14:51:29

**Metadata Structure:**
```json
{
  "bytes_recv": 3063211,
  "bytes_sent": 161906694,
  "drops_in": 0,
  "drops_out": 0,
  "errors_in": 0,
  "errors_out": 0,
  "interface": "..."
}
```

---

## 🌐 SNMP Metrics (200 rows)

The database contains **200 SNMP device metrics** from monitored network devices.

### Monitored Device:

**Device:** hp-630-notebook
- **Host:** 192.168.1.14
- **First Seen:** 2026-01-28 17:28:43
- **Metrics Collected:** Memory and system statistics

### SNMP Metrics Collected:

| OID | Metric Name | Sample Value | Type |
|-----|-------------|--------------|------|
| 1.3.6.1.4.1.2021.4.11.0 | memory_total_real_kb | 9,720,520 KB | gauge |
| 1.3.6.1.4.1.2021.4.12.0 | memory_available_real_kb | 16,000 KB | gauge |
| 1.3.6.1.4.1.2021.4.13.0 | memory_total_swap_kb | 39,668 KB | gauge |
| 1.3.6.1.4.1.2021.4.14.0 | memory_total_free_kb | 55,520 KB | gauge |
| 1.3.6.1.4.1.2021.4.15.0 | memory_minimum_swap_kb | 1,821,032 KB | gauge |

**SNMP Devices Summary:**
- 1 device monitored: hp-630-notebook (192.168.1.14)
- Memory metrics collected
- Using NET-SNMP MIB (1.3.6.1.4.1.2021.*)

---

## 🚨 Alerts (4 alerts)

All 4 alerts are **memory warnings** (unresolved).

### Alert Details:

| ID | Timestamp | Severity | Message | Value | Threshold | Status |
|----|-----------|----------|---------|-------|-----------|--------|
| 1 | 2026-02-03 14:51:29 | ⚠️ WARNING | memory WARNING: 90.00% (threshold: 85.00%) | 90.0% | 85.0% | 🔴 Open |
| 2 | 2026-02-03 14:51:59 | ⚠️ WARNING | memory WARNING: 91.00% (threshold: 85.00%) | 91.0% | 85.0% | 🔴 Open |
| 3 | 2026-02-03 14:52:29 | ⚠️ WARNING | memory WARNING: 86.00% (threshold: 85.00%) | 86.0% | 85.0% | 🔴 Open |
| 4 | 2026-02-03 14:52:59 | ⚠️ WARNING | memory WARNING: 86.00% (threshold: 85.00%) | 86.0% | 85.0% | 🔴 Open |

**Alert Pattern:**
- All alerts triggered on 2026-02-03 within 2 minutes
- Memory usage ranged from 86% to 91%
- Threshold set at 85%
- None have been resolved
- No agent_id associated (missing data)

---

## 📁 Exported Data

Full database export available at:
**`./data/database_export_20260209_185123.json`**

This JSON file contains all data from all tables in a structured format.

---

## 🔍 Data Quality Issues

### Missing Agent IDs
- **Metrics table:** Many metrics have empty agent_id field
- **Alerts table:** All 4 alerts have empty agent_id field
- **SNMP Metrics:** Many entries have empty agent_id field

**Impact:** Cannot correlate metrics/alerts with specific agents.

### Possible Causes:
1. Agent not sending agent_id in requests
2. Server not properly extracting agent_id from certificates
3. Database schema migration issue
4. Agent configuration issue

### Recommendations:
1. ✅ Check agent configuration - ensure agent_id is being sent
2. ✅ Review server logs for agent identification errors
3. ✅ Verify mTLS certificate CN matches agent_id
4. ✅ Update agent to send agent_id in all requests

---

## 📈 Data Statistics

### By Data Type:
- **System Metrics:** 200 rows
- **SNMP Metrics:** 200 rows
- **Agents:** 1 agent
- **Alerts:** 4 unresolved alerts
- **Total Data Points:** 404

### Database Size:
- Run this to check: `ls -lh data/dcim_server.db`

### Time Coverage:
- **Metrics:** 2026-02-02 to 2026-02-03 (~1 day)
- **SNMP:** 2026-01-28 to 2026-02-03 (~6 days)
- **Alerts:** 2026-02-03 (single day)
- **Agent Activity:** Last seen 2026-02-03 14:53:29

**Note:** All data appears to be historical. No recent activity detected.

---

## 🎯 Quick Access Queries

### View all agents:
```bash
curl http://localhost:3001/api/v1/agents
```

### View all alerts:
```bash
curl http://localhost:3001/api/v1/alerts
```

### View metrics for an agent:
```bash
curl "http://localhost:3001/api/v1/metrics?agent_id=Faber&time_range=7d"
```

### View SNMP metrics:
```bash
curl http://localhost:3001/api/v1/snmp/metrics
```

### Check server health:
```bash
curl http://localhost:3001/health
```

---

## 💡 Next Steps

1. **Start an agent** to collect fresh metrics:
   ```bash
   cd DCIM_Agent
   # Configure and run agent
   ```

2. **Clear old alerts** through the UI or API:
   ```bash
   curl -X POST http://localhost:3001/api/v1/alerts/{id}/resolve
   ```

3. **Monitor database growth**:
   - Current: 405 rows across all tables
   - Expected growth: ~1000-5000 rows per day per agent
   - Retention: 90 days (metrics), 365 days (alerts)

4. **Consider PostgreSQL migration** if scaling:
   - SQLite works well for 1-10 agents
   - PostgreSQL recommended for 10+ agents
   - Config already has PostgreSQL settings

---

**Database Export Tool:** `export_db_data.py`
- Run anytime with: `python export_db_data.py`
- Creates timestamped JSON exports
- Shows detailed table information
- Works with both SQLite and PostgreSQL
