# gNMI Architecture — Datacenter Network Simulator

**Version:** 1.0 · **Last updated:** 2026-06-17

This document catalogs the gNMI (gRPC Network Management Interface) plane of the
simulator: which device classes run a gNMI agent, what telemetry each exposes,
and the exact OpenConfig paths served with their human-readable meaning. It
complements [`PROTOCOL_ARCHITECTURE.md`](PROTOCOL_ARCHITECTURE.md) (protocol map
across SNMP / gNMI / sFlow / Redfish / BACnet) and
[`SNMP_ARCHITECTURE.md`](SNMP_ARCHITECTURE.md).

OpenConfig documents are produced by `core/gnmi_data_generator.py` into
`datasets/gnmi/<ip>.gnmi.json` and served over gRPC by
`simulator/gnmi_server.py`, orchestrated by `simulator/gnmi_controller.py`.

---

## 1. How gNMI is served

| Concern | Detail |
|---|---|
| Transport | gRPC (insecure / plaintext), JSON-IETF encoding |
| Encoding advertised | `JSON_IETF`, `JSON` |
| gNMI version | `0.9.1` |
| **Aggregating proxy port** | **50051**, bound `0.0.0.0` — set `prefix.target` to address any device |
| **Per-device server port** | **57400**, one gRPC server per device IP (`<ip>:57400`) |
| Target routing | gNMI `prefix.target` = device IP → routes to that device's data/server |
| Dataset format | OpenConfig JSON-IETF, one file per device |
| Live values | `_overlay()` reads the shared `DeviceStateStore` per request, so SNMP and gNMI return the **same** values for the same tick; falls back to random injection when no store attached |

A client can connect **two ways**, mirroring a real gNMI gateway:
- directly to a device — `<ip>:57400`
- via the proxy — `<host>:50051` with `prefix.target=<ip>`

### 1.1 Supported RPCs

| RPC | Behaviour |
|---|---|
| **Capabilities** | Returns supported OpenConfig models + gNMI version `0.9.1` |
| **Get** | Returns JSON-IETF for the requested path(s) with live values overlaid |
| **Subscribe** | `ONCE` (snapshot + sync, close), `POLL` (snapshot per poll trigger), `STREAM` (initial snapshot + periodic push; default interval 30 s, honours `sample_interval`) |
| **Set** | Accepted but discarded — simulated ack (read-only simulator) |

### 1.2 Advertised OpenConfig models (Capabilities)

| Model | Version | Scope |
|---|---|---|
| openconfig-interfaces | 2.4.3 | all |
| openconfig-lldp | 0.2.1 | all |
| openconfig-system | 0.11.1 | all |
| openconfig-network-instance | 0.13.0 | all |
| openconfig-vlan | 3.2.0 | switches |
| openconfig-bgp | 7.0.0 | routers |
| openconfig-ospfv2 | 0.4.1 | routers |
| openconfig-aft | 1.0.0 | routers |
| openconfig-if-ip | 3.1.0 | routers |

> `openconfig-platform` (temperature) is served in every document though not in
> the advertised catalogue list.

---

## 2. Which devices run gNMI

gNMI is generated **only for routers and switches** — see
`GNMIDataGenerator.generate_device()`, which returns `None` for any other type.
This mirrors the real world: OpenConfig/gNMI adoption outside switches and
routers is marginal, so servers, firewalls, load balancers, and all power /
cooling / sensor gear speak **no gNMI** (they use SNMP / Redfish / BACnet).

| Device type | gNMI? | Bind IP | OpenConfig modules served |
|---|---|---|---|
| **Switch** | ✔ | mgmt IP (else prod IP) | interfaces, lldp, system, platform, network-instance (**VLANs + FDB**) |
| **Router** | ✔ | mgmt IP (else prod IP) | interfaces, lldp, system, platform, network-instance (**BGP + OSPF + AFT/routes**) |
| Firewall / Load balancer | ✘ | — | SNMP only |
| Server | ✘ | — | SNMP (OS + BMC) + Redfish |
| OOB switch | ✘ | — | SNMP only |
| UPS / PDU / Generator / Sensor / CRAH / CDU | ✘ | — | SNMP / BACnet |

Bind IP = `mgmt_ip` when present, else `ip_address` (`generate_device()`).

