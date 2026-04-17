import type { Agent, ServerConfig, SNMPDevice, TopologyLink } from './types'
import { computeLayeredLayout } from './topology-layered'

export interface LayoutNode {
  id: string
  name: string
  type: 'server' | 'agent' | 'network'
  status: 'online' | 'offline'
  position: [number, number, number]
  color: string
  ip?: string
  serverId?: string
  agentId?: string
  serverName?: string
  agentName?: string
  metrics?: number
  alerts?: number
  lastSeen?: string
}

export interface D2DInfo {
  sourceIp: string
  sourceName: string
  sourcePort?: number
  targetIp: string
  targetName: string
  targetPort?: string
  lastSeen: string
  sourceStatus?: 'online' | 'offline'
  targetStatus?: 'online' | 'offline'
}

export interface LayoutLink {
  sourceId: string
  targetId: string
  sourcePos: [number, number, number]
  targetPos: [number, number, number]
  connected: boolean
  linkType?: 'agent-server' | 'device-agent' | 'device-device'
  d2dInfo?: D2DInfo
}

export interface LayoutResult {
  nodes: LayoutNode[]
  links: LayoutLink[]
}

// ── Role-based layer assignment (mirrors Topology.tsx) ──────────────────────
// Every node is pinned to a specific Y plane so the 3D view reads top-to-
// bottom as the same six-tier Clos hierarchy as the 2D topology:
//
//   0  server
//   1  agent
//   2  routers / LBs
//   3  fabric / spine switches
//   4  pod / aggregation switches
//   5  ToR / leaf switches
//   6  compute servers
//   7  storage / GPU / specialty servers
//
// Unknown devices fall back to `2 + BFS depth` so they still slot into a
// sensible tier based on graph distance from the seed.
function roleLayerOf(name: string): number | null {
  if (!name) return null
  if (/\brouter\b|\brtr\b|cluster-?r\b|^r[-\d]|core-?r\b/i.test(name)) return 2
  if (/\blb\b|load-?balancer/i.test(name)) return 2
  if (/fabric|spine|super-?spine/i.test(name)) return 3
  if (/\bpod\b|aggregation|\bagg\b/i.test(name)) return 4
  if (/\btor\b|top-?of-?rack|\bleaf\b/i.test(name)) return 5
  if (/gpu|storage|\bnas\b|\bsan\b/i.test(name)) return 7
  if (/compute|host|\bsrv?\b|^server\b/i.test(name)) return 6
  return null
}

// Spacing tuned for the 3D renderer — each unit is roughly one THREE.js
// world unit, matching the rack/icon scale used by the Topology3D page.
const LAYER_GAP_Y = 28 // vertical distance between layers
const NODE_GAP_X = 14 // horizontal distance between siblings on a layer
const TWO_HOURS_MS = 2 * 60 * 60 * 1000
// When a layer has more than this many nodes (e.g. 120 compute hosts), fan
// them onto multiple rows in the XZ plane instead of one absurdly wide
// horizontal strip. Keeps the 3D camera path usable.
const WIDE_LAYER_THRESHOLD = 40
const WIDE_LAYER_ROW_DEPTH = 14 // Z step between wrap rows

