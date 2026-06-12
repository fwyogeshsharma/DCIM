# Protocol Architecture — Datacenter Network Simulator

**Version:** 4.0 · **Last updated:** 2026-06-12

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
| gNMI | mgmt IP | **Switches and routers only.** OpenConfig YANG over gRPC — interfaces, LLDP, BGP, OSPF, platform. gRPC port 50051, optional aggregating proxy |
| sFlow | → collector | **Switches and routers only.** v5 datagrams pushed UDP to a collector (default :6343); flow samples + counter samples |

No BMC: there is no out-of-band controller. The mgmt port is just a
non-transit port on the NOS, usually in a management VRF. Power the device
off and *all* protocols stop. (Remote power control for network gear is done
through switched PDU outlets, not a BMC.) Firewalls and load balancers speak
SNMP only — OpenConfig adoption outside switches/routers is marginal, which
the simulator mirrors.

### 2.2 Servers — two agents, two IPs

| Agent | IP | Protocols | Survives power-off? |
|---|---|---|---|
| **Host OS** | production IP | SNMP (OS agent): ifTable, LLDP, CPU/mem/disk, HOST-RESOURCES · monitoring agents: node_exporter, Datadog, Telegraf (and the planned **fwAgent** for fwDCIM) | **No** — dies with the OS |
| **BMC** (iDRAC/iLO/XCC) | mgmt IP | Redfish (HTTP, `/redfish/v1/`) · SNMP (BMC agent): power state, temps, fans, PSUs · platform-event traps | **Yes** — standby power |

The two SNMP agents serve **disjoint metric sets**:

| | OS SNMP (prod IP) | BMC SNMP (mgmt IP) |
|---|---|---|
| CPU/memory/disk *usage* | ✔ | ✘ (BMC can't see inside the OS) |
| Interface traffic counters, LLDP | ✔ | ✘ |
| Temps, fan RPM, PSU health/watts | ✘ | ✔ |
| Chassis power state | ✘ | ✔ (`…99999.20.1.1.0`, 1=On 2=Off) |

OS-resident monitoring agents (node_exporter, Datadog, fwAgent) live in the
same box as the OS SNMP agent — richer data, push-capable, but equally dead
when the chassis is off.

### 2.3 Power & Cooling

| Devices | Protocol | Notes |
|---|---|---|
| EV2 energy monitors, chillers, CRAHs, pumps, cooling towers, valves, CDUs | **BACnet/IP** (UDP 47808) | Who-Is/I-Am discovery, ReadProperty, COV subscriptions |
| UPS, rack/floor PDUs, environmental sensors, generators | **SNMP** (mgmt IP) | Vendor MIBs (UPS-MIB, Raritan, Vertiv, APC…), enterprise traps |

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
| Traps | BMC sends `serverPowerOff` / `serverPowerOn` platform events (`1.3.6.1.4.1.99999.20.0.1/.2`). The rule engine publishes **no facts** for an Off server — no phantom OS-agent traps |
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
