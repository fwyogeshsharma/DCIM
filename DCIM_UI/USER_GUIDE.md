# FWDCIM — User Guide

A guide to navigating the **DCIM** web interface: signing in, finding your way around, and what you can do on every screen.

> **What is this app?** FWDCIM is a Data Center Infrastructure Management console. It gives you a single, real-time view of every device, alert, power system, network link, and safety system across your data centers.

---

## 1. Getting Started

### Signing in
1. Open the app URL in your browser.
2. On the **Sign In** page, enter your **Username** and **Password**, then click **Sign In**.
3. Don't have an account? Click **Create Account**, fill in username, email, and password on the **Sign Up** page, then sign in.
4. After signing in you land on the **Dashboard**.

> Your session is remembered in the browser, so you stay logged in between visits until you sign out.

---

## 2. Getting Around (Layout)

The screen has three fixed areas:

```
┌────────────┬─────────────────────────────────────────────┐
│            │  TOP BAR:  title · 🔔 notifications · 👤 you │
│            ├─────────────────────────────────────────────┤
│  SIDEBAR   │                                             │
│  (menu)    │            MAIN CONTENT (the page)          │
│            │                                             │
└────────────┴─────────────────────────────────────────────┘
```

### The sidebar (left menu)
The collapsible menu (click the arrow at the top to expand/collapse) is your main navigation. Menu items, in order:

| Icon | Menu | What it's for |
|---|---|---|
| 🟦 | **Dashboard** | Real-time overview of all data centers |
| 🖥️ | **Devices** | Browse and inspect every monitored device |
| ⚠️ | **Alerts** | View and filter active/resolved alerts |
| 🎫 | **Tickets** | Track and manage incidents |
| ⚡ | **Power Mgmt** | UPS, PDU, generator, and energy monitoring |
| 📡 | **Network Ops** | Network devices, ports, IPs, BGP/VLAN |
| 🔥 | **Fire & Safety** | Fire suppression, sensors, compliance |
| 📦 | **Inventory** | Hardware, links, IP and change management |
| 🌐 | **Topology** | Interactive 2D network map |
| ⚙️ | **Servers** | Register and configure data-center servers |
| 🔧 | **Settings** | Theme, time range, license info |

The footer shows **FWDCIM Enterprise · v1.0.0**.

### The top bar
- **Title** — "Data Center Infrastructure Management".
- **🔔 Notifications** — a red/yellow badge shows the number of unresolved alerts. Click it to see the latest 6 alerts (severity, message, device, time) and a **View all alerts** link.
- **👤 Profile** — your avatar (your initial). Click it to see your username/email and a **Sign out** button.

---

## 3. The Pages

### 🟦 Dashboard
Your at-a-glance health view. Shows summary cards for **Networks**, **Total Devices** (online/offline), and **Active Alerts** (with critical count).

Sections:
- **Datacenter Health** — one row per data center with online/offline counts and a status bar. *Click a row to jump to its topology.*
- **Recent Alerts** — the latest unresolved alerts; **View All** opens the Alerts page.
- **Device Distribution** — overall reachability plus a per-network breakdown.
- **Recently Active Devices** — the most recently seen devices with status and "last seen" time.

---

### 🖥️ Devices
A searchable, filterable table of every monitored device.

**Columns:** Server · Unique ID · Hostname · Type (PDU, UPS, Sensor, Server, Switch…) · IP Address · Status · Last Seen · Metrics count · Alerts.

**Filters:** by Server, by Status (Online/Offline), by Type, and a free-text **Search** (hostname, ID, IP). **Clear Filters** resets them. The list loads more rows as you scroll.

**Actions:** Click a device's **Unique ID**, or the **Analytics** button, to drill in.

#### Device Detail
Full telemetry for one device — header with hostname, type badge, ID, and info cards (Server, IP, Status, Group). Shows **latest metrics** from **gNMI** and **sFlow** protocols, with detailed tables for telemetry values and interface traffic. Use **View Analytics** for history.

#### Device Analytics
Historical performance. **Quick stats** (total metrics, active alerts, last seen, metric types), **System Health** radial gauges (CPU %, Memory %, Disk %, color-coded), **performance charts** for the last hour, **24-hour trend** lines, and **SNMP trap activity** (hourly bar chart + recent traps list).

---

### ⚠️ Alerts
Manage alerts, deduplicated by agent + metric + severity so you don't see the same thing repeated.

- Header shows total **active** and **critical** counts.
- **Filters:** by Agent and by Severity (CRITICAL / WARNING / INFO).
- Each row shows time, device, message, severity badge, **first seen**, and **occurrence count**. *Click a row to expand its full history.*
- The list auto-refreshes every few seconds.

---

### 🎫 Tickets
Incident tracking. Header stats show **Open**, **In Progress**, and **Resolved** counts.

- **Filters:** by Status (Open/Acknowledged/In Progress/Resolved/Closed), by Priority (P1–P4), and Search.
- **+ Create Ticket** to log a new one, or use **Generate Ticket from Critical Alert** to auto-create one from an unhandled critical alert.
- The table shows ID, Title, Status, Priority, Assigned To, Category (power/cooling/network/compliance/security), and timestamps.
- *Click **Details*** to open a ticket: read the full description, see the activity log, change status, reassign to a team (e.g. NOC Team, Facilities, Power & Cooling), and add comments.

