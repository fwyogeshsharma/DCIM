# Fire detection and gas suppression

What a data centre's life-safety systems look like **from the BMS**, which is
the only place a DCIM ever sees them. Built 2026-10-13.

## How it works on a real site

A fire system is its own system. The fire alarm control panel (FACP), its
detectors and the gas-release panels are listed, wired and powered separately
(NFPA 72, EN 54) and answer to the fire authority, not to operations. Two
things follow, and the simulator keeps both.

1. **The BMS monitors; it never commands.** What crosses over is a one-way
   feed. Nothing in these maps is writable, and a test holds that.
2. **Three states, everywhere.** Every panel reduces to *alarm* (there is a
   fire condition), *trouble* (the system cannot vouch for itself - a broken
   loop, a blocked pipe) and *supervisory* (something that protects is off
   normal - a low cylinder, a held abort). Sites that buy nothing else wire
   those three relays into the BMS.

| Device | What it is | How a BMS reads it | Vendors |
|---|---|---|---|
| Fire alarm control panel | Addressable panel, hundreds of points grouped in zones | The maker's Modbus or BACnet gateway, or three dry contacts | Notifier NFS2-3030 + MODBUS-GW / BACNET-GW-3, Simplex 4100ES + BACpac, Siemens Cerberus PRO / Desigo Fire, Bosch FPA-5000, Edwards EST4 |
| Aspirating smoke detector | Draws room air down a sampling pipe to a laser chamber; reads % obscuration per metre, two orders of magnitude more sensitive than a spot detector | High-level interface to Modbus, or four relays | Xtralis VESDA VLP/VEP (Honeywell), Wagner Titanus, Siemens FDA |
| Clean-agent release panel | Cross-zoned detection, pre-discharge countdown, abort switch, cylinder supervision | Relays, or a gateway through the FACP | Kidde ARIES, Fike SHP PRO, Siemens XC10 |

Common practice: an aspirating detector in every data hall (very early
warning is the reason to have one), gas suppression in halls - FK-5-1-12
(Novec 1230), FM-200 or inert gas, to NFPA 2001 / EN 15004 - and often in UPS
and battery rooms too. Vendor-specific: the gateway's register layout; every
maker's is different, and the maps here are a generic stand-in. Rare:
BMS-visible per-detector analogue values from the FACP loop.

## What the simulator models

Ten instruments, on the BMS trunk behind each site's Moxa gateway, fitted by
`tools/add_s3_instruments.py` (idempotent):

| Name | Role | Map | Where |
|---|---|---|---|
| `FACP-<DC>-OR` | `fire_panel` | `SIM-FACP-GW-v1` | Operations Room, in sight of the desk |
| `ASD-<DC>-HA`, `-HB` | `smoke_aspirating` | `SIM-ASD-HLI-v1` | each hall's north wall |
| `GSP-<DC>-HA`, `-HB` | `gas_release` | `SIM-GASREL-v1` | by each hall's door |

Registers (all read-only: input registers and discrete inputs):

| Map | Register | Meaning |
|---|---|---|
| FACP | `Fire_Alarm`, `Trouble`, `Supervisory` | the three states |
| FACP | `Zones_In_Alarm` u16 | bit per room: HA 0, HB 1, NR 2, UR 3, MM 4, OR 5 |
| FACP | `Zone_Alarm_Count` | zones at Fire 1 or above |
| ASD | `Smoke_Obscuration` ×1000 %/m | chamber reading |
| ASD | `Alarm_Stage` | 0 normal, 1 Alert, 2 Action, 3 Fire 1, 4 Fire 2 |
| ASD | `Airflow_Fault` | pipe network blocked or broken |
| GSP | `Release_State` | 0 normal, 1 first alarm, 2 pre-discharge, 3 released, 4 abort held |
| GSP | `Discharge_Countdown` s | counts from 30 |
| GSP | `Agent_Pressure` ×10 bar | 25.0 charged, ~0.6 discharged |
| GSP | `Agent_Released`, `Abort_Active`, `Agent_Low_Pressure` | |

Behaviour (`core/device_state_store.py`, pure steps `asd_stage`, `asd_step`,
`gas_release_step`):

