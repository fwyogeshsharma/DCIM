# DCIM Thermal & Power Heatmap — Technical Design Document

**Audience:** DCIM application developer implementing the heatmap feature.
**Status:** Implementation-ready.
**Scope:** Defines every metric, data source, algorithm, data contract, and
industry reference needed to build floor-plan heatmaps (thermal, exhaust,
humidity, power density, capacity) on top of the Datacenter Network Simulator.

---

## 1. Architectural Principle (read first)

A heatmap is **not** something a device produces. Real datacenter equipment
exposes **point telemetry** over standard protocols; the **DCIM/visualization
layer** polls those points and renders the spatial field. This separation is
mandatory — do not push interpolation or rendering logic into the device/agent
layer.

Two distinct data planes:

| Plane | Content | Owner | How obtained |
|---|---|---|---|
| **Telemetry** | inlet/exhaust temp, humidity, power, airflow | the device | poll a protocol (Redfish, SNMP, Modbus, gNMI) |
| **Placement** | floor X/Y, rack, aisle, room geometry | the **DCIM asset database** | static config / asset import |

A server BMC reports an exhaust temperature; it does **not** know its floor
(x, y). Precise floor coordinates live in the DCIM's asset DB. In this
simulator, that asset DB is modeled by the topology JSON's per-device
`floor_x`/`floor_y`/`cold_aisle`/`hot_aisle` fields and the top-level
`floorplan` block (see §5).

The heatmap join is always:

```
telemetry value  +  placement (rack → floor x,y)  →  aggregate per rack
   →  spatial interpolation  →  color map  →  render
```

### 1.1 Integration surface — how the DCIM gets telemetry

**Critical:** the simulated devices expose their metrics exactly like real
hardware — over **SNMP, gNMI, Redfish, Modbus, and BACnet**. A DCIM integrates
with the simulator the same way it integrates with a real datacenter: by
**polling those device protocols**. This is the integration surface this
document targets (§3, §5.1).

The simulator also ships a **FastAPI REST API** (`/devices`, etc.) — but that is
**only the backend for the simulator's own bundled web UI**. It is an internal
convenience, *not* the path a real/external DCIM uses, and it should not be
treated as a datacenter telemetry protocol. It is documented here only for the
case where you are extending the simulator's *built-in* web-UI heatmap (§5.2).

| Consumer | Telemetry source | Placement source |
|---|---|---|
| **External / real DCIM** (primary) | poll device protocols: Redfish, SNMP, gNMI, Modbus, BACnet | DCIM's own asset DB (imported once); device-side hint via Redfish `Location.Placement` / SNMP `sysLocation` |
| **Simulator's bundled web UI** (internal) | simulator REST `GET /devices` | same REST + topology `floorplan` block |

Everywhere below, the **protocol path is the source of truth**. REST field names
appear only as a secondary note for the internal web-UI case.

---

## 2. Heatmap Types

Implement these as variants of one pipeline (§7), differing only in **metric**,
**per-rack aggregation**, and **color scale**.

| # | Heatmap | Primary metric | Question it answers |
|---|---|---|---|
| H1 | **Cold-aisle / Inlet thermal** | rack inlet temp | Are intake temps within ASHRAE envelope? |
| H2 | **Hot-aisle / Exhaust thermal** | server exhaust temp | Where is the heat being rejected? |
| H3 | **Humidity** | %RH, dew point | Condensation / ESD risk? |
| H4 | **Power density** | kW per rack, W/ft² | Stranded vs overloaded racks? |
| H5 | **Capacity** | rack-U utilization | Where is mounting space left? |
| H6 | **CRAH return** (room-level) | CRAH return-air temp | Aggregate hot-aisle load per CRAH zone |

**H1 (inlet thermal) is the canonical "the heatmap."** Build it first; the rest
reuse the pipeline.

---

## 3. Metrics & Sources

Every value the heatmap consumes, with the real-world source and **the exact
protocol access path a DCIM polls** (the integration surface, §1.1). The
simulator `Device` field is the backing store. The internal REST field (last
column) applies *only* to the simulator's bundled web UI (§5.2) — an external
DCIM never uses it.

### 3.1 Telemetry metrics — protocol access paths

