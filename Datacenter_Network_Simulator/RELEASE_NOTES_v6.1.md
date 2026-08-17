# Datacenter Network Simulator — Release Notes v6.1

**Release Date:** August 17, 2026
**Version:** 6.1
**Compared against:** `Faberwork-release-Datacenter_Network_Simulator_v6.0`
**Scope:** 41 commits, 2026-08-12 → 2026-08-17 · 59 source files, +8,949 / −2,118 lines
(excluding regenerated topology JSON and 14 removed `.bak` snapshots)

---

## Overview

v6.0 made the datacenter physically honest. v6.1 makes it **honest about its own
instruments** — what speaks which protocol, what owns an IP address, and where a
published number actually comes from.

Two themes.

The first is **fieldbus realism**. A Modbus/TCP plane joins SNMP, BACnet, gNMI, Redfish
and sFlow, carrying the 30 electrical devices as a second rendering of the same state.
More consequentially, fifty field devices **lost their IP addresses**, because real
transmitters, actuators and probes do not have them: plant header instruments now sit
behind Moxa Modbus gateways, pumps and valves on RS-485 MS/TP trunks behind BACnet/IP
routers, and Raritan DPX2 probes on PDU sensor ports. The managed address count fell
1002 → 956 — the first release where the topology got *smaller* and more realistic at
the same time.

The second is the **cooling model's causality**. In v6.0 a DC's cooling figure was set
top-down by a calibration constant, and every plant device was scaled so the sum matched
it. That is backwards from how a plant meters, and it hid a class of defect that cannot
fail a test: a calibration number and the device curves can disagree indefinitely while
each stays internally consistent. `OH_VAR` implied a plant COP of 3.1 while the chiller
module rated 5.5, and only `OH_VAR` reached PUE. v6.1 inverts it. Every plant device now
computes its own draw from its own physics, and the DC total is their **sum**. PUE became
an output rather than a constant, moving 1.688 → ~1.44 on the shipped topology.

A consequence worth stating plainly: PUE *improves* during a cooling failure, because a
plant that has stopped working draws less power. The status bar now says so.

This release does **not** change the topology schema. It does change device addressing
for field gear and the plant override channel — see **Migration Notes**.

---

## What's New

### 1. Modbus/TCP simulation plane

A fourth control-plane protocol, modelled the way Modbus actually behaves — no
self-description, no unsolicited messaging, and no way to ask a device what it is.

* **Register maps per vendor** (`core/modbus_register_map.py`, 649 lines) — seven maps
  covering utility feed, switchgear, MCC, MPP, generator, ATS and UPS, plus probe maps
  for temperature and flow transmitters. Per-vendor word order (`WORD_BIG` / `WORD_SWAP`),
  scaling (×10 / ×100), IEEE-754 float32, saturation rather than wrapping.
* **A real slave** (`simulator/modbus_device.py`) — FC 01/02/03/04/05/06/15/16/43, MBAP
  framing, exception codes 0x01–0x04, and the gateway-specific 0x0A (path unavailable) /
  0x0B (target failed to respond) that a serial bridge returns when the RTU behind it is
  silent.
* **One listener, routed by destination** (`simulator/modbus_controller.py`) — a single
  `0.0.0.0:502` socket demultiplexes on `getsockname()`, so hundreds of slaves cost one
  file descriptor. RS-485 latency is modelled with a pending queue.
* **`Data_Valid` discretes on all seven electrical maps** — a Modbus register always
  reads *something*, so validity has to be a separate bit. Backed by a `presence_of`
  field so the bit reflects whether the underlying telemetry exists, not whether a default
  was substituted.
* **8 REST endpoints** (`api/routers/modbus.py`) and a **Modbus panel** in the web UI,
  themed to match the BACnet panel and reading status from the store.

CRAH, CDU and PDU are deliberately **excluded** — those are SNMP/BACnet devices in the
field, and giving them Modbus would have been convenience rather than realism.

### 2. Field devices lose their IP addresses

Fifty devices moved off own-IP addressing onto the buses they really sit on.

| Migration | Devices | Now reached via |
|---|---|---|
| Plant header instruments | 12 | 2 Moxa Modbus gateways (unit ID) |
| Pumps and valves | 18 | BACnet/IP routers, MS/TP trunk (network + MAC) |
| Raritan DPX2 probes | 20 | PDU external-sensor ports (host + slot) |

