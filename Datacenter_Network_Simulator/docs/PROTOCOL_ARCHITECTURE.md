# Protocol Architecture — Datacenter Network Simulator

**Version:** 4.1 · **Last updated:** 2026-06-24

This document describes which management protocol runs on which simulated
device class, on which IP/network it answers, and how that maps to real
datacenter hardware. The simulator mirrors real-world management-plane
architecture so DCIM/NMS products can be integration-tested faithfully.

---

## 1. The Two Networks

Every topology with a management layer has two address spaces:

| Network | Addressing | Purpose |
|---|---|---|
| **Production** | `10.x.x.x` (`ip_address`) | Data traffic; servers' OS-level NICs |
| **OOB Management** | `192.168.x.x` (`mgmt_ip`) | Out-of-band management via OOB switches |

Who owns the mgmt port differs per device class — this is the key
architectural distinction:

- On a **switch/router/firewall**, the mgmt port belongs to the **same NOS**
  that forwards traffic (one brain). No BMC exists.
- On a **server**, the mgmt port belongs to the **BMC** — a separate
  always-on controller (iDRAC / iLO / XCC) with its own CPU and firmware,
  alive on standby power even when the chassis is off (two brains).

---

## 2. Protocol Map by Device Class

```
                                ┌──────────────────────────────┐
                                │         DCIM / NMS           │
                                │ (openDCIM, Nlyte, Prometheus)│
                                └──┬───┬───┬───┬───┬───┬───┬───┘
            SNMP      gNMI   sFlow │   │   │   │   │   │   │ BACnet/IP
        ┌──────────┬───────┬───────┘   │   │   │   │   │   └────────────┐
        │          │       │      SNMP │   │   │   │   │ Redfish        │
        ▼          ▼       ▼     (OS)  │   │   │   │   │ (HTTPS)        ▼
┌───────────────────────────┐          │   │   │   │   │      ┌─────────────────┐
│  SWITCHES / ROUTERS /     │          ▼   │   │   │   ▼      │ POWER & COOLING │
│  FIREWALLS / LB / OOB     │   ┌──────────┴───┴───┴──────┐   │ EV2 energy mon. │
│  ───────────────────────  │   │         SERVERS         │   │ Chillers, CRAH, │
│  One brain: the NOS       │   │  ─────────────────────  │   │ pumps, cooling  │
│                           │   │  Two brains: OS + BMC   │   │ towers, valves, │
│  mgmt IP (mgmt VRF):      │   │                         │   │ CDUs            │
│   • SNMP  (NOS agent)     │   │  prod IP (OS NICs):     │   │                 │
│   • gNMI  (sw/rtr only)   │   │   • SNMP (OS agent)     │   │  • BACnet/IP    │
│   • sFlow export          │   │   • node_exporter,      │   │    (UDP 47808)  │
│     (sw/rtr only)         │   │     Datadog, fwAgent…   │   │                 │
│                           │   │                         │   │ UPS / PDU /     │
│  NO BMC — power off the   │   │  mgmt IP (BMC, OOB):    │   │ sensors /       │
│  box and everything dies  │   │   • Redfish (HTTP/S)    │   │ generators:     │
│                           │   │   • SNMP (BMC agent)    │   │  • SNMP         │
│                           │   │   • BMC traps (PET)     │   │    (vendor MIBs)│
└───────────────────────────┘   │  BMC alive on STANDBY   │   └─────────────────┘
                                │  power — answers even   │
                                │  when chassis is OFF    │
                                └─────────────────────────┘
```

### 2.1 Switches, Routers, Firewalls, Load Balancers, OOB Switches

| Protocol | IP | Notes |
|---|---|---|
| SNMP | mgmt IP | Served by the NOS itself. ifTable, LLDP/CDP, BRIDGE-MIB (switches), BGP (routers/firewalls) |
| gNMI | mgmt IP | **Switches and routers only.** OpenConfig YANG over gRPC — interfaces, LLDP, BGP, OSPF, platform. Per-device gRPC server on port 57400; optional aggregating proxy on 50051. Full feature breakdown below |
| sFlow | → collector | **Switches and routers only.** v5 datagrams pushed UDP to a collector (default :6343); flow samples + counter samples |