| Metric | Device type(s) | Protocol & access path (poll here) | Backing `Device` field | (web-UI REST field) | Unit |
|---|---|---|---|---|---|
| Inlet/intake temp (server) | server | **Redfish** `GET /redfish/v1/Chassis/{id}/Thermal` → `Temperatures[Name="Inlet Temp"].ReadingCelsius`; **SNMP** BMC `1.3.6.1.4.1.99999.26.2.1.0` (×10) | `inlet_temp` | `inlet_temp` | °C |
| Inlet/intake temp (network) | switch, router | **SNMP** ENTITY-SENSOR-MIB `entPhySensorValue 1.3.6.1.2.1.99.1.1.1.4.1` (×10); CISCO-ENVMON `1.3.6.1.4.1.9.9.13.1.3.1`; **gNMI** `/components/component[CHASSIS]/state/temperature/instant` | `inlet_temp` | `inlet_temp` | °C |
| Exhaust/outlet temp | **server only** | **Redfish** `…/Thermal` → `Temperatures[Name="Exhaust Temp", PhysicalContext="Exhaust"]` | `outlet_temp` | `outlet_temp` | °C |
| Rack inlet temp (bottom) | sensor (Raritan DPX2-T3H1) | **SNMP** `1.3.6.1.4.1.13742.6.5.5.3.1.4.1.1` (×10) | `inlet_temp` | `inlet_temp` | °C |
| Rack mid-height temp | sensor (Raritan DPX2-T3H1) | **SNMP** `1.3.6.1.4.1.13742.6.5.5.3.1.4.1.2` (×10) | `mid_temp` | `mid_temp` | °C |
| Rack top/exhaust temp | sensor (Raritan DPX2-T3H1) | **SNMP** `1.3.6.1.4.1.13742.6.5.5.3.1.4.1.3` (×10) | `outlet_temp` | `outlet_temp` | °C |
| Rack inlet temp (other) | sensor (Geist, NetBotz) | **SNMP** Geist `1.3.6.1.4.1.21239.5.1.*`; APC NetBotz `1.3.6.1.4.1.318.1.1.10.4.2.2.1.*` | `inlet_temp` | `inlet_temp` | °C |
| Humidity | sensor | **SNMP** Raritan `…13742.6.5.5.3.1.4.1.4` (×10) / Geist / NetBotz probe tables | `humidity` | `humidity` | %RH |
| Dew point | sensor | **SNMP** vendor probe table (or derive from temp+RH) | `dewpoint` | `dewpoint` | °C |
| Airflow (air velocity) | server, NetBotz sensor | **SNMP** APC NetBotz `1.3.6.1.4.1.318.1.1.10.4.2.2.1.10.3` (×10 m/s) | `airflow` | `airflow` | m/s |
| Server power draw | server | **Redfish** `GET /redfish/v1/Chassis/{id}/Power` → `PowerControl[0].PowerConsumedWatts` | `power_draw_w` → live `power_watts` | `power_watts` | W |
| PDU metered power / current | PDU | **SNMP** pduOutletTable `1.3.6.1.4.1.99999.5.20.1.*` (per-outlet current/power); **Modbus** holding regs (real PX3/APC MIBs in production) | (ext PDU state) | `pdu_real_power`, `pdu_outlet_power`, `pdu_load` | W / A |
| CPU/ASIC temp | server, switch | **Redfish** `…/Thermal Temperatures[Name="CPU Temp"]`; **SNMP** `entPhySensorValue …4.2`; **gNMI** `/components/component[CPU]/…/temperature/instant` | `cpu_temp` | `cpu_temp` | °C |

> All SNMP temperature values are integer **deci-degrees** (value ×10); divide by
> 10. Airflow is ×10 m/s. Redfish `ReadingCelsius` is a float in °C.

Notes:

- **Exhaust is servers-only by design.** Switches/routers have no BMC, so they
  expose inlet + ASIC temp only (SNMP ENTITY-SENSOR-MIB / gNMI), never a
  Redfish exhaust sensor. Do not expect an exhaust reading from network gear.
  This matches real datacenters: most ToR/access switches expose inlet + hotspot,
  not a labeled exhaust sensor.
- **Server power:** poll Redfish `PowerConsumedWatts` (the live value, =
  `power_draw_w × (0.55 + 0.45 × CPU_load)`). The static `power_draw_w` seed is
  nominal nameplate — do not use it directly for the power heatmap.
- **Rack power must come from the PDU, not summed server nameplate.** Poll the
  PDU outlet table for metered per-rack kW (§7.1).
- `outlet_temp` and `airflow` on servers are **live, ticker-derived** (computed
  from current inlet + power each tick). The corresponding Redfish/SNMP reading
  is absent/zero when the simulation is not running. Treat absent/0 as "no
  data," never as a real measurement.

