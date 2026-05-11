# DCIM Enterprise — Client Demo Script

---

## OPENING (2 min)

> "Thank you for joining us today. What I'm going to walk you through is the **DCIM Enterprise Platform** — a full-stack, production-ready system we've built to give your teams unified, real-time visibility across your entire data center infrastructure."

> "By the end of this demo you'll see how a single dashboard can aggregate data from hundreds of distributed servers, track thousands of monitoring agents, surface alerts as they happen, visualize your physical and logical network topology — and even generate infrastructure-as-code from plain English."

---

## SECTION 1 — LANDING PAGE (2 min)

**Navigate to:** `/` (root / marketing landing page)

> "This is the public-facing landing page. We built it with React and Three.js — notice the 3D model in the hero section. It's a live WebGL scene, not a static image."

**Point out:**
- Hero section with animated 3D model
- Feature breakdown cards (monitoring, topology, AI)
- 'Sign In' button → leads to the live application

> "From here a user signs in and lands on the main dashboard."

---

## SECTION 2 — DASHBOARD (3 min)

**Navigate to:** `/app/dashboard`

> "The dashboard gives you the command-center view — everything at a glance."

**Walk through each card:**

- **Servers card** — "This shows how many servers are currently healthy vs. total registered. Green means the aggregator can reach the server and it's responding correctly. A yellow or red state means it's unreachable or has a TLS handshake error — we surface that immediately."

- **Agents card** — "Agents are the lightweight monitoring processes running on individual machines. We show how many are online right now vs. offline. This updates every 5 seconds."

- **Alerts card** — "Active alerts across the whole estate — we break them down by severity: Critical, Warning, and Info."

**Scroll down:**

- **Server health summary** — "Each server has a colour-coded health pill. One row per server — you can see at a glance which sites are healthy and which need attention."

- **Agents by server** — "Drills one level deeper — so for each server you see how many agents are online vs. total enrolled."

- **Recent alerts list** — "The five most recent alerts across all servers, with timestamp and severity."

- **Recently active agents** — "Which agents phoned home most recently."

> "Everything on this page auto-refreshes every 5 seconds using Server-Sent Events — no manual reload needed."

---

## SECTION 3 — AGENTS PAGE (3 min)

**Navigate to:** `/app/agents`

> "This is the full agent inventory. Think of it as a live CMDB for your monitoring endpoints."

**Walk through features:**

- **Search bar** — "Filter by hostname, IP address, or agent ID — full text search."
- **Server filter** — "Scope the view to a specific site or server cluster."
- **Status filter** — "Show only online, only offline, or all."

> "Each row shows the agent's hostname, IP, group, when it was last seen, how many metrics it's reporting, and how many open alerts it has. Click any agent and you go to its detail view — historical metrics charted over time and its alert history."

---

## SECTION 4 — ALERTS PAGE (2 min)

**Navigate to:** `/app/alerts`

> "The alerts page is the operations team's home screen."

**Walk through features:**

- Severity filter (Critical / Warning / Info)
- Agent filter — drill to one machine
- Pagination — 50 alerts per page so the list never overwhelms
- Expandable rows — click an alert to see full detail: threshold value, actual value, timestamp, message

> "Alerts auto-refresh every 5 seconds. When an alert gets resolved on the source server, it drops off the active list automatically."

---

## SECTION 5 — TOPOLOGY (2D) (3 min)

**Navigate to:** `/app/topology`

> "This is where it gets visually interesting. We built a D3.js force-directed graph that maps every server, agent, and network device and the connections between them."

**Demonstrate:**

- **Zoom and pan** — "The graph is fully interactive. Scroll to zoom, drag to pan."
- **Node types** — "Servers are the larger nodes. Under each server you see its agents. SNMP devices sit at the edge — switches, routers, storage."
- **Collapse / expand a server** — "Click a server node to collapse all its agents into it — useful when you want to focus on a specific site."
- **Time filter** — "You can scope the view to the last 24 hours, last 30 days, or all-time — so you see only devices that were active in that window."
- **Node tooltip** — "Hover or click a node to see its IP, status, last seen time."
- **Legend** — "Bottom-right legend explains node types and link colours."

> "The underlying link data is synced from the actual SNMP topology walk — so this is not a manually-drawn diagram. It reflects what the network actually looks like."

---

## SECTION 6 — TOPOLOGY (3D) (2 min)

**Navigate to:** `/app/topology-3d`

> "For clients who want the data-centre 'rack view', we built a Three.js 3D visualisation."

**Demonstrate:**

- Orbit with mouse drag — "Rotate, zoom, and pan the 3D scene."
- Rack cabinets — "Each rack represents one server. The slots inside represent agents assigned to it."
- LED indicators — "Green LED = agent online. Red = offline. At a glance you can see which slots are dark."
- Server expand/collapse — "Same concept as the 2D view — expand a rack to see individual agent slots."

> "This gives ops teams, and especially executives, an intuitive physical representation of the estate."

---

## SECTION 7 — SERVER MANAGEMENT (3 min)

**Navigate to:** `/app/servers`

> "This is the admin panel for connecting new DCIM servers to the aggregator."

**Walk through:**