No BMC: there is no out-of-band controller. The mgmt port is just a
non-transit port on the NOS, usually in a management VRF. Power the device
off and *all* protocols stop. (Remote power control for network gear is done
through switched PDU outlets, not a BMC.) Firewalls and load balancers speak
SNMP only — OpenConfig adoption outside switches/routers is marginal, which
the simulator mirrors.

#### gNMI — supported features

Implemented in `simulator/gnmi_server.py` + `core/gnmi_data_generator.py`.
gRPC, gNMI **v0.9.1**. Encodings: **JSON_IETF** (primary), JSON. Live values
track the DeviceStateStore ticker via subtree overlay.

**RPCs:**

| RPC | Support | Notes |
|---|---|---|
| Capabilities | full | Advertises supported YANG models + encodings + gNMI version |
| Get | full | Path-based subtree fetch; prefix + target; multiple paths per request |
| Set | **ack-only** | Accepts UPDATE/REPLACE/DELETE and returns valid UpdateResult, but **discards** — no state change (read-only sim) |
| Subscribe | full | Bidirectional streaming; ONCE / POLL / STREAM modes |

**Subscribe modes:**

- **ONCE** — full snapshot + `sync_response`, then close
- **POLL** — snapshot + sync on each client Poll trigger
- **STREAM** — initial snapshot + sync, then periodic push; interval =
  client `sample_interval` (default 30 s)

**OpenConfig YANG models advertised** (`Capabilities`):

| Scope | Models |
|---|---|
| Common (all) | `openconfig-interfaces` 2.4.3, `openconfig-lldp` 0.2.1, `openconfig-system` 0.11.1, `openconfig-network-instance` 0.13.0 |
| Switch | `openconfig-vlan` 3.2.0 |
| Router | `openconfig-bgp` 7.0.0, `openconfig-ospfv2` 0.4.1, `openconfig-aft` 1.0.0, `openconfig-if-ip` 3.1.0 |

**Architecture:** one gRPC server per device on port 57400; optional
aggregating **proxy** (`GNMIProxyServicer`) on 50051 fronts them, routing by
`target`.

**Not supported (deliberate):** Set with effect (config writes acked but
dropped — no state mutation over gNMI); `ON_CHANGE` / `TARGET_DEFINED`
subscription sub-modes (STREAM is periodic SAMPLE only); PROTO / ASCII
encodings (JSON_IETF / JSON only); gNOI; `use_models` model-filter enforcement.

#### sFlow — supported features

Pure-Python sFlow **v5** (RFC 3176), real binary XDR datagrams over UDP.
Implemented in `simulator/sflow_controller.py` + `core/sflow_generator.py`.
**Export-only** (agent → collector); no subprocess, no external libraries.

**Config:** collector default `127.0.0.1:6343`; export interval default 30 s;
sample rate 1:1000. One agent per device; per-IP sequence counters (separate for
counter vs flow datagrams). Datagram header carries version 5, agent IPv4
address, sub-agent id, sequence, uptime, sample count.

**Sample types:**

| Type | Sub-record format | Contents |
|---|---|---|
| counter_sample (2) | generic if_counters (fmt 1, RFC 2863) | Full 88-byte if_counters block — **live** |
| flow_sample (1) | raw packet header / sampled_header (fmt 1) | Synthetic sampled packet header |

- **Counter sample** (live from DeviceStateStore): ifIndex, ifType
  (ethernetCsmacd), ifSpeed, ifDirection, ifStatus, ifIn/OutOctets (Counter64),
  ucast/multicast/broadcast pkts, discards, errors, unknownProtos. Up to 64
  interfaces per datagram (UDP payload capped < 9 kB).
- **Flow sample** (synthetic): sampled_header building Ethernet + IPv4 + TCP/UDP
  — src = device IP, dst = topology-neighbor IP (loopback if isolated), src/dst
  ports + L4 protocol per device type, input ifIndex, sample_rate, frame size.
  2–5 flow records per device per tick.

**Devices:** switches + routers only.

**Not supported (deliberate):** real packet sampling (flows are synthesized, not
captured); extended flow records (extended_switch VLAN/priority,
extended_router, extended_gateway BGP AS-path); expanded samples (formats 3/4);
Ethernet/VLAN counter records; IPv6 agent/flows (IPv4 only); inbound config (no
sFlow-MIB / counter-poll config over SNMP — interval & rate set via controller
only).

### 2.2 Servers — two agents, two IPs

