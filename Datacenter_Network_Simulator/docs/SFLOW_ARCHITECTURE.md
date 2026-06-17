# sFlow Simulator Architecture — Datacenter Network Simulator

**Version:** 1.0 · **Last updated:** 2026-06-17

This document describes the sFlow plane of the simulator: which devices export
sFlow, how the v5 datagrams are built and pushed to a collector, the exact
wire format of counter and flow samples, and the configuration surface. It
complements [`PROTOCOL_ARCHITECTURE.md`](PROTOCOL_ARCHITECTURE.md),
[`SNMP_ARCHITECTURE.md`](SNMP_ARCHITECTURE.md),
[`GNMI_ARCHITECTURE.md`](GNMI_ARCHITECTURE.md), and
[`REDFISH_ARCHITECTURE.md`](REDFISH_ARCHITECTURE.md).

sFlow is a **push / export** protocol — unlike SNMP/gNMI/Redfish (poll/pull),
the agent originates traffic *to* a collector. The simulator implements an
sFlow v5 agent in pure Python (`struct` + `socket`, no subprocess, no external
libraries): `core/sflow_generator.py` (datagram builder) +
`simulator/sflow_controller.py` (agent lifecycle / sender).

---

## 1. How sFlow is served

| Concern | Detail |
|---|---|
| Spec | **sFlow v5** (RFC 3176 / sFlow v5 spec), version field = `5` |
| Model | **Push** — agent sends UDP datagrams to a collector; no listener on the device |
| Transport | UDP, encoded XDR (big-endian, opaque fields padded to 4 bytes) |
| Default collector | `127.0.0.1:6343` (configurable) |
| Export interval | **30 s** default (configurable) |
| Sample rate | **1:1000** default (configurable) — carried in flow samples |
| Agent address | the device **production IP** (`ip_address`), encoded as IPv4 (addr-type 1) |
| Implementation | pure stdlib — one daemon thread, one shared UDP socket |
| Live source | interface counters read from `DeviceStateStore.get_metrics(ip)` each tick — same source as SNMP/gNMI |

There is **no subprocess and no bound port** on the simulated device — the agent
is a sender loop. Stopping it joins the thread and closes the socket.

### 1.1 Which devices export sFlow

The controller is handed device IPs by the UI and emits datagrams for **any
device that has live interface metrics** in the StateStore. In practice that is
the **data-plane devices** — switches, routers, servers, firewalls, load
balancers — because those carry interface tables. Devices with no interface
metrics (most power/sensor gear) produce no counter datagram and are silently
skipped (`get_metrics(ip)` empty → 0 bytes).

Flow-sample **port/protocol profiles** are defined for these device types
(others fall back to a generic TCP `(80, 49152)` profile):

| device_type | Typical (src,dst) ports | IP protocols |
|---|---|---|
| router | (179,179) BGP, (22,22) SSH, (161,161) SNMP, (443,8443) | TCP, UDP, OSPF(89) |
| switch | (22,22), (161,161), (80,80) | TCP, UDP |
| server | (80,49152), (443,49153), (22,49154), (3306,49155) | TCP, UDP |
| firewall | (443,443), (80,80), (22,22) | TCP, UDP |
| load_balancer | (80,80), (443,443), (8080,8080) | TCP, UDP |

> Conceptually sFlow belongs to switches/routers (see
> `PROTOCOL_ARCHITECTURE.md`); the implementation exports for every device with
> interface counters, which additionally covers servers/firewalls/LBs that have
> a profile above.

---

## 2. Export loop

`SFlowController._send_loop` runs in a daemon thread:

```
every <interval> seconds:
    uptime_ms = monotonic() ms (32-bit)
    for each device IP:
        metrics = StateStore.get_metrics(ip)        # skip if None
        ── COUNTER datagram ──  (sub_agent_id = 0)
            one counter sample per interface (cap 64 → UDP payload < 9 kB)
            per-IP sequence number ++
            sendto(collector)
        ── FLOW datagram ──     (sub_agent_id = 1)
            2–5 synthetic flow samples toward topology neighbours
            per-IP sequence number ++
            sendto(collector)
```

Key behaviours:
- **Two sub-agents per device:** counter datagrams use `sub_agent_id=0`, flow
  datagrams use `sub_agent_id=1`, each with an independent monotonic sequence
  counter kept per device IP.
- **Interface cap:** at most 64 interfaces per counter datagram so the UDP
  payload stays under ~9 kB.
- **Flow destinations:** `_build_flow_entries` picks 2–5 records, each with a
  random local `if_index`, the agent IP as source, and a random topology
  **neighbour IP** as destination (loopback to self if the device is isolated).
  Source/dest ports and protocol come from the device-type profile (§1.1).
- **Interface speed:** looked up from the `Device.interfaces` (defaults to
  1 Gbps if unknown).

---

## 3. Datagram wire format (sFlow v5)

All integers big-endian (`!`). The XDR layout is built by `SFlowGenerator`.

### 3.1 Datagram header (`_header`)

| Field | Bytes | Value |
|---|---|---|
| version | 4 | `5` |
| agent address type | 4 | `1` (IPv4) |
| agent address | 4 | packed agent IP |
| sub_agent_id | 4 | `0` (counter) / `1` (flow) |
| sequence_number | 4 | per-IP, per-sub-agent monotonic |
| uptime_ms | 4 | agent uptime (ms, 32-bit) |
| num_samples | 4 | sample count in this datagram |

