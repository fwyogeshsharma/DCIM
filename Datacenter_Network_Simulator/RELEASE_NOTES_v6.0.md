# Datacenter Network Simulator — Release Notes v6.0

**Release Date:** August 6, 2026
**Version:** 6.0
**Compared against:** `Faberwork-release-Datacenter_Network_Simulator_v5.0`
**Scope:** 189 commits, 2026-07-08 → 2026-08-06 · 128 source files, +23,102 / −1,951 lines
(excluding regenerated topology JSON)

---

## Overview

v5.0 made the datacenter *live*. v6.0 makes it *physically honest*.

The headline is the **electrical distribution chain**: power no longer starts at the UPS.
Five new device types — utility feed, switchgear, ATS, MCC, MPP — put a real service
entrance, a paralleling bus, transfer switches, and mechanical distribution between the
grid and the load, with a state machine that rides through a utility loss the way real
gear does (crank → qualify → transfer → retransfer, with gensets that can fail to start).
Alongside it, the **chiller plant** became a sequenced plant instead of a set of gauges:
chillers stage to load, CW/CHW pumps and tower cells sequence with them, CRAH fans track
local inlet temperature, and run-hour meters count *running* time so lead/lag rotation is
real.

Everything that carries a cord or a port was re-derived from the SKU. Cords now run
**outlet → PSU** with real IEC connector types and capacity enforcement; switches expose
their real per-model port count with data/mgmt roles; the RPP became genuinely passive
(zero ports, no IP, no SNMP agent) because a breaker panel has no monitoring card.

The alarm layer roughly doubled (71 → 145 rule definitions), every alarm now has a
matching clear, and traps and PDU data ship under **real vendor PENs** instead of a
private test arc.

This is a major release. It changes the topology schema (power modelling), device naming,
and the IP-binding workflow — see **Migration Notes**.

---

## What's New

### 1. Electrical distribution — service entrance to panelboard

Real facilities do not wire a generator to a UPS. v6.0 models the path properly.

New device types (`core/device_manager.py`):

| Type | Models | Typical protocol |
|---|---|---|
| `utility_feed` | Utility service entrance, revenue/ION meter | Modbus TCP |
| `switchgear` | LV main board / generator paralleling bus | SNMP + Modbus |
| `ats` | Automatic Transfer Switch (ASCO / Eaton / APC) | SNMP |
| `mcc` | Motor Control Center — unprotected mechanical distribution | SNMP/Modbus |
| `mpp` | Mechanical Power Panel — per-hall CRAH panelboard, fed from an MCC | SNMP |

* **Transfer machine** (`core/power_transfer.py`) — utility loss drives crank → genset
  qualify → transfer to emergency → retransfer on restoration. It **rechecks genset
  availability while in emergency**, not only at crank, so a genset that dies mid-run
  strands the bus instead of silently carrying it.
* **Genset failure modes that mean something** — battery failure makes a genset
  unstartable; low coolant and over-temp trip a *running* set; out-of-fuel and fail-start
  take the same branch. A dead bus is reachable: fail utility + fail both gensets → both
  UPS on battery → drain → exhaust → hard-dark with a linkDown storm, then a COLD_START
  recovery storm when utility returns.
* **"On battery" physically drains** — `_ups_source_ok` also returns False when the
  rectifier cannot accept its input, so injected rectifier/input conditions actually put
  the UPS on battery and run the discharge model.
* **Rack-local power** — the shared-pair model is gone. Every device's draw is counted in
  the cascade, so PDU / RPP / UPS load % reads true and a PDU or feed fault drops the
  network and facility gear hanging off it (not just servers).
* **Panel-meter depth on the MPP** — per-phase V/I, current, imbalance, load-coupled EC-fan
  PF (~0.92), frequency, kVAR/kVA with noise. Grid frequency is now per-region and PF is
  load-coupled across the electrical devices.
* **3D + floor plan** — each electrical device renders as its own slim dead-front cabinet;
  MPPs flank the CRAH wall where they electrically belong.

### 2. Chiller plant sequencing and thermal integrity

* **Staged plant** — chillers stage to the fleet's cap; CW/CHW pumps and cooling-tower
  cells sequence *with* the chillers rather than running flat. Boot lead is deterministic
  (lowest index), and tower-cell rotation buckets on a weekly period with already-running
  cells winning ties.
* **Run-hour meters count running time**, not wall-clock, so lead/lag rotation and
  maintenance intervals are meaningful.
* **Per-hall CRAH control** — full CRAH complement per hall, fan speed on *local* inlet
  temperature, speed↔power coupling, split pump loops.