### 3.2 Placement data — NOT polled from the device

Placement is **asset-database data, not telemetry.** A real DCIM does not poll a
device for its floor (x, y) — it holds that in its own asset DB (imported once)
and joins it to the polled telemetry by device identity. The device exposes at
most a **coarse location hint**:

| Placement field | Real device-side hint (if any) | Backing `Device` field | Unit |
|---|---|---|---|
| Datacenter / room / floor | Redfish `Chassis.Location.PostalAddress`; SNMP `sysLocation` (`1.3.6.1.2.1.1.6.0`) | `datacenter` / `room` / `floor` | — |
| Rack name | Redfish `Location.Placement.Rack`; `sysLocation` token | `datacenter`+`rack_row`+`rack_num` | — |
| Rack row | Redfish `Location.Placement.Row` | `rack_row` | — |
| Rack U offset | Redfish `Location.Placement.RackOffset` (`RackOffsetUnits=EIA_310`) | `rack_unit` | RU |
| **Floor X / Y** | **none — DCIM asset DB only** | `floor_x` / `floor_y` | m |
| **Cold / hot aisle** | **none — DCIM asset DB only** | `cold_aisle` / `hot_aisle` | — |

In this simulator, the "asset DB" is the **topology JSON**: per-device
`floor_x`/`floor_y`/`cold_aisle`/`hot_aisle` + the top-level `floorplan` block
(§4). An external DCIM imports that once (or maps its own asset DB to the same
racks) and binds telemetry → rack using the Redfish `Location.Placement` /
`sysLocation` hint the device exposes. Floor coordinates are **never** obtained
by polling a protocol.

> The simulator's *bundled web-UI* heatmap reads placement from the REST
> `GET /devices` response instead. For that path only, `floor_x`/`floor_y`/
> `cold_aisle`/`hot_aisle` must be added to the REST `DeviceInfo` schema — see
> §5.2. This is irrelevant to an external DCIM.

---

## 4. Coordinate System & Floor Model

Defined by the topology `floorplan` block (generated by
`tools/add_floorplan.py`). Industry-standard raised-floor pod:

| Parameter | Value | Source |
|---|---|---|
| Units | meters | `floorplan.units` |
| Origin | per-room local; room corner = (0,0); X along rows, Y across aisles | `floorplan.origin` |
| Rack footprint | 0.6 m W × 1.2 m D (600 mm cabinet) | `floorplan.rack_footprint` |
| Rack pitch | 0.6 m (center-to-center along a row) | `floorplan.rack_pitch` |
| Row pitch | 2.4 m (1.2 m rack + 1.2 m aisle) | `floorplan.row_pitch` |
| Aisle width | 1.2 m (4 ft / 2 floor tiles) | `floorplan.aisle_width` |

`floor_x`/`floor_y` on each device are the **rack center** in room-local meters.
All devices in the same rack share the same (x, y) — placement is a rack
property, denormalized onto devices.

**Hot/cold aisle containment** (the realism that makes inlet vs exhaust
meaningful): rows pair front-to-front and back-to-back.

- Cold aisle between rows (2k−1, 2k) → id `CAk`
- Hot aisle between rows (2k, 2k+1) → id `HAk`
- Odd rows face +Y (`rack_facing = "S"`), even rows face −Y (`"N"`); each pair
  shares a cold aisle on its facing sides.

Per-room geometry and the aisle list (id, type, bounding rows, centerline Y,
width) live in `floorplan.rooms["<dc>/<room>"]`. Only Server Halls (white-space)
carry aisles; facility rooms (UPS, generator, plant) get coordinates but no
aisle/facing (floor-standing gear).

---

## 5. Data Acquisition

There are two acquisition paths. **§5.1 is the real one** — an external DCIM
polls device protocols. §5.2 is only for extending the simulator's own web UI.

### 5.1 External DCIM — poll device protocols (primary)

This is identical to integrating with real hardware. Per device class:

