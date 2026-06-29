# Datacenter Network Simulator — Release Notes v5.0

**Release Date:** June 29, 2026
**Version:** 5.0.0
**Compared against:** `Faberwork-release-Datacenter_Network_Simulator_v4.1`

---

## Overview

Version 5.0 turns the simulator from a *static* datacenter into a *living* one. The
headline change is the **Fleet Lifecycle Engine** — a new subsystem that churns the
server fleet day-by-day (provision, decommission, fill racks, grow halls, open new
halls) and hot-commissions every new device onto the live protocols. Around it, this
release adds a **physics-based live power chain** (server → PDU → RPP → UPS → generator
with meter-derived PUE), a **thermal model with a 3D heatmap floor-plan**, and a
**live, save/load-able floor plan** that reflects the running topology in 2D and 3D.

This is a major release: it introduces a new simulation subsystem (Fleet Lifecycle) on
the scale of the Redfish/BMC addition that defined v4.0, plus live power/thermal physics
and 3D visualization. All changes are additive — no breaking API removals.

---

## What's New

### 1. Fleet Lifecycle Engine (new subsystem)

Real datacenters aren't static — compute is provisioned and decommissioned continuously.
The engine models that on a compressed clock: one "sim-day" every *N* minutes (off until
started), adding and removing **servers** (and the ToR switch + dual PDUs a new rack
needs) with lumpy, net-positive churn.

* **Coherent provisioning** — a new server lands in a rack with free U, a ToR and a PDU;
  gets a free production IP; uplinks to the rack ToR; draws A/B power from its PDUs; and
  clones vendor/model/port profile from an existing peer so the fleet stays consistent.
* **Grid-based growth** — each hall holds `compute_rows_per_room × max_racks_per_row`
  compute racks. The fleet fills existing racks first, then adds racks up to the hall's
  grid, then **opens a brand-new hall** (next floor, "Server Hall C/D…") with its own
  RPP pair fed from the DC UPS and a registered floor-plan room extent.
* **Hot-commission** — new devices answer on SNMP/gNMI/Redfish without a restart; SNMP
  agents are auto-reloaded once per sim-day that changed devices.
* **Correct addressing & naming** — production IPs from the prod `/16` (10.50.x), mgmt
  IPs from the DC's mgmt `/22` (192.168.x), and curated-style names (`DC2-SRV121`).
* **Activity log** — per-sim-day, expandable, showing each added/removed device's name,
  vendor, prod IP and mgmt IP.
* **Bigger initial halls** — `tools/enlarge_halls.py` + `core/hall_geometry.py` grow the
  curated halls' floor extent (more compute rows) so there's headroom for the fleet to
  fill, footprint-frozen and non-destructive to the curated layout.

Controls (FleetPanel): minutes/day, provision/day, decommission/day, power cap/rack,
racks/row, rows/hall, max total servers.

### 2. Live Power Cascade & PUE

Power is now load-coupled bottom-up: live per-server watts (CPU-scaled nameplate) flow
up `server → PDU → RPP → UPS → generator`, split across redundant A/B feeds, so PDU
load, UPS output load and RPP/EV2 panel kW track the real IT load. Facility power
(IT + cooling) drives a **meter-derived PUE** widget exactly the way a DCIM computes it
from physical meter readings. Per-model nameplate fill, a load-coupled generator, and
UPS runtime ∝ load round it out. The cascade is cache-invalidated on any topology change
(fleet churn, manual add/remove), so new load reaches the upstream meters.

### 3. Thermal Physics & 3D Heatmap

Metrics now have physics: temperatures couple to load and cooling, liquid-cooled servers
run cooler than air-cooled, inlet temp drifts from the sensor side, and disk/CPU models
were retuned (no more pinned 90%). The floor-plan viewer gains a **3D rack view** and a
**spatial heatmap** (IDW interpolation, banded colors) for power density and inlet
thermal, with a dynamic cutaway.

### 4. Live Floor Plan + Upload/Save

The Floor Plan page is now **always live**: 2D and 3D render from the current in-memory
devices, so fleet-added racks/servers/devices appear and update as the fleet grows
(no Live toggle to click). New File-menu actions:

* **Upload Floorplan…** — load a floor-plan JSON to view (overrides the live build).
* **Save Floorplan** — download the current floor-plan (curated + fleet racks/devices,
  coords, power feeds, room extents incl. fleet-created halls).

`Device` now carries floor-plan fields (`floor_x/floor_y/rack_facing/cold_aisle/
hot_aisle/sub_floor`) and dual-feed `power_source_a/b`, so placement and power feeds
survive load/save and reach the live floor plan.

### 5. Fault Injection & Trigger Events

* Every device exposes **Simulate Fault ▶** and **Inject Fault** — ramp a metric across
  its SNMP thresholds to drive realistic alarms/traps.
* Right-click **Trigger Event** is now a submenu (not a modal).
* **Autonomous Faults** toggle (default off); the rule engine is always on.
* Trap fixes: unique OIDs per recovery/variant trap, metric included in details, OOB
  linkDown dedup.

### 6. Realism & Fixes

* **Cooling topology** — Hall B CRAH/CDU cooling links wired in both DCs; CRAH/CDU layout
  fixes; per-rack CDU for liquid-cooled servers.
* **SNMP CPU OIDs** — servers now emit the standard UCD `ssCpuUser/System/Idle`
  (`2021.11.9/10/11`) and patch them live, so a poller's `100 − ssCpuIdle` tracks the
  device instead of reading a frozen ~99%.
* **Tick cadence** — 1 s metric tick, SNMP `.snmprec` re-sync at least every 5 ticks;
  per-device Metric-Tick control.
* **Concurrency** — guarded the rule engine's per-device state against a
  "dictionary changed size during iteration" crash under fleet churn + API reads.

---

## API Additions

| Method & Path | Purpose |
|---|---|
| `GET  /api/fleet/status` | Lifecycle state, config, fleet size, recent sim-days |
| `POST /api/fleet/config` | Update cadence/caps |
| `POST /api/fleet/start` / `stop` | Start/stop the sim-day scheduler |
| `POST /api/fleet/advance` | Apply one sim-day now |
| `GET  /api/floorplan` | Live floor-plan (curated + fleet); uploaded plan overrides |
| `POST /api/floorplan/upload` | Upload a floor-plan JSON to drive the view |
| `POST /api/floorplan/clear` | Revert to the live floor-plan |
| `GET  /api/topology/export` | Now includes the `floorplan` extent block |

---

## Migration Notes

* **Fleet is in-memory.** The on-disk topology JSON is never rewritten; a restart/redeploy
  reverts to the original fleet. To keep fleet growth, **Save Topology** (export includes
  fleet devices + positions + floor coords) and reload it after redeploy.
* **SNMP for fleet devices.** New SNMP agents become pollable after a snmpsim re-index —
  the fleet auto-reloads once per changed sim-day; host IP binding still needs admin.
* **`Device` schema** gained `floor_x/floor_y/rack_facing/cold_aisle/hot_aisle/sub_floor`
  and `power_source_a/b`. Older topology JSON loads unchanged; these default empty/None.
* **Version bump** to 5.0.0 (`app/main.py` `setApplicationVersion`).
* No breaking changes to existing endpoints or topology format.