Followed by `num_samples` sample records.

### 3.2 Sample envelope

Each sample is wrapped `(sample_type, length, data)`:

| sample_type | Meaning |
|---|---|
| `1` | flow_sample |
| `2` | counter_sample |

Sub-records inside a sample are wrapped `((enterprise<<12 | format), length, data)`
with `enterprise = 0`.

### 3.3 Counter sample (type 2)

Sample body: `(sequence, source_id=if_index, num_records=1)` + one sub-record of
format **1** = generic interface counters (`if_counters`, RFC 2863) — an 88-byte
block:

| Field | Type | Source |
|---|---|---|
| ifIndex | u32 | interface index |
| ifType | u32 | `6` (ethernetCsmacd) |
| ifSpeed | u64 | interface speed (bps) |
| ifDirection | u32 | `1` |
| ifStatus | u32 | `0b11` (admin+oper up) |
| ifInOctets | u64 | `in_octets` (live) |
| ifInUcastPkts | u32 | `in_unicast_pkts` |
| ifInMulticastPkts / BroadcastPkts | u32 | `0` |
| ifInDiscards | u32 | `in_discards` |
| ifInErrors | u32 | `in_errors` |
| ifInUnknownProtos | u32 | `0` |
| ifOutOctets | u64 | `out_octets` (live) |
| ifOutUcastPkts | u32 | `out_unicast_pkts` |
| ifOutMulticastPkts / BroadcastPkts | u32 | `0` |
| ifOutDiscards | u32 | `out_discards` |
| ifOutErrors | u32 | `out_errors` |
| ifPromiscuousMode | u32 | `0` |

The eight live counter values come straight from the StateStore metrics dict, so
they agree with the SNMP IF-MIB and gNMI interface counters for the same tick.

### 3.4 Flow sample (type 1)

Sample body: `(sequence, source_id=if_index, sample_rate, sample_pool,
drops=0, input_if=if_index, output_if=0x40000000)` + `num_records=1` + one
sub-record of format **1** = raw packet header (`sampled_header`):

| Field | Value |
|---|---|
| sample_rate | configured rate (e.g. 1000) |
| sample_pool | `sample_rate × seq` (synthetic) |
| drops | `0` |
| output_if | `0x40000000` (multiple/unknown) |
| header protocol | `1` (Ethernet) |
| frame_length | random 64–1514 |
| header (opaque) | synthetic Ethernet + IPv4 + TCP/UDP ports |

The sampled header is a hand-built frame: fixed src/dst MACs, an IPv4 header
(`src_ip` = agent, `dst_ip` = neighbour, `protocol` from profile, TTL 64), and
4-byte TCP/UDP ports when the protocol is TCP(6)/UDP(17). XDR-opaque padded to a
4-byte boundary.

---

## 4. Configuration

Set via the sFlow panel / REST `start`, stored on the controller:

| Param | Default | Meaning |
|---|---|---|
| `collector_ip` | `127.0.0.1` | Destination collector address |
| `collector_port` | `6343` | Destination UDP port (sFlow standard) |
| `interval` | `30` (s) | Export period |
| `sample_rate` | `1000` | 1:N packet sampling rate (in flow samples) |
| `device_ips` | all device prod IPs | Agents to simulate |

Reporting helpers: `get_collector()` → `ip:port`, `get_interval()`,
`get_device_count()`, `is_running()`.

---

## 5. Control plane (UI / REST)

`api/routers/sflow.py` (prefix `/api/sflow`):

| REST endpoint | Purpose |
|---|---|
| `GET /status` | Running state, collector, interval, agent count |
| `POST /start` | Start the sFlow agents (collector/interval/sample_rate params) |
| `POST /stop` | Stop the sender thread |

UI: `ui/sflow_panel.py` (start/stop, collector config, device-type counts,
F11 shortcut). Dependencies injected at startup: state store, topology, device
manager (`set_state_store` / `set_topology` / `set_device_manager`).

---

## 6. Test collector

`testscripts/test_sflow.py` is a stdlib UDP receiver that decodes sFlow v5 and
prints a live aggregated summary — use it to verify export without a real NMS.

```bash
python testscripts/test_sflow.py                 # summary every 30 s on :6343
python testscripts/test_sflow.py --interval 10   # summary every 10 s
python testscripts/test_sflow.py --verbose       # also dump raw datagrams
python testscripts/test_sflow.py --counters-only # hide flow output
python testscripts/test_sflow.py --flows-only     # hide counter output
python testscripts/test_sflow.py --host 127.0.0.1 --port 6343
```

It maps protocol numbers (TCP/UDP/OSPF/ICMP) and well-known ports
(SSH/HTTP/BGP/SNMP/HTTPS/MySQL…) to names and renders per-interface throughput
bars.

---

## 7. Source map

| Concern | File |
|---|---|
| sFlow v5 datagram builder (header, counter + flow samples) | `core/sflow_generator.py` |
| Agent lifecycle, sender loop, flow synthesis, UDP send | `simulator/sflow_controller.py` |
| Live interface counters (shared with SNMP/gNMI) | `core/device_state_store.py` |
| Topology neighbours (flow destinations) | `core/topology_engine.py` |
| Port/protocol profiles | `core/sflow_generator.py` (`_PORT_PROFILES`, `_PROTO_CHOICES`) |
| REST control endpoints | `api/routers/sflow.py` |
| UI panel | `ui/sflow_panel.py` |
| Test collector / decoder | `testscripts/test_sflow.py` |