| Device class | Protocol | What to poll | Discovery |
|---|---|---|---|
| Servers | **Redfish** (HTTP/JSON) | `GET /redfish/v1/Chassis/{id}/Thermal` (inlet/exhaust/CPU temps, fans) and `…/Power` (PowerConsumedWatts). Bind to rack via `Chassis/{id}` → `Location.Placement`. | `GET /redfish/v1/Chassis` |
| Servers (alt) | **SNMP** / IPMI | BMC sensor OIDs (`1.3.6.1.4.1.99999.26.*`), `sysLocation` | SNMP walk |
| Switches / routers | **SNMP** | ENTITY-SENSOR-MIB `entPhySensorValue` walk (`1.3.6.1.2.1.99.1.1.1.4.*`), CISCO-ENVMON; `sysLocation` | SNMP walk |
| Switches / routers (modern) | **gNMI** (gRPC) | Subscribe `…/components/component/state/temperature/instant` | gNMI Capabilities |
| PDUs | **SNMP** / **Modbus** | pduOutletTable (`1.3.6.1.4.1.99999.5.20.1.*`) per-outlet current/power | SNMP walk / register map |
| Environmental sensors | **SNMP** (or Modbus/BACnet) | Raritan `1.3.6.1.4.1.13742.6.5.5.3.1.*`, Geist `…21239.5.1.*`, NetBotz `…318.1.1.10.4.2.2.1.*` | SNMP walk |
| Cooling (CRAH/CDU/chiller) | **BACnet** / **Modbus** | analog input objects (return/supply temp, valve %) | BACnet Who-Is |

Acquisition pattern (per poll cycle, §12):

1. For each managed device, poll its protocol for the metric value(s) (§3.1).
2. Resolve the device → rack using the device-side location hint (Redfish
   `Location.Placement` / SNMP `sysLocation`) **or** the DCIM's own asset DB.
3. Look up the rack's floor (x, y) + aisle in the **asset DB** (§3.2 — never
   polled). In this simulator that DB is the topology JSON `floorplan` (§4).
4. Run the pipeline (§7).

This is the surface a DCIM developer implements. The SNMP simulator, Redfish
simulator, and gNMI simulator must be running for the corresponding polls to
answer (start them from the simulator UI).

### 5.2 Simulator's bundled web UI — internal REST (only if extending it)

The simulator ships a FastAPI backend for its **own** web UI. If you are adding
a heatmap *inside that web UI* (not building an external DCIM), use these instead
of polling protocols:

| Endpoint | Returns | Use |
|---|---|---|
| `GET /devices` | `DevicesResponse` = list of `DeviceInfo` | all telemetry + placement, one snapshot |
| `GET /devices/{id}` | `DeviceInfo` | single device |
| `GET /topology/graph` | devices + links + **canvas** positions | NOT floor coords — ignore for heatmap |
| `GET /events` (SSE) | log/progress/sync stream | optional refresh trigger |

> `GET /topology/graph` `position` is the **logical canvas** coordinate
> (topology editor), **not** the physical floor. Never use it for the heatmap.

For this internal path, the placement fields must be added to the REST schema
(they exist on `Device` but are not serialized). Add to `DeviceInfo`
(`api/models/schemas.py`):

```python
floor_x: Optional[float] = None      # rack center X, room-local meters
floor_y: Optional[float] = None      # rack center Y, room-local meters
cold_aisle: Optional[str] = None     # aisle id at rack front, e.g. "CA1"
hot_aisle: Optional[str] = None      # aisle id at rack rear, e.g. "HA1"
```

Map them in `_device_to_info()` (`api/routers/devices.py`):

```python
floor_x=getattr(device, "floor_x", None),
floor_y=getattr(device, "floor_y", None),
cold_aisle=getattr(device, "cold_aisle", None) or None,
hot_aisle=getattr(device, "hot_aisle", None) or None,
```

Also expose room geometry (rack/row pitch, room extent, aisle centerlines,
containment) for drawing the floor background. The topology JSON carries a
top-level `floorplan` block, but the app's save path (`TopologyEngine.to_dict`)
emits only `nodes`/`edges` and **drops** it. Either add `GET /topology/floorplan`
that re-derives it server-side (same math as `tools/add_floorplan.py`, so it
survives save/load), or have the client reconstruct room extent from the min/max
`floor_x`/`floor_y` of devices in the room. The asset-DB placement (§3.2) is the
same data either way — only the transport differs.

---

## 6. Internal Data Model

Below, **`DeviceRecord`** denotes the DCIM's normalized per-device object — the
result of a protocol poll (§5.1) joined to asset-DB placement (§3.2). When using
the simulator's internal web-UI path (§5.2) it maps 1:1 to a REST `DeviceInfo`.
The pipeline does not care which transport produced it.

