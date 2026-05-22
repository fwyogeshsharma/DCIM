# Datacenter Network Simulator — Release Notes v3.0

**Release Date:** May 22, 2026
**Version:** 3.0.0

---

## Overview

Version 3.0 is a major release introducing a full browser-based Web UI, a real-time Metrics Tick controller, and a Live Metrics dashboard. The simulator can now be operated entirely from a web browser without the Qt desktop application. All simulator controls, status panels, and live device metrics are accessible over HTTP from any machine on the network.

---

## What's New

### 1. Web UI — Browser-Based Simulator Control

A complete React web interface is now bundled with the simulator, served automatically on the same port as the REST API (`http://<host>:8000`). No installation required — open the URL in any modern browser.

#### Feature Parity with Desktop UI

| Feature | Web UI |
|---------|--------|
| Topology canvas (pan, zoom, drag nodes) | ✓ |
| Network layer switching (All / Production / Management / Power / Environmental) | ✓ |
| Device node context menu (Break Link, Restore Link, Send Trap) | ✓ |
| IP Binding panel | ✓ |
| SNMP Simulator panel (generate, start, stop, status) | ✓ |
| gNMI Simulator panel (generate, start, stop, proxy, clients) | ✓ |
| sFlow panel | ✓ |
| Traps panel with live feed | ✓ |
| Rule Engine panel | ✓ |
| Metrics Tick panel | ✓ |
| Console log (SNMP / gNMI / sFlow tabs) | ✓ |
| Real-time SSE-driven updates (no polling) | ✓ |

#### Real-Time Updates via SSE

The Web UI connects to a Server-Sent Events stream (`/api/events`). All panels update instantly when simulator state changes — no page refresh required. Metric data refreshes are driven by the metrics ticker, so the UI update cadence automatically matches the configured tick interval.

#### Topology Canvas

The interactive topology canvas renders all devices and links with live status indicators. Nodes can be repositioned by dragging. Right-clicking a node opens a context menu for link operations and trap injection. Layer filters isolate production, management, power, and environmental devices independently.

---

### 2. Metrics Tick Controller

A new **Metrics Tick** panel gives full runtime control over the background metrics simulation engine.

#### Ticker Lifecycle

The ticker starts automatically when any simulator (SNMP, gNMI, or sFlow) is started and stops only when all simulators are stopped. It cannot be started independently.

#### Ticker Control

| Control | Description |
|---------|-------------|
| **Enabled** toggle | Pause or resume metric updates while simulators are running |
| **Interval** | Tick period in seconds (1–3600). Changes take effect on the next tick. UI refresh cadence matches this interval automatically. |

#### Metric Flags

Individual metric simulation can be enabled or disabled per category. Disabled metrics stop updating — their last value is frozen.

| Group | Metrics |
|-------|---------|
| **All Devices** | CPU Usage, Memory Used, Disk Used, System Uptime, CPU / ASIC Temp, Chassis Inlet Temp, Iface Byte Counters, Iface Error Counters, Iface Discard Counters, Interface Flapping |
| **Sensor Devices** | Humidity, Dew Point, Airflow |
| **UPS Devices** | UPS Status, Output Load, Battery Status, Input Voltage, Input Frequency, Fan Status, Charger Status, Rectifier Status, Phase Status |
| **PDU / Floor PDU** | Load, Voltage, Power Factor, Phase Imbalance, Outlet Status, Breaker Status, Outlet Failure, Smoke Detection, Outlet Current, Ground Fault |
| **Router / Firewall** | BGP Sessions |

#### Metric Limits (Constraints Tab)

Each metric can be constrained to a fixed range or locked to a specific state value. Numeric constraints clamp the random-walk output to `[min, max]`. State locks force a metric to always emit a chosen value (e.g. force `ups_status = on_battery`).

#### API Endpoints

```
GET  /api/tick/settings    — current ticker state, flags, limits
POST /api/tick/settings    — apply interval, pause/resume, flags, limits
```

---

### 3. Live Metrics Page

A dedicated **Live Metrics** page is accessible from the Devices menu in the Web UI. It displays real-time device metrics in a sortable, searchable table organised by device type. Metrics refresh automatically at the configured tick interval via SSE — no manual refresh needed.

#### Device Type Tabs

