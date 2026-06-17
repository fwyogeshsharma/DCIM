# Redfish Simulator Architecture — Datacenter Network Simulator

**Version:** 1.0 · **Last updated:** 2026-06-17

This document describes the Redfish plane of the simulator: which devices expose
a Redfish/BMC endpoint, the resource tree served, the authentication model, the
supported HTTP methods and actions, push-model eventing, and how telemetry stays
live. It complements [`PROTOCOL_ARCHITECTURE.md`](PROTOCOL_ARCHITECTURE.md),
[`SNMP_ARCHITECTURE.md`](SNMP_ARCHITECTURE.md), and
[`GNMI_ARCHITECTURE.md`](GNMI_ARCHITECTURE.md).

Redfish is the **server out-of-band (BMC) management interface** — the DMTF
standard replacing IPMI. It is served by `simulator/redfish_controller.py`
(HTTP lifecycle), `simulator/redfish_device.py` (routing + auth), and
`core/redfish_data_generator.py` (resource bodies).

---

## 1. How Redfish is served

| Concern | Detail |
|---|---|
| Transport | **Plain HTTP** (no TLS in this MVP) — `http://<ip>:443/redfish/v1/` |
| Port | **443** (configurable) |
| Server | one stdlib `ThreadingHTTPServer` **per server**, bound to that server's own IP (not `0.0.0.0`) — each BMC answers only on its own address, like real hardware |
| Bind IP | `device.mgmt_ip or device.ip_address` — the OOB management network (same IP the BMC SNMP agent uses) |
| Redfish version | `1.6.0` (ServiceRoot `RedfishVersion`) |
| OData version | `4.0` (sent as `OData-Version` header) |
| Default credentials | `admin` / `password` |
| Resource bodies | built **on demand** from the live `Device` object → telemetry always reflects the latest `DeviceStateStore` tick (no per-tick push needed) |
| Server identity | `server_version: RedfishSim/1.0`, HTTP/1.1 |

A perf note: the controller uses `_FastBindHTTPServer`, which skips the stock
`socket.getfqdn()` reverse-DNS lookup (~1.5 s/server on Windows) so hundreds of
BMCs bind quickly.

### 1.1 Which devices run Redfish

**Servers only.** Redfish models the BMC (iDRAC / iLO / XClarity / etc.), which
exists only on servers. Switches, routers, power, cooling, and sensor gear have
no BMC and expose no Redfish endpoint — they use SNMP / gNMI / BACnet.

Because the BMC runs on **standby power**, the Redfish endpoint **answers even
while the server chassis is powered Off** (see §7).

### 1.2 Vendor BMC branding

The `Manager` resource is branded per server vendor (`_bmc_branding`):

| Vendor | BMC product | Manager Id base | Firmware |
|---|---|---|---|
| Dell Technologies | iDRAC9 | iDRAC.Embedded.1 | 6.10.30.00 |
| HPE | iLO 6 | iLO.Embedded.1 | 1.55 |
| Lenovo | XClarity Controller | XCC.Embedded.1 | 22A |
| Supermicro | Supermicro BMC | BMC.Embedded.1 | 01.04.16 |
| IBM | IMM2 | IMM.Embedded.1 | 9.10 |
| Cisco Systems | Cisco IMC | CIMC.Embedded.1 | 4.3(2.240009) |
| (other) | BMC | BMC.Embedded.1 | 1.00 |

The Redfish `Manager` member id is the fixed string `BMC`.

---

## 2. Authentication model

Mirrors a real BMC (`redfish_device.py`):

| Endpoint | Auth required |
|---|---|
| `GET /redfish` (version stub) | No |
| `GET /redfish/v1/` (ServiceRoot) | No |
| `POST …/SessionService/Sessions` (login) | No |
| **everything else** | **Yes** |

Two accepted credential forms:
- **HTTP Basic** — `Authorization: Basic base64(admin:password)`
- **Session token** — `X-Auth-Token: <token>` from a created session

**Session login:** `POST /redfish/v1/SessionService/Sessions` with
`{"UserName": "...", "Password": "..."}` → `201` with `X-Auth-Token` +
`Location` headers. **Logout:** `DELETE` the session URL. Tokens live in an
in-memory dict per BMC; session timeout advertised as 1800 s.

Unauthenticated/invalid requests get `401` with a Redfish error body
(`Base.1.0.InsufficientPrivilege`).

---

## 3. Resource tree

`@odata.id` paths under `/redfish/v1/`. `<id>` = server member id
(`device.id` or sanitized name); `BMC` = the manager id.

