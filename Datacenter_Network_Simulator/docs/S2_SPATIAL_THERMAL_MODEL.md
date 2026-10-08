# S2 — Spatial Thermal Model for the Server Halls

**Status:** Steps 1-3 of §10 BUILT 2026-10-08 (uncommitted), default `uniform`, so nothing published has changed yet. D-1 decided: **option A** (aisle-end walls). Steps 4-6 wait for the RAM.
**Parent plan:** `dcim-platform/docs/27-digital-twin-plan.md` §6 S2 and §8a.
**Owner:** simulator (`core/device_state_store.py` plus a new pure module `core/air_model.py`).
**Blocks:** the platform's heat map, the "trip one CRAH" acceptance test (docs/27 §8), and Phase 5 (cooling zones and the CRAH-failure what-if).
**Needs before build:**
- The RAM upgrade, so a shadow run can sit beside the live stack.
- An answer to **Q1**: may this change today's published cooling numbers? §9 sets out exactly what moves.
- A decision on **D-1**, where the CRAHs stand (§4).

---

## 0. What is wrong today, in one paragraph

The simulator computes one supply temperature per room (`_room_supply_temp`) and gives every device in the room that air. Every inlet then adds a fixed 0–3 K by rack height. So:

- Every rack in a hall reads the same band, give or take the height term and noise.
- A tripped CRAH warms the whole room evenly instead of the racks in front of it.
- Every CRAH in a room publishes one shared return-air temperature.
- Humidity is a separate random walk per sensor, so two probes 600 mm apart can disagree by 10 % RH. Dew point therefore isn't uniform, which real room air is.
- The 0–3 K vertical rise is the figure for an **uncontained** hall. All four server halls are `containment: cold_aisle`, where the real rise is a fraction of a kelvin.

The platform now draws all of this faithfully: rack gradients, per-aisle heat maps, RCI/RTI/SHI and hot spots. That is why it shows a flat, uniform hall. The fix belongs here, in the device plane, not in the DCIM.

---

## 1. How it works in real halls

### 1.1 Air path (downflow raised floor, chilled-water CRAH)

1. A CRAH (Vertiv Liebert PCW, Stulz CyberAir, Schneider Uniflair) draws warm air from the room through its top inlet. It cools the air over a chilled-water coil and blows it **down** into the raised-floor plenum.
2. The plenum is pressurised. Air leaves through perforated tiles in the cold aisles. The pressure is **not** uniform:
   - It is highest near a running unit's discharge.
   - It falls with distance and around obstructions such as cable trays and pipes.
   - It drops locally when the unit in front of it stops.

   That is why a CRAH "owns" the tiles nearest it. Measured influence studies (Vigilent, EkkoSense, Future Facilities CFD validation) consistently find each unit dominating a zone a few metres across, with overlap between neighbours.
3. Servers pull tile air through their fronts and push it out of their backs into the hot aisle. The rise across a server is P / (ρ · cp · Q): typically 10–15 K, and up to 20 K or more on dense GPU boxes.
4. Hot air returns over the top of the rows to the CRAH inlets.

### 1.2 Where inlet temperature departs from tile temperature

- **Recirculation.** Exhaust finds its way back into an intake:
  - over the top of the rack (uncontained halls, worst at the top U);
  - around the row ends ("wrap-around");
  - through gaps where blanking panels are missing (empty U).

  This is what RCI, RHI/SHI and RTI measure. It is what the platform's new layers are built to show.
- **Starvation.** When the tiles in front of a rack deliver less air than its servers pull, the shortfall is made up from wherever the rack can draw. In practice that means recirculated hot-aisle air. A failed CRAH starves the racks nearest it **first**, because its zone loses plenum pressure. Racks far away barely notice until the survivors run out of capacity.
- **Containment.** Cold-aisle containment (doors on the aisle ends, a roof over the aisle) cuts recirculation to **leakage**: a few percent, mostly through gaps and missing blanking. The vertical inlet gradient collapses to tenths of a kelvin. Starvation still happens, and inside a contained aisle it shows as the whole aisle warming together, because the roof stops air from coming over the top.
- **Bypass.** Cold air that returns to a CRAH without passing through any IT. Typical causes are tiles in the wrong place, oversupply, or leaky containment. Bypass lowers return temperature and drives RTI under 100 %.