---

### ⚡ Power Mgmt
Power infrastructure monitoring, organized in tabs:
- **Overview** — UPS status & battery %, PDU load, generator status at a glance.
- **UPS** — charger/rectifier/battery status, temperature, phase status, live metrics.
- **PDU** — per-PDU load gauges, current (A), apparent power (VA), frequency, power factor, phase imbalance.
- **Generator** — run status, fuel level, load, and a **test schedule** (use **+ Schedule Test**).
- **Energy** — kWh trend chart, cost projection, per-device breakdown.
- **Forecast** — predictive consumption and cost, peak-hour indications.

---

### 📡 Network Ops
Network operations across tabs:
- **Inventory** — switches/routers/firewalls with model, vendor, location, up/down status, uptime.
- **Ports** — interface status and in/out traffic rates.
- **Patch** — patch-panel and cabling layout per rack.
- **Changes** — network change requests and maintenance windows.
- **IPAM** — subnets, gateways, VLANs, allocated vs available IPs.
- **BGP/VLAN** — BGP peers (state, prefixes) and VLAN configuration.

---

### 🔥 Fire & Safety
Fire protection and compliance, in tabs:
- **Tests** — suppression-system tests (VESDA, FM200, Sprinkler, CO2, Novec1230) with status and certificates; **+ Add Test**.
- **Sensors** — heat/smoke/water-leak/CO2 sensors on a floor plan with live status.
- **EPA** — suppression-agent inventory (current vs capacity kg, service dates, charge log).
- **Egress** — emergency exit routes and evacuation checklist.
- **Maintenance** — regulatory schedule (e.g. NFPA) with due dates and owners.
- **Postmortem** — incident logs with timeline, root cause, and corrective actions.
- **3D** — a 3D building view showing sensor positions.

---

### 📦 Inventory
Hardware and resource tracking, in tabs:
- **Devices** — full hardware list (vendor, model, serial, location, CPU/RAM/storage, power, ports) with status (idle/in use/maintenance/decommissioned); **+ Add Device**.
- **Ports** — physical/logical ports per device.
- **Links** — cabling between devices (ethernet/fiber/DAC/InfiniBand) with status.
- **Changes** — change-control records with approval workflow.
- **IPAM** — subnets, VLANs, and assigned IPs.
- **BGP/VLAN** — routing peers and VLAN definitions.

---

### 🌐 Topology
An interactive 2D map of your network. Nodes are devices, edges are links; cooling loops animate as flowing water. Green = online, red = offline, yellow = warning, with alert badges on affected nodes.

**You can:** search by hostname/IP, zoom and pan, **fit to screen**, and **click a node** for details that link to its Device Detail. Add `?network=<id>` to focus one data center (the Dashboard does this for you).

> **Two more topology views** (reachable via links/deep URLs):
> - **Topology 3D** — an immersive 3D scene with a **temperature heatmap** (blue = cool, amber = warning, red = hot), orbit controls, live link-status updates, and a trap feed.
> - **Topology Editor** — design or edit networks by hand: drag to place nodes, draw links, set properties, apply layouts (force/star/grid/tree…), and **save**, **download**, or **upload** a topology.

---

### ⚙️ Servers
Register the data-center servers FWDCIM pulls data from. The list shows each server's name, URL, enabled toggle, location, environment (prod/staging/dev), and health.

**+ Add Server** opens a form for name, management URL, environment, location, color, and **certificate uploads** (CA cert, client cert, client key) for secure (mTLS) connections. Use **Test Connection** to confirm reachability before saving.

---

### 🔧 Settings
Personal preferences and system info:
- **Appearance** — toggle **Dark / Light** theme.
- **Default Time Range** — pick the default window for charts: 5m, 1h, 6h, 24h, 7d, 30d.
- **License Information** — status, max agents, expiry (read-only).
- **API Configuration** — the API and live-events (SSE) endpoints (read-only).

---

### Other tools
- **AI Analytics** — predictive forecasts (CPU/memory/disk/temperature, anomaly timeline). *Some charts are still in development.*
- **Natural Language Query** — describe infrastructure in plain English and generate **Terraform** / **Terragrunt** configs to download.

---

## 4. Tips & Common Tasks

| I want to… | Go to |
|---|---|
| See if anything is wrong right now | **Dashboard** → Active Alerts card, or the 🔔 bell |
| Find one specific device | **Devices** → Search box |
| Investigate why a device is unhealthy | **Devices** → device → **Analytics** |
| Acknowledge/track an incident | **Alerts** → create or open a **Ticket** |
| Check power/cooling health | **Power Mgmt** |
| See how the network is wired | **Topology** (2D) or **Topology 3D** (heatmap) |
| Confirm fire-safety compliance | **Fire & Safety** → Maintenance / Tests |
| Switch to light mode | **Settings** → Appearance |
| Sign out | 👤 profile menu (top right) → **Sign out** |

### Good to know
- **Live updates:** alerts, metrics, and topology refresh automatically — no need to reload the page.
- **Color coding is consistent:** green = healthy/online, yellow = warning, red = critical/offline.
- **Drill down by clicking:** dashboard cards, datacenter rows, device IDs, and topology nodes are all clickable shortcuts into detail screens.