```
Rack {
  id            : string         // `${dc}:${room}:R${row}:RACK${num}`
  dc, room, floor
  row, num
  x, y          : meters         // floor_x, floor_y (rack center, from asset DB)
  coldAisle, hotAisle : string
  devices       : DeviceRecord[] // all devices at this (row,num)
}

SamplePoint { x, y : meters; value : number }   // one per rack with a reading

HeatmapGrid {
  room          : string
  metric        : string         // "inlet_temp" | "outlet_temp" | "power_kw" | ...
  units         : string
  timestamp     : ISO-8601
  cellSize      : meters         // grid pitch, default 0.6
  cols, rows    : int
  values        : number[rows][cols]   // interpolated field, null where undefined
  samples       : SamplePoint[]        // raw per-rack values (overlay dots)
  min, max      : number
  thresholds    : { warn, crit, ... }  // color scale (see §7.4)
}
```

---

## 7. Algorithms

### 7.1 Per-rack aggregation

Group `DeviceInfo` by `(datacenter, room, rack_row, rack_num)`. For each rack,
reduce its devices' metric to one representative value. **Aggregation is
metric-specific** — do not hard-code one rule.

| Heatmap | Metric field | Aggregation | Rationale |
|---|---|---|---|
| H1 Inlet | `inlet_temp` | **max** over servers in rack | worst-case hotspot drives risk; ASHRAE limit is per-inlet |
| H2 Exhaust | `outlet_temp` | **max** over servers | hottest exhaust = peak heat rejection |
| H3 Humidity | `humidity` | **mean** over rack/zone sensors | RH is a zone property |
| H4 Power | `pdu_real_power` (W) | **sum** of PDUs feeding the rack | metered, authoritative |
| H5 Capacity | `rack_unit` | **count** of occupied U / rack height | space utilization |

Rules:

- **Exclude devices with no reading** (`null`/`0` for a live-only metric). A
  rack with zero sensored devices yields **no SamplePoint** — let interpolation
  fill it (§7.3). Never substitute `0`.
- For H1, prefer the 3-height inlet profile if available (§7.5); otherwise use
  the single `inlet_temp`.
- For H4, if PDU metering is unavailable for a rack, fall back to
  `Σ power_watts` of servers in the rack and **flag the cell as estimated**.

### 7.2 Coordinate mapping

Each rack's `(x, y)` comes directly from `floor_x`/`floor_y`. If those are
absent (API not yet patched), derive from the logical grid using the floorplan
constants:

```
x = (rack_num - 1) * RACK_PITCH + RACK_W / 2      # RACK_PITCH=0.6, RACK_W=0.6
y = (rack_row - 1) * ROW_PITCH  + RACK_D / 2      # ROW_PITCH=2.4,  RACK_D=1.2
```

Build the grid per **room** (never span rooms — walls break the thermal field).
Grid extent = room `width_m × depth_m`; cell size default 0.6 m (one tile).

### 7.3 Spatial interpolation — Inverse Distance Weighting (IDW)

Racks are discrete samples; the heatmap is a continuous field. Use **IDW** — the
method used by commercial DCIM (Sunbird, Nlyte). For each grid cell center
`(gx, gy)`:

```
value(gx, gy) = Σ_i ( w_i * v_i ) / Σ_i w_i
where  w_i = 1 / max(d_i, ε)^p
       d_i = euclidean distance from cell to sample i  (meters)
       p   = power parameter (default 2)
       ε   = 0.1 m  (avoids divide-by-zero at a sample point)
```

Parameters:

- **p = 2** (standard). Higher p → more "blocky," each cell dominated by nearest
  rack; lower p → smoother/flatter. Make configurable.
- **Search radius:** limit to samples within `R = 3 × row_pitch` (~7.2 m). Beyond
  that, a sample has negligible influence and including all samples blurs local
  hotspots. Cells with **no** sample inside `R` → `null` (rendered transparent).
- **Anisotropy (optional, advanced):** thermal gradients are steeper across
  aisles (Y) than along a row (X). Optionally scale the Y-distance by 1.5 before
  weighting so heat doesn't "bleed" across a cold aisle into the hot aisle.

**Per-cell fallback:** if IDW is overkill (e.g. a coarse rack-level view), color
each rack's own cell directly with its aggregated value and leave gaps
uncolored. openDCIM uses this simpler approach.

**Out of scope:** kriging (marginal gain over IDW) and CFD (a physics simulation
— separate product class, e.g. 6SigmaDCX; not a poll-based heatmap).

### 7.4 Color mapping (thresholds)

Map a scalar to a color via configurable bands. Defaults below.

