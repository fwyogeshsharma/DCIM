# BACnet/IP EV2 Energy Monitor — Developer Reference

**Simulator:** Datacenter Network Simulator v3.1
**Protocol:** BACnet/IP (Annex J), UDP
**Default Port:** 47808 (0xBAC0)

---

## Overview

The simulator exposes one or more Verdigris EV2 energy monitor devices over BACnet/IP. Each EV2 device monitors a single electrical panel with per-circuit current transformers (CTs). Each device has its own IP address, BACnet device instance number, and a full BACnet object tree.

---

## Device Identity

Each EV2 device reports the following Device Object properties:

| Property | Value |
|----------|-------|
| Object Type | Device (8) |
| Vendor Name | `Verdigris Technologies` |
| Vendor Identifier | `9999` |
| Model Name | `EV2` |
| Firmware Revision | `2.4.1` |
| Application Software Version | `EV2-SIM-1.0` |
| Protocol Version | 1 |
| Protocol Revision | 14 |
| System Status | operational |
| Segmentation Supported | no-segmentation |
| Max APDU Length Accepted | 1476 |

**Device Instance** is assigned at simulator start time. The first EV2 gets `base_instance` (default `40001`), the second gets `base_instance + 1`, and so on.

---

## Supported BACnet Services

### Unconfirmed Services

| Service | Code | Supported |
|---------|------|-----------|
| Who-Is | 8 | ✓ |
| I-Am | 0 | ✓ (response only) |
| Unconfirmed-COV-Notification | 2 | ✓ (sent by device) |

### Confirmed Services

| Service | Code | Supported |
|---------|------|-----------|
| ReadProperty | 12 | ✓ |
| ReadPropertyMultiple | 14 | ✓ |
| SubscribeCOV | 5 | ✓ |

> **Not supported:** WriteProperty, WritePropertyMultiple, AddListElement, ConfirmedCOVNotification (unconfirmed COV only), BACnet-Router services.

---

## Network Architecture

```
BACnet client
     │
     │  Who-Is (broadcast to 255.255.255.255:47808)
     ▼
0.0.0.0:47808  ← shared receive socket (all traffic arrives here)
     │
     ├── I-Am from 10.1.0.10:47808  (EV2 instance 40001)
     ├── I-Am from 10.1.0.11:47808  (EV2 instance 40002)
     └── I-Am from 10.1.0.12:47808  (EV2 instance 40003)

     │  ReadProperty (unicast to 10.1.0.10:47808)
     ▼
10.1.0.10:47808  ← per-device send socket (responses sourced from device IP)
```

Each EV2 device binds its own UDP socket to `device_ip:47808` with `SO_REUSEADDR`. Unicast requests to a device IP are delivered to that device's socket. Broadcast Who-Is packets arrive on the shared receive socket and are fanned out to all devices.

---

## Discovery

### Step 1 — Send Who-Is Broadcast

Send a BVLL-wrapped Who-Is APDU to `255.255.255.255:47808` (or the subnet broadcast address).

**Who-Is with no range (discover all):**
```
BVLL header:  0x81 0x0b <length>
NPDU:         0x01 0x20
APDU:         0x10 0x08
              (PDU-type=Unconfirmed, Service=Who-Is, no range parameters)
```

**Who-Is with instance range (e.g. 40001–40099):**
```
APDU:  0x10 0x08
       [context tag 0] <lower-bound>
       [context tag 1] <upper-bound>
```

### Step 2 — Receive I-Am Responses

Each EV2 sends an I-Am from its own source IP. The I-Am carries:

| Field | Description |
|-------|-------------|
| Device OID | `DEVICE:instance` |
| Max APDU Length | 1476 |
| Segmentation Supported | `no-segmentation` (3) |
| Vendor Identifier | 9999 |

Parse the source IP from the UDP packet to map `device_instance → IP address`.

---

## Object Instance Numbering

Instance numbers are **deterministic and stable** across simulator restarts.

