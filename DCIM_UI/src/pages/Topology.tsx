import { useEffect, useRef, useState, useCallback } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import * as d3 from 'd3'
import { Activity, Server, ZoomIn, ZoomOut, Maximize2, RefreshCw, Edit3, Calendar, Box, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { getMockTopologyData } from '@/lib/topology-mock-data'

// ── Toggle this to use 500+ node mock data for testing ──
const USE_MOCK_DATA = false

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

interface TopoLink extends d3.SimulationLinkDatum<TopoNode> {
  source: string | TopoNode
  target: string | TopoNode
  strength: number
  distance?: number
  linkType?: 'agent-server' | 'device-agent'
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

    // Build SNMP device nodes for visible agents
    const twoHoursAgo = Date.now() - 2 * 60 * 60 * 1000
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

        const isActive = new Date(device.last_seen).getTime() > twoHoursAgo
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
        if (agentNodeIdSet.has(agentNodeId)) {
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
    ]

    // ── Static hierarchical tree layout ──
    const nodeCount = nodes.length

    // Group visible agents by server for layout
    const visibleAgentsByServer = new Map<string, TopoNode[]>()
    agentNodes.forEach(an => {
      const sid = `server-${an.serverId}`
      if (!visibleAgentsByServer.has(sid)) visibleAgentsByServer.set(sid, [])
      visibleAgentsByServer.get(sid)!.push(an)
    })

    // Group devices by agent
    const devicesByAgentMap = new Map<string, TopoNode[]>()
    deviceNodes.forEach(dn => {
      const key = `${dn.serverId}:${dn.agentId}`
      if (!devicesByAgentMap.has(key)) devicesByAgentMap.set(key, [])
      devicesByAgentMap.get(key)!.push(dn)
    })

    // Node sizing constants
    const nodeRadius = { server: 40, agent: 30, network: 20 }

    // ── Generous spacing constants ──
    const NODE_GAP_X = 140        // horizontal gap between sibling nodes
    const SERVER_GAP_X = 200      // extra gap between server subtrees
    const TIER_GAP_Y = 250        // vertical gap between tiers

    // Tier Y positions
    const serverRowY = 100
    const agentRowY = serverRowY + TIER_GAP_Y
    const deviceRowY = agentRowY + TIER_GAP_Y

    // ── First pass: position agents and devices bottom-up to compute subtree widths ──
    // For each server, compute total subtree width
    const serverSubtreeWidths = new Map<string, number>()
    const agentSubtreeWidths = new Map<string, number>()

    serverNodes.forEach(sn => {
      const sAgents = visibleAgentsByServer.get(sn.id) || []
      if (sAgents.length === 0) {
        serverSubtreeWidths.set(sn.id, NODE_GAP_X)
        return
      }

      let serverWidth = 0
      sAgents.forEach((agent, i) => {
        const devs = devicesByAgentMap.get(agent.id) || []
        const agentWidth = devs.length > 1
          ? (devs.length - 1) * NODE_GAP_X
          : NODE_GAP_X
        agentSubtreeWidths.set(agent.id, agentWidth)
        serverWidth += agentWidth
        if (i < sAgents.length - 1) serverWidth += NODE_GAP_X * 0.5 // gap between agent subtrees
      })

      serverSubtreeWidths.set(sn.id, serverWidth)
    })

    // Total layout width
    const totalWidth = Array.from(serverSubtreeWidths.values()).reduce((a, b) => a + b, 0)
      + Math.max(0, serverNodes.length - 1) * SERVER_GAP_X

    // ── Second pass: assign X positions ──
    let cursorX = width / 2 - totalWidth / 2

    serverNodes.forEach((sn, si) => {
      const subtreeW = serverSubtreeWidths.get(sn.id) || NODE_GAP_X
      sn.x = cursorX + subtreeW / 2
      sn.y = serverRowY

      const sAgents = visibleAgentsByServer.get(sn.id) || []
      if (sAgents.length > 0) {
        let agentCursorX = cursorX
        sAgents.forEach((agent, ai) => {
          const agentW = agentSubtreeWidths.get(agent.id) || NODE_GAP_X
          agent.x = agentCursorX + agentW / 2
          agent.y = agentRowY

          const devs = devicesByAgentMap.get(agent.id) || []
          if (devs.length > 0) {
            const devTotalW = (devs.length - 1) * NODE_GAP_X
            const devStartX = agent.x! - devTotalW / 2
            devs.forEach((dn, j) => {
              dn.x = devStartX + j * NODE_GAP_X
              dn.y = deviceRowY
            })
          }

          agentCursorX += agentW
          if (ai < sAgents.length - 1) agentCursorX += NODE_GAP_X * 0.5
        })
      }

      cursorX += subtreeW
      if (si < serverNodes.length - 1) cursorX += SERVER_GAP_X
    })

    // No force simulation — positions are static
    simulationRef.current = null

    // Resolve link source/target strings to node objects
    links.forEach(l => {
      if (typeof l.source === 'string') l.source = nodeMap.get(l.source) || l.source
      if (typeof l.target === 'string') l.target = nodeMap.get(l.target) || l.target
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
        if (d.linkType === 'device-agent') return '#06b6d4'
        return isLinkDisconnected(d) ? '#ef4444' : '#10b981'
      })
      .attr('stroke-width', d => d.linkType === 'device-agent' ? 1.5 : isLinkDisconnected(d) ? 2.5 : 2)
      .attr('stroke-opacity', d => d.linkType === 'device-agent' ? 0.4 : isLinkDisconnected(d) ? 0.7 : 0.5)
      .attr('stroke-dasharray', d => {
        if (d.linkType === 'device-agent') return '6,4'
        return isLinkDisconnected(d) ? '8,6' : 'none'
      })
      .attr('x1', d => (d.source as TopoNode).x || 0)
      .attr('y1', d => (d.source as TopoNode).y || 0)
      .attr('x2', d => (d.target as TopoNode).x || 0)
      .attr('y2', d => (d.target as TopoNode).y || 0)

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
  }, [agents, servers, filteredData, expandedServers, snmpDevices])

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
          <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <h3 className="text-sm font-semibold text-white mb-3">Legend</h3>
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

          {/* Stats */}
          <div className="absolute top-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
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