**Thermal — inlet (H1), per ASHRAE TC 9.9 (2021), Class A1–A2:**

| Band | Inlet °C | Color | Meaning |
|---|---|---|---|
| Cold | ≤ 18 | blue | overcooled — wasted cooling energy |
| Recommended | 18 – 27 | green | ASHRAE recommended envelope |
| Allowable | 27 – 32 | yellow | A1/A2 allowable upper |
| Marginal | 32 – 35 | orange | A3 territory |
| Breach | > 35 | red | exceeds allowable |

**Thermal — exhaust (H2):** shift bands up (~+12 °C ΔT): warn 42 °C, crit 52 °C.

**Power density (H4), per-rack kW:**

| Band | kW/rack | Color |
|---|---|---|
| Low | < 5 | blue/green |
| Normal | 5 – 10 | green |
| High | 10 – 15 | yellow |
| Critical | > 15 | red |

**Humidity (H3):** green 40–60 %RH; yellow 30–40 / 60–70; red <30 (ESD) or >70
(condensation). Also compute dew-point margin (inlet temp − dew point); flag if
< 3 °C.

Return the active thresholds in the `HeatmapGrid.thresholds` payload so the
renderer and legend stay in sync. Make all bands user-configurable.

### 7.5 Vertical dimension (3-height inlet)

A floor heatmap is 2-D, but ASHRAE specifies measuring inlet temperature at
**three rack heights** (top / middle / bottom) because the top of a rack runs
hottest (recirculation). The simulator models this on the Raritan DPX2-T3H1
sensor via `inlet_temp` (bottom), `mid_temp` (middle), `outlet_temp` (top).

Implementation:

- Default H1 uses **bottom/`inlet_temp`** or the **max of the three** (worst
  case) — pick one and document it in the UI.
- Optionally expose a **height selector** (top/mid/bottom) that swaps the source
  field. Treat each height as an independent 2-D field.

> **Semantics to lock:** on the T3H1 sensor, `inlet_temp`/`mid_temp`/`outlet_temp`
> represent **vertical heights** (bottom/mid/top of the cold-aisle inlet). On a
> **server**, `inlet_temp` = intake and `outlet_temp` = chassis exhaust (front
> vs rear). The heatmap must branch on `device_type` when choosing the field.

---

## 8. Per-Heatmap Recipes

Each recipe = (filter, metric field, aggregation, color scale).

```
H1 Inlet thermal
  filter:  device_type in {server, sensor}, room = R
  field:   inlet_temp   (or max(inlet,mid,outlet) for sensors; height selector)
  agg:     max per rack
  scale:   ASHRAE inlet

H2 Exhaust thermal
  filter:  device_type = server, room = R
  field:   outlet_temp
  agg:     max per rack
  scale:   exhaust (warn 42, crit 52)

H3 Humidity
  filter:  device_type = sensor, room = R
  field:   humidity (+ dewpoint for margin)
  agg:     mean per rack/zone
  scale:   humidity band

H4 Power density
  filter:  device_type = pdu, room = R     (fallback: server power_watts)
  field:   pdu_real_power  → kW
  agg:     sum per rack
  scale:   kW band

H5 Capacity
  filter:  all rack-mounted devices, room = R
  field:   rack_unit
  agg:     occupied-U count / rack height (default 42U)
  scale:   utilization %
```

---

## 9. Real-World Practices & Standards

Implement to these so the feature matches production DCIM behavior.

1. **ASHRAE TC 9.9 Thermal Guidelines (2021), Classes A1–A4.** Authoritative
   source for inlet temperature/humidity envelopes. Recommended 18–27 °C;
   allowable extends to 32–35 °C (A3). Measure at the **rack inlet**, at 3
   heights. RH recommended 8 °C DP to 60% RH / 15 °C DP. This drives the H1/H3
   color bands.

2. **Inlet, not outlet, is the controlled variable.** Cooling is regulated to
   keep **intake** within envelope. Exhaust temp is informational (heat
   rejection / containment effectiveness), not a compliance limit.

3. **Sampling, not exhaustive instrumentation.** Real halls do not put a
   3-probe sensor in every rack. Per-rack inlet is read from the **intelligent
   PDU's onboard sensor** (every rack has A+B PDUs) plus a few **reference
   rack/aisle sensors** (typically one per row). The DCIM interpolates between
   them — which is exactly why IDW exists. The simulator mirrors this: ~1
   reference DPX2-T3H1 per row, with PDUs carrying per-rack inlet.

