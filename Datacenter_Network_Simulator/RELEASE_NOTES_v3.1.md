# Datacenter Network Simulator — Release Notes v3.1

**Release Date:** June 4, 2026
**Version:** 3.1.0

---

## Overview

Version 3.1 delivers full BACnet/IP simulation for Verdigris EV2 energy monitors with a dedicated Energy tab in the Live Metrics page, expanded UPS and PDU metric coverage, Cisco network device performance telemetry via SNMP, sensor mid-rack and exhaust temperature support, an expanded SNMP trap library for UPS/PDU/sensor devices, and a completely overhauled SNMP test script with per-device-type flags and auto-detection. Two new device types (RPP and Generator) are added. Headless mode now initialises all simulator controllers, including BACnet and sFlow, bringing it to full parity with the desktop application.

---

## What's New

### 1. Verdigris EV2 BACnet/IP Simulator

A complete BACnet/IP stack simulates Verdigris EV2 energy monitor devices with per-circuit telemetry. No external BACnet library is required — the implementation is pure Python UDP.

#### Protocol Support

| Service | Description |
|---------|-------------|
| Who-Is / I-Am | BACnet device discovery — each EV2 responds from its own source IP |
| ReadProperty | Single-object property read |
| ReadPropertyMultiple | Batch property read |
| SubscribeCOV | Change-of-value subscription with configurable lifetime; notifications dispatched after each telemetry tick |

#### Per-Device Telemetry

Each EV2 device exposes a full BACnet object tree updated every simulator tick:

**Panel-level metrics (Analog Input objects)**

| Object | Description |
|--------|-------------|
| `Panel_Total_kW` | Total panel active power |
| `Panel_Total_kWh` | Cumulative energy |
| `Voltage_PhA/B/C` | Phase voltages |
| `Current_PhA/B/C` | Phase currents |
| `Line_Frequency` | Mains frequency |
| `Panel_PF` | Panel power factor |
| `Voltage_THD` / `Current_THD` | Total harmonic distortion |
| `Harmonic_3/5/7/9_Current` | Individual harmonic components |

**Alarm objects (Binary Input)**

| Alarm | Trigger Condition |
|-------|------------------|
| `Alarm_Overcurrent` | Panel current exceeds threshold |
| `Alarm_VoltageImbalance` | Phase voltage imbalance |
| `Alarm_HighTHD` | THD exceeds limit |
| `Alarm_PhaseLoss` | Phase loss detected |
| `Alarm_SensorFault` | Internal sensor fault |

**Per-circuit metrics** (up to 42 circuits per device)

Each circuit (`Ckt01`–`Ckt42`) exposes: `Current`, `kW`, `kWh`, `PF`, `THD`.

#### Topology Integration

The BACnet start endpoint automatically determines circuit count by walking the power graph — counting downstream breaker connections from the EV2's panel. If no topology power edges exist, the circuit count is derived from the model name (`Verdigris EV2-42` → 42 circuits).

#### REST API

```
GET  /api/bacnet/status          — running state, active devices, instance range, port
POST /api/bacnet/start           — start with configurable base instance, frequency, port
POST /api/bacnet/stop            — graceful shutdown
GET  /api/bacnet/ev2/metrics     — current BACnet present-values for all active EV2 devices
```

#### Web UI — BACnet Panel

The BACnet panel in the right sidebar provides start/stop controls, configurable base instance number, mains frequency (50 Hz / 60 Hz), and UDP port. A device table shows all active EV2 instances with IP, instance number, circuit count, and status. Errors are now surfaced inline — previously silent API failures are displayed in the panel.

#### Headless Mode Fix

BACnet (and sFlow) controllers were not registered in headless mode (`--headless`), causing every BACnet API call to return HTTP 503. Both controllers are now correctly initialised and registered with `AppState` in headless mode.

---

### 2. Energy (EV2) Tab — Live Metrics Page

A new **Energy (EV2)** tab has been added to the Live Metrics page, providing a real-time view of all active Verdigris EV2 BACnet devices.

#### Features

- **Per-device cards** showing panel-level metrics: Total kW, Total kWh, Phase voltages A/B/C, Phase currents A/B/C, Frequency, Power Factor, Voltage THD, Current THD, Harmonics 3/5/7/9
- **Active alarm indicators**: Overcurrent, Voltage Imbalance, High THD, Phase Loss, Sensor Fault
- **Expandable circuit sub-table**: clicking a device row expands it to reveal all active circuits with Current (A), kW, kWh, PF, and THD per circuit
- **Topology name resolution**: each circuit row shows the name of the downstream device connected to that breaker, resolved from the power graph
- **Graceful offline state**: tab shows an empty state when BACnet is not running — no errors displayed

---

### 3. UPS Metric Expansion