```
/redfish                                              version stub {v1: …}
/redfish/v1/                                          ServiceRoot
├── Systems/                                          Computer System Collection
│   └── {id}                                          ComputerSystem  ← power, CPU, mem, BIOS
│       ├── EthernetInterfaces/                       NIC collection
│       │   └── NIC.{n}                               EthernetInterface (Rx/Tx, link)
│       ├── Storage/                                  Storage collection
│       │   └── Storage.1                             Storage (RAID)
│       ├── LogServices/                              Log Service collection
│       │   └── SEL/                                  System Event Log
│       │       └── Entries/                          LogEntry collection
│       └── Actions/
│           ├── ComputerSystem.Reset                  power actions (POST)
│           └── Oem/Simulator.RefreshInventory        (POST)
├── Chassis/                                          Chassis Collection
│   └── {id}                                          Chassis  ← RackMount
│       ├── Thermal                                   Temperatures + Fans
│       └── Power                                     PowerControl + PowerSupplies
├── Managers/                                         Manager Collection
│   └── BMC                                           Manager (the BMC) + Manager.Reset
├── SessionService/                                   SessionService
│   └── Sessions/                                     Session Collection (login here)
└── EventService/                                     EventService (push events)
    ├── Subscriptions/                                EventDestination collection
    └── Actions/EventService.SubmitTestEvent          (POST)
```

### 3.1 Resource → OData type map

| Resource | `@odata.type` |
|---|---|
| ServiceRoot | `#ServiceRoot.v1_5_0.ServiceRoot` |
| ComputerSystem | `#ComputerSystem.v1_13_0.ComputerSystem` |
| Chassis | `#Chassis.v1_14_0.Chassis` |
| Thermal | `#Thermal.v1_7_0.Thermal` |
| Power | `#Power.v1_7_0.Power` |
| Manager | `#Manager.v1_10_0.Manager` |
| EthernetInterface | `#EthernetInterface.v1_6_0.EthernetInterface` |
| Storage | `#Storage.v1_9_0.Storage` |
| LogService | `#LogService.v1_3_0.LogService` |
| LogEntry | `#LogEntry.v1_8_0.LogEntry` |
| SessionService / Session | `#SessionService.v1_1_8` / `#Session.v1_3_0` |
| EventService | `#EventService.v1_5_0.EventService` |
| EventDestination (subscription) | `#EventDestination.v1_10_0.EventDestination` |
| Event (delivered) | `#Event.v1_7_0.Event` |
| Collections | `#Collection.Collection` |

---

## 4. Metrics & telemetry exposed

All values are built per-request from the live `Device`, so they track the
ticker (and agree with the SNMP BMC agent).

### 4.1 ComputerSystem — `/redfish/v1/Systems/{id}`

| Field | Source / meaning | Live |
|---|---|---|
| `PowerState` | `On` / `Off` (chassis) | ✔ |
| `IndicatorLED` / `LocationIndicatorActive` | identify LED (`Off`/`Lit`/`Blinking`) | ✔ |
| `Status.State` | `Enabled` (On) / `StandbyOffline` (Off) | ✔ |
| `Manufacturer` / `Model` / `SerialNumber` / `UUID` | vendor, model, derived SN/UUID | |
| `BiosVersion` | `U30 v2.66` | |
| `ProcessorSummary` | count 2 + vendor CPU model | |
| `MemorySummary.TotalSystemMemoryGiB` | total RAM | |
| `Oem.Simulator.CpuUtilizationPercent` | live CPU % | ✔ |
| `Oem.Simulator.MemoryUtilizationPercent` / `MemoryUsedBytes` | live memory | ✔ |
| `Oem.Simulator.DiskUtilizationPercent` / `DiskUsedBytes` / `DiskTotalBytes` | live disk | ✔ |
| `Oem.Simulator.NetworkRxMbps` / `NetworkTxMbps` | summed NIC throughput | ✔ |
| `Oem.Simulator.AlarmCount` | active alarm conditions | ✔ |

### 4.2 Chassis Thermal — `/redfish/v1/Chassis/{id}/Thermal`

| Field | Meaning | Live |
|---|---|---|
| `Temperatures[0]` "CPU Temp" | `cpu_temp` °C, `UpperThresholdCritical` 95 | ✔ |
| `Temperatures[1]` "Inlet Temp" | `inlet_temp` °C, `UpperThresholdCritical` 45 | ✔ |
| `Fans[0..3]` | 4 fans, RPM from ticker `fan_rpm`, Health `Warning` ≥14000 RPM | ✔ |