4. **Hot/cold-aisle containment.** Heatmaps are only meaningful when aisle
   orientation is known. Cold-aisle map (intake) and hot-aisle map (exhaust) are
   separate views. The `cold_aisle`/`hot_aisle`/`rack_facing` fields encode this.

5. **Server ΔT.** Across-server rise is typically 10–20 °C; fans regulate to keep
   it roughly constant. The simulator models exhaust as `inlet + ΔT`, ΔT ≈
   1.76·P/CFM (with airflow tracking power), landing ~10–14 °C. Use this when
   reasoning about H2.

6. **Power density.** Modern enterprise racks run 5–15 kW; HPC/AI racks 30–80+
   kW (liquid-cooled). Per-rack kW must come from **metered PDUs**, never summed
   nameplate (which overstates by 30–50%). W/ft² (or W/m²) = rack kW / footprint
   for the density view.

7. **Protocols by device class** (what a real DCIM polls):
   - Servers → **Redfish** (`/redfish/v1/Chassis/{id}/Thermal`, `…/Power`),
     IPMI, or `node_exporter`.
   - Network → **SNMP** ENTITY-SENSOR-MIB (`entPhySensorValue`), vendor MIBs
     (CISCO-ENVMON, `jnxOperatingTemp`), or **gNMI/OpenConfig**
     (`/components/component/state/temperature/instant`).
   - PDU/UPS → **SNMP** / **Modbus**.
   - Environmental sensors → **SNMP** / **Modbus** / **BACnet**.
   - Cooling (CRAH/CDU/chiller) → **BACnet** / **Modbus**.

8. **Device-side location hint.** Redfish `Chassis.Location.Placement`
   (`RackName`, `Row`, `RackOffset`, `RackOffsetUnits: EIA_310`) lets a DCIM bind
   a server reading to a rack/RU **without** an external asset import. The
   simulator exposes this. Precise floor (x, y), however, remains DCIM-DB data.

9. **Commercial reference implementations:** Sunbird dcTrack, Nlyte, Vertiv
   Trellis, openDCIM (open source) — all per-rack value + IDW-style floor
   interpolation, ASHRAE color bands, hot/cold-aisle aware.

---

## 10. Output Payload (renderer contract)

`buildHeatmap(devices, floorplan, {room, metric, height?})` → `HeatmapGrid`
(§6). Example:

```json
{
  "room": "DC1/Server Hall A",
  "metric": "inlet_temp",
  "units": "°C",
  "timestamp": "2026-06-18T12:00:00Z",
  "cellSize": 0.6,
  "cols": 14, "rows": 16,
  "values": [[22.1, 22.4, null, ...], ...],
  "samples": [
    {"x": 0.3, "y": 0.6, "value": 23.4},
    {"x": 0.3, "y": 3.0, "value": 26.2}
  ],
  "min": 21.8, "max": 34.9,
  "thresholds": {"cold": 18, "ok": 27, "warn": 32, "crit": 35}
}
```

Renderer: draw room background + rack rectangles (from floorplan/floor_x,y),
overlay the `values` grid as a colored raster (bilinear-smoothed in the canvas),
plot `samples` as dots, and draw aisle centerlines. Provide a legend bound to
`thresholds`.

---

## 11. Edge Cases & Data-Quality Rules

| Case | Handling |
|---|---|
| Rack with no sensored device | no SamplePoint; IDW fills; if no sample within R → `null` cell (transparent) |
| Live-only metric reads 0 (sim stopped) | treat as **no data**, not a measurement; exclude from samples |
| Network device queried for exhaust | none exists — exclude; H2 is servers-only |
| PDU metering missing for H4 | fall back to Σ `power_watts`; mark cell `estimated: true` |
| Powered-off server (`power_state = Off`) | OS metrics dashed, but **temps stay live** (BMC on standby) — keep inlet/exhaust |
| Facility room (UPS/generator) | has floor_x,y but no aisle; render coordinates, skip aisle-based logic |
| Mixed device types in a rack | aggregate only the field-relevant types (e.g. servers for exhaust) |
| Sensor vs server field semantics | branch on `device_type` (see §7.5) |

---

## 12. Performance & Refresh

- Poll on a **2–5 s** interval for a live floor view. External DCIM: poll device
  protocols (§5.1) — batch SNMP gets, reuse Redfish sessions, use gNMI
  subscriptions (streaming) where available to avoid re-polling. Web-UI path:
  one `GET /devices` snapshot per cycle.