### Panel-Level Objects

| Instance | Object Type | Name |
|----------|-------------|------|
| `device_instance` | Device | Device object |
| 1001 | Analog Input | Panel_Total_kW |
| 1002 | Analog Input | Panel_Total_kWh |
| 1003 | Analog Input | Voltage_PhA |
| 1004 | Analog Input | Voltage_PhB |
| 1005 | Analog Input | Voltage_PhC |
| 1006 | Analog Input | Current_PhA |
| 1007 | Analog Input | Current_PhB |
| 1008 | Analog Input | Current_PhC |
| 1009 | Analog Input | Line_Frequency |
| 1010 | Analog Input | Panel_PF |
| 1011 | Analog Input | Voltage_THD |
| 1012 | Analog Input | Current_THD |
| 1013 | Analog Input | Harmonic_3_Current |
| 1014 | Analog Input | Harmonic_5_Current |
| 1015 | Analog Input | Harmonic_7_Current |
| 1016 | Analog Input | Harmonic_9_Current |
| 1020 | Binary Input | Alarm_Overcurrent |
| 1021 | Binary Input | Alarm_VoltageImbalance |
| 1022 | Binary Input | Alarm_HighTHD |
| 1023 | Binary Input | Alarm_PhaseLoss |
| 1024 | Binary Input | Alarm_SensorFault |
| 1025 | Binary Input | Alarm_Undervoltage |
| 1026 | Binary Input | Alarm_UnderFrequency |

### Per-Circuit Objects

Circuit N (1-based) uses base instance `(N + 1) × 1000`:

| Circuit | Base Instance | Objects (base+1 to base+5) |
|---------|---------------|---------------------------|
| Ckt01 | 2000 | 2001=Current, 2002=kW, 2003=kWh, 2004=PF, 2005=THD |
| Ckt02 | 3000 | 3001=Current, 3002=kW, 3003=kWh, 3004=PF, 3005=THD |
| Ckt03 | 4000 | 4001=Current, 4002=kW, 4003=kWh, 4004=PF, 4005=THD |
| … | … | … |
| Ckt42 | 43000 | 43001–43005 |
| Ckt84 | 85000 | 85001–85005 |

**Formula:** `instance = (circuit_number + 1) × 1000 + offset`

| Offset | Metric |
|--------|--------|
| 1 | Current (A) |
| 2 | Active Power (kW) |
| 3 | Energy (kWh) — accumulator |
| 4 | Power Factor (dimensionless) |
| 5 | Current THD (%) |

---

## Full Object Catalogue

### Analog Input Objects

