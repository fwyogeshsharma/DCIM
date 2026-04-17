import type { ServerConfig, Agent, SNMPDevice, TopologyLink } from './types'

// ── Mock Topology: 144-node Clos fabric ──────────────────────────────────────
// Hand-crafted to mirror the reference NetworkX diagram. Six device tiers:
//
//   L0 (4)   Cluster-R1 · Cluster-R2 · LB-1 · LB-2         (core + edge servers)
//   L1 (2)   Fabric-GW1 · Fabric-GW2                        (spine)
//   L2 (4)   Pod-GW1..4                                     (super-spine / pod gw)
//   L3 (8)   ToR-P1..8                                      (leaf / top-of-rack)
//   L4 (120) Compute-1..120                                 (compute rack)
//   L5 (6)   GPU-1..3 · Storage-1..3                        (specialty hosts)
//
// Total = 144 SNMP devices. One synthetic server + one synthetic agent wrap
// them (required by the data model) and land on layout layers 0/1 above the
// fabric. topology_links carry `source_depth`/`target_depth` so the layered
// layout in Topology.tsx naturally assigns each role to its own row.
//
// Edge pattern is a fat-tree Clos:
//   Cluster-R ↔ Fabric-GW   — full mesh (2×2 = 4)
//   Fabric-GW ↔ Pod-GW      — full mesh (2×4 = 8)
//   Pod-GW ↔ ToR-P          — each ToR dual-homes to 2 adjacent pods
//   ToR-P ↔ Compute         — 15 computes per ToR (8×15 = 120)
//   Compute-edge ↔ GPU/Stg  — the first 6 computes fan out to specialties
// plus:
//   Cluster-R1 ↔ Cluster-R2 — peer sync (same-layer, rendered as cross-link)
//   LB-1 ↔ Cluster-R1, LB-2 ↔ Cluster-R2 — LB attachments
//
// The topology is fully deterministic so the diagram stays stable across
// reloads (important for demos and visual regression).

const SERVER_ID = 'mock-dc-server'
const SERVER_NAME = 'DC-Core'
const AGENT_ID = 'mock-dc-agent'
const AGENT_HOST = 'dc-core-agent'

function iso(secondsAgo = 0): string {
  return new Date(Date.now() - secondsAgo * 1000).toISOString()
}

interface DeviceSpec {
  name: string
  ip: string
  depth: number
  role: 'router' | 'lb' | 'switch' | 'compute' | 'gpu' | 'storage'
}

function buildSpecs(): DeviceSpec[] {
  const specs: DeviceSpec[] = []

  // L0 — 4 top-of-fabric nodes. Depth 0 in the walker's BFS.
  specs.push(
    { name: 'Cluster-R1', ip: '10.0.0.1', depth: 0, role: 'router' },
    { name: 'Cluster-R2', ip: '10.0.0.2', depth: 0, role: 'router' },
    { name: 'LB-1',       ip: '10.0.0.3', depth: 0, role: 'lb' },
    { name: 'LB-2',       ip: '10.0.0.4', depth: 0, role: 'lb' },
  )

  // L1 — 2 spine / fabric gateways.
  specs.push(
    { name: 'Fabric-GW1', ip: '10.1.0.1', depth: 1, role: 'switch' },
    { name: 'Fabric-GW2', ip: '10.1.0.2', depth: 1, role: 'switch' },
  )

  // L2 — 4 pod gateways.
  for (let i = 1; i <= 4; i++) {
    specs.push({ name: `Pod-GW${i}`, ip: `10.2.0.${i}`, depth: 2, role: 'switch' })
  }

  // L3 — 8 top-of-rack switches.
  for (let i = 1; i <= 8; i++) {
    specs.push({ name: `ToR-P${i}`, ip: `10.3.0.${i}`, depth: 3, role: 'switch' })
  }

  // L4 — 120 compute servers, 15 per ToR. IPs span /16 to stay unique.
  for (let i = 1; i <= 120; i++) {
    const a = Math.floor((i - 1) / 250)
    const b = ((i - 1) % 250) + 1
    specs.push({ name: `Compute-${i}`, ip: `10.4.${a}.${b}`, depth: 4, role: 'compute' })
  }

  // L5 — 6 specialty servers hanging off the compute tier.
  specs.push(
    { name: 'GPU-1',     ip: '10.5.0.1', depth: 5, role: 'gpu' },
    { name: 'GPU-2',     ip: '10.5.0.2', depth: 5, role: 'gpu' },
    { name: 'GPU-3',     ip: '10.5.0.3', depth: 5, role: 'gpu' },
    { name: 'Storage-1', ip: '10.5.0.4', depth: 5, role: 'storage' },
    { name: 'Storage-2', ip: '10.5.0.5', depth: 5, role: 'storage' },
    { name: 'Storage-3', ip: '10.5.0.6', depth: 5, role: 'storage' },
  )

  return specs
}

// ── Seed a couple of offline nodes so the status rendering is exercised ─────
// Pick names rather than indices so the offline set stays stable when the
// tier counts change.
const OFFLINE_NAMES = new Set<string>([
  'Compute-57',  // mid-rack host down
  'Compute-103', // different rack, different ToR
  'ToR-P6',      // a whole rack segment offline
])

function isOffline(spec: DeviceSpec): boolean {
  return OFFLINE_NAMES.has(spec.name)
}