* **Condenser physics corrected** — flow keeps its demand shape (capped by `_cw_pump_frac`)
  and range became the derived quantity: `reject_kw / (cp · cw_flow)`, clamped by a new
  `COND_MAX_RANGE_C = 20.0`. Cycled-off tower cells are no longer counted as lost
  rejection capacity.
* **Chiller trips are latched and annunciated on the node** (not a global bar), with an
  explicit Reset; a CT fault must be cleared before the chiller can be restored. Trip
  thresholds ride the base value — `max(absolute, base + margin)`.
* **Standby trains fade and stop animating** on the canvas; `GET /api/bacnet/plant/standby`
  exposes which units the BMS has staged off.
* **CDU** is coupled to its own loop's IT heat, valve position is coupled to plant load,
  and a DLC SKU now *requires* a CDU loop rather than silently air-cooling.
* **Two-DC isolation** — plant timers and run-sets are scoped per DC (`_accrue_run_proof`
  takes a scope), after a two-DC fixture caught one DC's sweep clobbering the other's.

### 3. BMS / OT network separation

Facility gear no longer home-runs to the IT aggregation. Each room's facility devices
reach a **BMS access switch**, which reaches a BMS core behind the management firewall —
the OT access layer as it is actually built. A dedicated BMS OOB switch was added per
room (including the network room), and the cross-DC OOB WAN links were removed.

### 4. Alarms, traps, rules

* **71 → 145 rule definitions** in `core/trap_rules.py`; the running rules table reports
  166 rules. **Every alarm now has a clear** — no more latched-forever conditions.
* **Real vendor PENs** — new `core/vendor_oids.py` (492 lines): traps and PDU data are sent
  under the actual vendor enterprise arcs instead of a private test arc, so a real NMS
  resolves them.
* **New trap families** — ATS transfer (with `_ats_on_emg` latch so `TRANSFER_NORMAL` fires
  on the way back), switchgear protective-relay events, genset conditions, UPS
  ride-through, PDU breaker trip.
* **PDU breaker trips have consequences** — the trip kills power *and* flips
  `pdu_breaker_status` *and* fires the trap; the PDU carries the full condition set.
* **Trap engine hardening** — one `SnmpEngine` + `AsyncioDispatcher` + UDP transport per
  `TrapEngine`, built lazily on the trap loop and reused for every trap; metric coercion
  hardened in detail and varbind builders; no duplicate rule names or OIDs.
* **EV2 alarms release forced state**, so a cleared fault actually clears, and node blink
  tracks the *alarm* rather than the injection.

### 5. Port, cord and interface realism

* **Cords run outlet → PSU.** Devices carry `psus` (C14/C20 inlets, capacity) and PDUs
  carry `outlets` (C13/C19); power edges record the outlet and PSU they terminate on, and
  capacity is enforced. Each BMC reports the PSU feeding it, at the voltage of the PDU that
  feeds it (208 V single-phase vs 240 V wye).
* **Real per-SKU ports** — servers build per-SKU NICs plus a vendor BMC port (iDRAC / iLO /
  XCC); switches expose their model's real port count. **1,425 fabricated port references
  were cleared**, and links that all sat on interface 0 were re-seated onto real ports.
* **Interface roles** — `Interface.role` is `data` or `mgmt`; OOB-plane gear carries
  management traffic on data ports by design.
* **RPP is passive** — zero ports, no IP, no SNMP agent, `snmp_port = 0`. A bare breaker
  panel has no monitoring card; its only view is the EV2 sub-meter clamped to its output.
