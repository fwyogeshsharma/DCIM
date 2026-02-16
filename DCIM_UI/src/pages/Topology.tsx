import { useEffect, useRef, useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import * as d3 from 'd3'
import { Activity, Server, ZoomIn, ZoomOut, Maximize2, RefreshCw, Edit3, Calendar, Box } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'

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
}

interface TopoLink extends d3.SimulationLinkDatum<TopoNode> {
  source: string | TopoNode
  target: string | TopoNode
  strength: number
}

type TimeFilter = 'today' | '30days' | 'all'

export default function Topology() {
  const { data: agents, isLoading } = useAgents()
  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })
  const navigate = useNavigate()
  const svgRef = useRef<SVGSVGElement>(null)
  const [selectedNode, setSelectedNode] = useState<TopoNode | null>(null)
  const [timeFilter, setTimeFilter] = useState<TimeFilter>('all')
  const simulationRef = useRef<d3.Simulation<TopoNode, TopoLink> | null>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)

  // Fetch filtered metrics and alerts for each agent
  const { data: filteredData } = useQuery({
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
    enabled: !!agents && agents.length > 0,
    refetchInterval: 30000,
  })

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

    // Add zoom behavior
    const g = svg.append('g')
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
      })

    svg.call(zoom)
    zoomRef.current = zoom

    // Build server nodes from actual servers data
    const enabledServers = servers?.filter(s => s.enabled) || []
    const serverNodes: TopoNode[] = enabledServers.map((s, i) => ({
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
      uniqueServers.forEach((sid, i) => {
        const agentForServer = agents.find(a => a.server_id === sid)
        serverNodes.push({
          id: `server-${sid}`,
          name: agentForServer?.server_name || `Server ${i + 1}`,
          type: 'server',
          status: 'online',
          color: '#8b5cf6',
        })
      })
    }

    // Create agent nodes with filtered data — use compound ID to avoid collisions
    // when different servers have agents with the same agent_id
    const agentNodes: TopoNode[] = agents.map((agent) => {
      const filtered = filteredData?.find(f => f.agent_id === agent.agent_id)
      const parentServer = enabledServers.find(s => s.id === agent.server_id)
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

    const nodes: TopoNode[] = [...serverNodes, ...agentNodes]

    // Create links — each agent connects to its own server (using compound IDs)
    const links: TopoLink[] = agents.map(agent => {
      // Find the matching server node
      const serverNodeId = serverNodes.find(s => s.id === `server-${agent.server_id}`)?.id || serverNodes[0]?.id
      return {
        source: `${agent.server_id}:${agent.agent_id}`,
        target: serverNodeId || 'server-unknown',
        strength: agent.status === 'online' ? 1 : 0.3,
      }
    }).filter(l => l.target !== 'server-unknown')

    // Create force simulation
    const simulation = d3.forceSimulation<TopoNode>(nodes)
      .force('link', d3.forceLink<TopoNode, TopoLink>(links)
        .id(d => d.id)
        .distance(200)
        .strength(d => d.strength))
      .force('charge', d3.forceManyBody().strength(-1200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(70))

    simulationRef.current = simulation

    // Build a set of offline server IDs for quick lookup
    const offlineServerIds = new Set(
      serverNodes.filter(s => s.status === 'offline').map(s => s.id)
    )

    // Helper: is link disconnected? (agent offline OR its server offline)
    const isLinkDisconnected = (d: TopoLink) => {
      const sourceId = typeof d.source === 'string' ? d.source : d.source.id
      const targetId = typeof d.target === 'string' ? d.target : d.target.id
      const sourceNode = nodes.find(n => n.id === sourceId)
      const targetNode = nodes.find(n => n.id === targetId)
      return sourceNode?.status === 'offline' || targetNode?.status === 'offline'
    }

    // Create defs for markers and animated dash pattern
    const defs = svg.append('defs')

    // Arrow markers
    defs.selectAll('marker')
      .data(['online', 'offline', 'disconnected'])
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
      .attr('fill', d => d === 'online' ? '#10b981' : d === 'disconnected' ? '#ef4444' : '#64748b')

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

    // Create links — solid green for healthy, dashed red for disconnected
    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .enter().append('line')
      .attr('stroke', d => isLinkDisconnected(d) ? '#ef4444' : '#10b981')
      .attr('stroke-width', d => isLinkDisconnected(d) ? 2.5 : 2)
      .attr('stroke-opacity', d => isLinkDisconnected(d) ? 0.8 : 0.6)
      .attr('stroke-dasharray', d => isLinkDisconnected(d) ? '8,6' : 'none')
      .attr('marker-end', d => isLinkDisconnected(d) ? 'url(#arrow-disconnected)' : 'url(#arrow-online)')

    // Animate dashed lines for disconnected links (marching ants)
    link.filter(d => isLinkDisconnected(d))
      .append('animate')
      .attr('attributeName', 'stroke-dashoffset')
      .attr('from', '0')
      .attr('to', '28')
      .attr('dur', '1.5s')
      .attr('repeatCount', 'indefinite')

    // Create node groups
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .call(d3.drag<SVGGElement, TopoNode>()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended) as any)
      .on('click', (event, d) => {
        event.stopPropagation()
        setSelectedNode(d)
      })

    // Add circles for nodes — server nodes use their own color
    node.append('circle')
      .attr('r', d => d.type === 'server' ? 40 : 30)
      .attr('fill', d => {
        if (d.type === 'server') {
          return d.status === 'offline' ? '#991b1b' : (d.color || '#8b5cf6')
        }
        return d.status === 'online' ? '#10b981' : '#ef4444'
      })
      .attr('stroke', d => {
        if (d.type === 'server') {
          if (d.status === 'offline') return '#ef4444'
          const c = d3.color(d.color || '#8b5cf6')
          return c ? c.brighter(0.5).toString() : '#a78bfa'
        }
        return d.status === 'online' ? '#34d399' : '#f87171'
      })
      .attr('stroke-width', d => d.status === 'offline' ? 4 : 3)
      .style('cursor', 'pointer')
      .attr('filter', d => d.status === 'offline' ? 'url(#glow-red)' : null)

    // Add green pulse for ONLINE agents
    node.filter(d => d.status === 'online' && d.type === 'agent')
      .append('circle')
      .attr('r', 30)
      .attr('fill', 'none')
      .attr('stroke', '#10b981')
      .attr('stroke-width', 2)
      .attr('opacity', 0)
      .call(pulseGreen)

    // Add red danger pulse for OFFLINE servers
    node.filter(d => d.status === 'offline' && d.type === 'server')
      .append('circle')
      .attr('r', 40)
      .attr('fill', 'none')
      .attr('stroke', '#ef4444')
      .attr('stroke-width', 3)
      .attr('opacity', 0)
      .call(pulseRed)

    // Add red danger pulse for OFFLINE agents
    node.filter(d => d.status === 'offline' && d.type === 'agent')
      .append('circle')
      .attr('r', 30)
      .attr('fill', 'none')
      .attr('stroke', '#ef4444')
      .attr('stroke-width', 2)
      .attr('opacity', 0)
      .call(pulseRed)

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
      .attr('font-size', d => d.type === 'server' ? '24px' : '20px')
      .attr('fill', 'white')
      .text(d => d.type === 'server' ? '🖥️' : '💻')

    // Add labels
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.type === 'server' ? 55 : 45)
      .attr('font-size', '12px')
      .attr('fill', '#e2e8f0')
      .attr('font-weight', 'bold')
      .text(d => d.name)

    // Add server name sub-label for agents (helps distinguish same-named agents across servers)
    node.filter(d => d.type === 'agent' && !!d.serverName)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 58)
      .attr('font-size', '10px')
      .attr('fill', '#94a3b8')
      .text(d => d.serverName || '')

    // Add "OFFLINE" label under name for offline servers
    node.filter(d => d.type === 'server' && d.status === 'offline')
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 70)
      .attr('font-size', '11px')
      .attr('fill', '#ef4444')
      .attr('font-weight', 'bold')
      .text('DISCONNECTED')

    // Add warning badge (⚠) on offline server nodes
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

    // Add "NOT REACHABLE" label under name for offline agents
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

    // Update positions on simulation tick
    simulation.on('tick', () => {
      link
        .attr('x1', d => (d.source as TopoNode).x || 0)
        .attr('y1', d => (d.source as TopoNode).y || 0)
        .attr('x2', d => (d.target as TopoNode).x || 0)
        .attr('y2', d => (d.target as TopoNode).y || 0)

      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })

    // Drag functions
    function dragstarted(event: any, d: TopoNode) {
      if (!event.active) simulation.alphaTarget(0.3).restart()
      d.fx = d.x
      d.fy = d.y
    }

    function dragged(event: any, d: TopoNode) {
      d.fx = event.x
      d.fy = event.y
    }

    function dragended(event: any, d: TopoNode) {
      if (!event.active) simulation.alphaTarget(0)
      d.fx = null
      d.fy = null
    }

    // Green pulse for online agents
    function pulseGreen(selection: any) {
      (function repeat() {
        selection
          .transition()
          .duration(2000)
          .attr('r', 42)
          .attr('opacity', 0)
          .transition()
          .duration(0)
          .attr('r', 30)
          .attr('opacity', 0.6)
          .on('end', repeat)
      })()
    }

    // Red danger pulse for offline nodes
    function pulseRed(selection: any) {
      (function repeat() {
        selection
          .transition()
          .duration(1200)
          .attr('r', 55)
          .attr('opacity', 0)
          .transition()
          .duration(0)
          .attr('r', d => (d as TopoNode).type === 'server' ? 40 : 30)
          .attr('opacity', 0.9)
          .on('end', repeat)
      })()
    }

    // Auto-fit to view after simulation settles on initial load
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
    }, 1500)

    // Cleanup
    return () => {
      simulation.stop()
      clearTimeout(fitTimer)
    }
  }, [agents, servers, filteredData])

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
    if (!svgRef.current || !zoomRef.current || !simulationRef.current) return

    const svg = d3.select(svgRef.current)
    const width = svgRef.current.clientWidth
    const height = svgRef.current.clientHeight
    const nodes = simulationRef.current.nodes()

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

    svg.transition().duration(500).call(
      zoomRef.current.transform,
      d3.zoomIdentity.translate(translateX, translateY).scale(scale)
    )
  }

  const handleRestart = () => {
    simulationRef.current?.alpha(1).restart()
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
                <div>
                  <p className="text-sm text-slate-400">Connected Agents</p>
                  <p className="text-2xl font-bold text-purple-400">
                    {agents?.filter(a => `server-${a.server_id}` === selectedNode.id).length || 0}
                  </p>
                </div>
              )}

              {selectedNode.type === 'agent' && selectedNode.serverName && (
                <div>
                  <p className="text-sm text-slate-400">Server</p>
                  <p className="text-base font-semibold text-purple-400">{selectedNode.serverName}</p>
                </div>
              )}

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