* **BACnet MS/TP routing** (`core/bacnet_object_model.py`, `simulator/bacnet_controller.py`)
  — NPDU `DNET`/`DADR` dispatch inbound, `SNET`/`SADR` source routes on every reply. The
  failure mode when this is wrong is not an error; it is one device answering for all
  eighteen, so the reply path is tested per-MAC.
* **Probes chain off the A-feed PDU in their own rack** and carry no cord — a sensor port
  is not an outlet.
* **The plant override channel is re-keyed by device NAME**, not bind IP, because a device
  that gives up its address must not give up its identity.
* **Managed addresses 1002 → 956.** An orphan-IP reaper (`POST /binding/reap-orphans`)
  clears managed aliases no device claims, across every adapter.

### 3. Cooling model — top-down becomes bottom-up

The largest behavioural change in the release, delivered as five separate corrections.
Each was found by fixing the one before it.

* **`OH_VAR` is derived, not chosen.** `OH_VAR = (1 + OH_FLOOR × EVAP_HEAT_FRAC) /
  CHILLER_COP_RATED`. The two efficiency models are now anchored to the same point —
  `chiller_power_frac(1.0) == 1.0` and `ambient_factor(REF_AMBIENT_C) == 1.0`, so
  `chiller_cop()` returns exactly the rating at design. `EVAP_HEAT_FRAC` exists because
  the compressor lifts CRAH fan and pump heat along with the IT load, while tower fans and
  CW pumps reject on the far side.
* **Pump speed is the drive's own commanded fraction**, no longer back-derived from a
  normalised power share — which answered "what fraction of the plant's electrical bill is
  this machine", not "how fast is it turning". Scaled by trains running, because a
  survivor carries the whole header when its neighbours stage out.
* **Condenser flow holds the design range** and floors at the pumps' own turndown, instead
  of being sized with the *evaporator* loop's minimum-flow curve. That form made flow
  ≈ `reject / duty` — nearly load-independent, and inverted: the DC rejecting 153.9 kW
  moved less water than the one rejecting 111.4 kW.
* **Every VFD device publishes its own affinity curve.** The DC-wide scale factor is gone;
  the chiller absorbs the calibration residual, floored at its fixed losses. Two identical
  pumps at one speed had been publishing 2.15 and 1.41 kW.
* **Pump head is the real pump curve**, `h = 1.25·N² − 0.25·Q²`. Speed alone cannot fix
  head: a pump held at one speed while the valves open passes more water and develops
  *less*. Reduces to the affinity law exactly on the similarity line.
* **CRAH fan duty is thermal** — this room's heat against the cooling its CRAHs are rated
  for, not the plant's electrical ratio. Also removes the circular dependency that blocked
  the inversion.
* **The DC cooling total is `Σ` device draws**, summed after the fault-collapse pass.
  `cooling_electrical_w()` survives as the design envelope and as the fallback duty anchor.

**Consequence:** stopping plant can now only *reduce* cooling — structurally, since a
device that stops drawing cannot raise a sum. The failure mode where metered cooling rose
while the model collapsed is no longer expressible.

### 4. Plant health beside PUE

PUE is a ratio, not a health metric, and it moves the wrong way during a failure. Measured
live: stopping all three DC1 chillers took PUE from **1.441 to 1.298** — out of the amber
band and into the green one — while cooling collapsed 86.8 → 59.5 kW.

The status bar (right-aligned) now carries a plant-condition chip in four states:
`COOLING DEGRADED · DC1` (red) › `n TRIPPED` (amber) › `PLANT OK` (green) ›
`PLANT ?` (dim, health unknown). PUE renders dim rather than in its efficiency band while
the plant is degraded — not red, because the value is arithmetically correct; it simply
stops meaning efficiency.

`PLANT ?` is a distinct state on purpose: a failing endpoint returns empty fields, and
without it the chip would assert health it had never verified.

### 5. Web UI

* **Console** moved from a 320 px sidebar tab to a modal opened from Simulation ▸ Console —
  it is a log to read, not a control surface, and monospace lines want width.
* **Toolbar** reordered to binding / SNMP / gNMI / Redfish / sFlow / BACnet / Modbus, text
  labels removed, Modbus logo sized to match.
* **Menus** — a View menu now owns Topology / Floor Plan / Live Metrics with checked state;
  Floor Plan moved out of the menu it did not belong in.
* **Title bar** device-count badge removed; a Modbus status chip added. The bottom bar was
  cut to power and PUE only — the per-type counts live on Live Metrics with their readings.
