# Datacenter Network Simulator — User Guide

This guide walks through running a simulation end to end: load a topology, bind
device IPs, start each protocol simulator (SNMP, gNMI, BACnet, Redfish), then use
the trap, metric-tick, console, graph-layer, and live-metrics features.

The workflow is sequential — bind IPs before starting any simulator, and generate
datasets before starting SNMP/gNMI. Top-bar badges (`Devices`, `SNMP`, `gNMI`,
`BACnet`, `Redfish`) show each subsystem's state (`idle` / `ready` / `running`).

---

## Step 1 — Load a Topology

Open **File ▸ Open Topology** and pick a topology JSON (e.g. `dual_dc_enterprise.json`
or `demo_single_dc.json`). New / Save / Close / Export as JSON live in the same menu.

When nothing is loaded the canvas shows *No topology loaded*; the device list and graph
populate once a file opens.

![File ▸ Open Topology — empty canvas before a topology is loaded](docs/guide_assets\Open Topology.png)

---

## Step 2 — Bind IPs

Open the **Network Interface Bindings** panel (right side). Pick the network adapter
(Windows: *Microsoft KM-TEST Loopback*; Linux: `dcim0`), confirm the subnet mask, then
click **Bind IPs**. The simulator assigns one virtual IP per device onto the adapter.
**Remove Binding** tears them down.

Binding must succeed before any protocol simulator can serve traffic — each simulated
device listens on its own bound IP.

![Network Interface Bindings — select adapter and click Bind IPs](docs/guide_assets\Panel Bindings.png)

---

## Step 3 — Start the SNMP Simulator

Open the **SNMP Simulator** panel. It lists how many devices of each type will be
simulated and the **Port Configuration** (SNMP port `161`, management/SET port `1161`).

**3.1 — Generate datasets.** Click **Generate Datasets** to build the per-device SNMP
data (`.snmprec`) from the topology.

![SNMP Simulator — Generate Datasets builds per-device SNMP data](docs/guide_assets\Panel SNMP Generate datasets.png)

**3.2 — Start the simulator.** Once datasets are `READY`, click **Start Simulator**.
Use **Regenerate Datasets** to rebuild after topology changes, or **Close Datasets** to
discard them.

![SNMP Simulator — READY; click Start Simulator](docs/guide_assets\Panel SNMP Start sim.png)

---

## Step 4 — Start the gNMI Simulator

Open the **gNMI Simulator** panel (gNMI port `57400`). Generate datasets, then
**Start Simulator**. gNMI is the streaming-telemetry interface for the network devices.
Optionally **Enable Proxy** on the configured proxy port to aggregate device gNMI
endpoints behind a single port.

![gNMI Simulator — Generate Datasets then Start Simulator; optional proxy](docs/guide_assets\Panel gNMI Start sim.png)

---

## Step 5 — Start the BACnet Simulator

Open the **BACnet Simulator** panel — this models the mechanical / facility devices
(chillers, CRAHs, CDUs, pumps, cooling towers, valves, plant sensors) over **BACnet/IP**
on UDP port `47808` (BAC0). Set the **Base Instance** and **Name Template**, review the
target device counts, then click **Start BACnet**.

![BACnet Simulator — facility devices over BACnet/IP (UDP 47808)](docs/guide_assets\Panel BACnet.png)

---

## Step 6 — Start the Redfish Simulator

Open the **Redfish Simulator** panel — the modern server-management interface (DMTF
Redfish over HTTPS, port `8443`). Set the **Username / Password**, then click
**Start Redfish**. Each simulated server exposes a Redfish service root for out-of-band
(BMC) management.

![Redfish Simulator — server BMC management over HTTPS (port 8443)](docs/guide_assets\Panel Redfish.png)

---

## Enable Trap Simulation (optional)

Open the **SNMP Traps** panel. Set the **Trap Receiver** (destination IP:port) and click
**Set Receiver**, then toggle **Trap Simulation** on. The simulator emits SNMP traps to
the receiver; the panel logs each trap it sends.

![SNMP Traps — set receiver and toggle trap simulation](docs/guide_assets\Panel SNMP Traps.png)

---

## Enable / Disable Trap Rules

Open the **Traps Rule Engine**. Rules are grouped (Interface/Link, CPU, Memory,
Power/UPS, …); each has a checkbox to enable/disable it and a threshold. Use the per-rule
toggles to control which conditions generate traps, and **Reset Counts** to clear the
per-rule fire counters.

![Traps Rule Engine — enable/disable individual rules and thresholds](docs/guide_assets\Panel Trapes Rule.png)

---

## Metric Tick — Interval, Per-Metric Toggles, Limits

The **Metric Tick** panel drives how simulated metrics evolve over time.

**Metrics tab** — set the **Tick Interval** (seconds), toggle the engine **Enabled**, and
enable/disable individual metrics per device class (CPU usage, memory, temperatures,
interface counters; sensor temp/humidity; UPS load/battery, …). Click **Apply**.

![Metric Tick (Metrics) — tick interval and per-metric enable toggles](docs/guide_assets\Panel Metric Tick(Metrics).png)

**Limits tab** — bound each metric to a min/max range per device type so simulated values
stay realistic. Click **Apply** to commit.

![Metric Tick (Limits) — clamp each metric to a min/max range](docs/guide_assets\Panel Metric Tick(Limits).png)

---

## Monitor the Console

The **Console** panel streams runtime logs, tabbed by subsystem (Boot/Simulator, gNMI,
BACnet, Redfish). Use **Auto-Scroll** to follow live output and **Clear** to reset. This
is the first place to look when a simulator fails to start or a dataset build errors.

![Console — tabbed live logs per subsystem](docs/guide_assets\Panel Console.png)

---

## Switch Between Graph Layers

The layer toggle in the top bar filters the topology view by relationship type:
**All**, **Prod** (production network), **Mgmt** (OOB management), **Power** (generator →
UPS → RPP → PDU), and **Cool** (chilled-water + condenser loop). Switching layers isolates
one plane of the datacenter at a time.

![Graph Layers — All view showing every relationship plane](docs/guide_assets\Graph Layer  All.png)

![Graph Layers — Production network only](docs/guide_assets\Graph Layer  Prod.png)

![Graph Layers — Management (OOB) plane](docs/guide_assets\Graph Layer MGMT.png)

![Graph Layers — Power chain](docs/guide_assets\Graph Layer Power.png)

![Graph Layers — Cooling loop](docs/guide_assets\Graph Layer Cooling.png)

---

## Monitor Live Metrics

The **Live Metrics** page shows a real-time table of every device's current metric values,
with inline bars and per-device-type filter chips (Network, Server, UPS, PDU, Energy,
CDU, Pump, Cooling tower, CRAH, Chiller, Sensor, Valve). Values update each metric tick.

![Live Metrics — all devices with live value bars](docs/guide_assets\Live metrics All.png)

Filter to a single device class to focus on its metrics:

![Live Metrics — Server view](docs/guide_assets\Live metrics Server.png)

![Live Metrics — Chiller view](docs/guide_assets\Live metrics Chiller.png)

---

## Send Traps Manually

Right-click any node on the graph to open its context menu (**Edit Device**,
**Remove Device**, **Locate on Graph**, **Show Info**, **Send Trap**). Hover **Send Trap**
to pick a trap type — **Cold Start**, **Warm Start**, **Authentication Failure**,
**CPU High Usage**, **Temperature Alert**, **Link Flap** — to emit that trap for the
selected device on demand.

![Send Trap — right-click a node ▸ Send Trap ▸ choose trap type](docs/guide_assets\Send Traps manualy.png)