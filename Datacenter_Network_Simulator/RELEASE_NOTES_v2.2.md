# Datacenter Network Simulator — Release Notes v2.2

**Release Date:** May 15, 2026
**Version:** 2.2.0

---

## Overview

Version 2.2 is a platform and integration release. The simulator gains a full REST API that exposes every simulator control to external tooling, a headless Linux mode for server deployment, SNMP SET-based threshold management, and an expanded trap library covering PDU and UPS device types in depth.

---

## What's New

### 1. REST API — Full Simulator Control

Every simulator action is now accessible over HTTP. The API starts automatically alongside the Qt UI on port 8000, or standalone in headless mode.

**Interactive docs:** `http://<host>:8000/docs`

#### Endpoint Groups

| Group | Endpoints | Description |
|-------|-----------|-------------|
| **Topology** | `GET /api/topology` | Current topology info |
| | `POST /api/topology/upload` | Upload a topology JSON file from any client |
| | `GET /api/topology/links?layer=` | List links, filterable by `production` or `management` |
| | `POST /api/topology/links/break` | Break a link — sets `oper_status=2` and sends `LINK_DOWN` traps |
| | `POST /api/topology/links/restore` | Restore a link — sets `oper_status=1` and sends `LINK_UP` traps |
| **Devices** | `GET /api/devices?layer=&device_type=` | List devices with layer/type filtering |
| | `GET /api/devices/{id}` | Device detail including interfaces and live metrics |
| | `POST /api/devices` | Add a device to the topology |
| | `PUT /api/devices/{id}` | Edit a device |
| | `DELETE /api/devices/{id}` | Remove a device |
| **IP Binding** | `GET /api/binding/adapters` | List available network adapters |
| | `POST /api/binding/adapter` | Select adapter |
| | `POST /api/binding/bind` | Bind all device IPs |
| | `POST /api/binding/unbind` | Remove all IP bindings |
| | `GET /api/binding/status` | Current binding state |
| **SNMP** | `POST /api/snmp/datasets/generate` | Generate `.snmprec` datasets |
| | `POST /api/snmp/start` | Start SNMPSim |
| | `POST /api/snmp/stop` | Stop SNMPSim |
| | `GET /api/snmp/status` | Simulator status and active job |
| **gNMI** | `POST /api/gnmi/datasets/generate` | Generate OpenConfig JSON datasets |
| | `POST /api/gnmi/start` | Start gNMI server |
| | `POST /api/gnmi/stop` | Stop gNMI server |
| | `POST /api/gnmi/proxy/start` | Start aggregating proxy |
| | `GET /api/gnmi/status` | Simulator status |
| **Rules** | `GET /api/rules` | List all rules with fire counts |
| | `POST /api/rules/enable` / `disable` | Toggle rule engine |
| | `POST /api/rules/{name}/enable` / `disable` | Toggle individual rule |
| | `POST /api/rules/reset-counts` | Reset fired counters |
| **Traps** | `GET /api/traps` | Trap history (last 1000) |
| | `POST /api/traps/send` | Send a trap manually |
| | `DELETE /api/traps` | Clear trap history |

#### Async Job Pattern

Long-running operations (bind, generate, start) return a `job_id` immediately. Poll for completion:

```
GET /api/snmp/jobs/{job_id}
GET /api/gnmi/jobs/{job_id}
GET /api/binding/jobs/{job_id}
```

#### Bidirectional Link–Trap Coupling

Breaking or restoring a link via the API automatically sends the corresponding `LINK_DOWN` / `LINK_UP` traps on both endpoints. Sending a `LINK_DOWN` trap with a `peer_id` automatically breaks the topology link:

```json
POST /api/traps/send
{
  "device_id": "dc1-spine1",
  "trap_type": "LINK_DOWN",
  "peer_id": "dc1-leaf1"
}
```

#### Device Layer Filtering

```
GET /api/devices?layer=production      # router, switch, server, firewall, load_balancer
GET /api/devices?layer=management      # oob_switch
GET /api/devices?layer=power           # ups, pdu, floor_pdu
GET /api/devices?layer=environmental   # sensor
GET /api/devices?device_type=router,switch
```

---

### 2. Headless Linux Mode

The simulator can now run on a Linux server without any display. A `QCoreApplication` provides Qt signals and slots without requiring a screen or Xvfb.

```bash
# Run headless (API only, no GUI)
python app/main.py --headless

# Custom port
python app/main.py --headless --port 8001
```

All API endpoints work identically in headless mode. Topology is loaded via `POST /api/topology/upload` rather than the file picker.

**Packaged binary:**
```bash
./Datacenter-Network-Simulator --headless --port 8001
```