### 1.3 Humidity

Room air is well mixed in **absolute** moisture (humidity ratio W, g of water per kg of dry air). Dew point is therefore nearly uniform across a hall. **Relative** humidity is not: the same W reads lower RH where the air is warmer. A hot-aisle probe reads ~25–30 % RH while the cold aisle reads ~45 % from the same air mass.

Humidity is controlled per room. Options are CRAH-integrated humidifiers and dehumidification, a separate unit, or nothing, relying on the building AHU. The ASHRAE recommended envelope's moisture limits are dew-point based, roughly −9 to 15 °C, plus a 60 % RH ceiling.

---

## 2. Devices, protocols, metrics, vendors

| Device | Protocol (typical) | Points that S2 moves | Vendors | In the simulator today |
|---|---|---|---|---|
| CRAH (CW) | BACnet/IP via Liebert iCOM, Stulz C7000, Uniflair; Modbus RTU via gateway | Supply_Air_Temp, **Return_Air_Temp (now per unit)**, Return humidity, Fan speed, CHW valve | Vertiv, Stulz, Schneider/Uniflair, Munters | BACnet, 7 per hall |
| Server BMC | Redfish (Thermal: inlet, exhaust), IPMI SDR | **Inlet** (now per rack and position), Exhaust | Dell iDRAC, HPE iLO, Lenovo XCC, Supermicro | Redfish |
| Rack PDU probe | SNMP (PowerNet-MIB / PDU2-MIB) | Probe temperature, **humidity (now derived)** | APC, Raritan, Vertiv Geist | SNMP |
| Rack / room T-RH probe | SNMP (AP9335T/TH via NetBotz or PDU), DPX2 via Raritan PDU | Temperature, **humidity (derived)**, dew point | APC NetBotz, Raritan DPX2, Vertiv Geist | SNMP |
| Wireless rack sensors | Proprietary radio to gateway, gateway out over SNMP / BACnet / API | 3 heights per rack front, often rear | EkkoSense, Vigilent, Packet Power | **Not modelled; see §7, optional** |

This is common practice, not vendor-specific: BMC inlet plus rack-front probes are the inlet record, CRAH return via BMS. Vendor-specific behaviour includes Liebert "teamwork" group control and Stulz sequencing; the existing group control stays as it is (§6.5). Rare: CFD-calibrated digital twins (6SigmaDCX), which we do not attempt.

---

## 3. Unrealistic assumptions found while mapping (fix in S2)

| # | Today | Real | S2 action |
|---|---|---|---|
| A-1 | One supply temperature per room | Supply varies across the floor with distance to running units | Per-rack supply from influence weights (§5.2) |
| A-2 | Fixed 0–3 K bottom-to-top rise on every inlet | Contained cold aisle: ~0.2–0.8 K. Uncontained: 2–5 K. Rises at row ends | Height profile driven by recirculation and containment (§5.4) |
| A-3 | A trip warms the whole room (+0.4 K per lost unit, capped at 2 K, then the 12 K unmet term) | A trip warms its own zone first. The room only warms when the survivors run out | Local starvation term (§5.3). Room-level terms kept only as the conservation check (§6.3) |
| A-4 | One return-air temperature shared by every CRAH in a room | Each unit reads the air arriving at its own inlet | Per-CRAH return (§5.5) |
| A-5 | Independent humidity walks per sensor, 35–65 % | One humidity ratio per room. RH follows each sensor's temperature | Psychrometric humidity (§5.6) |
| A-6 | Seven 1.75 m-wide CRAHs on 1.0 m centres along one 8.4 m wall | Can't physically fit (needs 12.25 m plus service clearance) | **Decision D-1** (§4). Re-place the units; geometry is S2's input |
| A-7 | Racks without a coordinate get the room figure silently | Same, but it should be visible | Keep the fallback; count and log racks without coordinates (§6.6) |

---

## 4. Decision D-1 — where the CRAHs stand

Seven Liebert PCW 100 kW units per hall, sized N+1 to build-out at 39 racks × 12 kW design (`project_dc_capacity_design`). That sizing is right; only the placement is wrong.

The hall is 8.4 m (along the rows, x) × 12.3 m (across the rows, y). Rows run along x, so hot and cold aisles open to the walls at x = 0 and x = 8.4.

