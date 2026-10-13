# S3 — the compound, the fabric and the support rooms

Simulator side of dcim-platform `docs/27-digital-twin-plan.md` §8b and §8c.
Written 2026-10-08 against `669ff90`. The platform's one-viewer floor plan
(Phase 7) draws what this exports.

## What changed and why

**The gensets stand in a yard.** Standby generators of this size are outside
on nearly every enterprise and colocation site: weatherproof enclosures behind
a fence, bulk diesel, day tanks. The `Generator Room` is now the `Generator
Yard`, an **outdoor room** (`class: outdoor`, `level: "Site"`) placed 4 m east
of the building in the same plan frame. Its devices followed; their names did
not (`GEN1-DC1-GR` keeps the room code `GR`), because the platform keys device
identity on the name and a rename would churn every device at both sites.

**The chillers are on the ground.** Water-cooled chillers belong in a plant
room, and a plant room sits on the ground floor: a chiller is 8–15 t, vibrates,
and needs condenser-water risers and drainage. The `Central Plant` moved from
level 1 to G, beside the UPS and mechanical rooms. Every device in it now says
`floor: "G"`.

**Three support rooms** exist for the devices S3's next increment adds:
`Staging & Storage` (G), the `Operations Room` and the `Meet-Me Room` (level
1), all `class: support`. Empty for now.

**Fabric and compound are derived, not stored.** `core.equipment_geometry.
complete_site()` wraps `complete_building()` and adds, per datacenter:

- `fabric[dc]`: a 2.4 m corridor along the south side of every level's rooms,
  a 3 × 6 m stair-and-lift core at the east end of every level, and on the
  ground floor a lobby and a loading dock in the 3.6 m band beyond the
  corridor, with the entrance and the dock door. One door opening per room,
  on the wall that meets the corridor, else on the west external wall. Doors
  of the halls, the Network Room, the UPS Room and the Meet-Me Room are marked
  `monitored` (the BMS contact comes in the next increment).
- `compound[dc]`: the fenced site around the building — 18 m west (the utility
  substation), 22 m east (the generator yard and the bulk fuel tank), 14 m
  south (apron, service road, car park, gate, guardhouse), 10 m north (makeup
  water tank, fire pump house) — and two telecom entrance routes from opposite
  fence lines. Outdoor rooms appear as zones that carry `attrs.room`; the
  rest is drawing with no state.

The building's own outline grows by the 6 m service strip, so a 25 × 15 m
ground floor becomes a 25 × 21 m building.

## Export

`tools/export_dcim_floorplan.py` and `/api/topology/export` emit
**`dcim-floorplan/1.2`**: `floorplan.rooms` may carry `class` `support` or
`outdoor` and `level` `"Site"`; `floorplan.fabric` and `floorplan.compound`
are keyed by datacenter. 1.1 readers ignore both and keep working.

## Idempotent tooling

```
python tools/layout_buildings.py topologies/dual_dc_enterprise.json          # apply
python tools/layout_buildings.py topologies/dual_dc_enterprise.json --check  # overlaps
python tools/export_dcim_floorplan.py topologies/dual_dc_enterprise.json topologies/dual_dc_enterprise_floorplan.json
```

The tool renames `Generator Room` → `Generator Yard`, creates the support
rooms, sets every device's `floor` to its room's level, and places everything
absolutely. Run twice, it reports "already laid out".

## Switching it on

The running simulator does not pick this up until the topology is reloaded:
Stop → Regenerate Datasets → Start (the fingerprint changes), then the
platform's `python -m app.importer.cli --geometry-only`. That regeneration is
shared with S2 and waits for the RAM upgrade.

Platform side (docs/27 §8b.4/§8c.4, not yet built): migration 0102 must admit
`room_class` `support` and `outdoor`, add the `space` table for fabric and
zones, and the importer must drop an import-sourced room that no longer exists
in the source and holds nothing (the old `Generator Room` rows).

## Next increment

Devices that make the new spaces real, each because a real site monitors it:
door contacts and the UPS Room's hydrogen and battery temperature as Modbus
probe roles behind the BMS gateway; bulk-fuel and makeup-water level
transmitters; a `transformer` device type (Modbus from a protection relay or
temperature monitor) in the substation; the carriers' handoff routers in the
Meet-Me Room; one room probe in the Operations Room; the fleet lifecycle
holding `planned`/`stock` devices in Staging & Storage and opening a door when
it installs or decommissions in a room.

## Increment 2 — the instruments (2026-10-13)

What a real site wires to its BMS and never networks, on the same RS-485 trunk
as the thermowells, behind the same Moxa gateway, fitted by the idempotent
`tools/add_s3_instruments.py` (run after `tools/layout_buildings.py`):