### 4.3 Chassis Power — `/redfish/v1/Chassis/{id}/Power`

| Field | Meaning | Live |
|---|---|---|
| `PowerControl[0].PowerConsumedWatts` | live draw = `power_draw_w × (0.55 + 0.45·cpuLoad)`; **0 when Off** | ✔ |
| `PowerControl[0].PowerMetrics` | avg / max(×1.15) / min(×0.85) watts | ✔ |
| `PowerSupplies[0..1]` | 2 redundant PSUs, AC 230 V, 1100 W capacity, load split in half | ✔ |

### 4.4 EthernetInterfaces — `…/Systems/{id}/EthernetInterfaces/NIC.{n}`

One member per interface: `MACAddress`, `SpeedMbps`, `LinkStatus`
(`LinkUp`/`LinkDown` — down if unconnected), `InterfaceEnabled`,
`Oem.Simulator.RxMbps`/`TxMbps` (synthesized from link speed × CPU load). ✔ live.

### 4.5 Storage — `…/Systems/{id}/Storage/Storage.1`

Vendor RAID controller name, `SupportedRAIDTypes` (RAID0/1/5/10),
`Oem.Simulator` → `RaidType` (RAID5), `RaidStatus` (`Optimal`/`Degraded`),
`VolumeCapacityGiB`, `VolumeUsedPercent` (live). Health `Warning` at ≥95 % used.

### 4.6 System Event Log — `…/Systems/{id}/LogServices/SEL/Entries`

`LogEntry` members: `Id`, `Severity`, `Message`, `Created`. Seeded with "BMC
initialized" / "System powered on"; appended on every action and alarm
transition. `OverWritePolicy: WrapsWhenFull`.

---

## 5. Supported HTTP methods & actions

| Method | Purpose | Examples |
|---|---|---|
| **GET** | Read any resource in the tree (§3) | ServiceRoot, System, Thermal, Power, SEL, … |
| **POST** | Login, server actions, eventing | see action table |
| **PATCH** | Mutable properties | `Systems/{id}` `IndicatorLED` / `LocationIndicatorActive` |
| **DELETE** | Tear-down | session URL, subscription URL |

Unsupported method/path → `405 Base.1.0.ActionNotSupported`.

### 5.1 POST actions

| Action path | Effect |
|---|---|
| `…/Systems/{id}/Actions/ComputerSystem.Reset` | Power control via `ResetType` |
| `…/Systems/{id}/Actions/Oem/Simulator.RefreshInventory` | Record inventory refresh |
| `…/Systems/{id}/LogServices/SEL/Actions/LogService.ClearLog` | Clear the SEL |
| `…/Managers/BMC/Actions/Manager.Reset` | Reset the BMC itself |
| `…/SessionService/Sessions` | Create session (login) |
| `…/EventService/Subscriptions` | Create push subscription |
| `…/EventService/Actions/EventService.SubmitTestEvent` | Fire a test event |

**`ComputerSystem.Reset` ResetType values:** `On`, `ForceOn`, `ForceOff`,
`GracefulShutdown`, `GracefulRestart`, `ForceRestart`, `PowerCycle`,
`PushPowerButton`. A real power transition mirrors onto `Device.power_state` and
fires the BMC platform-event trap (see §7). Reboots do **not** toggle state.

**`Manager.Reset`** accepts `GracefulRestart` / `ForceRestart` and models BMC
RAM volatility: **event subscriptions and live alarm tracking are lost** across
a BMC reset (the SEL, modeling NVRAM, survives). A collector must reconcile by
re-listing/re-creating subscriptions. Host reboots do not clear subscriptions.

---

## 6. Push-model eventing (EventService)

The BMC supports Redfish event subscriptions (EventDestination push model).

| Aspect | Value |
|---|---|
| Create | `POST …/EventService/Subscriptions` with `{"Destination": url, "Protocol":"Redfish", "Context", "EventTypes"}` → `201` + `Location` |
| Delete | `DELETE …/EventService/Subscriptions/{id}` |
| Delivery | BMC POSTs `#Event.v1_7_0.Event` docs to each subscriber `Destination` (async, best-effort) |
| Per-BMC cap | **20** subscriptions (`MAX_SUBS`) → `503 CreateLimitReachedForResource` |
| EventTypes | `Alert`, `ResourceUpdated`, `StatusChange` |
| RegistryPrefixes | `Base`, `ResourceEvent` |
| ResourceTypes | `ComputerSystem`, `Chassis`, `Manager` |
| Retry policy (advertised) | 3 attempts, 5 s interval |
| Test event | `POST …/EventService/Actions/EventService.SubmitTestEvent` |