function specToDevice(spec: DeviceSpec): SNMPDevice {
  return {
    device_name: spec.name,
    device_ip: spec.ip,
    agent_id: AGENT_ID,
    server_id: SERVER_ID,
    server_name: SERVER_NAME,
    // Offline devices look stale (> 2 hours ago); live ones look fresh.
    last_seen: isOffline(spec) ? iso(4 * 60 * 60) : iso(30),
  }
}

function buildLinks(specs: DeviceSpec[]): TopologyLink[] {
  const byName = new Map(specs.map(s => [s.name, s]))
  const get = (name: string): DeviceSpec => {
    const s = byName.get(name)
    if (!s) throw new Error(`Missing spec: ${name}`)
    return s
  }

  const links: TopologyLink[] = []
  let portCounter = 1
  const addLink = (src: DeviceSpec, tgt: DeviceSpec): void => {
    const fresh = !(isOffline(src) || isOffline(tgt))
    links.push({
      server_id: SERVER_ID,
      server_name: SERVER_NAME,
      source_ip: src.ip,
      source_name: src.name,
      source_depth: src.depth,
      source_port: portCounter++,
      target_ip: tgt.ip,
      target_name: tgt.name,
      target_depth: tgt.depth,
      target_port: `Eth1/${portCounter}`,
      last_seen: fresh ? iso(30) : iso(4 * 60 * 60),
    })
  }

  // Same-layer peer link between the two cluster routers.
  addLink(get('Cluster-R1'), get('Cluster-R2'))

  // LBs homed to their nearest cluster router (same layer — appears as a
  // horizontal tap in the diagram).
  addLink(get('LB-1'), get('Cluster-R1'))
  addLink(get('LB-2'), get('Cluster-R2'))

  // Cluster-R ↔ Fabric-GW: full mesh (2 × 2).
  for (const r of ['Cluster-R1', 'Cluster-R2']) {
    for (const f of ['Fabric-GW1', 'Fabric-GW2']) {
      addLink(get(r), get(f))
    }
  }

  // Fabric-GW ↔ Pod-GW: full mesh (2 × 4).
  for (const f of ['Fabric-GW1', 'Fabric-GW2']) {
    for (let p = 1; p <= 4; p++) {
      addLink(get(f), get(`Pod-GW${p}`))
    }
  }

  // Pod-GW ↔ ToR-P: each ToR dual-homes to 2 adjacent pods so the lattice
  // shows crossing lines (like the reference image) rather than 4 disjoint
  // trees.
  for (let t = 1; t <= 8; t++) {
    const podA = ((t - 1) % 4) + 1
    const podB = (podA % 4) + 1
    addLink(get(`Pod-GW${podA}`), get(`ToR-P${t}`))
    addLink(get(`Pod-GW${podB}`), get(`ToR-P${t}`))
  }

  // ToR-P ↔ Compute: 15 compute hosts per ToR (8 × 15 = 120).
  for (let c = 1; c <= 120; c++) {
    const torIdx = Math.floor((c - 1) / 15) + 1
    addLink(get(`ToR-P${torIdx}`), get(`Compute-${c}`))
  }

  // Compute ↔ Specialty: specialty hosts tap the first 6 computes (matches
  // the reference image where GPU/Storage hang off the edge of the compute
  // row rather than connecting to ToRs directly).
  const specialties = ['GPU-1', 'GPU-2', 'GPU-3', 'Storage-1', 'Storage-2', 'Storage-3']
  specialties.forEach((s, i) => {
    addLink(get(`Compute-${i + 1}`), get(s))
  })

  return links
}

// ── Public API ──────────────────────────────────────────────────────────────

export interface MockTopologyData {
  servers: ServerConfig[]
  agents: Agent[]
  snmpDevices: SNMPDevice[]
  topologyLinks: TopologyLink[]
}

let _cached: MockTopologyData | null = null

export function getMockTopologyData(): MockTopologyData {
  if (_cached) return _cached

  const specs = buildSpecs()

  const server: ServerConfig = {
    id: SERVER_ID,
    name: SERVER_NAME,
    url: 'https://dcim-core.internal:8443',
    enabled: true,
    auth_type: 'certificate',
    metadata: {
      location: 'US-East-1',
      environment: 'production',
      color: '#8b5cf6',
    },
    health: {
      status: 'healthy',
      responseTime: 42,
      timestamp: iso(0),
    },
    created_at: iso(60 * 60 * 24 * 30),
    updated_at: iso(0),
  }

  const agent: Agent = {
    id: 1,
    agent_id: AGENT_ID,
    server_id: SERVER_ID,
    server_name: SERVER_NAME,
    server_url: server.url,
    certificate_cn: `${AGENT_HOST}.internal`,
    hostname: AGENT_HOST,
    ip_address: '10.0.0.100',
    status: 'online',
    group: 'production',
    last_seen: iso(15),
    first_seen: iso(60 * 60 * 24 * 30),
    registered_at: iso(60 * 60 * 24 * 30),
    approved_at: iso(60 * 60 * 24 * 29),
    approved: true,
    total_metrics: 4231,
    total_alerts: 3,
    created_at: iso(60 * 60 * 24 * 30),
    updated_at: iso(0),
  }

  const snmpDevices = specs.map(specToDevice)
  const topologyLinks = buildLinks(specs)

  _cached = {
    servers: [server],
    agents: [agent],
    snmpDevices,
    topologyLinks,
  }
  return _cached
}