The document root carries two routing helpers: `target` (the device prod IP) and
`device_type` (`"switch"` / `"router"`).

---

## 3. Common modules (every switch & router)

### 3.1 `openconfig-interfaces` — `/interfaces/interface[name=…]`

One entry per interface. Unconnected ports report `oper-status: DOWN` and zero
counters (matches the SNMP agent).

| OpenConfig path (under `/interfaces/interface[name]/`) | Readable name | Live |
|---|---|---|
| `state/name` | Interface name | |
| `state/type` | `iana-if-type:ethernetCsmacd` | |
| `state/mtu` | MTU (1500) | |
| `state/admin-status` | Admin status (`UP`) | |
| `state/oper-status` | Operational status (`UP`/`DOWN`) | ✔ |
| `state/counters/in-octets` | Inbound octets | ✔ |
| `state/counters/out-octets` | Outbound octets | ✔ |
| `state/counters/in-unicast-pkts` | Inbound unicast packets | ✔ |
| `state/counters/out-unicast-pkts` | Outbound unicast packets | ✔ |
| `state/counters/in-errors` | Inbound errors | ✔ |
| `state/counters/out-errors` | Outbound errors | ✔ |
| `state/counters/in-discards` | Inbound discards | ✔ |
| `state/counters/out-discards` | Outbound discards | ✔ |
| `ethernet/state/mac-address` | MAC address | |
| `ethernet/state/port-speed` | Port speed (`SPEED_1GB`…`SPEED_100GB`) | |
| `ethernet/state/duplex-mode` | Duplex (`FULL`) | |

**Interface 1** also carries a management subinterface with IPv4
(`openconfig-if-ip`): `subinterfaces/subinterface[index=0]/openconfig-if-ip:ipv4/addresses/address[ip]/state` →
`ip`, `prefix-length` (24), `origin` (`STATIC`).

### 3.2 `openconfig-lldp` — `/lldp`

| Path | Readable name |
|---|---|
| `state/enabled`, `hello-timer` (30), `hold-multiplier` (4) | LLDP global config |
| `interfaces/interface[name]/neighbors/neighbor[id]/state/chassis-id` | Neighbor chassis MAC |
| `…/state/port-id` | Neighbor port (interface name) |
| `…/state/system-name` | Neighbor hostname |
| `…/state/system-description` | Neighbor sysDescr |
| `…/state/management-address` | Neighbor management IP |

Neighbors are derived from topology production-layer edges.

### 3.3 `openconfig-system` — `/system`

| Path | Readable name | Live |
|---|---|---|
| `state/hostname` | Hostname | |
| `state/domain-name` | Domain (`lab.local`) | |
| `state/boot-time` | Boot time (ns epoch) | |
| `state/uptime` | Uptime | ✔ (recomputed from boot-time) |
| `state/software-version` | OS version | |
| `state/os-name` | OS name | |
| `memory/state/physical` | Total physical memory | |
| `memory/state/reserved` | Used memory | ✔ |
| `memory/state/free` | Free memory | ✔ |
| `memory/state/utilized` | Memory utilization % | ✔ |
| `cpus/cpu[index=ALL]/state/total/{instant,avg,min,max}` | CPU load % | ✔ |

### 3.4 `openconfig-platform` — `/components/component[name=…]`

Two components: `CHASSIS` and `CPU`, each with a temperature sensor.

| Path (under `component[name]/state/temperature/`) | Readable name | Live |
|---|---|---|
| `instant` | Current temp °C | ✔ |
| `avg` / `min` / `max` | Avg / min / max temp °C | ✔ |
| `alarm-status` | Alarm active (`instant ≥ threshold`) | ✔ |
| `alarm-threshold` | Threshold — **CHASSIS 55 °C, CPU 80 °C** | |
| `alarm-severity` | `openconfig-alarm-types:WARNING` | |

---

## 4. Switch-only — `openconfig-network-instance` (`name=DEFAULT`)

### 4.1 VLANs — `/network-instances/network-instance[DEFAULT]/vlans/vlan[vlan-id]`

VLANs 1–10 for every switch, all `status: ACTIVE`:

| VLAN | Name | VLAN | Name |
|---|---|---|---|
| 1 | default | 6 | voice |
| 2 | management | 7 | iot |
| 3 | servers | 8 | backup |
| 4 | storage | 9 | monitoring |
| 5 | dmz | 10 | quarantine |