| Agent | IP | Protocols | Survives power-off? |
|---|---|---|---|
| **Host OS** | production IP | SNMP (OS agent): ifTable, LLDP, CPU/mem/disk, HOST-RESOURCES · **OS-agent traps** (HighCPU, HighMemory, HighTemperature, LinkDown/Up, LinkFlap) · monitoring agents: node_exporter, Datadog, Telegraf (and the planned **fwAgent** for fwDCIM) | **No** — dies with the OS |
| **BMC** (iDRAC/iLO/XCC) | mgmt IP | Redfish (HTTP, `/redfish/v1/`, port 8443 — plain HTTP in MVP, no TLS): GET polling + POST/PATCH/DELETE actions (ComputerSystem.Reset) + EventService push subscriptions · SNMP (BMC agent): power state, temps, fans, PSUs · platform-event traps (power on/off) | **Yes** — standby power |

The two SNMP agents serve **disjoint metric sets**:

| | OS SNMP (prod IP) | BMC SNMP (mgmt IP) |
|---|---|---|
| CPU/memory/disk *usage* | ✔ | ✘ (BMC can't see inside the OS) |
| Interface traffic counters, LLDP | ✔ | ✘ |
| Temps, fan RPM, PSU health/watts | ✘ | ✔ |
| Chassis power state | ✘ | ✔ (`…99999.26.1.1.0`, 1=On 2=Off) |

OS-resident monitoring agents (node_exporter, Datadog, fwAgent) live in the
same box as the OS SNMP agent — richer data, push-capable, but equally dead
when the chassis is off.

**Traps come from both agents, with disjoint trap sets and source IPs**
(`_trap_source_ip`, `core/trap_engine.py`):

| Trap source | Source IP | Trap set |
|---|---|---|
| OS agent | production IP | HighCPU, HighMemory, HighTemperature, LinkDown/Up, LinkFlap — driven by OS metrics |
| BMC | mgmt IP | `serverPowerOff` / `serverPowerOn` platform events |

An Off server emits **no** OS-agent traps — not because the OS agent lacks
them, but because `core/device_state_store.py` publishes no facts for an Off
device, so no rule fires. (The OS SNMP *poll* still answers — sim
approximation; see §3.)

### 2.3 Power & Cooling

| Devices | Protocol | Notes |
|---|---|---|
| EV2 energy monitors, chillers, CRAHs, pumps, cooling towers, valves, CDUs | **BACnet/IP** (UDP 47808) | Who-Is/I-Am discovery, ReadProperty, COV subscriptions |
| CRAH, CDU | **also SNMP** (mgmt IP) | Real units ship native network/comm cards (Liebert IntelliSlot-style); SNMP serves the same ticking values as BACnet |
| Chiller, pump, cooling tower, valve | **BACnet only — no SNMP** | Real units have no SNMP card; their points reach IT via the BMS. The simulator deliberately does not answer SNMP for these |
| UPS, rack/floor PDUs, environmental sensors, generators | **SNMP** (mgmt IP) | Vendor MIBs (UPS-MIB, Raritan, Vertiv, APC…), enterprise traps |

#### BACnet/IP — supported features

Implemented in `simulator/bacnet_controller.py` + `core/bacnet_object_model.py`.
Devices running BACnet: EV2 energy monitors + plant (chiller, pump, cooling
tower, valve, CDU, CRAH).

**Transport** — UDP 47808 (0xBAC0). BVLL types: Original-Unicast,
Original-Broadcast, Forwarded-NPDU (BBMD-style). One shared recv socket
`0.0.0.0:47808` + per-device send socket `device_ip:47808` so discovery tools
(YABE, Niagara) see distinct source IPs.

**Services (read + COV-push only):**

| Service | Type | Notes |
|---|---|---|
| Who-Is / I-Am | unconfirmed | Discovery. Broadcast → all devices reply; unicast → only that device |
| ReadProperty | confirmed | Single-property GET |
| ReadPropertyMultiple | confirmed | Multi-object GET, incl. `PROP_ALL` (read-all) pseudo-property |
| SubscribeCOV | confirmed | Register change-of-value subscription |
| (Unconfirmed)COVNotification | unconfirmed | **Push** — changed points notified to subscribers after each tick |
| Reject | — | Returned on unroutable / malformed requests |

**Object types:** AnalogInput/Output/Value, BinaryInput/Output/Value, Device
(AI/BI + Device used in practice).

**Properties served:** PresentValue, ObjectName/Type/Identifier, ObjectList,
Units, StatusFlags, Reliability, COV-Increment, Min/MaxPresValue, OutOfService,
VendorName/ID, ModelName, Firmware/SoftwareVersion, Protocol-Version/Revision,
SystemStatus, SegmentationSupported, ActiveCOVSubscriptions.

**Telemetry model:** two paths — **pull** (ReadProperty/Multiple) + **push**
(COV). Values driven live by the EV2 + plant telemetry engines each tick.

**Not supported (deliberate):** WriteProperty / WritePropertyMultiple
(read-only sim — no commanding setpoints/actuators over BACnet; plant alarms
and forces are driven by the Metric-Tick UI instead), WhoHas/IHave,
AtomicReadFile/WriteFile, TimeSynchronization, DeviceCommunicationControl,
ConfirmedCOVNotification, segmentation (advertised, not performed).

---

## 3. Power-Off Semantics (servers)

Powering a server off via Redfish (`ComputerSystem.Reset`) cascades through
every layer, exactly as on real hardware:

| Layer | While chassis is OFF |
|---|---|
| Redfish (mgmt IP) | **Alive.** `PowerState: Off`; power-on works remotely |
| BMC SNMP (mgmt IP) | **Alive.** Power-state OID reads 2; temps decay to ambient; fans 0 RPM; PSU output 0 W |
| OS SNMP (prod IP) | Dead host: CPU/mem/uptime 0, interfaces down, counters frozen. *(Sim approximation: the agent still answers with these values; a real one would time out)* |
| Production links | Broken **both ends** — server NICs and switch ports go oper-down; topology shows the link broken |
| Traps | BMC sends `serverPowerOff` / `serverPowerOn` platform events (`1.3.6.1.4.1.99999.26.0.1/.2`). The rule engine publishes **no facts** for an Off server — no phantom OS-agent traps |
| gNMI / sFlow | n/a — servers never run these |
| UI | Node dimmed on both canvases; Live Metrics shows "—" for OS metrics, live temps, red OFF pill |

---

## 4. Simulator Implementation Map

| Concern | Where |
|---|---|
| SNMP datasets (`.snmprec`, community = IP) | `core/snmprec_generator.py` → `datasets/snmp/<ip>.snmprec`; servers get two files: `<prod_ip>` (OS) + `<mgmt_ip>` (BMC, `_bmc_entries`) |
| SNMP runtime | snmpsim subprocess (`simulator/snmpsim_controller.py`), one process bound per-IP, port 161; SET agent on 1161 |
| gNMI | `simulator/gnmi_controller.py` + per-device gRPC servers; OpenConfig JSON from `core/gnmi_data_generator.py` |
| sFlow | `simulator/sflow_controller.py` — v5 datagrams to configured collector |
| Redfish | `simulator/redfish_controller.py` — one HTTP server per server mgmt IP; resources built live by `core/redfish_data_generator.py` |
| BACnet | `simulator/bacnet_controller.py` — EV2 + plant engines |
| Live values | `core/device_state_store.py` ticker mutates `Device` fields each tick; all protocols read the same source |
| Traps | `core/trap_engine.py` (SNMPv2c) — rule-driven (`core/trap_rules.py`) + direct BMC platform events |
| Power state | `Device.power_state`, mutated only by `RedfishDevice.reset()` |

**Polling conventions for a DCIM under test:**

- Network/power/sensor device → `mgmt IP`, community = mgmt IP
- Server OS metrics → `prod IP`, community = prod IP
- Server hardware health → `mgmt IP` (SNMP community = mgmt IP, or Redfish
  `http://<mgmt_ip>:<port>/redfish/v1/`)
- Test tooling: `testscripts/test_snmp.py <ip> [--full|--bmc|--switch|…]`

---

## 5. Future: fwAgent

fwDCIM's OS-resident agent (**fwAgent**) will be simulated as one lightweight
HTTP endpoint per server on the production IP — the node_exporter analog.
Because it is OS-resident, it must refuse connections while the chassis is
Off, which `Device.power_state` already makes possible — giving fwDCIM a
true agent-down test case that OS SNMP (always-answering) cannot provide.