---

### 3. SNMP SET Threshold Management

Trap rule thresholds can now be updated live via SNMP SET requests — no restart, no config file edit required. Any NMS or script that can issue SNMP SETs can reconfigure alert thresholds on a per-device basis while the simulator is running.

The SNMP SET agent listens on port 1161 (separate from the simulation port 161) to avoid interfering with simulated device polling.

---

### 4. Expanded Trap Library — PDU and UPS (25 New Traps)

25 new trap types covering power infrastructure devices:

**UPS Traps**

| Trap | OID | Description |
|------|-----|-------------|
| `UPS_ON_BATTERY` | RFC 1628 | Mains power lost — UPS running on battery |
| `UPS_LOW_BATTERY` | RFC 1628 | Battery below critical threshold |
| `UPS_BATTERY_NORMAL` | RFC 1628 | Recovery: battery restored |
| `UPS_OVERLOAD` | RFC 1628 | Output load exceeds rated capacity |
| `UPS_BYPASS_ACTIVE` | RFC 1628 | Static bypass engaged |
| `UPS_COMMUNICATION_LOST` | RFC 1628 | Agent lost contact with UPS hardware |
| `UPS_INPUT_FAILURE` | RFC 1628 | Input voltage out of range |
| `UPS_BATTERY_REPLACE` | RFC 1628 | Battery replacement recommended |
| `UPS_OUTPUT_VOLTAGE_BAD` | RFC 1628 | Output voltage deviation detected |
| `UPS_TEMPERATURE_FAULT` | RFC 1628 | Internal temperature fault |

**PDU Traps**

| Trap | Description |
|------|-------------|
| `PDU_OUTLET_OFF` | Outlet switched off (planned or fault) |
| `PDU_OUTLET_ON` | Outlet switched on |
| `PDU_OVERCURRENT` | Branch circuit current exceeded threshold |
| `PDU_PHASE_IMBALANCE` | Load imbalance detected across phases |
| `PDU_INPUT_VOLTAGE_HIGH` | Input voltage above upper limit |
| `PDU_INPUT_VOLTAGE_LOW` | Input voltage below lower limit |
| `PDU_CIRCUIT_BREAKER_OPEN` | Circuit breaker tripped |
| `PDU_COMMUNICATION_LOST` | Agent lost contact with PDU |
| `PDU_OVERTEMPERATURE` | PDU internal temperature fault |
| `PDU_LOAD_HIGH` | Total load above threshold |
| `PDU_LOAD_CRITICAL` | Total load above critical threshold |
| `PDU_GROUND_FAULT` | Ground fault detected |
| `PDU_INRUSH_CURRENT` | Inrush current spike on startup |
| `PDU_BANK_OVERCURRENT` | Bank-level current threshold exceeded |
| `PDU_ENERGY_THRESHOLD` | Cumulative energy consumption exceeded |

All new traps carry semantically correct varbinds (outlet index, phase, voltage, current, load%) and are available in the rule engine as trap actions.

---

## UI Updates

### Rule Engine — Threshold Column

The Rules Panel table now includes a **Threshold** column showing the configured trigger value for each rule. The value updates live when a threshold is changed via SNMP SET, giving immediate visual confirmation of the change without restarting the simulator.

---

## Bug Fixes

| Area | Fix |
|------|-----|
| API | `Rule.name` → `Rule.rule_name` attribute error on `GET /api/rules` |
| API → UI | gNMI dataset generation via API now shows progress bar and console logs in the Qt UI |
| API → UI | IP bindings created via API now correctly reflect in binding panel and enable Remove Binding button |
| Packaging | Windows and Linux PyInstaller builds now include FastAPI, uvicorn, starlette, anyio, and the `api/` package |
| Packaging | `sys.stdout.isatty()` crash in windowed exe fixed by disabling uvicorn's log formatter |
| Packaging | `app.log` and `crash.log` now written next to the exe instead of the temp extraction folder |

---

## Upgrade Notes

- Topologies from v2.1 are fully compatible — no migration needed.
- `trap_rules.json` from v2.1 is compatible without modification.
- New dependency: `python-multipart>=0.0.9` (required for topology file upload). Run `pip install -r requirements.txt` to update.
- The `POST /api/topology/open` endpoint (server-side file path) has been removed. Use `POST /api/topology/upload` instead.

---

## Known Limitations

- SNMP simulation is SNMPv2c only; SNMPv3 auth/privacy not yet supported.
- REST API has no authentication — restrict network access when deploying on shared or cloud infrastructure.

---

*Datacenter Network Simulator is an internal simulation platform for testing NMS integrations, SNMP tooling, and datacenter topology modeling.*