**Alarm-driven events.** A background monitor thread in the controller sweeps
every BMC every **5 s** (`evaluate_alerts`), comparing live telemetry against
thresholds and pushing edge-triggered events (assert → Warning/Critical, clear →
OK). A sustained fault fires exactly once. Thresholds (`_alarms`):

| Condition | Threshold | Severity |
|---|---|---|
| CPU temperature high | `cpu_temp ≥ 85 °C` | Critical |
| Inlet temperature high | `inlet_temp ≥ 40 °C` | Warning |
| Memory utilization high | `≥ 90 %` | Warning |
| Disk utilization high | `≥ 90 %` | Warning |
| Fan over-speed | fan Health ≠ OK (≥14000 RPM) | Warning |

A powered-off chassis has no live sensors → all alarms clear.

---

## 7. Power-off semantics

Because the BMC runs on standby power, the Redfish endpoint stays alive when the
chassis is Off:

| Behaviour while chassis OFF | Result |
|---|---|
| Redfish endpoint | **Alive** — answers all reads |
| `ComputerSystem.PowerState` | `Off`; `Status.State` = `StandbyOffline` |
| Power-on remotely | `ComputerSystem.Reset` `On`/`ForceOn` works |
| Chassis Power watts | `0 W` (`_live_watts` returns 0 when Off) |
| Thermal | temps decay to ambient, fans toward 0 (ticker-driven) |
| Alarms | cleared (no live sensors) |
| BMC platform trap | a power transition fires the controller's `trap_cb` → SNMP `serverPowerOff` (`.26.0.1`) / `serverPowerOn` (`.26.0.2`) — see [`SNMP_ARCHITECTURE.md`](SNMP_ARCHITECTURE.md) §9.6 |

`RedfishDevice.reset()` mirrors `power_state` onto the `Device` so the ticker and
all other protocols (SNMP, gNMI, UI) see the chassis state change.

---

## 8. Control plane (UI / REST)

The controller exposes server operations to the UI and the REST API
(`api/routers/redfish.py`, prefix `/api/redfish`), independent of the Redfish
HTTP path:

| REST endpoint | Purpose |
|---|---|
| `GET /status` | Per-BMC summary (power, LED, sessions, SEL count, subs) |
| `POST /start`, `POST /stop` | Start/stop all BMC endpoints |
| `GET /sessions` | Active sessions across all BMCs |
| `POST /action` | Run an action on one BMC (power_on/off, reboot, power_cycle, led_on/off, refresh, clear_log, reboot_bmc) |
| `GET /log` | SEL for a BMC |
| `GET /subscriptions`, `POST /subscribe`, `POST /unsubscribe` | Manage push subscriptions |
| `POST /test-event` | Fire a test event |

`RedfishController.perform_action()` maps these high-level actions onto the same
`RedfishDevice` methods the HTTP actions use, so UI/REST and a real Redfish
client drive identical state.

---

## 9. Source map

| Concern | File |
|---|---|
| HTTP server lifecycle, per-IP bind, health monitor, trap hook | `simulator/redfish_controller.py` |
| Request routing, auth, sessions, actions, subscriptions, SEL | `simulator/redfish_device.py` |
| Resource document bodies (all `@odata` types) | `core/redfish_data_generator.py` |
| Live values (shared with SNMP/gNMI) | `core/device_state_store.py` |
| Power-state field mutated by `reset()` | `Device.power_state` |
| BMC SNMP platform traps on power change | `core/trap_engine.py` (via controller `trap_cb`) |
| REST control endpoints | `api/routers/redfish.py` |
| UI panel | `ui/redfish_panel.py` |
| Test tooling | `testscripts/test_redfish.py <ip> [--power-on/--led/--view-log/--subscribe/…]` |

### Quick start (test client)

```bash
# Walk a BMC: System, Chassis (Thermal+Power), Manager
python testscripts/test_redfish.py 10.1.0.20 --port 443 --user admin --pass password
# Power control
python testscripts/test_redfish.py 10.1.0.20 --power-off
python testscripts/test_redfish.py 10.1.0.20 --power-on
# Identify LED + event log
python testscripts/test_redfish.py 10.1.0.20 --led on --view-log
# Subscribe and stream live push events (Ctrl-C to stop)
python testscripts/test_redfish.py 10.1.0.20 --subscribe
```