Paths: `vlan[vlan-id]/state/{vlan-id,name,status}`.

### 4.2 FDB / MAC table — `…/fdb/mac-table/entries/entry[mac-address,vlan]`

One dynamic entry per connected interface, plus a few synthetic entries.

| Path | Readable name |
|---|---|
| `state/mac-address` | Learned MAC |
| `state/vlan` | VLAN id |
| `state/entry-type` | `DYNAMIC` |
| `state/age` | Entry age (s) |
| `interface/interface-ref/state/interface` | Egress interface |

Global: `fdb/state/{mac-aging-time (300), mac-learning (true)}`.

---

## 5. Router-only — `openconfig-network-instance` (`name=DEFAULT`)

### 5.1 BGP — `…/protocols/protocol[BGP]/bgp`

Autonomous-system numbers are deterministic per device: `65000 + (id % 1000)`.
One neighbor per directly-connected router.

| Path | Readable name | Live |
|---|---|---|
| `global/state/as` | Local AS number | |
| `global/state/router-id` | BGP router-id (device IP) | |
| `global/state/total-paths` / `total-prefixes` | Path / prefix counts | |
| `neighbors/neighbor[addr]/state/peer-as` | Peer AS | |
| `…/state/session-state` | Session state (`ESTABLISHED`) | |
| `…/state/established-transitions` | Session flaps | |
| `…/state/messages/{received,sent}/{UPDATE,KEEPALIVE}` | Message counters | ✔ (random) |
| `…/timers/state/{hold-time,keepalive-interval}` | 90 / 30 s | |

### 5.2 OSPFv2 — `…/protocols/protocol[OSPF]/ospfv2`

| Path | Readable name |
|---|---|
| `global/state/router-id` | OSPF router-id |
| `areas/area[0.0.0.0]/interfaces/interface[id]/state/network-type` | `POINT_TO_POINT` |
| `…/interface[id]/state/metric` | Interface metric (10) |
| `…/interface[id]/neighbors/neighbor[id]/state/adjacency-state` | Adjacency (`FULL`) |

Area is always `0.0.0.0` (backbone); one OSPF interface per router neighbor.

### 5.3 AFT / routing table — `…/afts/ipv4-unicast/ipv4-entry[prefix]`

A simulated IPv4 forwarding table: a default route plus one `/24` per neighbor.

| Path | Readable name |
|---|---|
| `state/prefix` | Destination prefix (`0.0.0.0/0`, `<net>/24`) |
| `state/origin-protocol` | `STATIC` (default + non-router) / `OSPF` (router neighbor) |
| `state/metric` | Route metric |
| `next-hops/next-hop[index]/state/ip-address` | Next-hop IP |

---

## 6. Live telemetry behaviour

Dynamic fields are overlaid per request by `_overlay()`:

- **Store-backed** (`_apply_store_metrics`): when a `DeviceStateStore` is
  attached, uptime, memory, CPU, temperature (CHASSIS→`inlet_temp`,
  CPU→`cpu_temp`), and interface counters are read from the same tick source as
  SNMP — so polling both protocols yields identical numbers.
- **Random injection** (`_inject_live_values`): fallback when no store is
  attached — counters increment, CPU/mem/temps jitter within bounds.

This means `STREAM` subscribers see counters climb and gauges move over time,
matching what a real router/switch streams via telemetry.

---

## 7. Source map

| Concern | File |
|---|---|
| OpenConfig document generation | `core/gnmi_data_generator.py` |
| gNMI servicer (Capabilities/Get/Set/Subscribe) | `simulator/gnmi_server.py` → `GNMIServicer` |
| Aggregating proxy (target routing) | `simulator/gnmi_server.py` → `GNMIProxyServicer` |
| gRPC server wrapper | `simulator/gnmi_server.py` → `GNMIServer` |
| Lifecycle / per-device servers / proxy ports | `simulator/gnmi_controller.py` → `GNMIController` |
| Live value source (shared with SNMP) | `core/device_state_store.py` |
| Compiled proto stubs | `proto/compiled/gnmi_pb2*.py` |
| UI panel | `ui/gnmi_panel.py` · REST: `api/routers/gnmi.py` |