- **Detector.** A clean hall reads about 0.008 %/m. Smoke put in the room
  reaches the chamber over a 25 s time constant - the pipe's transport time;
  EN 54-20 allows a Class A system 120 s. Thresholds are VESDA VLP's factory
  defaults: Alert 0.08, Action 0.14, Fire 1 0.20, Fire 2 2.0 %/m.
- **Release panel.** Cross-zoned on the detector's two fire thresholds, the
  usual way a single aspirating detector is used for release. Fire 1 is the
  first alarm: sounders, nothing discharges. Fire 2 starts a 30 s
  pre-discharge delay. The abort switch is a dead-man: held, it parks the
  countdown at 10 s; let go, the countdown resumes.
- **Everything latches.** NFPA 72 has a control unit hold an alarm until it
  is reset by hand. Smoke thinning out stands nothing down: a first alarm
  stays one, a countdown that has started runs to discharge unless somebody
  is holding the abort, a release empties the cylinders and stays a release.
  Only `fire/reset` goes backwards.
- **Site panel.** Alarm from the moment any hall reaches Fire 1, latched
  until reset; trouble while any detector has an airflow fault; supervisory
  while any cylinder bank is low or any abort is held.
- **HVAC interlock.** From the start of the countdown the release panel's
  relay stops every air handler serving the room, so the agent is not blown
  out of the room it is discharged into, and they stay stopped - through the
  discharge and NFPA 2001's ten-minute hold - until the panel is reset. The
  CRAHs read `Unit_Running = 0` on the BACnet plane, which is what a BMS
  shows for a unit stopped by a fire relay, and the room then warms by the
  cooling model's own rules for lost air handlers: the IT load does not stop.
  It rides the autonomous-protection channel (the one a chiller's
  head-pressure trip uses), never the operator's override map. Fire 1 alone
  stops nothing: one detector does not cost a hall its cooling.

Scenarios (`api/routers/modbus.py`):

```
POST /api/modbus/fire/smoke  {"datacenter":"DC1","room":"HA","pct_per_m":0.25}   # Fire 1
POST /api/modbus/fire/smoke  {"datacenter":"DC1","room":"HA","pct_per_m":5.0}    # Fire 2 -> countdown -> release
POST /api/modbus/fire/smoke  {"datacenter":"DC1","room":"HA","airflow_fault":true}
POST /api/modbus/fire/abort  {"datacenter":"DC1","room":"HA","held":true}
POST /api/modbus/fire/reset  {"datacenter":"DC1"}
```

## What it does not do, and why that matters

- **Design-dependent: when the air stops.** Here it stops with the
  countdown. Some designs stop it at the first alarm, and a duct detector on
  an air handler stops that unit by itself; neither is modelled.
- **No dampers, no agent concentration.** The room's dampers are not
  devices, and nothing computes whether the agent held its concentration -
  a door propped open during a discharge changes nothing here, and on a real
  site it is the difference between a fire put out and one that is not.
- **The interlock needs the BACnet plane running.** Like every plant fault
  in this simulator, a stopped unit takes effect through the published plant
  state; with BACnet stopped the relay is set and nothing reads it.
- **No emergency power-off.** Automatic EPO on agent release is rare now and
  not modelled.
- **One detector per hall.** Real halls have several, plus under-floor and
  return-air sampling and spot detectors on the FACP loop. One per hall is
  what a BMS summary shows.
- **Reset is a button.** A discharged bank is a contractor's visit and days
  of lead time; `fire/reset` refills it at once.
- **No fire system in the UPS, plant or support rooms.** Zone bits are
  reserved for them; nothing drives them yet.
- **Smoke is put there by a scenario.** Nothing in the simulator starts a
  fire by itself, deliberately: a random gas discharge in a demo estate is
  noise, not realism.

## Switching on

The topology gained ten devices, so its fingerprint changed:
Stop -> upload the topology -> Regenerate Datasets -> Start. No new management
addresses (the instruments have none), so no re-bind. Then the platform's full
import, once it has templates for the three maps.

## Platform side (owed)

Registry keys (`fire_alarm`, `fire_trouble`, `fire_supervisory`,
`smoke_obscuration`, `smoke_stage`, `gas_release_state`,
`agent_pressure`, ...), three role-selected templates generated from these
maps, a `fire` alarm category, rules (Alert/Action as warnings, Fire 1 and
release as critical, trouble and supervisory as major), and the room viewer's
fire points reading them instead of being drawn by rule.