| Instance | Name | Description | Units | Min | Max | COV Increment | Min COV Interval |
|----------|------|-------------|-------|-----|-----|---------------|-----------------|
| 1001 | Panel_Total_kW | Panel Total Active Power | kW | 0 | 200 | 0.5 | 60 s |
| 1002 | Panel_Total_kWh | Panel Total Energy Accumulation | kWh | 0 | — | 2.0 | 300 s |
| 1003 | Voltage_PhA | Line-to-Neutral Voltage Phase A | V | 200 | 260 | 1.0 | 120 s |
| 1004 | Voltage_PhB | Line-to-Neutral Voltage Phase B | V | 200 | 260 | 1.0 | 120 s |
| 1005 | Voltage_PhC | Line-to-Neutral Voltage Phase C | V | 200 | 260 | 1.0 | 120 s |
| 1006 | Current_PhA | Line Current Phase A | A | 0 | 200 | 1.0 | 60 s |
| 1007 | Current_PhB | Line Current Phase B | A | 0 | 200 | 1.0 | 60 s |
| 1008 | Current_PhC | Line Current Phase C | A | 0 | 200 | 1.0 | 60 s |
| 1009 | Line_Frequency | Mains Supply Frequency | Hz | 45 | 65 | 0.05 | 120 s |
| 1010 | Panel_PF | Panel Power Factor | — | 0 | 1 | 0.02 | 120 s |
| 1011 | Voltage_THD | Voltage Total Harmonic Distortion | % | 0 | 50 | 0.5 | 120 s |
| 1012 | Current_THD | Current Total Harmonic Distortion | % | 0 | 50 | 0.5 | 120 s |
| 1013 | Harmonic_3_Current | 3rd Harmonic Current | % | 0 | 50 | 0.5 | 120 s |
| 1014 | Harmonic_5_Current | 5th Harmonic Current | % | 0 | 50 | 0.5 | 120 s |
| 1015 | Harmonic_7_Current | 7th Harmonic Current | % | 0 | 50 | 0.5 | 120 s |
| 1016 | Harmonic_9_Current | 9th Harmonic Current | % | 0 | 50 | 0.5 | 120 s |
| (N+1)×1000+1 | CktNN_Current | Circuit NN Current | A | 0 | — | 0.3 | 60 s |
| (N+1)×1000+2 | CktNN_kW | Circuit NN Active Power | kW | 0 | — | 0.05 | 60 s |
| (N+1)×1000+3 | CktNN_kWh | Circuit NN Energy Accumulation | kWh | 0 | — | 1.0 | 300 s |
| (N+1)×1000+4 | CktNN_PF | Circuit NN Power Factor | — | 0 | 1 | 0.02 | 120 s |
| (N+1)×1000+5 | CktNN_THD | Circuit NN Current THD | % | 0 | 50 | 0.3 | 120 s |

### Binary Input Objects (Alarms)

| Instance | Name | Description | Active Condition |
|----------|------|-------------|-----------------|
| 1020 | Alarm_Overcurrent | Overcurrent on any phase | Any phase current exceeds the panel's overcurrent threshold |
| 1021 | Alarm_VoltageImbalance | Phase voltage imbalance | `max(V) - min(V)` across the three phases > 5 V |
| 1022 | Alarm_HighTHD | Voltage THD exceeds limit | **Voltage** THD > 7% (IEEE-519) |
| 1023 | Alarm_PhaseLoss | Phase loss on any phase | Any phase voltage < 10 V |
| 1024 | Alarm_SensorFault | Internal measurement error | Internal fault condition |
| 1025 | Alarm_Undervoltage | Undervoltage / voltage sag | Any phase < 90% of nominal **and** ≥ 10 V |
| 1026 | Alarm_UnderFrequency | Under-frequency | Line frequency < 98% of nominal |

Binary Input present-value: `inactive` (0) = normal, `active` (1) = alarm.  
COV notifications for Binary Inputs fire **immediately** (no minimum interval) on state change.

### Alarm Semantics

**Thresholds scale with the panel.** Fractional limits are taken against the panel's own
nominal voltage and frequency, so a 208 V / 60 Hz panel scales correctly:

| Alarm | Limit @ 230 V / 50 Hz | Basis |
|-------|----------------------|-------|
| Alarm_VoltageImbalance | 5 V spread | Absolute |
| Alarm_HighTHD | 7% V-THD | Absolute |
| Alarm_PhaseLoss | 10 V | Absolute |
| Alarm_Undervoltage | 207 V | 0.90 × nominal voltage |
| Alarm_UnderFrequency | 49.0 Hz | 0.98 × nominal frequency |
| Alarm_Overcurrent | Derived per panel | Scales with pole count × branch breaker rating — **not** a fixed amp figure |

**Alarm_HighTHD is on VOLTAGE THD, not current THD.** `%THD-i` is naturally high at
light load, because distortion is measured against a shrinking fundamental — a lightly
loaded panel of healthy active-PFC server supplies reads high current distortion and is
not faulted. Alarming on `Current_THD` (AI:1012) would fire on every partly built panel.
IEEE-519 puts the utility-side limit on voltage distortion, and so does this alarm.

