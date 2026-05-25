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
  /** Raw device type from orchestrator, e.g. "DeviceType.ROUTER" */
  deviceType?: string
  /** Latest temperature reading (°C) for heatmap mode */
  temperature?: number
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

// ── Floor navigation levels (one per tier in the hierarchy) ─────────────────
// Listed bottom-to-top so index 0 = lowest Y plane (infrastructure) and
// index 5 = highest Y plane (DCIM servers). The navigator renders them
// reversed so the top button moves the camera "up".
export const FLOORS = [
  { name: 'Infrastructure', label: 'L6', y: -140, color: '#64748b' },
  { name: 'Compute/Srvs',   label: 'L5', y: -112, color: '#10b981' },
  { name: 'ToR Switches',   label: 'L4', y: -84,  color: '#06b6d4' },
  { name: 'Agg Switches',   label: 'L3', y: -56,  color: '#f59e0b' },
  { name: 'Core Routers',   label: 'L2', y: -28,  color: '#ef4444' },
  { name: 'DCIM Servers',   label: 'L1', y: 20,   color: '#a855f7' },
]

// ── Role-based layer assignment ──────────────────────────────────────────────
// Mirrors Topology.tsx exactly:
//   0  server (DCIM server config node)
//   1  routers / LBs
//   2  aggregation / fabric / spine switches
//   3  ToR / leaf switches
//   4  compute servers
//   5  infrastructure (OOB, PDU, UPS, sensors)
//
// For SNMP devices that don't carry a DeviceType enum, name-pattern detection
// is used as a fallback. Unrecognised devices fall back to `2 + BFS depth`.
function roleLayerOf(name: string): number | null {
  if (!name) return null
  if (/core-?r\b|cluster-?r\b|\brouter\b|\brtr\b|^r\d/i.test(name)) return 1
  if (/\blb\b|load-?balancer/i.test(name)) return 1
  if (/^agg-|fabric|spine|super-?spine|\bagg\b/i.test(name)) return 2
  if (/\bpod\b|aggregation/i.test(name)) return 2
  if (/^tor-|\btor\b|top-?of-?rack|\bleaf\b/i.test(name)) return 3
  if (/^oob-|^sensor|^fpdu|^ups-|^pdu-/i.test(name)) return 5
  if (/gpu|storage|\bnas\b|\bsan\b/i.test(name)) return 5
  if (/^srv-|compute|host/i.test(name)) return 4
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

export type DeviceAlertInfo = { active: number; critical: number; warning: number }

export function computeHierarchicalLayout(
  servers: ServerConfig[],
  agents: Agent[],
  snmpDevices: SNMPDevice[] = [],
  topologyLinks: TopologyLink[] = [],
  _unused1?: unknown,
  _unused2?: unknown,
  deviceAlertsByIp?: Map<string, DeviceAlertInfo>
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

  // ── BFS-based device depth (fallback for unknown SNMP devices) ──────
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

  // ── Orphan-agent fallback — maps each agent to its effective server ──
  const fallbackServerId: string | null =
    enabledServers.length > 0 ? `server-${enabledServers[0].id}` : null
  const agentEffectiveServer = new Map<string, string>()
  agents.forEach((a) => {
    if (agentEffectiveServer.has(a.agent_id)) return
    const desired = `server-${a.server_id}`
    if (enabledServers.some((s) => `server-${s.id}` === desired)) {
      agentEffectiveServer.set(a.agent_id, desired)
    } else if (fallbackServerId) {
      agentEffectiveServer.set(a.agent_id, fallbackServerId)
    }
  })

  // Group DCIM agents by effective server ID for the fallback path
  const agentsByEffectiveServer = new Map<string, Agent[]>()
  agents.forEach((a) => {
    const sid = agentEffectiveServer.get(a.agent_id)
    if (!sid) return
    if (!agentsByEffectiveServer.has(sid)) agentsByEffectiveServer.set(sid, [])
    agentsByEffectiveServer.get(sid)!.push(a)
  })

  // ── Server nodes ────────────────────────────────────────────────────
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

  // ── Agent nodes from DCIM agents ────────────────────────────────────
  enabledServers.forEach((server) => {
    const serverId = `server-${server.id}`
    const serverAgents = agentsByEffectiveServer.get(serverId) || []
    serverAgents.forEach((agent) => {
      const compoundId = `${agent.server_id}:${agent.agent_id}`
      if (nodeIdSet.has(compoundId)) return
      nodeIdSet.add(compoundId)
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
        serverName: server.name,
        metrics: agent.total_metrics,
        alerts: agent.total_alerts,
      })
    })
  })

  // ── SNMP device nodes ────────────────────────────────────────────────
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

  // ── Synthetic nodes for ingest-API devices ──────────────────────────
  // Devices ingested via POST /api/v1/ingest live in the `devices` table,
  // not in snmpDevices. Create a minimal node for any topology-link endpoint
  // whose IP isn't already rendered so the edges become visible.
  if (topologyLinks.length > 0) {
    const existingIps = new Set(nodes.map((n) => n.ip).filter(Boolean) as string[])
    topologyLinks.forEach((tl) => {
      const endpoints: [string, string][] = [
        [tl.source_ip, tl.source_name],
        [tl.target_ip, tl.target_name],
      ]
      for (const [ip, name] of endpoints) {
        if (!ip || existingIps.has(ip)) continue
        existingIps.add(ip)
        const ts = new Date(tl.last_seen).getTime()
        const active = !isNaN(ts) && ts > deviceActiveCutoff
        const nodeId = `ingest-device-${ip}`
        if (nodeIdSet.has(nodeId)) continue
        nodeIdSet.add(nodeId)
        const alertInfo = deviceAlertsByIp?.get(ip)
        nodes.push({
          id: nodeId,
          name: name || ip,
          type: 'network',
          status: active ? 'online' : 'offline',
          position: [0, 0, 0],
          color: alertInfo?.critical ? '#ef4444' : alertInfo?.warning ? '#f59e0b' : active ? '#06b6d4' : '#475569',
          ip,
          alerts: alertInfo?.active ?? 0,
        })
      }
    })
  }

  // ── Device ↔ device links from topology_links (aggregator DB) ───────
  const allNodeByIp = new Map<string, LayoutNode>()
  nodes.forEach((n) => { if (n.ip && n.type !== 'server') allNodeByIp.set(n.ip, n) })
  const seenPairsGlobal = new Set<string>()

  if (topologyLinks.length > 0) {
    topologyLinks.forEach((tl) => {
      const srcN = allNodeByIp.get(tl.source_ip)
      const tgtN = allNodeByIp.get(tl.target_ip)
      if (!srcN || !tgtN || srcN.id === tgtN.id) return
      const pairKey = [srcN.id, tgtN.id].sort().join('|')
      if (seenPairsGlobal.has(pairKey)) return
      seenPairsGlobal.add(pairKey)
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

  // ── Layer assignment (mirrors Topology.tsx layerOf exactly) ─────────
  const layerOfNode = (n: LayoutNode): number => {
    if (n.type === 'server') return 0
    if (n.type === 'agent') {
      if (n.deviceType) {
        const dt = n.deviceType
        if (dt.includes('ROUTER')) return 1
        if (dt.includes('SERVER')) return 4
        if (dt.includes('OOB_SWITCH') || dt.includes('SENSOR') ||
            dt.includes('FLOOR_PDU') || dt.includes('UPS') ||
            dt.includes('PDU')) return 5
        if (dt.includes('SWITCH')) {
          if (/^agg-/i.test(n.name || '')) return 2
          if (/^tor-/i.test(n.name || '')) return 3
          return 2
        }
      }
      return 1
    }
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

  // ── Project 2D layered result onto 3D ────────────────────────────────
  // X → 3D X, -Y → 3D Y (layer 0 at top of scene), Z = 0 normally.
  // Wide layers wrap onto multiple Z rows to keep the scene walkable.

  const layerCounts = new Map<number, number>()
  nodes.forEach((n) => {
    const l = layerOfNode(n)
    layerCounts.set(l, (layerCounts.get(l) ?? 0) + 1)
  })

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
    const cols = Math.max(1, Math.ceil(Math.sqrt(sorted.length * 4)))
    sorted.forEach((n, idx) => {
      wrapIndexById.set(n.id, {
        row: Math.floor(idx / cols),
        col: idx % cols,
        cols,
      })
    })
    void l
  })

  nodes.forEach((n) => {
    const p = layered.positions.get(n.id)
    if (!p) return
    const wrap = wrapIndexById.get(n.id)
    if (wrap) {
      const span = (wrap.cols - 1) * NODE_GAP_X
      const x = -span / 2 + wrap.col * NODE_GAP_X
      const z = wrap.row * WIDE_LAYER_ROW_DEPTH - ((wrap.cols > 0 ? Math.floor((layerCounts.get(layerOfNode(n)) ?? 1) / wrap.cols) : 0) * WIDE_LAYER_ROW_DEPTH) / 2
      n.position = [x, -p.y, z]
    } else {
      n.position = [p.x, -p.y, 0]
    }
  })

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