Three additional UPS fields are now tracked in the simulation state store and exposed via the API (in addition to the six live-derived metrics — see below):

| Field | Description |
|-------|-------------|
| `ups_operating_mode` | Derived: `online` / `battery` / `bypass` — reflects current bypass and power status |
| `ups_battery_health` | State-of-health % — slow monotonic decay; faster decay during battery fault |
| `ups_energy_kwh` | Cumulative energy accumulator — integrates `output_load% × 3 kW` per tick |

#### Six New Live-Derived UPS Metrics

Six UPS-MIB standard OIDs that were previously set once at dataset generation time are now live-patched on every simulator tick, reflecting real-time load and battery state.

| Metric | Formula | Notes |
|--------|---------|-------|
| Battery Voltage | `180 + 40 × (charge% / 100)` V | Sags during on-battery/low-battery — 220 V at full, ~186 V at low |
| Output Voltage | 220 V regulated | UPS output is actively regulated |
| Output Current | `(load% × 3000 VA) / 220 V` | Tracks load directly |
| Output Power | `(load% × 3000 VA) × PF 0.9` | Real power (W) |
| Input Current | `output_power / efficiency (0.92) / input_voltage` | Accounts for UPS losses |
| Input Power | `output_power / 0.92` | Draw from utility (W) |

#### Live Metrics UPS Tab

The UPS table in the Live Metrics page now has 18 data columns (up from 9 in v3.0):

| New Column | Description |
|------------|-------------|
| Operating Mode | online / battery / bypass pill |
| Battery Health | % state-of-health with warn/crit thresholds |
| Battery Voltage | V — amber below 200 V, red below 185 V |
| Output Voltage | V |
| Output Current | A |
| Output Power | W |
| Input Current | A |
| Input Power | W |
| Energy (kWh) | Cumulative output energy |

All new columns are sortable. New derived metrics also appear in the Tick Panel UPS group as read-only entries with formula tooltips.

---

### 4. PDU Metric Expansion

Seven additional PDU fields are now tracked, exposed via the API, and displayed in the Live Metrics PDU tab.

| Field | Description |
|-------|-------------|
| `pdu_real_power` | Real power W — `voltage × current × power_factor` |
| `pdu_apparent_power` | Apparent power VA — `voltage × current` |
| `pdu_outlet_power` | Per-outlet real power W |
| `pdu_energy_kwh` | Cumulative energy accumulator (kWh) |
| `pdu_frequency` | Mains input frequency (Hz) |
| `pdu_temperature` | Ambient temperature at PDU (°C) |
| `pdu_humidity` | Ambient humidity at PDU (%) |

#### Live Metrics PDU Tab

The PDU table has been completely rebuilt with all 17 fields as dedicated columns:

| Section | Columns |
|---------|---------|
| Power | PDU Load, Input Voltage, Outlet Current, Power Factor, Phase Imbalance, Real Power (W), Apparent (VA), Outlet Power (W), Input Freq, Energy (kWh) |
| Environment | Ambient Temp, Ambient Humidity |
| Status | Outlet Status, Breaker Status, Outlet Failure, Smoke Detection, Ground Fault |

---

### 5. Sensor Metric Expansion

Two new temperature fields are now tracked for the **Raritan DPX2-T3H1** 3-sensor probe:

| Field | Description |
|-------|-------------|
| `mid_temp` | Mid-rack temperature (°C) — sensor slot 2 |
| `outlet_temp` | Exhaust/outlet temperature (°C) — sensor slot 3 |

These are exposed via `/api/devices` and appear in the Live Metrics Sensor tab alongside the existing `inlet_temp`. The DPX2-T3H1 is the only model with three temperature sensors; single-probe models (DPX2-T1H1) are unaffected.

---

### 6. Cisco Network Device SNMP Performance Metrics

Cisco switches, routers, firewalls, and load balancers now expose CPU and memory metrics via standard Cisco MIBs, generated at dataset creation and live-patched every tick.

#### New OIDs

| MIB | OID | Metric |
|-----|-----|--------|
| CISCO-PROCESS-MIB | `1.3.6.1.4.1.9.9.109.1.1.1.1.7.1` | CPU utilisation — 1-minute rolling average (%) |
| CISCO-PROCESS-MIB | `1.3.6.1.4.1.9.9.109.1.1.1.1.8.1` | CPU utilisation — 5-minute rolling average (%) |
| CISCO-MEMORY-POOL-MIB | `1.3.6.1.4.1.9.9.48.1.1.1.2.1` | Memory pool name (`Processor`) |
| CISCO-MEMORY-POOL-MIB | `1.3.6.1.4.1.9.9.48.1.1.1.5.1` | Memory pool used (MB) |
| CISCO-MEMORY-POOL-MIB | `1.3.6.1.4.1.9.9.48.1.1.1.6.1` | Memory pool free (MB) |