**Option A — recommended: units at the aisle ends.**
- 4 units on the x = 0 wall and 3 on the x = 8.4 wall, facing into the room and spread across y.
- This is the common layout: units line up with the aisles, so hot-aisle air has a short, direct path back to a return.
- **Cost:**
  - The room grows from 8.4 m to about 12.2 m wide. Each end gets 0.9 m of unit depth plus 1.0 m of clearance in front, roughly what service access requires.
  - A geometry change, run through `tools/layout_buildings.py`.
  - The platform needs a `--geometry-only` re-import (never the full importer on the live estate; see memory).

**Option B: units on the two long walls.**
- 4 units at y ≈ 0.45 and 3 at y ≈ 11.85, facing across the rows.
- This needs no room resize, because 4 × 1.75 m = 7.0 m fits in 8.4 m.
- It is common in older raised-floor halls. Its weakness is that return air has to cross rows to reach a unit. The model handles this; it is just the less textbook picture.

**Recommendation: A.** It is what an operator expects, and it gives the influence zones a clean shape:
- the units on the x = 0 end own the left half of each aisle;
- the units on the x = 8.4 end own the right half.

That makes the "trip one CRAH" demonstration easy to read. If you would rather not touch room sizes, B works with the same model.

---

## 5. The model

All of it lives in a new **pure** module, `core/air_model.py`: no store, no I/O, plain dataclasses in and out. It can then be unit-tested against hand-worked cases, the way the platform's `thermal_indices.py` is. The store gathers the inputs once per tick per room, calls `solve_room(...)`, and reads results by rack and by unit.

### 5.1 Inputs per room (per tick)

- **Racks *i*:**
  - centre (x, y), facing (N/S), cold-face point *c_i*, hot-face point *h_i*, row and position in the row;
  - air-side heat P_i in W: IT power minus the share captured by a CDU loop. Use the existing `_room_it_w` split; it is already air-side;
  - open (unblanked) U fraction *o_i*.
- **CRAHs *j*:**
  - discharge point *d_j*: the unit's front face centre at floor level, because air enters the plenum there;
  - delivered fraction *f_j* (existing `_crah_delivered_frac`: 0 stopped, 0.8 dirty filter, 1 healthy);
  - discharge temperature *S_j* (live `Supply_Air_Temp`; already carries the CHW penalty);
  - rated sensible capacity *C_j* (existing `cooling_capacity_w`, sim sizes 80 kW).
- **Room:** containment (`cold_aisle` | `hot_aisle` | `none`); aisles (y bands and type); previous tick's exhaust per rack (*E_i*, from the server model); room humidity ratio *W*.

### 5.2 Tile supply — who cools which rack

Influence of unit *j* on rack *i*:

```
w_ij = f_j · exp( −dist(c_i, d_j) / L )
```

`L` is the influence length. **Default 5 m. This is uncertain**: published influence-zone sizes run ~3–8 m depending on plenum depth and obstructions, so it is a named constant and the tests pin behaviour, not the number.

The rack's tile air is the influence-weighted mix of what the running units discharge:

```
T_tile,i = Σ_j w_ij · S_j / Σ_j w_ij        (if Σ_j w_ij > 0, else: no supply → see 5.3)
```

### 5.3 Local coverage — starvation instead of a room-wide penalty

Starvation is about **airflow**, not rated capacity. Group control (Liebert teamwork, Stulz sequencing) throttles each unit's fans to what the room needs, so the air actually moving is the rating × fan speed, not the rating. Tile flow then follows two things:

- **plenum pressure**, i.e. influence;
- **tile provisioning**: a tuned hall has higher-flow tiles (grates) in front of denser racks, so a rack's share also scales with its heat.

```
share_ij   = w_ij · P_i / Σ_k w_kj · P_k               (unit j's air out of rack i's tiles)
cover_i    = Σ_j C_j · fan_j · share_ij  /  P_i       (C_j at design rise; fan_j = speed fraction)
starve_i   = clamp(1 − cover_i, 0, 1)
```