**Undervoltage and phase loss are mutually exclusive.** A phase near 0 V raises
Alarm_PhaseLoss (1023) only; Alarm_Undervoltage (1025) requires the phase to be above the
10 V phase-loss floor. A lost phase is never double-reported as a sag.

**All alarms are debounced — 2 consecutive ticks above threshold are required to latch.**
De-assertion is immediate on the first tick the condition clears. A client polling faster
than the tick interval will see the alarm rise up to 2 ticks after the underlying analog
crosses its limit.

**Alarm_SensorFault does not follow from any published reading.** It represents a failed
CT or input channel: nothing changes electrically, the *measurement* fails. It occurs
spontaneously (≈0.05% chance per tick) and self-clears after 3–8 ticks. While active, the
panel under-reports slightly and the harmonic/THD points read 0 — expect the
Σ-branches-vs-mains reconciliation to break by ~3% rather than the panel to look faulted.

---

## Supported Object Properties

### Analog Input Properties

| Property ID | Name | Notes |
|-------------|------|-------|
| 75 | Object_Identifier | `AI:<instance>` |
| 77 | Object_Name | String name (e.g. `Panel_Total_kW`) |
| 79 | Object_Type | 0 = Analog Input |
| 85 | Present_Value | Current engineering value (REAL) |
| 28 | Description | Human-readable description string |
| 117 | Units | Engineering units enum (see table below) |
| 67 | Status_Flags | 4-bit: {in-alarm, fault, overridden, out-of-service} |
| 103 | Reliability | `no-fault-detected` |
| 81 | Out_Of_Service | FALSE |
| 22 | COV_Increment | COV threshold for subscription notifications |
| 132 | Min_Pres_Value | Minimum expected value |
| 133 | Max_Pres_Value | Maximum expected value (omitted for accumulators) |
| 106 | Resolution | Measurement resolution |
| 76 | Object_List | (Device object only) list of all object identifiers |

### Binary Input Properties

| Property ID | Name | Notes |
|-------------|------|-------|
| 75 | Object_Identifier | `BI:<instance>` |
| 77 | Object_Name | String name (e.g. `Alarm_Overcurrent`) |
| 79 | Object_Type | 3 = Binary Input |
| 85 | Present_Value | `inactive` (0) or `active` (1) |
| 28 | Description | Human-readable alarm description |
| 67 | Status_Flags | 4-bit flags |
| 103 | Reliability | `no-fault-detected` |
| 81 | Out_Of_Service | FALSE |

### Special Property Value: ALL (512)

ReadPropertyMultiple supports `propertyIdentifier = ALL (512)` to retrieve all properties of an object in a single request.

---

## Engineering Units Reference

| Enum | Unit | Used For |
|------|------|---------|
| 3 | Amperes | Current |
| 5 | Volts | Voltage |
| 18 | Watt-hours | Energy (Wh) |
| 19 | Kilowatt-hours | Energy (kWh) |
| 27 | Hertz | Frequency |
| 47 | Watts | Power (W) |
| 48 | Kilowatts | Power (kW) |
| 95 | No units | Power Factor, dimensionless |
| 98 | Percent | THD, harmonics |

---

## Reading Properties

### ReadProperty — Single Object

Request one property from one object:

```
Service: ReadProperty (12)
Parameters:
  objectIdentifier:  AI:1001          (panel total kW)
  propertyIdentifier: Present_Value (85)
```

Response contains the property value as a REAL (IEEE 754 float).

### ReadPropertyMultiple — Batch Read

Efficient bulk read — recommended for polling all panel metrics in one request:

```
Service: ReadPropertyMultiple (14)
List of ReadAccessSpecification:
  { objectIdentifier: AI:1001, listOfPropertyReferences: [{propertyIdentifier: 85}] }
  { objectIdentifier: AI:1002, listOfPropertyReferences: [{propertyIdentifier: 85}] }
  { objectIdentifier: AI:1003, listOfPropertyReferences: [{propertyIdentifier: 85}] }
  ...
  { objectIdentifier: BI:1020, listOfPropertyReferences: [{propertyIdentifier: 85}] }
  { objectIdentifier: BI:1021, listOfPropertyReferences: [{propertyIdentifier: 85}] }
```