#### BGP4-MIB for Routers

Router devices now include a live BGP4-MIB peer table (`1.3.6.1.2.1.15.3.1`) regenerated on every tick from the current BGP session state. Peer entries are stripped and rewritten as sessions are established or torn down — no stale peer entries remain after a state change.

| OID | Field |
|-----|-------|
| `bgpPeerState` | 1=Idle 2=Connect 3=Active 4=OpenSent 5=OpenConfirm 6=Established |
| `bgpPeerAdminStatus` | 2=start (always active) |
| `bgpPeerRemoteAddr` | Peer IP address |

---

### 7. SNMP Test Script — Per-Device-Type Flags

`testscripts/test_snmp.py` has been completely rewritten with dedicated flags for each device type and full auto-detection in `--full` mode.

#### New Flags

| Flag | Data Queried |
|------|-------------|
| `--switch` | BRIDGE-MIB MAC address table, STP scalars, CDP neighbours, Cisco PROCESS-MIB + MEMORY-POOL-MIB |
| `--router` | BGP4-MIB peer table, CDP neighbours, Cisco PROCESS-MIB + MEMORY-POOL-MIB |
| `--ups` | UPS-MIB standard (battery, input, output) + enterprise UPS status OIDs |
| `--pdu` | Enterprise PDU OIDs (load, power, status, environment) |
| `--sensor` | Vendor sensor OIDs — Raritan DPX2, Vertiv Geist, APC NetBotz |
| `--full` | Auto-probe device type; enable all matching flags |

#### Auto-Detection (`--full`)

`--full` probes signature OIDs to determine device type without any prior knowledge:

- `1.3.6.1.2.1.17.2.1.0` (STP protocol spec) → switch
- `1.3.6.1.4.1.9.9.109.1.1.1.1.7.1` (Cisco CPU) → Cisco network device
- BGP4-MIB walk → router
- `1.3.6.1.2.1.33.1.2.1.0` (UPS battery status) → UPS
- `1.3.6.1.4.1.99999.5.1.0` (PDU load) → PDU

#### Output Label Alignment

All output labels in `--ups` and `--pdu` sections now match the column names shown in the Live Metrics page (e.g. `Output Load`, `Input Voltage`, `Input Freq`, `Energy (kWh)`, `PDU Load`, `Real Power (W)`, `Ambient Temp`, `Ambient Humidity`).

---

### 8. SNMP Trap Enhancements — UPS, PDU, Sensor

New trap rules covering enterprise UPS status metrics, additional PDU states, and environmental sensor readings. Sensor traps now use model-aware filtering so Raritan, Vertiv, and APC devices only fire traps relevant to their OID space.

#### New UPS Traps

| Trap | Trigger |
|------|---------|
| `UPS_FAN_FAILURE` | Fan status transitions to failure |
| `UPS_FAN_RESTORED` | Fan status recovers to ok |
| `UPS_CHARGER_FAILURE` | Battery charger fault |
| `UPS_CHARGER_RESTORED` | Charger fault cleared |
| `UPS_RECTIFIER_FAILURE` | Rectifier fault |
| `UPS_RECTIFIER_RESTORED` | Rectifier fault cleared |
| `UPS_PHASE_FAILURE` | Input phase fault |
| `UPS_PHASE_RESTORED` | Phase fault cleared |
| `UPS_BATTERY_DEGRADED` | Battery health below threshold |
| `UPS_BYPASS_ACTIVE` | Bypass engaged |
| `UPS_BYPASS_CLEARED` | Bypass disengaged |
| `UPS_OPERATING_MODE_CHANGE` | Mode change (online ↔ battery ↔ bypass) |
| `UPS_ENERGY_THRESHOLD` | Cumulative energy output exceeded limit |

#### New PDU Traps

| Trap | Trigger |
|------|---------|
| `PDU_SMOKE_DETECTED` | Smoke sensor triggered |
| `PDU_SMOKE_CLEARED` | Smoke sensor cleared |
| `PDU_OUTLET_FAILURE` | Outlet fault state |
| `PDU_OUTLET_FAILURE_CLEARED` | Outlet fault cleared |

#### Sensor Trap Enhancements

Environmental sensor traps now carry the correct enterprise OID based on vendor:

| Vendor | OID Namespace |
|--------|---------------|
| Raritan DPX2 | `1.3.6.1.4.1.13742.6.5.5.3.1` |
| Vertiv Geist | `1.3.6.1.4.1.21239.5.1` |
| APC NetBotz | `1.3.6.1.4.1.318.1.1.10.4.2.2.1` |