- In steady state the fans sit just above the load, so `cover_i ≥ 1` and nothing starves.
- Trip a unit and its tiles lose pressure. The dense racks in front of it drop below 1 and pull hot-aisle air **until the survivors ramp**. That is the real transient, and the platform's timeline can replay it.
- If the survivors reach full speed and still can't cover the room, the starvation stays. That is the shortfall today's room-wide 12 K term stood in for.

*(Corrected 2026-10-08 during the build. The first draft shared **rated** capacity: with 4× more installed cooling than load, a trip moved nothing. A second try shared fan-scaled air by pressure **alone**: a 30 kW rack then starved even in a healthy hall, which a tuned hall prevents with its tiles. Both fixes are kept: fan-scaled air, shared by influence × heat.)*

### 5.4 Inlet temperature — tile air plus recirculation

```
T_hot,i  = heat-weighted mean of E_k over racks k sharing i's hot aisle within ±2 positions
           (falls back to the rack's own exhaust, then to T_tile,i + 12 K)

r_i(z)   = clamp( r_base(containment, z, row_end_i, o_i) + starve_i · (1 − r_base), 0, 0.9 )

T_in,i(z) = T_tile,i + r_i(z) · (T_hot,i − T_tile,i)
```

`r_base` is the structural recirculation. It is a table, and every value in it is a named constant:

| Containment | Bottom third | Middle | Top third | Row-end rack | Per open-U fraction |
|---|---|---|---|---|---|
| cold aisle (all four halls) | 0.01 | 0.02 | 0.03 | +0.02 | +0.10 · o_i |
| none | 0.03 | 0.08 | 0.18 | +0.10 | +0.10 · o_i |
| hot aisle | 0.01 | 0.02 | 0.02 | +0.01 | +0.05 · o_i |

*z* is the device's height in the rack (U to metres, the same geometry the platform uses). This **replaces** the fixed `rack_unit/42 · 3 K`.

In a contained hall with the hot aisle 12 K above the tile, the bottom-to-top rise becomes about 0.25 K. Uncontained, it is about 1.8 K, or more at the row ends. Those are the textbook magnitudes.

`E_i` is the previous tick's exhaust. Using the last tick breaks the inlet ↔ exhaust loop without iteration, and one tick of lag (~60–120 s) is the right order for room air anyway.

### 5.5 Per-CRAH return

Each unit's return is the heat-weighted hot air of the racks it serves (the transpose of the influence), plus bypass:

```
R_j = (1 − b) · Σ_i w_ij·P_i·T_hot,i / Σ_i w_ij·P_i  +  b · S_j
```

- `b = 0.15` today (`_RETURN_MIX_FRAC = 0.85`, the same number, now applied per unit).
- A **stopped** unit keeps publishing a return. Its sensor sits in room air: it reads the air at its inlet, the w-weighted mix as if *f_j* = 1. That keeps the existing rule that a stopped unit's return is real.
- The existing per-unit valve and fan logic reads `R_j` instead of the room figure.

### 5.6 Humidity

- One random walk per room, on humidity ratio *W* (g/kg). It mean-reverts toward the value that gives 45 % RH at the room's mean supply temperature, bounded to dew points between 5.5 and 15 °C (ASHRAE recommended).
- Per sensor: `RH = psychro.rh_from_w(T_sensor, W, P_atm = 101.325 kPa)`; `dew point = psychro.dew_point_from_w(W)`. The same dew point applies everywhere in the room.
- The existing per-device humidity walks and their 35–65 % clamp are **removed**.
- The Magnus constants already used by the platform and by `dew_point_c` stay the single source.

### 5.7 What every consumer reads

| Consumer (today) | Reads after S2 |
|---|---|
| Server inlet (`device_state_store` ~6727) | `T_in,i(z_device)` |
| Rack probe ambient (~6826) | `T_in,i(z_probe)`; probes on the rear face read `T_hot,i` |
| Rack PDU probe (~7800) | `T_in,i(z)` at mid-strip height. Where a PDU's external probe hangs is up to the installer (front door, rear, or coiled among the cords). The sim keeps today's inlet-side reading, and the platform keeps leaving PDU probes out of the indices for that reason |
| CRAH Return_Air_Temp (~5803) | `R_j` per unit |
| Humidity on every probe | §5.6 |
| Fan duty / group control | unchanged; reads the room aggregate built from the new per-rack inlets |

---

## 6. Integration and safety