* **SNMP surfaces tell the truth** (this release's last change): the device list, canvas
  tooltip and device-info modal show the port an agent is *actually served on*, `—` for
  devices with no agent at all (BACnet/Modbus plant gear, passive panels), and a distinct
  "not serving" state for stopped-sim vs hot-added-after-start.

### 6. Provisioning, placement and topology editing

* **Manual Provision Rack / Hall** on the Floor-Plan page, with a hall picker for Add-Rack.
* **Add Device carries links** — link mode folded into the Add Device dialog (the separate
  bulk-add dialog is gone); `GET /api/devices/link-candidates` and per-device port pickers
  list every port with used ones disabled and labelled with their peer.
* **Placement pickers cascade** — DC / Room / Floor / Row hide when nothing beneath them
  qualifies; the rack level lists everything with ineligible cabinets disabled and labelled.
* **Rack model** — 42U face for display, U1–U40 for server placement; rack role inferred
  from occupants (compute / network / facility); `rack-occupancy` returns row positions;
  `rack_air_budget_w` is a tunable FleetConfig field (default 15 kW).
* **Canvas** — multi-node drag, layout reset, per-layer positions endpoints, cooling link
  colours fixed, new `core/canvas_layout.py`.
* **Unified device naming** — `CODE-DC-ROOM-Rrow-rack` (racked) and `CODE-DC-ROOM`
  (facility), e.g. `DC1-ER1` → `RTR1-DC1-NR-R1-01`. The leading code carries the functional
  role the runtime parses.
* **Hall B is its own network pod** (own spines + OOB), both DCs' halls have identical
  fabric shape, and fleet-opened halls get their own Mechanical Power Panels.

### 7. Dataset and binding lifecycle

* **Sims never auto-bind IPs.** The Binding panel owns address assignment; simulators
  refuse to start unbound with an actionable message. `reconcile_bound_ips()` asks the OS
  which of this topology's IPs are already aliased, so a restart adopts them.
* **Topology fingerprint on datasets** (`core/dataset_fingerprint.py`) — datasets
  regenerate when the topology changed since they were written.
* **Orphan reaping** — any `.snmprec` not in `snmp_bind_ips()` is deleted, so an address
  that once belonged to a retired device stops answering as that device.
* **`active_endpoints` is gated on `is_ready()`** — it reports what is actually bound, not
  what was intended, and `/api/snmp/clear` removes the sidecar with its datasets. The gNMI
  sidecar leak in `/api/gnmi/clear` was fixed the same way.

### 8. Performance and stability

* **Ticker starvation fixed** — `GET /rules` took (2 × rules + 1) full snapshots under the
  lock the ticker needs. A frozen Run_Hours meant a stalled ticker, which made injected
  faults return `ok:true` and do nothing. Replaced with `get_rules_table_stats()`.
* **Thread safety** — `TopologyEngine` and `DeviceManager` are RLock-guarded; iterate via
  engine methods so fleet churn cannot race API and ticker reads.
* **Monotonic tick clock** — elapsed time per tick is measured from a monotonic clock and
  used as `dt`, so meters do not jump when the loop is delayed.
* **Web UI re-render discipline** — Add-Device and Provision dialogs subscribe per-slice, so
  the 4 s status poll no longer re-renders the whole store (open dropdowns stopped
  flashing).
* **Faster start** for all five simulators; protocol panels warn when IP binding has not
  been done.

### 9. Theming

All colour moved into `index.css :root` tokens plus `src/theme.ts` — no raw hex at call
sites, light and dark both handled.

---

## Fleet inventory (curated topology `dual_dc_enterprise.json`)

| Device type | v5.0 | v6.0 |
|---|---:|---:|
| ats | 0 | 4 |
| cdu | 12 | 12 |
| chiller | 6 | 6 |
| cooling_tower | 4 | 6 |
| crah | 16 | 28 |
| energy_monitor | 18 | 24 |
| firewall | 4 | 12 |
| generator | 4 | 4 |
| load_balancer | 4 | 4 |
| mcc | 0 | 4 |
| mpp | 0 | 8 |
| oob_switch | 15 | 36 |
| pdu | 66 | 80 |
| pump | 12 | 14 |
| router | 4 | 8 |
| rpp | 16 | 16 |
| sensor | 31 | 31 |
| server | 308 | 310 |
| switch | 29 | 38 |
| switchgear | 0 | 4 |
| ups | 4 | 4 |
| utility_feed | 0 | 2 |
| valve | 4 | 4 |
| **Total** | **557** | **659** |

Edges: 2,194 → 2,560 (production 410→436, management 535→689, power 933→1,079,
cooling 316→356).

---

## API Additions

| Method & Path | Purpose |
|---|---|
| `GET  /api/bacnet/electrical` | Electrical-chain metrics (utility → switchgear → ATS → MCC → MPP) |
| `GET  /api/bacnet/electrical/devices` | Electrical device roster |
| `POST /api/bacnet/electrical/utility` | Fail / restore a utility feed |
| `POST /api/bacnet/electrical/ats` | Drive an ATS condition |
| `GET  /api/bacnet/plant/chiller-trips` | Latched chiller trips (with degraded flag) |
| `GET  /api/bacnet/plant/standby` | Plant units the BMS has staged off |
| `POST /api/bacnet/plant/chiller-reset` | Reset a latched chiller trip |
| `GET  /api/devices/rack-occupancy` | Rack occupancy + row positions for placement pickers |
| `GET  /api/devices/link-candidates` | Valid link peers for a new/edited device |
| `GET  /api/devices/faulted` | Fleet-wide injected conditions (one call, not per node) |
| `POST /api/fleet/provision-rack` | Manually provision a rack |
| `POST /api/fleet/provision-hall` | Manually provision a hall |
| `GET  /api/topology/devices/{id}/ports` | Per-port used/free with peer |
| `GET  /api/topology/devices/{id}/power-terminations` | Outlet ↔ PSU terminations for a device |
| `POST /api/topology/positions` | Persist canvas positions (bulk) |
| `POST /api/topology/positions/reset` | Reset to computed layout |

`DeviceInfo` gained `snmp_agent` and `snmp_ips`; `/api/topology/graph` devices carry the
same two fields.

---

## Testing

The project had **no test suite** at v5.0. It now ships 20 test modules with `pytest.ini`
and `requirements-dev.txt`:

`test_chiller_staging`, `test_chw_loop`, `test_cooling_regression`, `test_cooling_tower`,
`test_store_condenser`, `test_store_staging`, `test_store_tower_bank`, `test_plant_probes`,
`test_plant_rotation`, `test_two_dc_isolation`, `test_cdu_loop_cache`, `test_sim_clock`,
`test_pdu_breaker_trip`, `test_facility_alarms`, `test_fault_campaign`,
`test_ev2_power_quality`, `test_rule_engine_stats`, `test_vendor_oids`, `test_store_smoke`,
plus a shared `conftest.py` whose two-DC plant fixture exists specifically because
single-DC fixtures hid cross-DC bugs.

A live exhaustion campaign (`tools/live_campaign_exhaustion.py`) exercises the plant
against the running app; it found two faults the fixtures could not, because the fixture
*alarms* machines where the live harness *stops* them.

---

## Migration Notes

* **Topology schema — power modelling changed.** `power_source`, `power_source_a` and
  `power_source_b` are removed. Power is now carried by edges on the `power` layer with
  `outlet` / `psu` terminations, and devices carry `psus` (and PDUs `outlets`). A v5.0
  topology JSON will load, but its power feeds will not survive as cords — re-export from
  a running v6.0 instance.
* **Device names changed.** Every curated device was renamed to
  `CODE-DC-ROOM-Rrow-rack` / `CODE-DC-ROOM` (`DC1-ER1` → `RTR1-DC1-NR-R1-01`). Anything
  keyed on the old names — saved poller configs, external NMS entries, scripts — must be
  updated. The leading code is functional, not cosmetic: the runtime parses it for role.
* **Bind IPs before starting a simulator.** Simulators no longer bind addresses
  themselves. Use Binding → Bind IPs first; an unbound start now fails with an explicit
  message instead of half-working.
* **SNMP port is a UI setting.** Agents bind whatever port was chosen at start (1611 is
  normal — 161 is privileged and needs root/Administrator). The device list, tooltip and
  info modal now report the *served* port; poll that, not the configured 161.
* **RPP has no SNMP agent** (`snmp_port = 0`, no IP, no ports). Anything polling an RPP
  address will now get nothing — read its load from the EV2 sub-meter instead.
* **Plant gear has no SNMP agent** — chiller, pump, cooling tower and valve are
  BACnet/Modbus devices; no `.snmprec` is generated for them and orphaned ones are reaped.
* **Datasets regenerate on topology change** (fingerprint-gated). First start after a
  topology edit will re-index.
* **Version bumped to v6.0.** Internal version strings — which need three parts — read
  `6.0.0`: `app/main.py` (`setApplicationVersion`), `api/main.py` (FastAPI `version` + the
  root payload), `setup.py`, `version_info.txt` (Windows file/product version, previously
  stale at 2.0.0.0) and `webui/package.json` (previously 1.0.0). User-facing surfaces show
  **v6.0**: the desktop window title and About box, and the web UI menu-bar badge and
  About dialog.

---

## Known Gaps

* **Rack/plant sensors are modelled as own-IP SNMP nodes.** A Raritan DPX2 probe has no
  Ethernet port and no agent — it plugs into a PDU sensor port and is polled through the
  host PDU's MIB. The same applies to field valves and pumps on the BMS side. The remodel
  (drop the own IP + OOBM edge, hang the point off its host controller/PDU) is planned,
  not done.
* **Plant probe locations emit rack tokens.** `sensor` is not in `FACILITY_TYPES`, so
  pipe-mounted plant probes (`CHWS/CHWR/FLOW/CWS/CWR/CTB-*-CP`) render as "Row 1, Rack 1".
  Cosmetic; rack-mounted DPX2 units are correct.
* **Facility metric rows in the info modal** are gated on `PASSIVE_DEVICE_TYPES` (RPP
  only), so BACnet plant gear still shows an interface count and CPU row.
* **Per-outlet SNMP tables** are not yet exposed; the outlet/PSU model is in the topology
  and Redfish but not in the PDU MIB walk.
