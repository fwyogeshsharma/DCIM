# DCIM UI — Feature List

Data Center Infrastructure Management platform. Feature inventory of everything built so far.

---

## 1. Monitoring & Operations

### Dashboard (`/app/dashboard`)
- Real-time status cards: networks, total/online/offline devices, active & critical alerts
- Network health with per-network online/offline bar charts
- Recent alerts panel with severity badges
- Device distribution / reachability breakdown per network
- Recently active devices list with last-seen timestamps

### Agents (`/app/agents`)
- Filterable agent table — search by hostname / ID / IP; filter by server, status, device type
- Per-row: server, agent ID, hostname, device-type badge, IP, status, last seen, metric & alert counts
- Server-down health indicator; power devices highlighted
- Per-agent link to analytics

### Agent Detail (`/app/agents/:agentId`)
- Agent info cards (server, IP, status, group) + power-device callout
- Latest metrics grid (value + unit per metric)
- gNMI telemetry table (timestamp, path, sample values, last 20)
- sFlow interface events table (interface up/down, in/out Mbps, last 30)

### Agent Analytics (`/app/agents/:agentId/analytics`)
- Quick stats: total metrics, active alerts, last seen, metric-type count
- System health radial gauges (CPU, memory, disk) — current / avg / range
- Performance charts: last hour (area) and 24-hour trends (line) per metric type
- SNMP trap activity (24h) stacked bar by severity + recent traps list
- All-metric-types summary table

---

## 2. Alerting & Ticketing

### Alerts (`/app/alerts`)
- Per-agent alert count cards (click to filter) — critical / warning / info
- Filterable alerts table with infinite scroll & auto-refresh (5s)
- Expandable rows: recurrence duration, first/latest seen, total occurrences
- Deduplication consolidated by agent + metric + severity

### Tickets (`/app/tickets`)
- Ticket stat cards: open, unassigned, P1 open, resolved
- "Critical alerts awaiting tickets" with one-click ticket generation
- Filterable table: number, priority, status, title, assignee, created date
- Ticket detail modal: status / assignee / resolution controls, activity timeline, comments

---

## 3. Power Management (`/app/reports`)
Tabs: PUE/DCiE · UPS Health · PDU Circuits · Generator Tests · Energy Cost · AI Forecast — with time-range selector (24h/7d/30d) and CSV export.

- **Overview** — facility power input, PUE/DCiE KPIs with rating bar, per-server power/temp/efficiency cards, power trend chart
- **UPS Health** — per-UPS battery % + estimated runtime, power/voltage/current, load trend
- **PDU Circuits** — circuit load bar (% of rated capacity), electrical readings, overload alerts, power trend
  - **Energy Monitors** — `energy_monitor`-role devices surfaced here (hidden from topology); avg/peak power, voltage, current, trend
- **Generator Tests** — schedule tests, pass/fail tracking, test log cards
- **Energy Cost** — monthly/annual cost KPIs, per-server cost trend
- **AI Forecast** — 14-day power forecast with confidence bounds

---

## 4. Network Operations (`/app/network-ops`)
Tabs: Topology · Port Utilisation · Patch Panel · Change Mgmt · IPAM · BGP/VLAN

- **Topology** — device-type summary cards, device inventory table, L1 links (LLDP/CDP/ARP)
- **Port Utilisation** — per-port in/out traffic charts, error counts
- **Patch Panel** — add panels, editable port grid (label, connected-to, notes)
- **Change Management** — change request workflow (draft → pending → approved → implemented/rejected) with approve/reject + comments
- **IPAM** — subnet management, CIDR occupancy bars, IP search, auto-discovered IP cloud
- **BGP/VLAN** — VLAN cards, BGP peer state (AS, prefixes rx/tx, uptime)

---

## 5. Topology Visualization

