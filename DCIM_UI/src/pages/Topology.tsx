import { useEffect, useRef, useState, useCallback } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import * as d3 from 'd3'
import { Activity, Server, ZoomIn, ZoomOut, Maximize2, RefreshCw, Edit3, Calendar, Box, ChevronsDownUp, ChevronsUpDown, X } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { getMockTopologyData } from '@/lib/topology-mock-data'
import { computeLayeredLayout } from '@/lib/topology-layered'

// ── Toggle this to use 500+ node mock data for testing ──
const USE_MOCK_DATA = true

interface TopoNode extends d3.SimulationNodeDatum {
  id: string
  name: string
  type: 'server' | 'agent' | 'network'
  status: 'online' | 'offline'
  metrics?: number
  alerts?: number
  ip?: string
  color?: string
  serverId?: string
  agentId?: string
  serverName?: string
  agentName?: string
}

interface D2DInfo {
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

interface TopoLink extends d3.SimulationLinkDatum<TopoNode> {
  source: string | TopoNode
  target: string | TopoNode
  strength: number
  distance?: number
  linkType?: 'agent-server' | 'device-agent' | 'device-device'
  d2dInfo?: D2DInfo
}

type TimeFilter = 'today' | '30days' | 'all'

export default function Topology() {
  const mockData = USE_MOCK_DATA ? getMockTopologyData() : null
  const { data: realAgents, isLoading: realLoading } = useAgents()
  const { data: realServers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
    enabled: !USE_MOCK_DATA,
  })

  const agents = USE_MOCK_DATA ? mockData!.agents : realAgents
  const servers = USE_MOCK_DATA ? mockData!.servers : realServers
  const isLoading = USE_MOCK_DATA ? false : realLoading