Trap rules with `device_types=["sensor"]` only evaluate against the matching vendor OID — cross-vendor false positives are suppressed.

---

## New Device Types

### RPP — Remote Power Panel

Remote Power Panel (RPP) devices are now supported in the topology. RPP devices act as passive breaker panels between floor PDUs and server racks. They appear in the power layer topology view but do not generate SNMP datasets (no agent). Power graph traversal for EV2 circuit counting correctly handles RPP hops — RPP is treated as a transparent pass-through in the upstream filter.

### Generator

Generator devices are now fully supported with enterprise SNMP OIDs (`1.3.6.1.4.1.99999.7`):

| OID | Metric |
|-----|--------|
| `.1.0` | Fuel level (%) |
| `.2.0` | Run hours |
| `.3.0` | Status (1=standby, 2=running, 3=fault) |
| `.4.0` | Output load (%) |
| `.5.0` | Output power (kW) |
| `.6.0–.8.0` | Output voltage Phase A/B/C (V×10) |
| `.9.0` | Output frequency (Hz×10) |
| `.10.0` | Coolant temperature OK (1=ok, 2=warning) |
| `.11.0` | Oil pressure OK (1=ok, 2=warning) |
| `.12.0` | Battery status (1=ok, 2=fault) |
| `.13.0` | Start attempts (counter) |

---

## Bug Fixes

| Area | Fix |
|------|-----|
| Headless mode | `BACnetController` and `SFlowController` not registered in `AppState` — all BACnet API calls returned 503 in `--headless` mode |
| Live Metrics — UPS | Energy (kWh) value was displaying under the Fan Status column due to a data-row/header misalignment |
| BACnet panel | Start/stop errors silently swallowed (`catch { /* ignore */ }`) — failures now surfaced as inline error messages in the panel |
| snmprec patching | File was opened in text mode on Python 3.12+ causing `TypeError` on binary writes; fixed to use explicit text mode |
| snmprec patching | Write used truncate-then-write pattern — could leave a zero-byte file on crash; replaced with atomic temp-file rename |

---

## API Changes

### New Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/bacnet/status` | BACnet controller state, active devices, config |
| `POST /api/bacnet/start` | Start BACnet/IP simulation |
| `POST /api/bacnet/stop` | Stop BACnet/IP simulation |
| `GET /api/bacnet/ev2/metrics` | Live BACnet present-values for all EV2 devices with circuit detail |

### Extended `DeviceInfo` Response (`/api/devices`)

**UPS — new fields:**
```
ups_operating_mode       string   online | battery | bypass
ups_battery_health       float    %
ups_energy_kwh           float    kWh
ups_battery_voltage      float    V
ups_output_voltage       float    V
ups_output_current       float    A
ups_output_power         float    W
ups_input_current        float    A
ups_input_power          float    W
```

**PDU — new fields:**
```
pdu_real_power           float    W
pdu_apparent_power       float    VA
pdu_outlet_power         float    W
pdu_energy_kwh           float    kWh
pdu_frequency            float    Hz
pdu_temperature          float    °C
pdu_humidity             float    %
```

**Sensor — new fields:**
```
mid_temp                 float    °C  (Raritan DPX2-T3H1 only)
outlet_temp              float    °C  (Raritan DPX2-T3H1 only)
```

### New Response Models

```
EV2DeviceSnapshot        — full BACnet device snapshot (panel + circuits)
EV2PanelMetrics          — panel-level power metrics and alarms
EV2CircuitMetrics        — per-circuit metrics with topology device name
```

---

## Upgrade Notes

- Topologies from v3.0 are fully compatible — no migration needed.
- Web UI must be rebuilt on Linux machines: `cd webui && npm install && npm run build`. The `node_modules` directory from Windows cannot be reused on Linux due to platform-specific native binaries (esbuild, vite).
- BACnet requires EV2 energy monitor devices (`device_type=energy_monitor`) to be present in the topology and their IPs to be bound before starting.
- Running on Linux with `--headless` now fully supports BACnet and sFlow. No additional configuration required.
- BGP4-MIB peer entries are only populated after the first simulator tick. Querying BGP OIDs immediately after starting will return empty results.

---

## Known Limitations

- SNMP simulation is SNMPv2c only; SNMPv3 auth/privacy not yet supported.
- REST API and Web UI have no authentication — restrict network access when deploying on shared or cloud infrastructure.
- Cisco MEMORY-POOL-MIB values are reported in MB (not bytes) due to the simulator's memory model. NMS tools expecting bytes will need scaling.
- Generator devices have SNMP OIDs but no state simulation yet — values are set at dataset generation time and do not update on tick.

---

*Datacenter Network Simulator is an internal simulation platform for testing NMS integrations, SNMP tooling, and datacenter topology modeling.*