- **Server list** — each row shows name, URL, environment (prod/staging), location, and live connection status
- **Add server form** — "Name, URL, environment tag, location. We also support colour-coding per server so it's easy to distinguish sites on the dashboard."
- **mTLS certificate upload** — "For secure deployments, servers can require mutual TLS authentication. We support uploading a CA certificate, a client certificate, and a client private key — all stored securely."
- **Test connection button** — "Before saving, you can test the connection live. The aggregator will attempt a handshake and report back whether it succeeded, failed, or hit a TLS error."
- **Enable / disable toggle** — "Disable a server without removing it — useful during maintenance windows."

---

## SECTION 8 — NATURAL LANGUAGE QUERY / AI FEATURES (3 min)

**Navigate to:** `/app/nlquery`

> "This is our AI-powered infrastructure assistant. Instead of writing Terraform by hand, you describe what you need in plain English and the system generates the code for you."

**Demo:**

1. Type: *"Create an AWS VPC with two public subnets and an internet gateway"*
2. Show generated Terraform / Terragrunt output
3. Show download button — "You can download the generated `.tf` file directly."

> "Under the hood this calls an LLM endpoint. The model understands infrastructure terminology and produces validated, production-ready HCL code."

**Navigate to:** `/app/analytics`

> "The AI Analytics page is our predictive layer. It forecasts CPU, memory, disk, and temperature trends based on historical data — and surfaces anomalies with root-cause analysis. This is currently showing projected data; as more historical metrics accumulate it becomes increasingly accurate."

---

## SECTION 9 — BACKEND: THE AGGREGATOR (3 min)

> "Now let me briefly explain what's running behind the scenes."

> "The backend is a **Node.js / TypeScript** service called the **DCIM Aggregator**. It sits between the UI and your existing DCIM servers."

**Key responsibilities:**

| Component | What it does |
|-----------|-------------|
| **Express REST API** | Serves the frontend — 30+ endpoints for servers, agents, metrics, alerts, topology, dashboard stats |
| **PostgreSQL + TimescaleDB** | Stores all data. TimescaleDB is a time-series extension — it automatically partitions metric tables by time, compresses old data, and runs continuous aggregates. This means historical queries stay fast even with billions of metric rows. |
| **Redis cache** | Caches server health status and hot data to reduce database load |
| **Background workers (node-cron)** | Six scheduled workers keep everything in sync — see next section |

---

## SECTION 10 — BACKGROUND WORKERS / AGENTS (2 min)

> "The aggregator runs six background workers that continuously pull data from your DCIM servers:"

| Worker | Interval | What it syncs |
|--------|----------|--------------|
| **MetricsSync** | Every 10 seconds | CPU, memory, disk, temperature metrics from all servers |
| **AgentsSync** | Every 30 seconds | Agent inventory — enrolls new agents, updates last_seen |
| **AlertsSync** | Every 15 seconds | Active and resolved alerts |
| **HealthMonitor** | Every 30 seconds | Connectivity check per server — marks healthy / offline / TLS error |
| **TrapSync** | Background | SNMP trap/notification events |
| **TopologyLinksSync** | Background | Device-to-device link data for topology visualisation |

> "These workers run completely autonomously. The UI is just reading from the database — there are no blocking API calls to individual servers at page load time. This means the UI stays fast regardless of how many servers are connected."

---

## SECTION 11 — TECHNOLOGY STACK SUMMARY (1 min)

> "Let me quickly summarise the stack we chose and why:"

### Frontend
| Technology | Purpose |
|-----------|---------|
| **React 19 + TypeScript** | Component framework with full type safety |
| **Vite** | Fast build tool |
| **Tailwind CSS + shadcn/ui** | Design system — consistent, accessible UI components |
| **TanStack React Query** | Server state — caching, background refetching, stale-while-revalidate |
| **Zustand** | Lightweight client state (theme, sidebar, filters) |
| **D3.js** | 2D network topology graph |
| **Three.js + @react-three/fiber** | 3D rack visualisation and landing page model |
| **Recharts** | Charts and sparklines on metrics/analytics pages |
| **Framer Motion** | Smooth page and component animations |

### Backend
| Technology | Purpose |
|-----------|---------|
| **Node.js + Express + TypeScript** | REST API server |
| **PostgreSQL 15** | Relational database |
| **TimescaleDB** | Time-series extension — hypertables, compression, continuous aggregates |
| **Redis** | In-memory cache for health checks and hot data |
| **node-cron** | Background worker scheduling |
| **Winston** | Structured logging |
| **Docker Compose** | Local and production deployment — PostgreSQL, Redis, pgAdmin, Aggregator all containerised |

---

## CLOSING (1 min)

> "To summarise what we've delivered:"

1. **Multi-server aggregation** — one pane of glass for your entire DCIM estate
2. **Real-time monitoring** — agents, metrics, and alerts updated every few seconds
3. **Interactive topology** — both 2D force-directed and 3D rack views
4. **AI features** — natural language infrastructure code generation and predictive analytics
5. **Secure by design** — mTLS certificate management, CORS protection, agent approval workflow
6. **Scalable data layer** — TimescaleDB handles billions of time-series rows without query degradation
7. **Fully containerised** — deploy the whole stack with a single `docker-compose up`

> "The system is production-ready and extensible. We can add new server connectors, additional metric types, new visualisation modes, or deeper AI integrations as your requirements evolve."

> "Happy to take any questions or do a deeper dive into any specific area."

---

*Demo script prepared for DCIM Enterprise client presentation — 2026-04-23*