  const navigate = useNavigate()
  const svgRef = useRef<SVGSVGElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const [selectedNode, setSelectedNode] = useState<TopoNode | null>(null)
  const [timeFilter, setTimeFilter] = useState<TimeFilter>('all')
  const simulationRef = useRef<d3.Simulation<TopoNode, TopoLink> | null>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)

  // Expand/collapse state — tracks which servers have their agents visible
  const [expandedServers, setExpandedServers] = useState<Set<string>>(new Set())
  const [showLegend, setShowLegend] = useState(true)
  const [showStats, setShowStats] = useState(true)
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const toggleServerExpansion = useCallback((serverId: string) => {
    setExpandedServers(prev => {
      const next = new Set(prev)
      if (next.has(serverId)) {
        next.delete(serverId)
      } else {
        next.add(serverId)
      }
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    const allServerIds = servers?.filter(s => s.enabled).map(s => `server-${s.id}`) || []
    setExpandedServers(new Set(allServerIds))
  }, [servers])

  const collapseAll = useCallback(() => {
    setExpandedServers(new Set())
  }, [])

  // Fetch filtered metrics and alerts for each agent
  const { data: realFilteredData } = useQuery({
    queryKey: ['topology-filtered-data', timeFilter, agents?.map(a => a.agent_id).join(',')],
    queryFn: async () => {
      if (!agents) return null

      const timeRangeMap = {
        today: '24h',
        '30days': '30d',
        all: undefined,
      }

      const timeRange = timeRangeMap[timeFilter]

      // Fetch metrics and alerts count for each agent
      const results = await Promise.all(
        agents.map(async (agent) => {
          try {
            const [metrics, alerts] = await Promise.all([
              timeRange
                ? api.getMetrics({ agent_id: agent.agent_id, time_range: timeRange, limit: 999999 })
                : Promise.resolve([]),
              timeRange
                ? api.getAlerts({ agent_id: agent.agent_id, time_range: timeRange })
                : Promise.resolve([]),
            ])

            return {
              agent_id: agent.agent_id,
              metrics_count: timeRange ? metrics.length : agent.total_metrics,
              alerts_count: timeRange ? alerts.filter(a => !a.resolved).length : agent.total_alerts,
            }
          } catch (error) {
            console.error(`Error fetching data for agent ${agent.agent_id}:`, error)
            return {
              agent_id: agent.agent_id,
              metrics_count: timeFilter === 'all' ? agent.total_metrics : 0,
              alerts_count: timeFilter === 'all' ? agent.total_alerts : 0,
            }
          }
        })
      )

      return results
    },
    enabled: !USE_MOCK_DATA && !!agents && agents.length > 0,
    refetchInterval: USE_MOCK_DATA ? false : 30000,
  })

  const filteredData = USE_MOCK_DATA
    ? agents?.map(a => ({ agent_id: a.agent_id, metrics_count: a.total_metrics, alerts_count: a.total_alerts })) ?? null
    : realFilteredData

  const { data: realSnmpDevices } = useQuery({
    queryKey: ['snmp-devices'],
    queryFn: () => api.getSNMPDevices(),
    staleTime: 30000,
    refetchInterval: USE_MOCK_DATA ? false : 60000,
    enabled: !USE_MOCK_DATA,
  })

  const snmpDevices = USE_MOCK_DATA ? mockData!.snmpDevices : realSnmpDevices

  // Device↔device wiring from the topology_links table (walker LLDP/CDP + discovery deep-scan)
  const { data: realTopologyLinks } = useQuery({
    queryKey: ['topology-links'],
    queryFn: () => api.getTopologyLinks(),
    staleTime: 30000,
    refetchInterval: USE_MOCK_DATA ? false : 60000,
    enabled: !USE_MOCK_DATA,
  })

  const topologyLinks = USE_MOCK_DATA ? mockData!.topologyLinks : realTopologyLinks

  useEffect(() => {
    if (!agents || !svgRef.current) return

    // Clear previous visualization
    d3.select(svgRef.current).selectAll('*').remove()

    const width = svgRef.current.clientWidth
    const height = svgRef.current.clientHeight

    // Create SVG
    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height)

    // Add zoom behavior — disable default dblclick.zoom
    const g = svg.append('g')
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
      })

    svg.call(zoom).on('dblclick.zoom', null)
    zoomRef.current = zoom

    // Build server nodes from actual servers data
    const enabledServers = servers?.filter(s => s.enabled) || []
    const serverNodes: TopoNode[] = enabledServers.map((s) => ({
      id: `server-${s.id}`,
      name: s.name,
      type: 'server' as const,
      status: (s.health?.status === 'healthy' ? 'online' : 'offline') as 'online' | 'offline',
      color: s.metadata?.color || '#8b5cf6',
      ip: s.url,
    }))

    // If no servers found, use a fallback based on agent data
    if (serverNodes.length === 0) {
      const uniqueServers = [...new Set(agents.map(a => a.server_id).filter(Boolean))]
      uniqueServers.forEach((sid) => {
        const agentForServer = agents.find(a => a.server_id === sid)
        serverNodes.push({
          id: `server-${sid}`,
          name: agentForServer?.server_name || `Server`,
          type: 'server',
          status: 'online',
          color: '#8b5cf6',
        })
      })
    }

    // Compute agent counts per server (always use full list)
    const agentCountByServer: Record<string, number> = {}
    agents.forEach(a => {
      const sid = `server-${a.server_id}`
      agentCountByServer[sid] = (agentCountByServer[sid] || 0) + 1
    })

    // ── O(1) lookup Maps ──
    const filteredDataMap = new Map(
      (filteredData || []).map(f => [f.agent_id, f])
    )
    const serverConfigMap = new Map(enabledServers.map(s => [s.id, s]))
    const agentLookup = new Map(
      agents.map(a => [`${a.server_id}:${a.agent_id}`, a])
    )
    const serverNodeMap = new Map(serverNodes.map(s => [s.id, s]))

    // Only show agents for expanded servers
    const visibleAgents = agents.filter(agent =>
      expandedServers.has(`server-${agent.server_id}`)
    )

    // Create agent nodes with filtered data
    const agentNodes: TopoNode[] = visibleAgents.map((agent) => {
      const filtered = filteredDataMap.get(agent.agent_id)
      const parentServer = serverConfigMap.get(agent.server_id)
      return {
        id: `${agent.server_id}:${agent.agent_id}`,
        name: agent.hostname,
        type: 'agent' as const,
        status: agent.status as 'online' | 'offline',
        metrics: filtered?.metrics_count ?? agent.total_metrics,
        alerts: filtered?.alerts_count ?? agent.total_alerts,
        ip: agent.ip_address,
        serverId: agent.server_id,
        agentId: agent.agent_id,
        serverName: parentServer?.name || agent.server_name,
      }
    })

    // Build SNMP device nodes for visible agents.
    // Device status (online/offline) is driven primarily by the freshest
    // topology_links.last_seen for that device's IP — every walker cycle
    // re-stamps the edges for devices still present, so a stale link row
    // means the device stopped responding. Devices with no topology_links
    // entry (isolated / leaf-only) fall back to snmp_devices.last_seen.
    const twoHoursAgo = Date.now() - 2 * 60 * 60 * 1000
    const lastSeenByIp = new Map<string, number>()
    if (topologyLinks) {
      topologyLinks.forEach(tl => {
        const ts = new Date(tl.last_seen).getTime()
        if (isNaN(ts)) return
        const prev = lastSeenByIp.get(tl.source_ip)
        if (prev === undefined || ts > prev) lastSeenByIp.set(tl.source_ip, ts)
        const prevT = lastSeenByIp.get(tl.target_ip)
        if (prevT === undefined || ts > prevT) lastSeenByIp.set(tl.target_ip, ts)
      })
    }

    // Per-device depth via our own BFS over the device↔device graph.
    //
    // We DON'T trust source_depth / target_depth from the walker verbatim —
    // the walker populates them during its own BFS, but rows can carry
    // stale depths from prior sweeps, and partially-discovered devices can
    // end up with depth values that don't form a consistent layering.
    // Running our own BFS here guarantees that every device's depth is
    // exactly one more than its shallowest neighbor, so the layered
    // layout always produces a clean tier-by-tier diagram.
    //
    // Seeds (depth 0) are devices the walker flagged with source_depth=0
    // in at least one row — the real entry points it was pointed at. If
    // nothing is flagged (older data), we fall back to the highest-degree
    // device as a pragmatic "most central" seed so BFS has a starting
    // point. Orphan devices with no link rows land on depth 0 too (by
    // default in `layerOf` below), so they render as their own small
    // floating roots rather than getting jammed into an unrelated tier.
    const deviceDepth = new Map<string, number>() // deviceIp -> depth (>=0)
    if (topologyLinks && topologyLinks.length > 0) {
      const adj = new Map<string, Set<string>>()
      const seeds = new Set<string>()
      topologyLinks.forEach(tl => {
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
      seeds.forEach(s => {
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

    const deviceNodes: TopoNode[] = []
    const deviceLinks: TopoLink[] = []
    const deviceNodeIds = new Set<string>()
    const agentNodeIdSet = new Set(agentNodes.map(n => n.id))

    if (snmpDevices) {
      snmpDevices.forEach(device => {
        const isAgentVisible = expandedServers.has(`server-${device.server_id}`)
        if (!isAgentVisible) return

        const deviceNodeId = `device-${device.server_id}-${device.agent_id}-${device.device_ip || device.device_name}`
        if (deviceNodeIds.has(deviceNodeId)) return
        deviceNodeIds.add(deviceNodeId)

        // Prefer topology_links last_seen (updated every walker cycle);
        // fall back to snmp_devices.last_seen for devices not in any link.
        const linkTs = device.device_ip ? lastSeenByIp.get(device.device_ip) : undefined
        const effectiveTs = linkTs ?? new Date(device.last_seen).getTime()
        const isActive = effectiveTs > twoHoursAgo
        const agentData = agentLookup.get(`${device.server_id}:${device.agent_id}`)

        deviceNodes.push({
          id: deviceNodeId,
          name: device.device_name || device.device_ip,
          type: 'network' as const,
          status: isActive ? 'online' : 'offline',
          ip: device.device_ip,
          serverId: device.server_id,
          agentId: device.agent_id,
          agentName: agentData?.hostname || device.agent_id,
        })

        const agentNodeId = `${device.server_id}:${device.agent_id}`
        // Only seed devices (BFS depth 0 in the device↔device graph, or
        // devices not in any link row at all) connect directly to the
        // agent. Everything deeper reaches the agent transitively via the
        // device↔device chain — without this gate, every device draws a
        // long line up through every layer to the single agent and the
        // diagram collapses into a hairball.
        const depth = device.device_ip ? deviceDepth.get(device.device_ip) : undefined
        const isSeedDevice = depth === undefined || depth === 0
        if (isSeedDevice && agentNodeIdSet.has(agentNodeId)) {
          deviceLinks.push({
            source: deviceNodeId,
            target: agentNodeId,
            strength: isActive ? 0.8 : 0.3,
            distance: 130,
            linkType: 'device-agent',
          })
        }
      })
    }

    const nodes: TopoNode[] = [...serverNodes, ...agentNodes, ...deviceNodes]
    const nodeMap = new Map(nodes.map(n => [n.id, n]))

    // Device↔device edges from topology_links. Resolve each row's source_ip
    // and target_ip to a rendered device node. We build an IP→deviceNodeId
    // lookup over the current device node set; rows referencing an IP that
    // isn't currently rendered (e.g. server collapsed, or device filtered
    // out) are dropped.
    const deviceNodeByIp = new Map<string, TopoNode>()
    deviceNodes.forEach(dn => {
      if (dn.ip) deviceNodeByIp.set(dn.ip, dn)
    })
    const deviceToDeviceLinks: TopoLink[] = []
    if (topologyLinks && topologyLinks.length > 0) {
      const seenPairs = new Set<string>()
      topologyLinks.forEach(tl => {
        const srcNode = deviceNodeByIp.get(tl.source_ip)
        const tgtNode = deviceNodeByIp.get(tl.target_ip)
        if (!srcNode || !tgtNode || srcNode.id === tgtNode.id) return
        // Dedup bidirectional duplicates: a↔b and b↔a render once.
        const pairKey = [srcNode.id, tgtNode.id].sort().join('|')
        if (seenPairs.has(pairKey)) return
        seenPairs.add(pairKey)
        const bothOnline = srcNode.status === 'online' && tgtNode.status === 'online'
        deviceToDeviceLinks.push({
          source: srcNode.id,
          target: tgtNode.id,
          strength: bothOnline ? 0.9 : 0.3,
          distance: 130,
          linkType: 'device-device',
          d2dInfo: {
            sourceIp: tl.source_ip,
            sourceName: tl.source_name,
            sourcePort: tl.source_port,
            targetIp: tl.target_ip,
            targetName: tl.target_name,
            targetPort: tl.target_port,
            lastSeen: tl.last_seen,
            sourceStatus: srcNode.status,
            targetStatus: tgtNode.status,
          },
        })
      })
    }

    // Create links — each agent connects to its own server
    const links: TopoLink[] = [
      ...visibleAgents.map(agent => {
        const sid = `server-${agent.server_id}`
        const serverNodeId = serverNodeMap.get(sid)?.id || serverNodes[0]?.id
        return {
          source: `${agent.server_id}:${agent.agent_id}`,
          target: serverNodeId || 'server-unknown',
          strength: agent.status === 'online' ? 1 : 0.3,
          distance: 200,
          linkType: 'agent-server' as const,
        }
      }).filter(l => l.target !== 'server-unknown'),
      ...deviceLinks,
      ...deviceToDeviceLinks,
    ]

    const nodeCount = nodes.length

    // ── Layered (multipartite) layout ───────────────────────────────────────
    // NetworkX-style. Every node is assigned a fixed LAYER (0 = top row), then
    // a barycenter sweep reorders nodes within each layer to minimize edge
    // crossings, and a per-layer x-pass spaces them evenly. This replaces the
    // old d3.tree-per-server rendering, which couldn't draw Clos/fat-tree
    // topologies (full mesh between aggregation tiers) because d3.tree is a
    // strict single-parent hierarchy.
    //
    // Layer assignment:
    //   0 — server (config-side DCIM server)
    //   1 — agent (aggregator agent running on a host)
    //   2 + N — SNMP devices at BFS depth N from their agent. N comes from
    //           topology_links.source_depth / target_depth populated by the
    //           walker. A device that never appears in any link row lands on
    //           layer 2.
    //
    // Edges fed to the layout:
    //   - agent → server (via the visibleAgents list)
    //   - device → agent (for devices whose agent is in scope)
    //   - device ↔ device (from topology_links; direction is whichever way
    //     source_depth < target_depth points)
    // Same-layer edges (not currently produced by the walker, but reserved
    // for peer links like cluster-R1 ↔ cluster-R2) are ignored by the layout
    // but still rendered.

    const TOP_Y = 100

    // Role-based layer assignment. Six device tiers, each pinned to a
    // fixed layer so the diagram always reads top-to-bottom as:
    //
    //     0  server (config-side DCIM server)
    //     1  agent  (aggregator agent)
    //     2  routers / LBs                 — top of the fabric
    //     3  fabric / spine / core switches
    //     4  pod / aggregation switches
    //     5  ToR / leaf switches
    //     6  compute / generic servers
    //     7  storage / GPU / specialty servers
    //
    // Name-pattern detection is the primary signal (real-world naming is
    // pretty consistent: "Cluster-R1", "Fabric-GW", "Pod-GW", "ToR-P3",
    // "Compute-17", "GPU-4", etc). Devices that don't match any pattern
    // fall back to `2 + BFS depth`, so generic / unknown devices still
    // slot into a sensible tier based on their graph distance from the
    // walker's seed.
    const roleLayerOf = (name: string): number | null => {
      if (!name) return null
      // Order matters: check specific patterns before generic ones, and
      // check specialty servers (gpu/storage) before generic "server".
      if (/\brouter\b|\brtr\b|cluster-?r\b|^r[-\d]|core-?r\b/i.test(name)) return 2
      if (/\blb\b|load-?balancer/i.test(name)) return 2
      if (/fabric|spine|super-?spine/i.test(name)) return 3
      if (/\bpod\b|aggregation|\bagg\b/i.test(name)) return 4
      if (/\btor\b|top-?of-?rack|\bleaf\b/i.test(name)) return 5
      if (/gpu|storage|\bnas\b|\bsan\b/i.test(name)) return 7
      if (/compute|host|\bsrv?\b|^server\b/i.test(name)) return 6
      return null
    }

    const layerOf = (n: TopoNode): number => {
      if (n.type === 'server') return 0
      if (n.type === 'agent') return 1
      const byRole = roleLayerOf(n.name || '')
      if (byRole !== null) return byRole
      const d = n.ip ? deviceDepth.get(n.ip) ?? 0 : 0
      return 2 + d
    }

    const layeredInput = nodes.map(n => ({ id: n.id, layer: layerOf(n) }))
    const layeredEdges = links.map(l => ({
      source: typeof l.source === 'string' ? l.source : (l.source as TopoNode).id,
      target: typeof l.target === 'string' ? l.target : (l.target as TopoNode).id,
    }))

    const layered = computeLayeredLayout(layeredInput, layeredEdges, {
      nodeGapX: 95,          // horizontal spacing between sibling nodes
      layerGapY: 180,        // vertical spacing between layers
      iterations: 28,        // barycenter sweeps (down+up per round)
      originX: width / 2,    // center on SVG viewport
      originY: TOP_Y,
    })

    // Apply computed positions onto the TopoNode objects so downstream
    // rendering (circles, links, labels) reads them transparently.
    nodes.forEach(n => {
      const p = layered.positions.get(n.id)
      if (p) {
        n.x = p.x
        n.y = p.y
      }
    })

    // No force simulation — positions are static
    simulationRef.current = null

    // ── Guard: position every node, or hide it ───────────────────────────────
    // Any node the d3.tree pass missed (truly orphaned — e.g. agent whose
    // serverId didn't match any rendered server AND no fallback existed)
    // would render at (0,0) with its links fanning back to the main tree —
    // the bug visible in img_1.png. Stash those nodes into a hidden
    // "unpositioned" set so we can strip them + their links before render.
    const unpositioned = new Set<string>()
    nodes.forEach(n => {
      if (typeof n.x !== 'number' || typeof n.y !== 'number' || !isFinite(n.x) || !isFinite(n.y)) {
        unpositioned.add(n.id)
      }
    })
    if (unpositioned.size > 0) {
      console.warn('[Topology] dropping', unpositioned.size, 'unpositioned nodes:', Array.from(unpositioned))
    }
    // Rebuild nodes + links excluding unpositioned ones so d3 never tries
    // to render lines into (0,0).
    const visibleNodes = nodes.filter(n => !unpositioned.has(n.id))
    const visibleLinks = links.filter(l => {
      const sid = typeof l.source === 'string' ? l.source : (l.source as TopoNode).id
      const tid = typeof l.target === 'string' ? l.target : (l.target as TopoNode).id
      return !unpositioned.has(sid) && !unpositioned.has(tid)
    })

    // Replace the originals so the downstream render uses the filtered set.
    nodes.length = 0
    nodes.push(...visibleNodes)
    links.length = 0
    links.push(...visibleLinks)

    // ── Auto-fit: compute bounding box and set initial zoom so the entire
    // tree is visible. Prevents branches from extending off-screen the
    // moment a server with a wide fan-out (e.g. /24 sweep hit) is expanded.
    let bboxMinX = Infinity, bboxMaxX = -Infinity
    let bboxMinY = Infinity, bboxMaxY = -Infinity
    nodes.forEach(n => {
      const x = n.x as number, y = n.y as number
      if (x < bboxMinX) bboxMinX = x
      if (x > bboxMaxX) bboxMaxX = x
      if (y < bboxMinY) bboxMinY = y
      if (y > bboxMaxY) bboxMaxY = y
    })
    if (isFinite(bboxMinX) && isFinite(bboxMinY)) {
      const pad = 100
      const contentW = (bboxMaxX - bboxMinX) + pad * 2
      const contentH = (bboxMaxY - bboxMinY) + pad * 2
      const fitScale = Math.min(width / contentW, height / contentH, 1)
      const contentCenterX = (bboxMinX + bboxMaxX) / 2
      const contentCenterY = (bboxMinY + bboxMaxY) / 2
      const tx = width / 2 - contentCenterX * fitScale
      const ty = height / 2 - contentCenterY * fitScale
      svg.call(zoom.transform as any, d3.zoomIdentity.translate(tx, ty).scale(fitScale))
    }

    // Resolve link source/target strings to node objects
    const nodeMapFiltered = new Map(nodes.map(n => [n.id, n]))
    links.forEach(l => {
      if (typeof l.source === 'string') l.source = nodeMapFiltered.get(l.source) || l.source
      if (typeof l.target === 'string') l.target = nodeMapFiltered.get(l.target) || l.target
    })

    // Build a set of offline server IDs for quick lookup
    const offlineServerIds = new Set(
      serverNodes.filter(s => s.status === 'offline').map(s => s.id)
    )

    // Helper: is link disconnected?
    const isLinkDisconnected = (d: TopoLink) => {
      const sourceId = typeof d.source === 'string' ? d.source : d.source.id
      const targetId = typeof d.target === 'string' ? d.target : d.target.id
      const sourceNode = nodeMap.get(sourceId)
      const targetNode = nodeMap.get(targetId)
      return sourceNode?.status === 'offline' || targetNode?.status === 'offline'
    }

    // Create defs for markers
    const defs = svg.append('defs')

    // Arrow markers
    defs.selectAll('marker')
      .data(['online', 'offline', 'disconnected', 'device'])
      .enter().append('marker')
      .attr('id', d => `arrow-${d}`)
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 35)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', d => d === 'online' ? '#10b981' : d === 'disconnected' ? '#ef4444' : d === 'device' ? '#06b6d4' : '#64748b')

    // Glow filter for offline/alert nodes
    const glowFilter = defs.append('filter')
      .attr('id', 'glow-red')
      .attr('x', '-50%').attr('y', '-50%')
      .attr('width', '200%').attr('height', '200%')
    glowFilter.append('feGaussianBlur')
      .attr('stdDeviation', '4')
      .attr('result', 'blur')
    glowFilter.append('feFlood')
      .attr('flood-color', '#ef4444')
      .attr('flood-opacity', '0.6')
      .attr('result', 'color')
    glowFilter.append('feComposite')
      .attr('in', 'color')
      .attr('in2', 'blur')
      .attr('operator', 'in')
      .attr('result', 'glow')
    const glowMerge = glowFilter.append('feMerge')
    glowMerge.append('feMergeNode').attr('in', 'glow')
    glowMerge.append('feMergeNode').attr('in', 'SourceGraphic')

    // Create links as straight lines
    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .enter().append('line')
      .attr('stroke', d => {
        if (d.linkType === 'device-device') {
          return isLinkDisconnected(d) ? '#ef4444' : '#f59e0b' // amber for physical wiring
        }
        if (d.linkType === 'device-agent') return '#06b6d4'
        return isLinkDisconnected(d) ? '#ef4444' : '#10b981'
      })
      .attr('stroke-width', d => {
        if (d.linkType === 'device-device') return isLinkDisconnected(d) ? 2 : 2
        return d.linkType === 'device-agent' ? 1.5 : isLinkDisconnected(d) ? 2.5 : 2
      })
      .attr('stroke-opacity', d => {
        if (d.linkType === 'device-device') return isLinkDisconnected(d) ? 0.6 : 0.8
        return d.linkType === 'device-agent' ? 0.4 : isLinkDisconnected(d) ? 0.7 : 0.5
      })
      .attr('stroke-dasharray', d => {
        if (d.linkType === 'device-device') return isLinkDisconnected(d) ? '8,6' : 'none'
        if (d.linkType === 'device-agent') return '6,4'
        return isLinkDisconnected(d) ? '8,6' : 'none'
      })
      .attr('x1', d => (d.source as TopoNode).x || 0)
      .attr('y1', d => (d.source as TopoNode).y || 0)
      .attr('x2', d => (d.target as TopoNode).x || 0)
      .attr('y2', d => (d.target as TopoNode).y || 0)

    // Widen the invisible pointer target for links so they're easy to hover
    // without losing the thin visible stroke.
    link.attr('stroke-linecap', 'round')

    // Tooltip on device↔device links: shows the two endpoints, LLDP port
    // mapping, last_seen age, and the fault reason when either side is
    // offline. Only wired for device-device links since the agent/device
    // hierarchy edges are implicit from the node tooltips.
    const linkTooltipHtml = (d: TopoLink): string => {
      if (d.linkType !== 'device-device' || !d.d2dInfo) return ''
      const info = d.d2dInfo
      const faulted = info.sourceStatus === 'offline' || info.targetStatus === 'offline'
      const ageMs = Date.now() - new Date(info.lastSeen).getTime()
      const ageMin = Math.max(0, Math.round(ageMs / 60000))
      const ageText = ageMin < 2 ? 'just now' : ageMin < 60 ? `${ageMin} min ago` : `${Math.round(ageMin / 60)} h ago`
      const statusColor = faulted ? 'text-red-400' : 'text-green-400'
      const statusText = faulted ? 'Link down' : 'Link up'
      let faultReason = ''
      if (info.sourceStatus === 'offline' && info.targetStatus === 'offline') {
        faultReason = 'Both endpoints offline'
      } else if (info.sourceStatus === 'offline') {
        faultReason = `${info.sourceName} offline`
      } else if (info.targetStatus === 'offline') {
        faultReason = `${info.targetName} offline`
      }
      return `
        <div class="font-bold text-base mb-1.5 text-amber-300">Link</div>
        <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <span class="text-slate-400">Status</span>
          <span class="${statusColor} font-semibold">${statusText}</span>
          ${faultReason ? `<span class="text-slate-400">Fault</span><span class="text-red-300">${faultReason}</span>` : ''}
          <span class="text-slate-400">Source</span>
          <span class="text-slate-200">${info.sourceName} <span class="text-slate-500 font-mono text-[11px]">(${info.sourceIp})</span></span>
          ${info.sourcePort ? `<span class="text-slate-400">Src port</span><span class="text-slate-300 font-mono text-[11px]">${info.sourcePort}</span>` : ''}
          <span class="text-slate-400">Target</span>
          <span class="text-slate-200">${info.targetName} <span class="text-slate-500 font-mono text-[11px]">(${info.targetIp})</span></span>
          ${info.targetPort ? `<span class="text-slate-400">Tgt port</span><span class="text-slate-300 font-mono text-[11px]">${info.targetPort}</span>` : ''}
          <span class="text-slate-400">Last seen</span>
          <span class="text-slate-300">${ageText}</span>
        </div>
      `
    }

    const moveTip = (event: MouseEvent) => {
      const tip = tooltipRef.current
      if (!tip) return
      const container = svgRef.current?.parentElement
      if (!container) return
      const rect = container.getBoundingClientRect()
      const tipW = 300
      let left = event.clientX - rect.left + 16
      let top = event.clientY - rect.top - 10
      if (left + tipW > rect.width) left = left - tipW - 32
      if (top < 0) top = 10
      tip.style.left = `${left}px`
      tip.style.top = `${top}px`
    }

    link.on('mouseenter', function (event, d) {
      const tip = tooltipRef.current
      if (!tip) return
      const html = linkTooltipHtml(d)
      if (!html) return
      tip.innerHTML = html
      tip.style.opacity = '1'
      tip.style.pointerEvents = 'none'
      moveTip(event)
      d3.select(this).attr('stroke-width', 4)
    })
    link.on('mousemove', function (event, d) {
      if (d.linkType !== 'device-device') return
      moveTip(event)
    })
    link.on('mouseleave', function (event, d) {
      const tip = tooltipRef.current
      if (tip) tip.style.opacity = '0'
      if (d.linkType === 'device-device') {
        d3.select(this).attr('stroke-width', isLinkDisconnected(d) ? 2 : 2)
      }
    })

    // Create node groups with drag
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .call(d3.drag<SVGGElement, TopoNode>()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended) as any)

    // Click handler with debounce (to distinguish single click from double-click)
    node.on('click', (event, d) => {
      event.stopPropagation()
      // For agents, select immediately
      if (d.type !== 'server') {
        if (clickTimerRef.current) {
          clearTimeout(clickTimerRef.current)
          clickTimerRef.current = null
        }
        setSelectedNode(d)
        return
      }
      // For servers, debounce to allow double-click detection
      if (clickTimerRef.current) {
        clearTimeout(clickTimerRef.current)
        clickTimerRef.current = null
      }
      clickTimerRef.current = setTimeout(() => {
        setSelectedNode(d)
        clickTimerRef.current = null
      }, 250)
    })

    // Double-click handler for expand/collapse
    node.on('dblclick', (event, d) => {
      event.stopPropagation()
      event.preventDefault()
      if (clickTimerRef.current) {
        clearTimeout(clickTimerRef.current)
        clickTimerRef.current = null
      }
      if (d.type === 'server') {
        toggleServerExpansion(d.id)
      }
    })

    // ── Tooltip on hover ──
    node.on('mouseenter', function (event, d) {
      const tip = tooltipRef.current
      if (!tip) return

      const agentCount = agentCountByServer[d.id] || 0
      let html = ''

      if (d.type === 'server') {
        html = `
          <div class="font-bold text-base mb-1.5" style="color:${d.color || '#a78bfa'}">${d.name}</div>
          <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <span class="text-slate-400">Status</span>
            <span class="${d.status === 'online' ? 'text-green-400' : 'text-red-400'} font-semibold">${d.status === 'online' ? 'Healthy' : 'Disconnected'}</span>
            <span class="text-slate-400">Type</span>
            <span class="text-slate-200">Server</span>
            ${d.ip ? `<span class="text-slate-400">URL</span><span class="text-slate-200 font-mono text-[11px]">${d.ip}</span>` : ''}
            <span class="text-slate-400">Agents</span>
            <span class="text-slate-200 font-semibold">${agentCount}</span>
            <span class="text-slate-400">Expanded</span>
            <span class="text-slate-200">${expandedServers.has(d.id) ? 'Yes' : 'No'}</span>
          </div>
          <div class="text-[10px] text-slate-500 mt-2 border-t border-white/10 pt-1.5">Double-click to ${expandedServers.has(d.id) ? 'collapse' : 'expand'} agents</div>
        `
      } else if (d.type === 'agent') {
        html = `
          <div class="font-bold text-base mb-1.5 text-white">${d.name}</div>
          <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <span class="text-slate-400">Status</span>
            <span class="${d.status === 'online' ? 'text-green-400' : 'text-red-400'} font-semibold">${d.status === 'online' ? 'Online' : 'Offline'}</span>
            <span class="text-slate-400">Type</span>
            <span class="text-slate-200">Agent</span>
            ${d.ip ? `<span class="text-slate-400">IP</span><span class="text-slate-200 font-mono text-[11px]">${d.ip}</span>` : ''}
            ${d.serverName ? `<span class="text-slate-400">Server</span><span class="text-purple-300">${d.serverName}</span>` : ''}
            ${d.agentId ? `<span class="text-slate-400">Agent ID</span><span class="text-slate-200 font-mono text-[11px]">${d.agentId}</span>` : ''}
            <span class="text-slate-400">Metrics</span>
            <span class="text-blue-400 font-semibold">${d.metrics != null ? d.metrics.toLocaleString() : '—'}</span>
            <span class="text-slate-400">Alerts</span>
            <span class="${(d.alerts || 0) > 0 ? 'text-red-400 font-semibold' : 'text-slate-300'}">${d.alerts || 0}</span>
          </div>
        `
      } else {
        // network / SNMP device
        html = `
          <div class="font-bold text-base mb-1.5 text-cyan-300">${d.name}</div>
          <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <span class="text-slate-400">Status</span>
            <span class="${d.status === 'online' ? 'text-green-400' : 'text-slate-400'} font-semibold">${d.status === 'online' ? 'Active' : 'Inactive'}</span>
            <span class="text-slate-400">Type</span>
            <span class="text-slate-200">SNMP Device</span>
            ${d.ip ? `<span class="text-slate-400">IP</span><span class="text-slate-200 font-mono text-[11px]">${d.ip}</span>` : ''}
            ${d.agentName ? `<span class="text-slate-400">Monitored By</span><span class="text-blue-300">${d.agentName}</span>` : ''}
          </div>
        `
      }

      tip.innerHTML = html
      tip.style.opacity = '1'
      tip.style.pointerEvents = 'none'

      // Position near cursor, clamped to container bounds
      const container = svgRef.current?.parentElement
      if (container) {
        const rect = container.getBoundingClientRect()
        const tipW = 280
        let left = event.clientX - rect.left + 16
        let top = event.clientY - rect.top - 10
        if (left + tipW > rect.width) left = left - tipW - 32
        if (top < 0) top = 10
        tip.style.left = `${left}px`
        tip.style.top = `${top}px`
      }
    })

    node.on('mousemove', function (event) {
      const tip = tooltipRef.current
      if (!tip) return
      const container = svgRef.current?.parentElement
      if (container) {
        const rect = container.getBoundingClientRect()
        const tipW = 280
        let left = event.clientX - rect.left + 16
        let top = event.clientY - rect.top - 10
        if (left + tipW > rect.width) left = left - tipW - 32
        if (top < 0) top = 10
        tip.style.left = `${left}px`
        tip.style.top = `${top}px`
      }
    })

    node.on('mouseleave', function () {
      const tip = tooltipRef.current
      if (tip) tip.style.opacity = '0'
    })

    // Add circles for nodes
    node.append('circle')
      .attr('r', d => d.type === 'server' ? 40 : d.type === 'network' ? 20 : 30)
      .attr('fill', d => {
        if (d.type === 'server') return d.status === 'offline' ? '#991b1b' : (d.color || '#8b5cf6')
        if (d.type === 'network') return d.status === 'online' ? '#0e7490' : '#334155'
        return d.status === 'online' ? '#10b981' : '#ef4444'
      })
      .attr('stroke', d => {
        if (d.type === 'server') {
          if (d.status === 'offline') return '#ef4444'
          const c = d3.color(d.color || '#8b5cf6')
          return c ? c.brighter(0.5).toString() : '#a78bfa'
        }
        if (d.type === 'network') return d.status === 'online' ? '#06b6d4' : '#475569'
        return d.status === 'online' ? '#34d399' : '#f87171'
      })
      .attr('stroke-width', d => d.type === 'network' ? 2 : d.status === 'offline' ? 4 : 3)
      .style('cursor', 'pointer')
      .attr('filter', d => d.status === 'offline' && d.type !== 'network' ? 'url(#glow-red)' : null)

    // Pulse animations — skip for large node counts (invisible at that zoom, heavy on perf)
    if (nodeCount <= 100) {
      // Online agents — SVG <animate> (GPU-composited, no JS timer overhead)
      node.filter(d => d.status === 'online' && d.type === 'agent')
        .append('circle')
        .attr('r', 30)
        .attr('fill', 'none')
        .attr('stroke', '#10b981')
        .attr('stroke-width', 2)
        .attr('opacity', 0.6)
        .each(function () {
          const el = d3.select(this)
          el.append('animate')
            .attr('attributeName', 'r').attr('values', '30;42;30')
            .attr('dur', '2s').attr('repeatCount', 'indefinite')
          el.append('animate')
            .attr('attributeName', 'opacity').attr('values', '0.6;0;0.6')
            .attr('dur', '2s').attr('repeatCount', 'indefinite')
        })

      // Offline servers
      node.filter(d => d.status === 'offline' && d.type === 'server')
        .append('circle')
        .attr('r', 40)
        .attr('fill', 'none')
        .attr('stroke', '#ef4444')
        .attr('stroke-width', 3)
        .attr('opacity', 0.9)
        .each(function () {
          const el = d3.select(this)
          el.append('animate')
            .attr('attributeName', 'r').attr('values', '40;55;40')
            .attr('dur', '1.2s').attr('repeatCount', 'indefinite')
          el.append('animate')
            .attr('attributeName', 'opacity').attr('values', '0.9;0;0.9')
            .attr('dur', '1.2s').attr('repeatCount', 'indefinite')
        })

      // Offline agents
      node.filter(d => d.status === 'offline' && d.type === 'agent')
        .append('circle')
        .attr('r', 30)
        .attr('fill', 'none')
        .attr('stroke', '#ef4444')
        .attr('stroke-width', 2)
        .attr('opacity', 0.9)
        .each(function () {
          const el = d3.select(this)
          el.append('animate')
            .attr('attributeName', 'r').attr('values', '30;45;30')
            .attr('dur', '1.2s').attr('repeatCount', 'indefinite')
          el.append('animate')
            .attr('attributeName', 'opacity').attr('values', '0.9;0;0.9')
            .attr('dur', '1.2s').attr('repeatCount', 'indefinite')
        })
    }

    // Add warning ring for agents connected to an offline server
    node.filter(d => d.type === 'agent' && d.status === 'online' && d.serverId && offlineServerIds.has(`server-${d.serverId}`))
      .append('circle')
      .attr('r', 36)
      .attr('fill', 'none')
      .attr('stroke', '#f59e0b')
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', '4,4')
      .attr('opacity', 0.8)

    // Add icons
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('font-size', d => d.type === 'server' ? '24px' : d.type === 'network' ? '14px' : '20px')
      .attr('fill', 'white')
      .text(d => d.type === 'server' ? '🖥️' : d.type === 'network' ? '📡' : '💻')

    // Add labels
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.type === 'server' ? 55 : d.type === 'network' ? 32 : 45)
      .attr('font-size', d => d.type === 'network' ? '10px' : '12px')
      .attr('fill', d => d.type === 'network' ? '#67e8f9' : '#e2e8f0')
      .attr('font-weight', 'bold')
      .text(d => d.name)

    // Add agent name sub-label for devices
    node.filter(d => d.type === 'network' && !!d.agentName)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 43)
      .attr('font-size', '9px')
      .attr('fill', '#94a3b8')
      .text(d => `via ${d.agentName}`)

    // Add server name sub-label for agents
    node.filter(d => d.type === 'agent' && !!d.serverName)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 58)
      .attr('font-size', '10px')
      .attr('fill', '#94a3b8')
      .text(d => d.serverName || '')

    // Add "OFFLINE" label for offline servers
    node.filter(d => d.type === 'server' && d.status === 'offline')
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 70)
      .attr('font-size', '11px')
      .attr('fill', '#ef4444')
      .attr('font-weight', 'bold')
      .text('DISCONNECTED')

    // Add warning badge on offline server nodes
    node.filter(d => d.type === 'server' && d.status === 'offline')
      .append('circle')
      .attr('cx', 0)
      .attr('cy', -45)
      .attr('r', 14)
      .attr('fill', '#ef4444')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)

    node.filter(d => d.type === 'server' && d.status === 'offline')
      .append('text')
      .attr('x', 0)
      .attr('y', -45)
      .attr('text-anchor', 'middle')
      .attr('dy', '0.4em')
      .attr('font-size', '14px')
      .attr('fill', 'white')
      .attr('font-weight', 'bold')
      .text('!')

    // Add "NOT REACHABLE" label for offline agents
    node.filter(d => d.type === 'agent' && d.status === 'offline')
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.serverName ? 72 : 60)
      .attr('font-size', '10px')
      .attr('fill', '#ef4444')
      .attr('font-weight', 'bold')
      .text('NOT REACHABLE')

    // Add warning badge on offline agents
    node.filter(d => d.type === 'agent' && d.status === 'offline')
      .append('circle')
      .attr('cx', 0)
      .attr('cy', -35)
      .attr('r', 10)
      .attr('fill', '#ef4444')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)

    node.filter(d => d.type === 'agent' && d.status === 'offline')
      .append('text')
      .attr('x', 0)
      .attr('y', -35)
      .attr('text-anchor', 'middle')
      .attr('dy', '0.4em')
      .attr('font-size', '10px')
      .attr('fill', 'white')
      .attr('font-weight', 'bold')
      .text('!')

    // Add metrics badge (for online agents)
    node.filter(d => d.type === 'agent' && d.status === 'online' && !!d.metrics)
      .append('circle')
      .attr('cx', 20)
      .attr('cy', -20)
      .attr('r', 12)
      .attr('fill', '#3b82f6')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)

    node.filter(d => d.type === 'agent' && d.status === 'online' && !!d.metrics)
      .append('text')
      .attr('x', 20)
      .attr('y', -20)
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('font-size', '10px')
      .attr('fill', 'white')
      .attr('font-weight', 'bold')
      .text(d => {
        if (!d.metrics) return ''
        if (d.metrics > 999) return '999+'
        return d.metrics.toString()
      })

    // Add alert badge
    node.filter(d => d.type === 'agent' && !!d.alerts && d.alerts > 0)
      .append('circle')
      .attr('cx', -20)
      .attr('cy', -20)
      .attr('r', 12)
      .attr('fill', '#ef4444')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)

    node.filter(d => d.type === 'agent' && !!d.alerts && d.alerts > 0)
      .append('text')
      .attr('x', -20)
      .attr('y', -20)
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('font-size', '10px')
      .attr('fill', 'white')
      .attr('font-weight', 'bold')
      .text(d => d.alerts?.toString() || '')

    // ── Agent count badge on server nodes ──
    node.filter(d => d.type === 'server' && (agentCountByServer[d.id] || 0) > 0)
      .append('circle')
      .attr('cx', 32)
      .attr('cy', -28)
      .attr('r', 15)
      .attr('fill', d => expandedServers.has(d.id) ? '#6366f1' : '#3b82f6')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)

    node.filter(d => d.type === 'server' && (agentCountByServer[d.id] || 0) > 0)
      .append('text')
      .attr('x', 32)
      .attr('y', -28)
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('font-size', '10px')
      .attr('fill', 'white')
      .attr('font-weight', 'bold')
      .text(d => {
        const count = agentCountByServer[d.id] || 0
        return count > 99 ? '99+' : count.toString()
      })

    // ── Expand/collapse hint text below server ──
    node.filter(d => d.type === 'server' && (agentCountByServer[d.id] || 0) > 0)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.status === 'offline' ? 86 : 70)
      .attr('font-size', '9px')
      .attr('fill', '#94a3b8')
      .style('cursor', 'pointer')
      .text(d => {
        const count = agentCountByServer[d.id] || 0
        if (expandedServers.has(d.id)) return `▾ ${count} agents`
        return `▸ ${count} agents`
      })

    // ── Static placement — no simulation tick needed ──
    node.attr('transform', d => `translate(${d.x},${d.y})`)

    // Drag functions — reposition node and update links in-place
    function dragstarted(_event: any, _d: TopoNode) {}

    function dragged(event: any, d: TopoNode) {
      d.x = event.x
      d.y = event.y
      d3.select(event.sourceEvent.target.closest('g')).attr('transform', `translate(${d.x},${d.y})`)
      // Update straight line endpoints
      link
        .attr('x1', (l: TopoLink) => (l.source as TopoNode).x || 0)
        .attr('y1', (l: TopoLink) => (l.source as TopoNode).y || 0)
        .attr('x2', (l: TopoLink) => (l.target as TopoNode).x || 0)
        .attr('y2', (l: TopoLink) => (l.target as TopoNode).y || 0)
    }

    function dragended(_event: any, _d: TopoNode) {}

    // Auto-fit to view immediately
    const fitTimer = setTimeout(() => {
      const xValues = nodes.map(n => n.x || 0)
      const yValues = nodes.map(n => n.y || 0)
      if (xValues.length === 0) return

      const xMin = Math.min(...xValues)
      const xMax = Math.max(...xValues)
      const yMin = Math.min(...yValues)
      const yMax = Math.max(...yValues)

      const padding = 100
      const boundsWidth = (xMax - xMin) + padding * 2
      const boundsHeight = (yMax - yMin) + padding * 2

      const scale = Math.min(width / boundsWidth, height / boundsHeight, 1)
      const centerX = (xMin + xMax) / 2
      const centerY = (yMin + yMax) / 2
      const translateX = width / 2 - scale * centerX
      const translateY = height / 2 - scale * centerY

      svg.transition().duration(750).call(
        zoom.transform,
        d3.zoomIdentity.translate(translateX, translateY).scale(scale)
      )
    }, 100)

    // Cleanup
    return () => {
      clearTimeout(fitTimer)
      if (clickTimerRef.current) {
        clearTimeout(clickTimerRef.current)
        clickTimerRef.current = null
      }
    }
  }, [agents, servers, filteredData, expandedServers, snmpDevices, topologyLinks])

  const handleZoomIn = () => {
    if (!svgRef.current || !zoomRef.current) return
    d3.select(svgRef.current).transition().call(
      zoomRef.current.scaleBy,
      1.3
    )
  }

  const handleZoomOut = () => {
    if (!svgRef.current || !zoomRef.current) return
    d3.select(svgRef.current).transition().call(
      zoomRef.current.scaleBy,
      0.7
    )
  }

  const handleReset = () => {
    if (!svgRef.current || !zoomRef.current) return
    const svg = d3.select(svgRef.current)
    svg.transition().duration(500).call(
      zoomRef.current.transform,
      d3.zoomIdentity
    )
  }

  const handleRestart = () => {
    // No simulation — this is a no-op in hierarchical mode
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading topology...</div>
        </div>
      </div>
    )
  }

  const hasExpanded = expandedServers.size > 0

  return (
    <div className="h-full flex flex-col space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <h1 className="text-4xl font-bold text-white">Network Topology</h1>
          <p className="text-slate-400 mt-2 text-lg">
            Interactive visualization of your infrastructure
          </p>

          {/* Time Filter */}
          <div className="flex items-center gap-2 mt-4">
            <Calendar className="w-4 h-4 text-slate-400" />
            <span className="text-sm text-slate-400">Time Period:</span>
            <div className="flex gap-2">
              <button
                onClick={() => setTimeFilter('today')}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  timeFilter === 'today'
                    ? 'bg-blue-500 text-white'
                    : 'bg-slate-800/50 text-slate-300 hover:bg-slate-700/50 border border-white/10'
                }`}
              >
                Today
              </button>
              <button
                onClick={() => setTimeFilter('30days')}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  timeFilter === '30days'
                    ? 'bg-blue-500 text-white'
                    : 'bg-slate-800/50 text-slate-300 hover:bg-slate-700/50 border border-white/10'
                }`}
              >
                30 Days
              </button>
              <button
                onClick={() => setTimeFilter('all')}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  timeFilter === 'all'
                    ? 'bg-blue-500 text-white'
                    : 'bg-slate-800/50 text-slate-300 hover:bg-slate-700/50 border border-white/10'
                }`}
              >
                All Time
              </button>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={hasExpanded ? collapseAll : expandAll}
            className="flex items-center gap-2 px-4 py-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 text-white rounded-lg transition-colors font-medium"
            title={hasExpanded ? 'Collapse all servers' : 'Expand all servers'}
          >
            {hasExpanded ? <ChevronsDownUp className="w-4 h-4" /> : <ChevronsUpDown className="w-4 h-4" />}
            {hasExpanded ? 'Collapse All' : 'Expand All'}
          </button>
          <button
            onClick={() => navigate('/app/topology-3d')}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors font-medium"
            title="Open 3D topology view"
          >
            <Box className="w-4 h-4" />
            3D View
          </button>
          <button
            onClick={() => navigate('/app/topology-editor')}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium"
            title="Open advanced topology editor"
          >
            <Edit3 className="w-4 h-4" />
            Advanced Editor
          </button>
          <button
            onClick={handleRestart}
            className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors"
            title="Restart simulation"
          >
            <RefreshCw className="w-5 h-5 text-slate-300" />
          </button>
          <button
            onClick={handleZoomIn}
            className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors"
            title="Zoom in"
          >
            <ZoomIn className="w-5 h-5 text-slate-300" />
          </button>
          <button
            onClick={handleZoomOut}
            className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors"
            title="Zoom out"
          >
            <ZoomOut className="w-5 h-5 text-slate-300" />
          </button>
          <button
            onClick={handleReset}
            className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors"
            title="Reset view"
          >
            <Maximize2 className="w-5 h-5 text-slate-300" />
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-4">
        {/* Main visualization */}
        <div className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden relative">
          <svg ref={svgRef} className="w-full h-full" />

          {/* Hover tooltip */}
          <div
            ref={tooltipRef}
            className="absolute z-50 w-[280px] bg-slate-900/95 border border-white/15 rounded-xl px-4 py-3 backdrop-blur-md shadow-2xl transition-opacity duration-150"
            style={{ opacity: 0, pointerEvents: 'none', top: 0, left: 0 }}
          />

          {/* Legend */}
          {showLegend && (
          <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-white">Legend</h3>
              <button onClick={() => setShowLegend(false)} className="ml-4 text-slate-400 hover:text-white transition-colors" aria-label="Close legend">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="space-y-2 text-xs">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-purple-500 border-2 border-purple-400" />
                <span className="text-slate-300">Server (Healthy)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-red-900 border-2 border-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]" />
                <span className="text-slate-300">Server (Disconnected)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-green-500 border-2 border-green-400" />
                <span className="text-slate-300">Agent (Online)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-red-500 border-2 border-red-400 shadow-[0_0_8px_rgba(239,68,68,0.5)]" />
                <span className="text-slate-300">Agent (Offline)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-8 h-0.5 bg-green-500" />
                <span className="text-slate-300">Healthy Link</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-8 h-0.5 border-t-2 border-dashed border-red-500" />
                <span className="text-slate-300">Disconnected Link</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center text-white text-xs font-bold">
                  #
                </div>
                <span className="text-slate-300">Metrics Count</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-red-500 flex items-center justify-center text-white text-xs font-bold">
                  !
                </div>
                <span className="text-slate-300">Alert Badge</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-5 h-5 rounded-full bg-cyan-800 border-2 border-cyan-400 flex items-center justify-center text-white" style={{fontSize:'9px'}}>📡</div>
                <span className="text-slate-300">SNMP Device</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-8 h-0.5 border-t-2 border-dashed border-cyan-400 opacity-60" />
                <span className="text-slate-300">Device Link</span>
              </div>
              <div className="mt-2 pt-2 border-t border-white/10">
                <p className="text-slate-400">Double-click a server to expand/collapse its agents</p>
              </div>
            </div>
          </div>
          )}

          {/* Stats */}
          {showStats && (
          <div className="absolute top-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <div className="flex justify-end mb-1">
              <button onClick={() => setShowStats(false)} className="text-slate-400 hover:text-white transition-colors" aria-label="Close stats">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <Server className="w-4 h-4 text-purple-400" />
                <span className="text-slate-300">Servers: <span className="font-bold text-white">{servers?.filter(s => s.enabled).length || 0}</span></span>
                {(() => {
                  const disconnected = servers?.filter(s => s.enabled && s.health?.status !== 'healthy').length || 0
                  return disconnected > 0 ? (
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-500/20 text-red-400 border border-red-500/30 animate-pulse">
                      {disconnected} down
                    </span>
                  ) : null
                })()}
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-blue-400" />
                <span className="text-slate-300">Agents: <span className="font-bold text-white">{agents?.length || 0}</span></span>
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-green-400" />
                <span className="text-slate-300">Online: <span className="font-bold text-green-400">
                  {agents?.filter(a => a.status === 'online').length || 0}
                </span></span>
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-red-400" />
                <span className="text-slate-300">Offline: <span className="font-bold text-red-400">
                  {agents?.filter(a => a.status === 'offline').length || 0}
                </span></span>
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-cyan-400" />
                <span className="text-slate-300">Devices: <span className="font-bold text-cyan-400">
                  {snmpDevices?.length || 0}
                </span></span>
              </div>
              <div className="border-t border-white/10 pt-2 mt-2 flex items-center gap-2">
                <span className="text-slate-400 text-xs">
                  {expandedServers.size} / {servers?.filter(s => s.enabled).length || 0} expanded
                </span>
              </div>
            </div>
          </div>
          )}
        </div>

        {/* Node details panel */}
        {selectedNode && (
          <div className="w-80 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xl font-bold text-white">Node Details</h3>
              <button
                onClick={() => setSelectedNode(null)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <p className="text-sm text-slate-400">Name</p>
                <p className="text-lg font-semibold text-white">{selectedNode.name}</p>
              </div>

              <div>
                <p className="text-sm text-slate-400">Type</p>
                <p className="text-lg font-semibold text-white capitalize">{selectedNode.type}</p>
              </div>

              <div>
                <p className="text-sm text-slate-400">Status</p>
                <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${
                  selectedNode.status === 'online'
                    ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                    : 'bg-red-500/20 text-red-400 border border-red-500/30'
                }`}>
                  {selectedNode.status.toUpperCase()}
                </span>
              </div>

              {selectedNode.ip && (
                <div>
                  <p className="text-sm text-slate-400">IP Address</p>
                  <p className="text-base font-mono text-white">{selectedNode.ip}</p>
                </div>
              )}

              {selectedNode.metrics !== undefined && (
                <div>
                  <p className="text-sm text-slate-400">Total Metrics</p>
                  <p className="text-2xl font-bold text-blue-400">{selectedNode.metrics.toLocaleString()}</p>
                </div>
              )}

              {selectedNode.alerts !== undefined && selectedNode.alerts > 0 && (
                <div>
                  <p className="text-sm text-slate-400">Active Alerts</p>
                  <p className="text-2xl font-bold text-red-400">{selectedNode.alerts}</p>
                </div>
              )}

              {selectedNode.type === 'server' && (
                <>
                  <div>
                    <p className="text-sm text-slate-400">Connected Agents</p>
                    <p className="text-2xl font-bold text-purple-400">
                      {agents?.filter(a => `server-${a.server_id}` === selectedNode.id).length || 0}
                    </p>
                  </div>
                  <button
                    onClick={() => toggleServerExpansion(selectedNode.id)}
                    className="block w-full text-center px-4 py-2 bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/30 text-indigo-400 rounded-lg transition-colors"
                  >
                    {expandedServers.has(selectedNode.id) ? 'Collapse Agents' : 'Expand Agents'}
                  </button>
                </>
              )}

              {selectedNode.type === 'agent' && selectedNode.serverName && (
                <div>
                  <p className="text-sm text-slate-400">Server</p>
                  <p className="text-base font-semibold text-purple-400">{selectedNode.serverName}</p>
                </div>
              )}

              {selectedNode.type === 'network' && selectedNode.agentName && (
                <div>
                  <p className="text-sm text-slate-400">Monitored By</p>
                  <p className="text-base font-semibold text-cyan-400">{selectedNode.agentName}</p>
                </div>
              )}

              {selectedNode.type === 'network' && (() => {
                const device = snmpDevices?.find(
                  d => d.agent_id === selectedNode.agentId && (d.device_ip === selectedNode.ip || d.device_name === selectedNode.name)
                )
                if (!device) return null
                return (
                  <div>
                    <p className="text-sm text-slate-400">Last Seen</p>
                    <p className="text-sm font-mono text-slate-300">{new Date(device.last_seen).toLocaleString()}</p>
                  </div>
                )
              })()}

              {selectedNode.type === 'agent' && (
                <Link
                  to={`/app/agents/${selectedNode.agentId || selectedNode.id}`}
                  className="block w-full text-center px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 text-blue-400 rounded-lg transition-colors"
                >
                  View Agent Details
                </Link>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
