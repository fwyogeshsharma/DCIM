# Datacenter Network Simulator — Release Notes v4.0

**Release Date:** June 12, 2026
**Version:** 4.0.0
**Compared against:** `Faberwork-release-Datacenter_Network_Simulator_v3.1`

---

## Overview

Version 4.0 introduces full Redfish/BMC simulation for servers — every server now runs
an out-of-band BMC (iDRAC/iLO/XCC-style) with REST API, sessions, server operations and
platform-event traps. Chassis power state is modeled end-to-end: powering a server off
via Redfish darkens its OS metrics, drops its production uplinks at both ends, dims the
node on both canvases, and fires BMC power traps — while the BMC itself stays reachable
on standby power. Servers now run **two SNMP agents** (OS agent on the production IP,
BMC agent on the management IP), and plant equipment protocol exposure now matches real
hardware (chillers/pumps/towers/valves are BACnet-only). Direct-to-chip liquid cooling
with CDUs, leak physics and a full chiller plant joins the topology, and the API + Web
UI gain JWT authentication.

---

## What's New

### 1. Redfish / BMC Server Simulation

Every server gets a simulated BMC serving the DMTF Redfish tree over HTTP, bound
per-server to the OOB management IP — the same address its BMC SNMP agent uses.

| Area | Detail |
|------|--------|
| Resource tree | ServiceRoot, Systems, Chassis (Thermal/Power), Managers, SessionService |
| Live telemetry | CPU/mem/disk, CPU+inlet temps, fan RPM, PSU watts, `PowerConsumedWatts` — read from the same ticking device state SNMP/gNMI serve |
| Auth | Sessions (`X-Auth-Token`) and HTTP Basic; configurable credentials |
| Server operations | `ComputerSystem.Reset` (On / ForceOff / GracefulShutdown / restart / PowerCycle / PushPowerButton), identify LED, SEL event log + clear |
| Vendor branding | iDRAC / iLO / XCC personality per server vendor |
| Control surfaces | Desktop panel, Web panel, REST API (`/api/redfish/*`), `testscripts/redfish_info.py` CLI probe |
| Headless parity | RedfishController registered in headless mode |

The web panel's per-BMC **Server Operations cluster** is a tone-coded 4×2 control grid:
state-aware enable/disable, inline busy spinners, lit-bulb LED indicator, instant icon
updates driven by the action response.

### 2. Chassis Power-State Modeling

`Device.power_state` is canonical and mutated only by Redfish power operations. Powering
a server off cascades through every layer, as on real hardware:

| Layer | While chassis is OFF |
|-------|----------------------|
| Redfish (mgmt IP) | Alive — `PowerState: Off`, remote power-on works |
| BMC SNMP (mgmt IP) | Alive — power-state OID reads Off, temps decay to ambient, fans 0 RPM, PSU 0 W |
| OS SNMP (prod IP) | Dead host — CPU/mem/uptime 0, interfaces down, counters frozen |
| Production links | Broken **both ends** (server NIC + switch port); restored on power-on; user-broken links untouched |
| Traps | BMC sends `serverPowerOff` / `serverPowerOn` platform events; rule engine publishes no facts for an Off server (no phantom OS-agent traps) |
| UI | Node dimmed on web + desktop canvases; Live Metrics shows "—" for OS metrics, live temps, red OFF pill; Power reads 0 |

### 3. Dual Server SNMP Agents (OS + BMC)

Servers now mirror real hardware with two independent SNMP agents:

| Agent | IP | Community | Content | Survives power-off |
|-------|----|-----------|---------|--------------------|
| OS agent | production | prod IP | ifTable, LLDP, CPU/mem/disk, HOST-RESOURCES | No |
| BMC agent | management | mgmt IP | power state, inlet/CPU temps, 4× fan RPM, 2× PSU status+watts, total draw (`1.3.6.1.4.1.99999.26`) | Yes |

`test_snmp.py` gains `--bmc` (and BMC auto-detection in `--full`); the trap receiver
script names the new power traps.

### 4. Realistic Plant Protocol Exposure