export function computeHierarchicalLayout(
  servers: ServerConfig[],
  agents: Agent[],
  snmpDevices: SNMPDevice[] = [],
  topologyLinks: TopologyLink[] = []
): LayoutResult {
  const enabledServers = servers.filter((s) => s.enabled)
  const nodes: LayoutNode[] = []
  const links: LayoutLink[] = []
  const nodeIdSet = new Set<string>()

  // ── Link freshness per IP (drives device online/offline state) ─────
  const linkLastSeenByIp = new Map<string, number>()
  topologyLinks.forEach((tl) => {
    const ts = new Date(tl.last_seen).getTime()
    if (isNaN(ts)) return
    const prevS = linkLastSeenByIp.get(tl.source_ip)
    if (prevS === undefined || ts > prevS) linkLastSeenByIp.set(tl.source_ip, ts)
    const prevT = linkLastSeenByIp.get(tl.target_ip)
    if (prevT === undefined || ts > prevT) linkLastSeenByIp.set(tl.target_ip, ts)
  })
  const deviceActiveCutoff = Date.now() - TWO_HOURS_MS
  const isDeviceActive = (device: SNMPDevice): boolean => {
    const linkTs = device.device_ip ? linkLastSeenByIp.get(device.device_ip) : undefined
    const effectiveTs = linkTs ?? new Date(device.last_seen).getTime()
    return effectiveTs > deviceActiveCutoff
  }

  // ── BFS-based device depth (same logic as 2D) ──────────────────────
  // Used only as a fallback when name-pattern detection can't classify a
  // device into one of the 6 known tiers.
  const deviceDepth = new Map<string, number>()
  if (topologyLinks.length > 0) {
    const adj = new Map<string, Set<string>>()
    const seeds = new Set<string>()
    topologyLinks.forEach((tl) => {
      if (!adj.has(tl.source_ip)) adj.set(tl.source_ip, new Set())
      if (!adj.has(tl.target_ip)) adj.set(tl.target_ip, new Set())
      adj.get(tl.source_ip)!.add(tl.target_ip)
      adj.get(tl.target_ip)!.add(tl.source_ip)
      if ((tl.source_depth ?? 0) <= 0) seeds.add(tl.source_ip)
    })
    if (seeds.size === 0 && adj.size > 0) {
      let best: string | null = null
      let bestDeg = -1
      adj.forEach((nbrs, ip) => {
        if (nbrs.size > bestDeg) {
          bestDeg = nbrs.size
          best = ip
        }
      })
      if (best) seeds.add(best)
    }
    const queue: string[] = []
    seeds.forEach((s) => {
      deviceDepth.set(s, 0)
      queue.push(s)
    })
    while (queue.length > 0) {
      const cur = queue.shift()!
      const d = deviceDepth.get(cur)!
      const nbrs = adj.get(cur)
      if (!nbrs) continue
      for (const n of nbrs) {
        if (!deviceDepth.has(n)) {
          deviceDepth.set(n, d + 1)
          queue.push(n)
        }
      }
    }
  }

  // ── Orphan-agent fallback (match the 2D behavior) ──────────────────
  const fallbackServerId: string | null =
    enabledServers.length > 0 ? `server-${enabledServers[0].id}` : null
  const agentEffectiveServer = new Map<string, string>() // agent_id → serverId
  agents.forEach((a) => {
    if (agentEffectiveServer.has(a.agent_id)) return
    const desired = `server-${a.server_id}`
    if (enabledServers.some((s) => `server-${s.id}` === desired)) {
      agentEffectiveServer.set(a.agent_id, desired)
    } else if (fallbackServerId) {
      agentEffectiveServer.set(a.agent_id, fallbackServerId)
    }
  })

  // ── Build nodes with placeholder positions ─────────────────────────
  // Actual (x, y, z) is filled in AFTER we know all node IDs and edges,
  // because the layered layout places each node relative to its
  // neighbors' positions — classic Sugiyama, same as the 2D view.

  const serverStatusMap = new Map<string, 'online' | 'offline'>()
  enabledServers.forEach((server) => {
    const serverId = `server-${server.id}`
    const status: 'online' | 'offline' =
      server.health?.status === 'healthy' ? 'online' : 'offline'
    serverStatusMap.set(serverId, status)
    nodes.push({
      id: serverId,
      name: server.name,
      type: 'server',
      status,
      position: [0, 0, 0],
      color: server.metadata?.color || '#8b5cf6',
      ip: server.url,
    })
    nodeIdSet.add(serverId)
  })

  // Agents
  agents.forEach((agent) => {
    const sid = agentEffectiveServer.get(agent.agent_id)
    if (!sid) return
    const compoundId = `${agent.server_id}:${agent.agent_id}`
    if (nodeIdSet.has(compoundId)) return
    nodeIdSet.add(compoundId)

    const parentServer = enabledServers.find((s) => `server-${s.id}` === sid)
    nodes.push({
      id: compoundId,
      name: agent.hostname,
      type: 'agent',
      status: agent.status === 'online' ? 'online' : 'offline',
      position: [0, 0, 0],
      color: agent.status === 'online' ? '#10b981' : '#ef4444',
      ip: agent.ip_address,
      serverId: agent.server_id,
      agentId: agent.agent_id,
      serverName: parentServer?.name,
      metrics: agent.total_metrics,
      alerts: agent.total_alerts,
    })

    // Agent → server link
    const serverOnline = serverStatusMap.get(sid) === 'online'
    const agentOnline = agent.status === 'online'
    links.push({
      sourceId: compoundId,
      targetId: sid,
      sourcePos: [0, 0, 0],
      targetPos: [0, 0, 0],
      connected: agentOnline && serverOnline,
      linkType: 'agent-server',
    })
  })

  // Devices (including orphan devices whose agent_id doesn't resolve to a
  // visible agent — they still render; their connections surface through
  // the topology_links pass below).
  const agentIdToCompound = new Map<string, string>()
  agents.forEach((a) => {
    agentIdToCompound.set(a.agent_id, `${a.server_id}:${a.agent_id}`)
  })
  const agentHostnameById = new Map<string, string>()
  agents.forEach((a) => agentHostnameById.set(a.agent_id, a.hostname))

  snmpDevices.forEach((device) => {
    const deviceId = `device-${device.server_id}-${device.agent_id}-${
      device.device_ip || device.device_name
    }`
    if (nodeIdSet.has(deviceId)) return
    nodeIdSet.add(deviceId)

    const active = isDeviceActive(device)
    nodes.push({
      id: deviceId,
      name: device.device_name || device.device_ip,
      type: 'network',
      status: active ? 'online' : 'offline',
      position: [0, 0, 0],
      color: active ? '#06b6d4' : '#475569',
      ip: device.device_ip,
      serverId: device.server_id,
      agentId: device.agent_id,
      agentName: agentHostnameById.get(device.agent_id) || device.agent_id,
      lastSeen: device.last_seen,
    })

    // Device → agent link, but only for seeds. Deeper devices reach the
    // agent transitively through their device↔device chain. Without this
    // gate, a Clos fabric would draw an edge from every device up to the
    // agent, cluttering every vertical level.
    const d = device.device_ip ? deviceDepth.get(device.device_ip) : undefined
    const isSeed = d === undefined || d === 0
    const compoundId = agentIdToCompound.get(device.agent_id)
    if (isSeed && compoundId && nodeIdSet.has(compoundId)) {
      links.push({
        sourceId: deviceId,
        targetId: compoundId,
        sourcePos: [0, 0, 0],
        targetPos: [0, 0, 0],
        connected: active,
        linkType: 'device-agent',
      })
    }
  })

  // Device ↔ device links from topology_links — one edge per distinct
  // unordered pair.
  if (topologyLinks.length > 0) {
    const deviceByIp = new Map<string, LayoutNode>()
    nodes.forEach((n) => {
      if (n.type === 'network' && n.ip) deviceByIp.set(n.ip, n)
    })
    const seenPairs = new Set<string>()
    topologyLinks.forEach((tl) => {
      const srcN = deviceByIp.get(tl.source_ip)
      const tgtN = deviceByIp.get(tl.target_ip)
      if (!srcN || !tgtN || srcN.id === tgtN.id) return
      const pairKey = [srcN.id, tgtN.id].sort().join('|')
      if (seenPairs.has(pairKey)) return
      seenPairs.add(pairKey)
      const connected = srcN.status === 'online' && tgtN.status === 'online'
      links.push({
        sourceId: srcN.id,
        targetId: tgtN.id,
        sourcePos: [0, 0, 0],
        targetPos: [0, 0, 0],
        connected,
        linkType: 'device-device',
        d2dInfo: {
          sourceIp: tl.source_ip,
          sourceName: tl.source_name,
          sourcePort: tl.source_port,
          targetIp: tl.target_ip,
          targetName: tl.target_name,
          targetPort: tl.target_port,
          lastSeen: tl.last_seen,
          sourceStatus: srcN.status,
          targetStatus: tgtN.status,
        },
      })
    })
  }

  // ── Layer each node and run the shared layered layout ──────────────
  const layerOfNode = (n: LayoutNode): number => {
    if (n.type === 'server') return 0
    if (n.type === 'agent') return 1
    const byRole = roleLayerOf(n.name || '')
    if (byRole !== null) return byRole
    const d = n.ip ? deviceDepth.get(n.ip) ?? 0 : 0
    return 2 + d
  }

  const layered = computeLayeredLayout(
    nodes.map((n) => ({ id: n.id, layer: layerOfNode(n) })),
    links.map((l) => ({ source: l.sourceId, target: l.targetId })),
    {
      nodeGapX: NODE_GAP_X,
      layerGapY: LAYER_GAP_Y,
      iterations: 28,
      originX: 0,
      originY: 0,
    }
  )

  // ── Project the 2D layered result onto 3D ──────────────────────────
  // X → 3D X, -Y → 3D Y (so layer 0 sits at the top of the 3D scene),
  // Z stays 0 for normal layers. Wide layers (e.g. 120 compute servers)
  // wrap onto multiple Z rows so the camera doesn't have to dolly across
  // a mile-long strip.

  // Count per layer so we can wrap wide tiers.
  const layerCounts = new Map<number, number>()
  nodes.forEach((n) => {
    const l = layerOfNode(n)
    layerCounts.set(l, (layerCounts.get(l) ?? 0) + 1)
  })

  // For wide layers, group by layer and sort by computed X, then assign
  // row/col on a grid that's roughly square-ish.
  const wrapIndexById = new Map<string, { row: number; col: number; cols: number }>()
  const byLayer = new Map<number, LayoutNode[]>()
  nodes.forEach((n) => {
    const l = layerOfNode(n)
    if (!byLayer.has(l)) byLayer.set(l, [])
    byLayer.get(l)!.push(n)
  })
  byLayer.forEach((layerNodes, l) => {
    if (layerNodes.length <= WIDE_LAYER_THRESHOLD) return
    const sorted = layerNodes.slice().sort((a, b) => {
      const pa = layered.positions.get(a.id)?.x ?? 0
      const pb = layered.positions.get(b.id)?.x ?? 0
      return pa - pb
    })
    // Aim for a grid that's wider than it is deep (4:1-ish) so we still
    // read as "rows of servers" rather than a cube of them.
    const cols = Math.max(1, Math.ceil(Math.sqrt(sorted.length * 4)))
    sorted.forEach((n, idx) => {
      wrapIndexById.set(n.id, {
        row: Math.floor(idx / cols),
        col: idx % cols,
        cols,
      })
    })
    // Suppress layerCounts lookup warnings from TS when it's unused.
    void l
  })

  nodes.forEach((n) => {
    const p = layered.positions.get(n.id)
    if (!p) return
    const wrap = wrapIndexById.get(n.id)
    if (wrap) {
      // Wrapped: override X with grid position so the row stays compact.
      const span = (wrap.cols - 1) * NODE_GAP_X
      const x = -span / 2 + wrap.col * NODE_GAP_X
      const z = wrap.row * WIDE_LAYER_ROW_DEPTH - ((wrap.cols > 0 ? Math.floor((layerCounts.get(layerOfNode(n)) ?? 1) / wrap.cols) : 0) * WIDE_LAYER_ROW_DEPTH) / 2
      n.position = [x, -p.y, z]
    } else {
      n.position = [p.x, -p.y, 0]
    }
  })

  // ── Refresh link endpoints with the final positions ────────────────
  const nodePosById = new Map<string, [number, number, number]>()
  nodes.forEach((n) => nodePosById.set(n.id, n.position))
  links.forEach((l) => {
    const sp = nodePosById.get(l.sourceId)
    const tp = nodePosById.get(l.targetId)
    if (sp) l.sourcePos = sp
    if (tp) l.targetPos = tp
  })

  return { nodes, links }
}