| Name | Role | What it is | Registers |
|---|---|---|---|
| `DOOR-<DC>-<HA,HB,NR,UR,MM>` | `door_contact` | reed switch on the frame, a dry contact into the BMS | one discrete bit + validity |
| `H2-<DC>-UR` | `hydrogen` | catalytic/electrochemical cell in the battery room, % LEL; IEEE 1635 / NFPA 1 cap the room at 1 % by volume = 25 % LEL | input ×100, alarm relay, validity |
| `BTT-<DC>-UR` | `battery_temp` | probe on the A string; VRLA life halves per ~10 K above 25 °C | input ×10 °C |
| `FUEL-<DC>-GR` | `fuel_level` | level transmitter on the 40 000 L bulk diesel tank in the yard | % ×10, litres u32, low relay |
| `WTR-<DC>-CP` | `water_level` | level transmitter on the 20 000 L makeup water tank, read by the plant | same registers, its own template |
| `THR-<DC>-OR` | `room_air` | the operations room's T/RH transmitter (22 °C, people in it) | as the other rooms |

Behaviour (`core/device_state_store.py`): the string runs 2.5 K above the
battery room's air; hydrogen is a base plus 0.6 % LEL per K the string sits
above 27 °C, plus whatever a scenario adds; the bulk tank burns 500 L/h per
running set scaled by its load, orders a delivery below 30 % that lands 8 h
later at 95 %; the makeup tank is drawn down by the towers' `Makeup_Flow` and
refilled by the mains below its float valve. A door reads 1 while held open:
the fleet lifecycle opens the room's door for 20-200 s when it racks or pulls
a device there, and `POST /api/modbus/doors {device, seconds}` holds one for a
scenario; `POST /api/modbus/hydrogen {datacenter, pct_lel}` gasses a room.

The cold-aisle air rules stay off every one of them (`_POINT_ROLES`), and none
publishes a water or room-air key.

Platform side (dcim-platform `0103`): registry keys `door_open`,
`hydrogen_lel_pct`, `water_level_pct`, `water_volume`, `fuel_volume`;
`fuel_level_pct` and `battery_temperature` admit sensors; templates generated
from these maps; a `security` alarm category; rules at the standards' numbers
(hydrogen 10/25 % LEL, string 30/35 °C, fuel 30/15 %, water 40/20 %, door held
open 120 s).

Not fitted, and why: a `transformer` device type in the substation needs a
Modbus map from a protection relay and a vendor catalogue entry - next. The
Meet-Me Room holds the carriers' own equipment, which a tenant's DCIM does not
poll; its door contact is what we see, which is realistic. Staging & Storage
holds no simulated stock yet; the lifecycle reserves a slot at the rack, as a
DCIM does.

## Increment 2b — the substation (2026-10-13)

Every site of this size takes its supply at medium voltage and steps it down
on its own compound. `tools/add_substation.py` (idempotent, repairs) gives each
datacenter a `Substation` outdoor room on the west margin - where
`complete_site()` already drew the utility substation; the room now IS that
zone - and in it `TX1-<DC>-SB`, a `transformer`: Schneider Minera 2000 kVA
ONAN/ONAF, 11/0.415 kV, 2.4 × 1.6 × 2.2 m on its plinth.

The transformer has no electronics. What answers is the monitor on its tank
(`SIM-XFMR-ITM-v1`, Qualitrol 509 ITM class) on Modbus TCP at the next
BMS-plane address after the revenue meter, with a management port on the
plant's BMS access switch and a POWER edge into the revenue meter: MV in, LV
out, meter on the secondary (`_POWER_RANK` -1, above `utility_feed`). No SNMP
(`_NO_SNMP_TYPES`).

| Register | What | Model |
|---|---|---|
| `Top_Oil_Temperature` ×10 °C | tank RTD | IEC 60076-7 exponential model, ONAN distribution constants (55 K rise at rated, R = 5, x = 0.8), 180 min time constant |
| `Winding_Temperature` ×10 °C | thermal image | top oil + 23 K × K^1.6, 4 min time constant |
| `Load_Percent` ×10, `Current_A/B/C` A | load CTs | what flows to the meter below, over the nameplate; I = S / (√3 · 415 V) |
| `Tap_Position` | off-circuit tap | 3 of 5, moves only by override `xfmr_tap` |
| `Fans_Running` | ONAF stage | on at 65 °C top oil, off below 55 |
| `Oil_Temp_Alarm`, `Winding_Temp_Alarm` | the monitor's relays | 85 °C / 105 °C, vendor defaults |
| `Gas_Relay_Alarm` | Buchholz | only from override `xfmr_gas_alarm`: there is no slow path to it |
| `Data_Valid` | | |

Ambient is the site's own outdoor air (`_plant_oa_by_name`): a transformer in
a yard is cooled by the weather and nothing else, so the same summer afternoon
that lifts the condenser water lifts the oil. `core.device_state_store.
transformer_thermal_step` is pure; `tests/test_transformer.py` runs a day
through it.

Platform side (dcim-platform `0104`): the `transformer` device type, keys
`oil_temperature`, `winding_temperature`, `tap_position` (`current` and
`load_pct` admit the type), rules oil 85/95 °C and winding 105/115 °C, the
estate's load rule learning the type.

Switching on: regenerate as for increment 2 (the topology gained a room and
two devices; the Modbus plane binds two new addresses), then the full
importer.