### 6.1 Feature flag and shadow mode (answers Q1 with data)

- `DCIM_SIM_AIR_MODEL = uniform | spatial | shadow`. Default **uniform** until Q1 is answered.
- `shadow` computes both models every tick and **publishes uniform**. It also writes a per-room comparison to `logs/air_model_shadow.jsonl`: mean, p90 and max inlet; per-CRAH return; rack-level deltas.
- One day of shadow on the live estate gives the before/after table Q1 asks for, without moving a single alarm.

### 6.2 Performance

- A hall is ≤ 39 racks × 7 units: about 300 weights, recomputed **only** when geometry or a unit's state changes (cache keyed on the delivered-fraction vector).
- Per tick: one pass per rack. Negligible next to the SNMP dataset writes.

### 6.3 Conservation check (keeps the existing regression suite honest)

The room-level shortfall model stays as an **oracle**, not as a term.
- With every unit healthy, the room-mean inlet under `spatial` must be within ±1.0 K of `uniform`, **minus the containment correction (A-2)**.
- Under total plant loss, both must hit the same thermal ceiling.

Those cases are tests (§8). `test_cooling_regression.py` must pass unchanged, except where a test pins a value that A-2 deliberately changes. Any such test is listed and changed in the same commit with the reason.

### 6.4 Datasets

SNMP values are file-backed and only `patch_metrics` OIDs move (memory: *SNMP values are file-backed*). Inlet, probe and humidity OIDs are already patched per tick, so no new OIDs are needed.

The topology fingerprint changes only if D-1 moves units. In that case: **Stop → Regenerate Datasets → Start**, and verify `pgrep snmpsim` and SNMP 894/894 afterwards (memory: *Sim restart drops the protocols*).

### 6.5 What does not change

- Chiller, tower, pump and CHW loops.
- CDU liquid loops: their heat stays out of `P_i`.
- Liebert-style group control.
- Trap and alarm rule definitions.
- The plant-walk rule: a live-driven plant point must `continue` out of the random walk (memory: *Plant walk beats the live value*). Any new per-unit point follows it.

### 6.6 Racks without coordinates

They keep today's `_room_supply_temp` value. The store counts them per room and logs once per topology load, so a gap is visible rather than silent.

---

## 7. Optional, separate from the core: more inlet sensors (S2b)

docs/27 sketched top/mid/bottom inlet probes on row-end racks and every third rack. That is the density EkkoSense and Vigilent deploy, and it would sharpen the platform's heat map a lot. But it adds **devices**, which means new SNMP endpoints, new collector targets and a platform import.

So it is its own step after the core model has run in shadow. The core model stands on its own: BMC inlets already give per-U readings on every server rack.

---

## 8. Tests (all in the simulator repo; pure tests against `core/air_model.py` unless noted)

1. **Influence:** weights fall with distance; a stopped unit has zero influence; the weights normalise.
2. **Trip test, the headline** (extends `conftest.build_two_dc_plant` with real hall geometry):
   - trip the unit nearest the densest rack;
   - the row nearest it rises most and the next row much less. With L = 5 m in a 12 m hall the loss spreads along the row rather than staying at the nearest rack; with two units down on one wall, the tripped end starves hardest;
   - the other hall and the other DC don't move (±0.05 K);
   - RCI-HI computed from the published inlets drops.
3. **Redundancy covers:** one trip with N+1 headroom leaves every rack's `starve_i` at 0. The local rise comes from the supply mix only, and stays under 1.0 K.
4. **Containment:** with a 12 K hot-aisle rise, the bottom-to-top inlet difference is under 0.5 K contained and between 1.5 and 3 K uncontained.
5. **Row ends:** in an uncontained hall, an end rack's top inlet is warmer than a middle rack's top inlet.
6. **Per-CRAH return:** the unit beside the hottest racks reads the highest return; a stopped unit still publishes a return.
7. **Humidity:** dew point is identical (±0.1 K) across every probe in a room, and RH is lower on the hot face than on the cold face.
8. **Conservation (§6.3):**
   - healthy hall: spatial room mean ≈ uniform room mean minus the A-2 correction;
   - total loss: both reach the same ceiling.
9. **Fallback:** a rack without coordinates reads the uniform figure, and the counter increments.
10. **Live campaign:** `tools/live_campaign_exhaustion.py` runs clean after the flag flips (memory: cooling remediation).