| Tab | Device Types | Columns |
|-----|-------------|---------|
| **All** | All devices | Name, Type, IP, CPU, Memory, Disk, CPU Temp, Uptime |
| **Network** | router, switch, firewall, load_balancer, oob_switch | CPU, Memory, Disk, CPU Temp, Inlet Temp, Interfaces, RX Total, TX Total, Errors, Discards, BGP Sessions, Uptime |
| **Server** | server | CPU, Memory, Disk, CPU Temp, Inlet Temp, Interfaces, RX Total, TX Total, Uptime |
| **UPS** | ups | Status, Battery, Output Load, Input Voltage, Input Frequency, Fan, Charger, Rectifier, Phase |
| **PDU** | pdu, floor_pdu | Load, Voltage, Current, Power Factor, Phase Imbalance, Outlet, Breaker, Failure, Smoke, Ground Fault |
| **Sensor** | sensor | Humidity, Dew Point, Airflow, Inlet Temp |

#### Expandable Per-Interface Rows (Network & Server)

Clicking any row in the Network or Server tab expands it to reveal a per-interface sub-table with all interface counters:

| Column | Description |
|--------|-------------|
| Interface | Interface name |
| Status | UP / DOWN pill |
| Speed | Link speed (Mbps / Gbps) |
| In Octets | Total inbound bytes |
| Out Octets | Total outbound bytes |
| In Pkts | Inbound unicast packets |
| Out Pkts | Outbound unicast packets |
| In Err | Inbound error count |
| Out Err | Outbound error count |
| In Disc | Inbound discard count |
| Out Disc | Outbound discard count |

#### Sorting and Search

All columns are sortable by clicking the column header. A search bar filters across name, IP, vendor, and device type simultaneously.

#### Color Thresholds

Metric values are color-coded automatically:

| Color | Meaning |
|-------|---------|
| Green | Normal |
| Amber | Warning threshold reached |
| Red | Critical threshold reached |

---

## API Additions

| Endpoint | Description |
|----------|-------------|
| `GET /api/devices` | Now includes all 26+ extended metrics per device (UPS, PDU, sensor, BGP, interface aggregates) |
| `GET /api/tick/settings` | Ticker state, interval, metric flags, metric limits |
| `POST /api/tick/settings` | Apply tick settings |

### Extended `DeviceInfo` Response

The `/api/devices` response now includes full per-device metric payloads:

- **All devices:** `cpu_temp`, `inlet_temp`, `memory_total`, `disk_total`, `sys_location`, `sys_contact`
- **Sensor:** `humidity`, `dewpoint`, `airflow`
- **UPS:** `ups_status`, `ups_output_load`, `ups_battery_status`, `ups_input_voltage`, `ups_input_frequency`, `ups_fan_status`, `ups_charger_status`, `ups_rectifier_status`, `ups_phase_status`
- **PDU / Floor PDU:** `pdu_load`, `pdu_voltage`, `pdu_power_factor`, `pdu_phase_imbalance`, `pdu_outlet_status`, `pdu_breaker_status`, `pdu_outlet_failure`, `pdu_smoke`, `pdu_outlet_current`, `pdu_ground_fault`
- **Router / Firewall:** `bgp_sessions_up`, `bgp_sessions_total`
- **Network devices (aggregated):** `total_rx_bytes`, `total_tx_bytes`, `total_errors`, `total_discards`, `flapping_count`, `interfaces_up`, `interfaces_total`
- **Per-interface detail:** `iface_stats[]` — full counters for every interface

---

## Upgrade Notes

- Topologies from v2.x are fully compatible — no migration needed.
- `trap_rules.json` from v2.x is compatible without modification.
- Web UI static files are served from the `webui/dist/` directory. Rebuild with `npm run build` inside `webui/` if modifying the frontend source.
- New Node.js dependency for building the Web UI: Node.js 18+ and npm. Pre-built `dist/` is included in the release package — a build step is only needed when modifying UI source.

---

## Known Limitations

- SNMP simulation is SNMPv2c only; SNMPv3 auth/privacy not yet supported.
- REST API and Web UI have no authentication — restrict network access when deploying on shared or cloud infrastructure.
- Interface flapping count shown in the Network/Server summary row reflects interfaces currently down, not flap events over time.

---

*Datacenter Network Simulator is an internal simulation platform for testing NMS integrations, SNMP tooling, and datacenter topology modeling.*