* **Distinct colour per link layer**, including a new `fieldbus` layer and a slate
  (`#94a3b8`) management colour that no longer collides with it.
* **Live Metrics shows sensors with their host device**, since a probe with no IP needs its
  controller named to be findable.

### 6. Tests and tooling

* **27 → 37 test files**; the suite runs **641 passed, 1 skipped**, stable under both
  fixed and randomised ordering.
* New coverage: Modbus protocol and per-device maps, BACnet MS/TP routing, MS/TP topology,
  DPX2 sensor ports, plant gateways, override keying, binding orphans, layer colours, and
  cooling calibration.
* **Fixture nameplates corrected** — the conftest plant was specifying a chiller at COP
  1.83 and a 45 kW fan on a tower cell rejecting 220 kW. Oversized nameplates inflate the
  fixed-loss floor, which read exactly like a model defect and was not one. Guarded by a
  test asserting implied COP stays in 3.0–8.0.
* **Live campaign harness fixed** (`tools/live_campaign_exhaustion.py`) — `is_clean()`
  judged the plant settled on water-side signals alone, so the air-side scenario passed its
  recovery check instantly while the hall was still at 36.8 °C, and the next scenario
  baselined off it. Now checks inlet temperature and over-threshold server count, and takes
  a scenario filter so one case can be re-measured in ~20 minutes instead of an hour.
* **Repo hygiene** — 14 stale `.bak` topology snapshots removed (1.27 M lines).

---

## Verification

The six-scenario exhaustion campaign was re-run live against the inverted power model.
All six pass; cooling falls or holds under every fault class, and nothing rises:

| Scenario | Δ cooling | Notes |
|---|---|---|
| `all-chillers` | −22.6 kW | degraded, recovered |
| `all-chwp` | −23.9 kW | CHW flow to 0.0, interlock shed |
| `all-towers` | −25.6 kW | all 3 chillers tripped on head, makeup stopped |
| `all-cwp` | −24.6 kW | rejection lost at the water side |
| `hall-crahs` | −1.0 kW | plant healthy, hall to 36.8 °C, recovered |
| `lead-chiller-stop` | −0.9 kW | clean failover, no thermal excursion |

Live plant after the change: IT ~197 kW, cooling ~86 kW, **PUE ~1.44**, every plant device
publishing its own curve and the DC total equal to their sum within metering jitter.

---

## Migration Notes

**Field devices no longer have IP addresses.** Anything keyed on the IP of a plant header
instrument, pump, valve or DPX2 probe will not resolve. Reach them by:

* Modbus instruments — gateway IP + unit ID
* Pumps and valves — router IP + MS/TP network and MAC
* DPX2 probes — host PDU + sensor slot

**Plant overrides are keyed by device name**, not bind IP. Stored overrides keyed by
address must be re-keyed; the migration tools do this, and re-running them is safe.

**Stale IP aliases.** After upgrading, run `POST /binding/reap-orphans` to clear managed
aliases no device claims. Verify against `ip addr`, not the response body.

**Port 502** must be free for the Modbus listener.

**PUE will read lower.** ~1.69 → ~1.44 on the shipped topology. This is the calibration
correction described above, not a plant change.

---

## Known Gaps

Carried forward deliberately, and recorded rather than quietly closed.

* **Bottom-up PUE runs below the design envelope at part load** (1.27–1.38 in fixtures
  against a 1.354 envelope). Throttled VFD gear on the cube law draws less than
  `OH_FLOOR × staged capacity` asserted. Whether the device inventory under-counts real
  fixed parasitics is an open question, not a settled one.
* **`max(metered, model)` is retained** in `get_power_summary`. Live sampling shows the two
  within ±0.6 % and crossing over, so it is arbitrating noise. Both are now nameplate-bounded,
  so the fleet-growth protection it was written for no longer exists — but removing it buys
  nothing either. Both components are exposed as `cooling_metered_kw` / `cooling_model_kw`
  for anyone who wants to settle it.
* **A saturated plant reads as an efficient one.** Inherent to any ratio of facility to IT.
  The health chip is the mitigation; `degraded`, inlet temperature and over-threshold server
  counts are the honest signals.
* **No system-curve model at pump turndown** — a pump pinned at its floor publishes head as
  a function of speed and flow, but the system curve itself is not modelled.
* **Live CRAH fan duty is pinned at its 30 % floor** until the halls fill; each room has
  700 kW of rated air-side cooling against ~48 kW of live heat. Correct for build-out
  sizing, but it means the thermal-duty path is exercised in fixtures rather than live.