Using `propertyIdentifier: ALL (512)` returns all properties of each object.

**Recommended batch groupings:**

| Batch | Objects | Purpose |
|-------|---------|---------|
| Panel power | AI:1001–1010 | Power, voltage, current, frequency, PF |
| Power quality | AI:1011–1016 | THD and harmonics |
| Alarms | BI:1020–1026 | All 7 alarm states |
| Circuit N | AI:(N+1)×1000+1 to +5 | All 5 metrics for one circuit |

---

## COV Subscriptions

Subscribe to receive automatic notifications when a value changes beyond the configured increment.

### Subscribe Request

```
Service: SubscribeCOV (5)
Parameters:
  subscriberProcessIdentifier: <your process ID, uint32>
  monitoredObjectIdentifier:   AI:1001
  issueConfirmedNotifications: FALSE    (simulator sends unconfirmed only)
  lifetime:                    600      (seconds; 0 = indefinite)
```

The simulator acknowledges with a SimpleAck. Notifications are dispatched after each simulator tick (default 30 s interval) if the value has changed by at least `COV_Increment`.

### COV Notification Format

```
Service: UnconfirmedCOVNotification (2)
  subscriberProcessIdentifier: <your process ID>
  initiatingDeviceIdentifier:  DEVICE:40001
  monitoredObjectIdentifier:   AI:1001
  timeRemaining:               <seconds left on subscription>
  listOfValues:
    { propertyIdentifier: Present_Value, value: 47.3 }
    { propertyIdentifier: Status_Flags,  value: {false, false, false, false} }
```

### COV Increment Guidelines

| Object Type | COV Increment | Rationale |
|-------------|---------------|-----------|
| Panel kW | 0.5 kW | ~1% of nominal 50 kW panel |
| Phase Voltage | 1.0 V | Meaningful voltage deviation |
| Phase Current | 1.0 A | Meaningful load change |
| Circuit Current | 0.3 A | Fine-grained per-circuit tracking |
| Circuit kW | 0.05 kW | ~1% of typical 5 kW circuit |
| kWh accumulators | 1.0–2.0 kWh | Avoid notification floods |
| Power Factor | 0.02 | 2% PF change |
| THD / Harmonics | 0.3–0.5 % | Meaningful power quality event |
| Binary Alarms | 1.0 | Fires on every state change |

Minimum COV intervals are enforced per object — rapid value changes will not produce notifications faster than the configured minimum (e.g. energy accumulators: 5 minutes minimum).

---

## Typical NMS/DCIM Integration Workflows

### Workflow 1 — Initial Discovery and Inventory

```
1. Send Who-Is broadcast (no range) to 255.255.255.255:47808
2. Collect I-Am responses — record (device_instance, source_ip) pairs
3. For each device: ReadProperty DEVICE:<instance> Object_List
4. Parse object list to enumerate all AI and BI objects
5. For each AI/BI: ReadProperty <object> Object_Name, Description, Units
6. Store inventory: device → panel → circuits
```

### Workflow 2 — Periodic Panel Polling (no COV)

```
1. Every N seconds, send ReadPropertyMultiple to device_ip:47808
2. Request Present_Value for:
   - AI:1001–1016 (all panel metrics)
   - BI:1020–1026 (all alarms)
3. Update DCIM database with new values
```

Recommended poll interval: 30–60 seconds (matches simulator tick interval).

### Workflow 3 — Event-Driven via COV

```
1. Subscribe to alarm objects BI:1020–1026 with lifetime=0 (indefinite)
2. Subscribe to high-priority metrics (AI:1001, AI:1006–1008) with appropriate increments
3. On receiving UnconfirmedCOVNotification:
   a. Parse monitoredObjectIdentifier and new Present_Value
   b. Update DCIM / raise alert if BI goes active
4. Periodically re-subscribe before lifetime expires
```