### 2D Topology (`/app/topology`)
- D3 force-directed, role-layered graph; nodes colored by role, sized by degree
- Online/offline state, alert badges, active-link styling with speed labels
- Zoom / pan / fit, drag-to-pin, search/filter
- Real-time SNMP trap feed (SSE)
- Excludes `energy_monitor` devices (handled in Power Management)

### 3D Topology (`/app/topology-3d`)
- Three.js hierarchical scene (floors → racks → devices), orbit controls
- Floor selector, search/filter
- Real-time link state via SSE (down breaks edges, up restores), trap feed
- Excludes `energy_monitor` devices

### Topology Editor (`/app/topology-editor`)
- Manual builder with multiple layout algorithms (force, star, chain, hierarchy, circle, concentric, grid, tree)
- Add/edit nodes & links with full device specs (incl. PDU outlet config)
- Context menus, drag-to-position, force-simulation sliders
- Export/import topology as JSON

---

## 6. Inventory (`/app/inventory`)
- Summary cards: total devices, idle/available, maintenance, free storage, available RAM, links
- Sync from live monitoring
- **Devices tab** — search/filter, table (vendor/model, location, status, CPU/RAM/storage utilization), analytics side panel with live metrics
- **Links tab** — source/destination ports, link type, speed, active status
- Add/Edit Device modal (basic info + specs incl. custom key-value pairs); Add/Edit Link modal

---

## 7. Fire & Safety (`/app/fire-safety`)
Tabs: Suppression Tests · Sensor Board · EPA Compliance · Egress Map · Maintenance · Incidents · 3D Facility

- **Suppression Tests** — schedule, pass/fail logging
- **Sensor Board** — floor-plan SVG with live sensor status (normal/alarm/fault/offline)
- **EPA Compliance** — suppression agent charge tracking with fill-level logs
- **Egress Map** — evacuation drills, zone head-count accountability, assembly/exit markers
- **Maintenance** — regulatory maintenance scheduling with due-date tracking
- **Incidents** — incident logging with timeline, root cause, post-mortem export
- **3D Facility** — 3D sensor/device visualization

---

## 8. AI & Automation

### AI Analytics (`/app/ai-analytics`)
- Predictive forecast views (CPU, memory, disk, temperature) and anomaly timeline

### Natural Language Query (`/app/nl-query`)
- Chat-style NL → Infrastructure-as-Code
- Generates downloadable Terraform & Terragrunt configs

---

## 9. Administration

### Server Management (`/app/servers`)
- Multi-server registry: add/edit/delete, enable toggle, location/environment/color
- SSL cert upload (CA / client cert / key), test-connection per server

### Settings (`/app/settings`)
- Theme (dark/light), default time range, license info

---

## 10. Simulator (dev/test)

### Simulator Control (`/app/simulator-control`)
- Container status (docker, device count, gNMI/sFlow), protocol start/stop
- Scenario selection, fault & trap injection, container log viewer

### Simulator Topology (`/app/simulator-topology`)
- SVG network diagram, real-time trap feed
- Point-and-click trap injection (type & severity selectors)

---

## Cross-Cutting Capabilities
- **Real-time (SSE)** — trap feeds, alerts, topology link state, simulator status
- **Metrics pipeline** — per-agent/per-type queries with time-range filtering and hourly aggregation
- **AI** — power forecasting, anomaly detection, NL→IaC generation
- **Device auto-discovery** — SNMP walk + LLDP/CDP/ARP; agent auto-registration with cert approval
- **Alert deduplication** — by agent + metric + severity, with occurrence/duration tracking
- **Inventory ↔ monitoring sync** — live monitoring status reflected in inventory
- **gNMI + sFlow telemetry** — per-device protocol collection stats
- **Ticket lifecycle** — alert-driven generation, status workflow, assignment, audit trail
- **Multi-server federation** — aggregates across multiple DCIM servers
- **Power/thermal modeling** — PUE/DCiE, UPS runtime, energy cost
- **Fire-safety compliance** — charge tracking, drills, incident post-mortems
- **3D facility visualization** — spatial floor/rack/device layout with live state
