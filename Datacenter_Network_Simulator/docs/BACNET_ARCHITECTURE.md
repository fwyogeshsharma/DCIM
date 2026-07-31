# BACnet/IP Simulator — Architecture

> Simulates Verdigris EV2 energy-monitoring panels over BACnet/IP.  
> Zero external BACnet libraries — pure Python UDP socket + hand-coded codec.

---

## Table of Contents

1. [Overview](#overview)
2. [File Map](#file-map)
3. [Layer 1 — Protocol Codec](#layer-1--protocol-codec)
4. [Layer 2 — Object Model](#layer-2--object-model)
5. [Layer 3 — EV2 Object Tree](#layer-3--ev2-object-tree)
6. [Layer 4 — Telemetry Engine](#layer-4--telemetry-engine)
7. [Layer 5 — Device Simulator](#layer-5--device-simulator)
8. [Layer 6 — Controller](#layer-6--controller)
9. [Socket Architecture & Routing](#socket-architecture--routing)
10. [Tick / Data Flow](#tick--data-flow)
11. [AppState & Headless Mode Integration](#appstate--headless-mode-integration)
12. [Supported BACnet Services](#supported-bacnet-services)
13. [Object Instance Numbering](#object-instance-numbering)
14. [UI Integration](#ui-integration)
15. [REST API](#rest-api)
16. [Test Clients](#test-clients)
17. [Known Limitations](#known-limitations)

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       BACnet/IP Simulator Stack                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Web UI (React)                Desktop UI (Qt)                          │
│  webui/.../BACnetPanel.tsx     ui/bacnet_panel.py                       │
│                 │                         │                             │
│                 └─────────┬───────────────┘                             │
│                           ▼                                             │
│  api/routers/bacnet.py    REST API  (/api/bacnet/*)                     │
│  api/state.py             AppState.bacnet  ←── shared singleton         │
│  ─────────────────────────────────────────────────────────────────────  │
│  simulator/bacnet_controller.py   BACnetController (lifecycle)          │
│  simulator/bacnet_device.py       EV2BACnetDevice  (per device)         │
│  ─────────────────────────────────────────────────────────────────────  │
│  core/bacnet_telemetry.py         EV2TelemetryEngine (physics sim)      │
│  core/bacnet_ev2_generator.py     build_ev2_object_tree()               │
│  core/bacnet_object_model.py      BACnetObject + codec helpers          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## File Map

| File | Purpose |
|------|---------|
| `core/bacnet_object_model.py` | All BACnet constants, `BACnetObject` dataclass, BVLL/NPDU/APDU encode + decode |
| `core/bacnet_ev2_generator.py` | Builds the complete object tree for one EV2 device |
| `core/bacnet_telemetry.py` | Physics-based random-walk electrical simulation |
| `simulator/bacnet_device.py` | One UDP socket + BACnet PDU handler per simulated IP |
| `simulator/bacnet_controller.py` | Start/stop lifecycle, recv thread, tick dispatch |
| `api/routers/bacnet.py` | REST endpoints — start, stop, status, ev2/metrics |
| `api/state.py` | `AppState.bacnet` — shared singleton registered at startup |
| `ui/bacnet_panel.py` | Qt configuration panel (desktop app) |
| `webui/src/components/RightPanel/BACnetPanel.tsx` | React configuration panel (Web UI) |
| `testscripts/test_bacnet.py` | Automated test client: Who-Is → ReadProperty poller |
| `testscripts/test_bacnet_interactive.py` | Interactive discovery and property browser |

---

## Layer 1 — Protocol Codec

**File:** `core/bacnet_object_model.py`

Hand-coded BACnet/IP wire format. Every packet follows the three-layer structure:

```
UDP datagram
  ┌───────────────────────────────────────┐
  │  BVLL  (4 bytes)                      │  BACnet Virtual Link Layer
  │    type=0x81  func  length(2B)        │
  ├───────────────────────────────────────┤
  │  NPDU  (2–3 bytes)                    │  Network PDU
  │    version=1  control  [hop-count]    │
  ├───────────────────────────────────────┤
  │  APDU  (variable)                     │  Application PDU
  │    PDU-type(4b) | flags(4b)           │
  │    invoke-id  service-choice          │
  │    context/application-tagged data …  │
  └───────────────────────────────────────┘
```

### BVLC Function Codes

| Constant | Value | Meaning |
|----------|-------|---------|
| `BVLC_ORIGINAL_UNICAST` | `0x0A` | Unicast to specific device |
| `BVLC_ORIGINAL_BROADCAST` | `0x0B` | LAN broadcast (Who-Is) |
| `BVLC_FORWARDED_NPDU` | `0x04` | BBMD forwarded packet |

### PDU Types

| Constant | Value | Used for |
|----------|-------|---------|
| `PDU_CONFIRMED_REQUEST` | `0` | ReadProperty, RPM, SubscribeCOV |
| `PDU_UNCONFIRMED_REQUEST` | `1` | Who-Is, I-Am, COV Notification |
| `PDU_SIMPLE_ACK` | `2` | SubscribeCOV acknowledgement |
| `PDU_COMPLEX_ACK` | `3` | ReadProperty / RPM response |
| `PDU_ERROR` | `5` | Unknown object/property |
| `PDU_REJECT` | `6` | Malformed request |
| `PDU_ABORT` | `7` | Internal error |

### BACnet Tag Encoding

BACnet uses a TLV scheme where the first byte encodes tag number, class, and length:

```
 bits 7–4   tag number (0–14; 15 = extended tag)
 bit  3     class: 0=application  1=context
 bits 2–0   length (0–4 bytes) or special:
              5 = extended length (next byte)
              6 = opening tag  (context only)
              7 = closing tag  (context only)
```

Key encoder functions:

| Function | Produces |
|----------|---------|
| `enc_app_real(f)` | App-tagged IEEE 754 float (0x44) |
| `enc_app_uint(n)` | App-tagged unsigned int |
| `enc_app_enum(n)` | App-tagged enumeration |
| `enc_app_bool(b)` | App-tagged boolean |
| `enc_app_charstr(s)` | App-tagged UTF-8 string |
| `enc_app_oid(t, i)` | App-tagged object identifier |
| `enc_ctx_oid(tag, t, i)` | Context-tagged OID |
| `enc_ctx_uint(tag, n)` | Context-tagged uint |
| `enc_ctx_open(tag)` | Context opening tag |
| `enc_ctx_close(tag)` | Context closing tag |

---

## Layer 2 — Object Model

**File:** `core/bacnet_object_model.py` — `BACnetObject` dataclass

Every simulated point is a `BACnetObject`:

```python
@dataclass
class BACnetObject:
    object_type:   int          # OBJ_ANALOG_INPUT=0, OBJ_BINARY_INPUT=3, OBJ_DEVICE=8
    instance:      int          # unique within device
    name:          str          # e.g. "Panel_Total_kW"
    description:   str
    units:         int          # UNIT_KILOWATTS=48, UNIT_AMPERES=3, …
    present_value: float = 0.0  # updated every tick
    status_flags:  list  = [0,0,0,0]
    reliability:   int   = 0    # 0=NO_FAULT, 12=COMM_FAILURE
    out_of_service: bool = False
    cov_increment: float = 0.5  # COV threshold
    min_cov_interval_sec: float = 60.0  # minimum notification interval
    _cov_last_sent: float = 0.0 # snapshot for threshold check
```

### Object Types Used

| Constant | Value | Purpose |
|----------|-------|---------|
| `OBJ_ANALOG_INPUT` | `0` | All electrical measurements |
| `OBJ_BINARY_INPUT` | `3` | Alarm states (active/normal) |
| `OBJ_DEVICE` | `8` | Device-level metadata |

---

## Layer 3 — EV2 Object Tree

**File:** `core/bacnet_ev2_generator.py`

`build_ev2_object_tree(device_instance, device_name, circuits)` — called once per device at startup. Returns `{(obj_type, instance): BACnetObject}`.

### Instance Numbering

```
Fixed (identical on every device):
  1001      Panel_Total_kW         (AI, kW)
  1002      Panel_Total_kWh        (AI, kWh)
  1003–1005 Voltage_PhA/PhB/PhC    (AI, V)
  1006–1008 Current_PhA/PhB/PhC    (AI, A)
  1009      Line_Frequency         (AI, Hz)
  1010      Panel_PF               (AI, no-units)
  1011      Voltage_THD            (AI, %)
  1012      Current_THD            (AI, %)
  1013–1016 Harmonic_3/5/7/9       (AI, %)
  1020      Alarm_Overcurrent      (BI)
  1021      Alarm_VoltageImbalance (BI)
  1022      Alarm_HighTHD          (BI)
  1023      Alarm_PhaseLoss        (BI)
  1024      Alarm_SensorFault      (BI)

Per-circuit (N = circuit number 1-based):
  base = (N + 1) × 1000
  base+1  CktNN_Current    (AI, A)
  base+2  CktNN_kW         (AI, kW)
  base+3  CktNN_kWh        (AI, kWh)
  base+4  CktNN_PF         (AI, no-units)
  base+5  CktNN_THD        (AI, %)

Example:
  Ckt01 → instances 2001–2005
  Ckt02 → instances 3001–3005
  Ckt42 → instances 43001–43005
  Ckt84 → instances 85001–85005
```

### Object Counts by Circuit Size

| Circuits | Total Objects | AI | BI |
|----------|--------------|----|----|
| 24 | 141 | 136 | 5 |
| 42 | 231 | 226 | 5 |
| 84 | 441 | 436 | 5 |

### Active vs. Total Circuits

`BACnetController.start()` receives a `circuits_map` dict of `ip → (total_circuits, active_circuits)`. The object tree is built with `total_circuits` objects. Circuits beyond `active_circuits` are wired up to breakers found in the power graph; spare circuits emit zero values.

> **Note:** Instance numbers are **shared across all simulated devices**. Routing must be by socket (destination IP), not by object instance lookup. See [Socket Architecture](#socket-architecture--routing).

---

## Layer 4 — Telemetry Engine

**File:** `core/bacnet_telemetry.py`

`EV2TelemetryEngine` — one instance per device, stateful physics simulation.

```python
engine = EV2TelemetryEngine(circuits=42, frequency_hz=50.0, active_circuits=24)
values = engine.tick(dt_seconds)  # returns flat dict of all object values
```

### tick() steps (in order)

```
1. _diurnal()           → load multiplier 0.3–1.0 (sine curve, peaks 14:00)
2. _step_voltages()     → random-walk ±0.5 V/tick + 0.2% transient chance
                          drift coefficient 0.02 pulls toward 230 V nominal
3. _step_frequency()    → random-walk ±0.02 Hz, regulation pulls toward 50/60 Hz
4. _step_thd()          → current THD random-walk; 0.5% spike chance (7.5–12%)
                          harmonics h3/h5/h7/h9 scale proportionally with I_THD
5. _step_pf()           → slow drift ±0.005/tick, clamped 0.70–0.99
6. _step_panel_current()→ base 60 A/phase × diurnal multiplier + ±2 A noise
                          0.3% load spike chance (+20–40 A)
7. _step_circuits()     → per-circuit drift toward random target (0.1–20A×mul)
                          circuits beyond active_circuits output 0.0
                          accumulate kWh: kwh += kW × dt / 3600
8. _update_alarms()     → evaluate conditions (see below)
9. Compute panel kW     → V_avg × I_avg × PF × √3 / 1000
10. Accumulate kWh      → panel_kwh += panel_kW × dt / 3600
```

### Alarm Thresholds

| Alarm | Condition |
|-------|-----------|
| `Alarm_Overcurrent` | Any phase current > 85 A |
| `Alarm_VoltageImbalance` | max(Va,Vb,Vc) − min(Va,Vb,Vc) > 5 V |
| `Alarm_HighTHD` | Current THD > 7% |
| `Alarm_PhaseLoss` | Any phase voltage < 10 V |
| `Alarm_SensorFault` | Random 0.05%/tick, auto-clears in 3–8 ticks |

---

## Layer 5 — Device Simulator

**File:** `simulator/bacnet_device.py`

`EV2BACnetDevice` — one per simulated device IP.

### Ownership

```
EV2BACnetDevice
  ├── device_ip / device_instance / device_name / circuits
  ├── _objects: {(obj_type, instance): BACnetObject}    ← object tree
  ├── _name_to_key: {str: (obj_type, instance)}         ← telemetry lookup
  ├── _cov_subs: {(obj_type, instance): [COVSubscription]}
  └── _send_sock: socket bound to device_ip:BACNET_PORT
```

### Incoming Service Dispatch

```
handle_whois(low, high, src_addr)
    └─ sends I-Am if device_instance in [low, high]

handle_confirmed(invoke_id, service, data, src_addr)
    ├─ SVC_READ_PROPERTY        → _handle_read_property()
    ├─ SVC_READ_PROPERTY_MULTIPLE → _handle_read_property_multiple()
    ├─ SVC_SUBSCRIBE_COV        → _handle_subscribe_cov()
    └─ unknown service          → Reject PDU
```

### ReadProperty Response Format

```
ComplexAck PDU:
  byte 0:  (PDU_COMPLEX_ACK << 4)
  byte 1:  invoke_id
  byte 2:  SVC_READ_PROPERTY (12)
  ctx[0]:  object-identifier  OID(obj_type, obj_inst)
  ctx[1]:  property-identifier  uint(prop_id)
  ctx[2]:  array-index  (only if array_index was requested)
  ctx[3] opening
    <application-tagged property value>
  ctx[3] closing
```

### ReadPropertyMultiple Response Format

```
ComplexAck PDU:
  For each requested object:
    ctx[0]:  object-identifier
    ctx[1] opening
      For each requested property:
        ctx[2] opening
          ctx[0]: property-identifier
          ctx[4] opening
            <value>
          ctx[4] closing
        ctx[2] closing
    ctx[1] closing
```

### COV Notification

Sent as `UnconfirmedCOVNotification` after each `tick()` for objects whose `present_value` changed by more than `cov_increment` since last notification and `min_cov_interval_sec` has elapsed:

```
UnconfirmedRequest PDU:
  ctx[0]: subscriber-process-id
  ctx[1]: initiating-device-id (OID)
  ctx[2]: monitored-object-id  (OID)
  ctx[3]: time-remaining (0 = indefinite)
  ctx[4] opening
    present-value property-value
    status-flags property-value
  ctx[4] closing
```

---

## Layer 6 — Controller

**File:** `simulator/bacnet_controller.py`

`BACnetController` — lifecycle manager, receive multiplexer, tick driver.

### Startup Sequence

```python
controller.start(
    device_ips    = ["192.168.1.212", "192.168.1.213"],
    base_instance = 40001,          # first device instance (increments by 1)
    circuits_map  = {
        "192.168.1.212": (42, 24),  # (total_capacity, active_circuits)
        "192.168.1.213": (42, 42),  # all circuits active
    },
    frequency_hz  = 50.0,
    port          = 47808,
)
```

`circuits_map` values can be:
- `(total, active)` tuple — capacity from model name, active from power graph
- `int` (legacy) — all circuits active, count from model name

Internal steps:

```
1. Bind wildcard recv socket   0.0.0.0:port  (SO_REUSEADDR, SO_BROADCAST)
2. For each IP (in order):
     instance = base_instance + i
     EV2BACnetDevice(device_ip=ip, port=port, circuits=total, ...)  → binds ip:port
     EV2TelemetryEngine(circuits=total, frequency_hz, active_circuits=active)
3. Launch background recv thread
```

### Recv Thread

```python
select.select([wildcard_sock] + [dev._send_sock for dev in devices])

for sock in readable:
    data, src_addr = sock.recvfrom(4096)
    target_dev = sock_to_dev.get(sock)   # None if wildcard socket
    dispatch(data, src_addr, target_dev)
```

### Dispatch Logic

```
Packet on wildcard socket (target_dev = None):
  Who-Is   → fan out to ALL devices (broadcast discovery)
  Confirmed → fallback object-instance lookup (Device OIDs only; correct)

Packet on device socket (target_dev = specific device):
  Who-Is   → only that device responds (correct unicast BACnet behaviour)
  Confirmed → routed DIRECTLY to that device (fixes multi-device routing bug)
```

### tick() — Called by DeviceStateStore

```python
controller.tick(dt_seconds)
  for each (instance, engine) in telemetry:
      values = engine.tick(dt)
      devices[instance].update_present_values(values)
      devices[instance].dispatch_cov_notifications()
```

---

## Socket Architecture & Routing

### Problem: Shared Object Instances

All simulated devices have identical AI/BI instance numbers (e.g. AI:1001 = `Panel_Total_kW` on every device). Routing by object instance → always returns the first device.

### Solution: Per-Device Sockets

```
                          OS UDP routing (Windows + Linux)
                     ┌────────────────────────────────────┐
Broadcast Who-Is ───►│  0.0.0.0:47808  (wildcard socket) │──► all devices respond
                     └────────────────────────────────────┘
                     ┌────────────────────────────────────┐
Unicast to           │  192.168.1.212:47808               │──► device 40001 only
192.168.1.212 ──────►│  (most-specific binding wins)      │
                     └────────────────────────────────────┘
                     ┌────────────────────────────────────┐
Unicast to           │  192.168.1.213:47808               │──► device 40002 only
192.168.1.213 ──────►│                                    │
                     └────────────────────────────────────┘
```

**Rules:**
- Wildcard socket bound **before** device sockets (`SO_REUSEADDR` required on all)
- Broadcast → wildcard only (specific-IP sockets don't receive broadcast on Windows)
- Unicast → most-specific socket wins → no IP_PKTINFO / recvmsg needed
- Works identically on Windows and Linux

---

## Tick / Data Flow

```
DeviceStateStore._tick()
        │
        ▼
BACnetController.tick(dt)
        │
        ├──► EV2TelemetryEngine.tick(dt)
        │        physics random-walk
        │        returns {"Panel_Total_kW": 15.8, "Ckt01_Current": 7.2, …}
        │
        ├──► EV2BACnetDevice.update_present_values(values)
        │        writes float into BACnetObject.present_value
        │        sets reliability=COMM_FAILURE if Alarm_SensorFault
        │
        └──► EV2BACnetDevice.dispatch_cov_notifications()
                 for each object where |pv - cov_last| > cov_increment
                                    AND elapsed >= min_cov_interval_sec:
                     send UnconfirmedCOVNotification to each subscriber

External BACnet client (YABE / Niagara / NMS)
        │
        ├─ Who-Is broadcast ─────────► wildcard socket → all devices I-Am
        ├─ Who-Is unicast to IP ──────► that device's socket → that device I-Am
        ├─ ReadProperty to IP:47808 ──► that device's socket → ComplexAck
        ├─ ReadPropertyMultiple ──────► same → batched ComplexAck
        └─ SubscribeCOV ─────────────► device handles, future ticks push COVNotification

REST API client (DCIM / NMS over HTTP)
        │
        └─ GET /api/bacnet/ev2/metrics ──► BACnetController.get_telemetry_snapshot()
                                           → resolved circuit names from topology graph
                                           → JSON response (no BACnet stack needed)
```

---

## AppState & Headless Mode Integration

**File:** `api/state.py`, `app/main.py`

`BACnetController` is registered as a singleton on `AppState`:

```python
AppState.get().bacnet   # → BACnetController instance (or None if not registered)
```

### Registration — Desktop Mode

`main()` in `app/main.py` creates `MainWindow`, which initialises `BACnetController` and registers it via `api_state.register(bacnet=bacnet_controller)`.

### Registration — Headless Mode

`_run_headless()` in `app/main.py` explicitly creates and registers the controller:

```python
from simulator.bacnet_controller import BACnetController
bacnet = BACnetController("datasets/bacnet")

api_state.register(
    ...
    sflow=sflow,
    bacnet=bacnet,
    ...
)
```

> **Important:** Prior to v3.1, headless mode did NOT register the BACnetController, causing all `/api/bacnet/*` calls to return HTTP 503. This is fixed in v3.1.

### DeviceStateStore Integration

When BACnet is started via the REST API:

```python
# api/routers/bacnet.py
s.bacnet.start(...)
if s.state_store and hasattr(s.state_store, "enable_bacnet"):
    s.state_store.enable_bacnet(s.bacnet)
```

`enable_bacnet()` wires the controller into the tick loop — `BACnetController.tick(dt)` is called on every `DeviceStateStore._tick()` thereafter. Calling `disable_bacnet()` removes it from the loop.

### Log Callback

The REST API wires a log callback before starting:

```python
s.bacnet.set_log_callback(
    lambda msg, lvl="info": s.notify_ui("log_bacnet", msg, lvl)
)
```

Log messages are pushed to the Web UI console and desktop console panel via SSE.

---

## Supported BACnet Services

| Service | Direction | Notes |
|---------|-----------|-------|
| Who-Is / I-Am | discovery | Broadcast → all respond; unicast → addressed device only |
| ReadProperty (12) | client → sim | All properties: name, present-value, units, object-list, device props |
| ReadPropertyMultiple (14) | client → sim | Batches multiple objects/properties; `ALL` property supported |
| SubscribeCOV (5) | client → sim | Per-object COV subscription with configurable lifetime |
| UnconfirmedCOVNotification (2) | sim → client | Pushed each tick when threshold crossed and min interval elapsed |
| SimpleAck (2) | sim → client | SubscribeCOV acknowledgement |
| Error / Reject / Abort | sim → client | Unknown object, unknown property, bad request |

### Not Supported

- Segmented responses (max APDU = 1476 bytes; 231-object list fits in one frame)
- Confirmed COV notification (unconfirmed only)
- WriteProperty / WritePropertyMultiple
- Who-Has / I-Have
- TimeSynchronization
- BACnet/SC (TLS)
- BACnet/MSTP (serial)
- BBMD (broadcast management device)

---

## Object Instance Numbering

### Device Object Properties

When the Device object itself is read (`obj_type=8, obj_inst=device_instance`):

| Property | Value |
|----------|-------|
| Object_Identifier | (8, device_instance) |
| Object_Name | e.g. `"Verdigris_EV2_40001"` |
| Vendor_Name | `"Verdigris Technologies"` |
| Vendor_Identifier | `9999` |
| Model_Name | `"EV2"` |
| Firmware_Revision | `"2.4.1"` |
| Max_APDU_Length_Accepted | `1476` |
| Segmentation_Supported | `NO_SEGMENTATION` |
| Object_List | array of all (obj_type, instance) pairs |

### Routing via Object_List

The `Object_List` is the only property that lets a client discover all objects on a device. When reading it:

```
array_index=0   → returns count (uint)
no array_index  → returns full array (bulk fetch, preferred)
array_index=N   → returns Nth element (fallback, slow)
```

---

## UI Integration

### Desktop UI — Qt

**File:** `ui/bacnet_panel.py`

`BACnetPanel` signals:
- `sig_start` → `MainWindow._start_bacnet()` → `BACnetController.start()`
- `sig_stop`  → `MainWindow._stop_bacnet()` → `BACnetController.stop()`

Configuration exposed in desktop UI:

| Control | Default | Notes |
|---------|---------|-------|
| Base Device Instance | 40001 | First BACnet instance number; subsequent increment by 1 |
| Mains Frequency | 50 Hz | Passed to `EV2TelemetryEngine` |
| UDP Port | 47808 (0xBAC0) | BACnet standard port; editable if 47808 is reserved by Hyper-V/WinNAT |

### Web UI — React

**File:** `webui/src/components/RightPanel/BACnetPanel.tsx`

Communicates exclusively via the REST API (`/api/bacnet/*`). Provides identical controls to the desktop panel. Error responses from the API are displayed inline — previously, all errors were silently swallowed.

Features:
- Start/Stop buttons with busy state
- Running badge (Idle / Starting / Running / Stopping)
- Configuration fields: Base Instance, Mains Frequency, UDP Port
- Active device table (IP, Instance, Circuits, Status) shown while running
- Inline error display for API failures (400/503)

---

## REST API

**File:** `api/routers/bacnet.py`

All BACnet controls are available over HTTP. This is the primary interface in headless mode.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bacnet/status` | Running state, active device count, config snapshot |
| POST | `/api/bacnet/start` | Start the BACnet simulator |
| POST | `/api/bacnet/stop` | Stop the BACnet simulator |
| GET | `/api/bacnet/ev2/metrics` | Live present-values for all active EV2 devices |

### Start Request Body

```json
{
  "base_instance": 40001,
  "frequency_hz": 50.0,
  "port": 47808
}
```

Start logic:
1. Validates EV2 `energy_monitor` devices exist in topology
2. Requires every EV2 IP to be bound (`AppState.bound_ips`, via
   `_bind_guard.require_bound()`) — refuses the start and names the gap otherwise.
   Chiller-plant devices stay a soft filter: unbound ones are skipped with a warning
3. Walks power graph to determine `(total, active)` circuit counts per device
4. Calls `BACnetController.start()` then `state_store.enable_bacnet()`

### EV2 Metrics Response

```json
[
  {
    "ip": "10.1.0.10",
    "instance": 40001,
    "name": "Verdigris_EV2_40001",
    "circuits": 42,
    "monitored_pdu_name": "DC1-PDU-01",
    "panel": {
      "total_kw": 47.3,
      "total_kwh": 1284.5,
      "voltage_pha": 231.4, "voltage_phb": 230.9, "voltage_phc": 231.1,
      "current_pha": 68.2,  "current_phb": 65.1,  "current_phc": 67.8,
      "frequency": 50.02,
      "power_factor": 0.93,
      "voltage_thd": 2.1,
      "current_thd": 4.8,
      "harmonic_3": 2.3, "harmonic_5": 1.9, "harmonic_7": 0.8, "harmonic_9": 0.4,
      "alarm_overcurrent": false,
      "alarm_voltage_imbalance": false,
      "alarm_high_thd": false,
      "alarm_phase_loss": false,
      "alarm_sensor_fault": false
    },
    "circuit_list": [
      {
        "circuit": 1,
        "label": "Ckt01",
        "device_name": "DC1-SRV001",
        "current": 12.4,
        "kw": 2.86,
        "kwh": 42.1,
        "pf": 0.94,
        "thd": 3.2
      }
    ]
  }
]
```

`monitored_pdu_name` and `device_name` per circuit are resolved from the topology power graph at query time — not stored in the BACnet object tree.

---

## Test Clients

### Automated Test — `testscripts/test_bacnet.py`

```bash
# from project root
.venv/bin/python testscripts/test_bacnet.py 192.168.1.212 --instance 40001
.venv/bin/python testscripts/test_bacnet.py 192.168.1.212 --broadcast
.venv/bin/python testscripts/test_bacnet.py 192.168.1.212 --timeout 3
```

**Flow:**
```
1. who_is(broadcast/unicast, collect_secs=3)
     sends Who-Is, collects I-Am responses
     returns {ip: {instance, max_apdu, vendor_id}}

2. For each discovered device (sorted by IP):
     read_device_info()                 ← name, model, vendor, firmware
     read_object_list()                 ← bulk fetch, falls back to per-element
     For each object:
         read_property(PROP_OBJECT_NAME)
         read_property(PROP_PRESENT_VALUE)
         read_property(PROP_UNITS)      ← AI only

3. Print formatted results table
```

### Interactive Test — `testscripts/test_bacnet_interactive.py`

Interactive property browser — prompts for device IP, object type, instance, and property.

> **Discovery note:** Unicast to a specific IP only discovers the device at that IP. Use broadcast (`--broadcast` or `255.255.255.255`) to discover all simulated devices simultaneously.

---

## Known Limitations

| Limitation | Impact | Workaround |
|------------|--------|------------|
| No segmented responses | Max response size ~1476 bytes; 441-object list (84-circuit EV2) may not fit in one packet | Use 24 or 42 circuit count |
| Shared object instances across devices | Routing MUST use per-device sockets, not object-tree search | Fixed: each device binds its own `ip:port` |
| Windows `SO_REUSEADDR` broadcast behaviour | Broadcast only received by `0.0.0.0` socket, not specific-IP sockets | Wildcard socket handles all Who-Is fan-out |
| No WriteProperty | Cannot change thresholds or setpoints via BACnet | Use REST API or SNMP SET agent |
| No BBMD | Cannot route across subnets without a BACnet router | All devices must be on the same IP subnet as the client |
| kWh accumulators | Reset on simulator restart | Values accumulate from start time only |
| BGP session data | Only available after first simulator tick (~30 s) | Wait one tick before querying BGP OIDs |
| Circuit names in BACnet | Object names are `CktNN_*` — not topology device names | Use REST `GET /api/bacnet/ev2/metrics` for named circuit data |