### Workflow 4 — Full Circuit Scan (42-circuit EV2)

```
ReadPropertyMultiple to device_ip:47808:
  AI:2001 Present_Value   → Ckt01 Current
  AI:2002 Present_Value   → Ckt01 kW
  AI:2003 Present_Value   → Ckt01 kWh
  AI:2004 Present_Value   → Ckt01 PF
  AI:2005 Present_Value   → Ckt01 THD
  AI:3001–3005            → Ckt02
  ...
  AI:43001–43005          → Ckt42
```

All 210 circuit objects (42 circuits × 5 metrics) can be read in a single ReadPropertyMultiple request. The simulator's max APDU is 1476 bytes — split into multiple requests if the response would exceed this.

### Workflow 5 — REST API Alternative

The simulator also exposes all BACnet present-values over HTTP for integration without a BACnet stack:

```
GET /api/bacnet/ev2/metrics
```

Returns all active EV2 devices with panel metrics, alarms, and per-circuit values in JSON. Useful for DCIM systems that prefer REST over BACnet.

---

## Multiple EV2 Devices

When multiple EV2 devices are running:

| Device | IP | Instance | Panel | Circuits |
|--------|----|----------|-------|----------|
| EV2 #1 | 10.1.0.10 | 40001 | DC1-PDU-01 | 24 |
| EV2 #2 | 10.1.0.11 | 40002 | DC1-PDU-02 | 42 |
| EV2 #3 | 10.1.0.12 | 40003 | DC1-PDU-03 | 42 |

Each device has **independent** object trees. Object instance 1001 (Panel_Total_kW) exists on every device — distinguish by source IP or device instance. Send all requests unicast to the specific `device_ip:47808`.

---

## REST API Quick Reference

All BACnet simulator controls are also available over HTTP:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/bacnet/status` | Running state, active device list, config |
| POST | `/api/bacnet/start` | Start simulator — body: `{base_instance, frequency_hz, port}` |
| POST | `/api/bacnet/stop` | Stop simulator |
| GET | `/api/bacnet/ev2/metrics` | All present-values + circuit names from topology |

### Start Request Body

```json
POST /api/bacnet/start
{
  "base_instance": 40001,
  "frequency_hz": 50.0,
  "port": 47808
}
```

### EV2 Metrics Response Schema

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
      "voltage_pha": 231.4,
      "voltage_phb": 230.9,
      "voltage_phc": 231.1,
      "current_pha": 68.2,
      "current_phb": 65.1,
      "current_phc": 67.8,
      "frequency": 50.02,
      "power_factor": 0.93,
      "voltage_thd": 2.1,
      "current_thd": 4.8,
      "harmonic_3": 2.3,
      "harmonic_5": 1.9,
      "harmonic_7": 0.8,
      "harmonic_9": 0.4,
      "alarm_overcurrent": false,
      "alarm_voltage_imbalance": false,
      "alarm_high_thd": false,
      "alarm_phase_loss": false,
      "alarm_sensor_fault": false,
      "alarm_undervoltage": false,
      "alarm_underfrequency": false
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

---

## Known Constraints

| Constraint | Detail |
|------------|--------|
| Segmentation | Not supported — max APDU 1476 bytes. Split large ReadPropertyMultiple requests if needed. |
| Confirmed COV | Not supported — only unconfirmed COV notifications are sent. |
| WriteProperty | Not supported — all objects are read-only. |
| BACnet/MSTP | Not supported — BACnet/IP only. |
| Authentication | None — no BACnet Security (BACnet/SC not implemented). |
| kWh accumulators | Accumulate from simulator start time; reset on simulator restart. |
| BGP peer entries | Only populated after first simulator tick (~30 s after start). |

---

*This document describes the BACnet/IP interface of the Datacenter Network Simulator v3.1. The simulator is intended for NMS/DCIM integration testing and does not represent real device firmware.*