- Per-room grids are tiny (≤ ~14 × ~16 cells); IDW over a few dozen samples is
  sub-millisecond. Build all room grids per poll.
- Keep the pipeline **pure/deterministic** (no RNG in the export path); identical
  input → identical grid.
- Optionally trigger refresh from the `GET /events` SSE `tick` event instead of
  fixed polling.

---

## 13. Implementation Checklist

**External DCIM (primary):**
1. **Pollers:** per device class, poll the protocol + path in §3.1/§5.1 (Redfish
   Thermal/Power, SNMP ENTITY-SENSOR-MIB + PDU/sensor OIDs, gNMI temperature).
2. **Binding:** resolve device → rack via Redfish `Location.Placement` /
   SNMP `sysLocation`, or your asset DB.
3. **Asset DB:** import floor coords + aisle from the topology `floorplan` (§4).
   Floor (x, y) is never polled (§3.2).

**Simulator web-UI path (only if extending the bundled UI), instead of 1–2:**
1b. Add `floor_x`/`floor_y`/`cold_aisle`/`hot_aisle` to REST `DeviceInfo` +
    `_device_to_info()`, and (recommended) `GET /topology/floorplan` (§5.2).

**Common (both paths):**
4. **Model:** `Rack`, `SamplePoint`, `HeatmapGrid` (§6).
5. **Pipeline:** group→aggregate (§7.1), map coords (§7.2), IDW (§7.3), color
   (§7.4).
6. **Recipes:** H1 first, then H2/H3/H4/H5 (§8).
7. **Renderer:** floor background + raster + sample dots + aisle lines + legend
   (§10).
8. **Controls:** room/metric/inlet-height selectors, threshold config.
9. **Edge cases:** apply §11 rules.

Ship H1 (inlet thermal) end-to-end first; the remaining heatmaps are the same
pipeline with a different (filter, field, aggregation, scale) tuple.

---

## Appendix A — Simulator Field Reference

Backing `Device` (`core/device_manager.py`); the internal web-UI REST mirror is
`DeviceInfo` (`api/models/schemas.py`). An external DCIM reads these values over
the protocols in §3.1, not these field names.

- Thermal: `cpu_temp`, `inlet_temp`, `mid_temp`, `outlet_temp` (°C)
- Environmental: `humidity` (%RH), `dewpoint` (°C), `airflow` (m/s)
- Power: `power_draw_w` (nominal W), `power_watts` (live W, server),
  `pdu_real_power` / `pdu_outlet_power` (W), `pdu_load` (A)
- Placement: `datacenter`, `room`, `floor`, `rack_row`, `rack_num`, `rack_unit`,
  `floor_x`, `floor_y`, `cold_aisle`, `hot_aisle`, `rack_facing`
- Floorplan block: `floorplan.{units, rack_footprint, rack_pitch, row_pitch,
  aisle_width, rooms[<dc>/<room>].{width_m, depth_m, rows, aisles[]}}`

## Appendix B — Key Formulas

```
IDW:            v(g) = Σ(v_i / d_i^p) / Σ(1 / d_i^p),   p=2, d clamped ≥ 0.1 m
Rack coords:    x = (num-1)*0.6 + 0.3 ;  y = (row-1)*2.4 + 0.6   (meters)
Server ΔT:      ΔT(°C) ≈ 1.76 * P_W / CFM   (airflow ∝ power ⇒ ~10–14 °C)
Exhaust:        outlet_temp = inlet_temp + ΔT
Live power:     power_watts = power_draw_w * (0.55 + 0.45 * cpu_usage/100)
Rack kW:        Σ(pdu_real_power feeding rack) / 1000
Power density:  W per m² = rack_W / (0.6 * 1.2)
Dew-point margin: inlet_temp - dewpoint   (flag if < 3 °C)
```

## Appendix C — Standards & References

- ASHRAE TC 9.9, *Thermal Guidelines for Data Processing Environments*, 5th ed.
  (2021) — inlet temp/humidity envelopes, Classes A1–A4, 3-height measurement.
- DMTF Redfish — `Chassis.Thermal`, `Chassis.Power`, `Chassis.Location.Placement`.
- IETF RFC 3433 — ENTITY-SENSOR-MIB (`entPhySensorTable`).
- OpenConfig `openconfig-platform` — component temperature telemetry.
- Uptime Institute / ASHRAE — hot/cold-aisle containment, power-density guidance.
```