| Devices | Protocols |
|---------|-----------|
| Chiller, pump, cooling tower, valve | **BACnet only** — real units carry no SNMP card; polling them now times out, exactly as a real DCIM would experience |
| CRAH, CDU | SNMP **+** BACnet (native comm cards); SNMP serves the same ticking values as BACnet |
| UPS, PDU, sensors, generators | SNMP (unchanged) |

SNMP **traps are now attributed to the agent that fired them**: server OS traps show the
production IP, BMC platform events the management IP, NOS/UPS/PDU/sensor traps the
management IP — in both trap tables and in the wire-level community string.

### 5. Direct-to-Chip Liquid Cooling & Chiller Plant

- New device types: CRAH, chiller, pump, cooling tower, valve, CDU (+ cooling topology layer)
- CDU loop physics: leak trigger, loop-pressure drop, cold-plate starvation heating the
  CPUs of the servers each CDU cools (fires HighTemperature traps)
- Full BACnet object trees + live telemetry engines per plant device
- Live Metrics gains CRAH / Chiller / Pump / Cooling Tower / Valve / CDU tabs
- BACnet metric tick controls; EV2/plant tabs refresh every simulator tick

### 6. Authentication

- JWT-based auth for the REST API and Web UI (login screen, token expiry handling)
- Credentials loaded from environment (`.env.example` provided); password inputs masked
- Web UI never surfaces raw backend errors — user-safe messages only

### 7. Web UI Improvements

- **Panel state persistence** — SNMP/gNMI/sFlow/BACnet/Redfish panel config and running
  state survive tab switches (global store; duplicate per-panel polling removed)
- Start buttons across all protocol panels require bound IPs and explain why when disabled
- Live Metrics: separate **Prod IP / Mgmt IP** columns (Network + Server tabs), **Power (W)**
  and **Power State** columns on the Server tab; Off servers render "—" for OS metrics
- Binding panel shows the bound-IP breakdown (production vs management)
- Footer simplified to device-type counts; badges added for all plant types
- RPPs renamed by load class — `RPP-IT-*`, `RPP-MECH-*`, `RPP-PLANT-*` — classified
  automatically from their power-layer neighbours (`tools/rename_rpps.py`)
- Canvas performance fixes (node paint caching, drag lag), automatic-layout reset fix
- Adapter list shows only the simulator's `dcim0` virtual adapter

### 8. Performance

- Redfish stop: shutdown signalled to all BMC HTTP servers concurrently (0.5 s flat
  instead of up to seconds-per-BMC)
- Redfish start: reverse-DNS lookup eliminated from per-BMC socket bind
  (~1.4 s/BMC → ~instant for hundreds of BMCs)

### 9. Documentation & Tooling

- `docs/PROTOCOL_ARCHITECTURE.md` — which protocol runs on which device class, on which
  IP, with power-off semantics and the simulator implementation map
- `testscripts/redfish_info.py` — Redfish client probe (inventory, power ops, SEL)
- `tools/rename_rpps.py` — idempotent RPP load-class renamer
- Repository hygiene: `.gitignore`, example env file, build artifacts untracked

---

## API Additions

```
POST /api/auth/login              GET  /api/auth/check
GET  /api/redfish/status          POST /api/redfish/start | stop
POST /api/redfish/action          GET  /api/redfish/sessions | log
GET  /api/devices                 + power_watts, power_state (servers)
GET  /api/topology/graph          + power_state per device
```

---

## Migration Notes

- **Regenerate SNMP datasets** after upgrading. Server OS datasets moved from the mgmt-IP
  file to the production-IP file; servers additionally produce a BMC dataset at the
  mgmt IP. Stale chiller/pump/tower/valve files keep answering until cleared.
- **DCIM polling conventions changed for servers:** OS metrics → production IP
  (community = prod IP); hardware health → management IP (community = mgmt IP, or
  Redfish at `http://<mgmt_ip>:<port>/redfish/v1/`).
- Server BMC enterprise subtree is `1.3.6.1.4.1.99999.26` (`.20`–`.25` belong to the
  chiller-plant devices).
- Chillers, pumps, cooling towers and valves no longer answer SNMP — poll them via
  BACnet/IP (UDP 47808).