---

## 9. What moves on the live estate (the Q1 answer, before shadow data)

| Published value | Change | Why |
|---|---|---|
| Server and probe inlets, healthy hall | Room mean **falls ~1–1.5 K**; rack-to-rack spread appears (~0.5–1.5 K) | A-2 removes the uncontained 0–3 K gradient (mean 1.5 K) from contained halls; A-1 adds distance variation |
| Inlet alarms (warn 27 / crit 32) | None expected in a healthy hall; fewer near-threshold flaps | Lower mean; healthy halls sit around 22–24 °C |
| CRAH Return_Air_Temp | Differs per unit by ~1–3 K | A-4 |
| CRAH valve and fan (per unit) | Follow their own return; spread across units | Consequence of A-4; group control unchanged |
| Humidity on probes | Hot-face probes read ~15–20 % RH lower; dew point flat | A-5; real behaviour |
| Platform THERMAL page compliance % | May rise slightly | Lower mean inlet |
| Platform heat map, RCI/RTI/SHI | Become meaningful | The point of S2 |

Shadow mode (§6.1) replaces every estimate in this table with measured numbers before anything is published.

---

## 10. Build order

1. **D-1** geometry: re-place the CRAHs (`tools/layout_buildings.py`), re-export the floor plan, run `--geometry-only` on the platform, regenerate datasets.
2. `core/air_model.py` plus the pure tests (§8: 1, 3–5, 7–8).
3. Store integration behind the flag, default `uniform`; the regression suite stays green.
4. Shadow for one day; publish the Q1 table from the logs.
5. On your go, flip to `spatial`; run tests 2, 6, 9 and the live campaign; screenshot the platform heat map and the trip test.
6. Optional S2b sensors.

Sizes: steps 1–3 about two sessions; step 4 is wall-clock; step 5 half a session.


---

## 11. Build log

**2026-10-08: steps 1-3 built, not deployed.**

1. **D-1 option A applied:**
   - `core/hall_geometry.py`: per-room grid origin `grid_x0_m` (0.3 default; server halls `HALL_X0` = 2.2), a `CRAH_END_ZONE_M` of 1.9 m, `hall_width()` and `crah_positions()`;
   - `core/fleet_lifecycle.py`: every rack placement uses the room's origin, CRAHs come from `crah_positions` with rotation, and the old back-wall layout is gone;
   - `tools/seed_hall_crahs.py` delegates to the same function;
   - `tools/regrid_halls_aisle_end_crahs.py` (new, idempotent) applied to `topologies/dual_dc_enterprise.json`: four halls 8.4 → 12.2 m, racks +1.9 m, 4 + 3 CRAHs on the end walls, panels in the back corners;
   - `tools/layout_buildings.py`: level-1 rooms beside Hall A moved (Network Room 12.2, Central Plant 15.2);
   - `--check` now reports no placement issues (it used to flag the CRAH line), and the floor plan was re-exported.
   - The back row stays reserved for compute capacity even though CRAHs left it. Changing that is a capacity decision, not part of S2.
2. **`core/air_model.py`** plus `tests/test_air_model.py` (14). §5.3 was corrected twice while building (see its note); the trip test now asserts per row (§8 item 2).
3. **Store wiring behind `DCIM_SIM_AIR_MODEL`:**
   - server inlet, rack probe and rack PDU probe;
   - per-CRAH `Return_Air_Temp`;
   - sensor RH from the room humidity ratio (`spatial` only);
   - shadow log `logs/air_model_shadow.jsonl`;
   - `tests/test_air_model_store.py` (5).
   - `uniform` never solves the model and draws the same random numbers in the same order.
   - Full suite: only `test_dpx2_sensor_port` and `test_vendor_oids` fail, and they fail identically on the clean commit.

**Also 2026-10-08:** rack PDU probe humidity now follows the room's humidity ratio in `spatial` mode, read at the probe's own temperature, so every probe in a room shares one dew point. Two more tests in `tests/test_air_model_store.py` (21 air-model tests in all).

**Not yet done:**
- step 4 onwards: deploy, Stop → Regenerate Datasets → Start, platform `--geometry-only` re-import, a day of `shadow`, then the Q1 decision.
