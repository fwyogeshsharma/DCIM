import { useEffect, useRef, useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import * as d3 from 'd3'
import { Activity, Server, ZoomIn, ZoomOut, Maximize2, RefreshCw, Edit3, Calendar } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'

interface Node extends d3.SimulationNodeDatum {
  id: string
  name: string
  type: 'server' | 'agent' | 'network'
  status: 'online' | 'offline'
  metrics?: number
  alerts?: number
  ip?: string
}

interface Link extends d3.SimulationLinkDatum<Node> {
  source: string | Node
  target: string | Node
  strength: number
}

type TimeFilter = 'today' | '30days' | 'all'

export default function Topology() {
  const { data: agents, isLoading } = useAgents()
  const navigate = useNavigate()
  const svgRef = useRef<SVGSVGElement>(null)
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [timeFilter, setTimeFilter] = useState<TimeFilter>('all')
  const simulationRef = useRef<d3.Simulation<Node, Link> | null>(null)

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

    // Create nodes with filtered data
    const nodes: Node[] = [
      {
        id: 'server',
        name: 'DCIM Server',
        type: 'server',
        status: 'online',
        x: width / 2,
        y: height / 2,
      },
      ...agents.map((agent) => {
        const filtered = filteredData?.find(f => f.agent_id === agent.agent_id)
        return {
          id: agent.agent_id,
          name: agent.hostname,
          type: 'agent' as const,
          status: agent.status as 'online' | 'offline',
          metrics: filtered?.metrics_count ?? agent.total_metrics,
          alerts: filtered?.alerts_count ?? agent.total_alerts,
          ip: agent.ip_address,
        }
      }),
    ]

    // Create links (all agents connect to server)
    const links: Link[] = agents.map(agent => ({
      source: agent.agent_id,
      target: 'server',
      strength: agent.status === 'online' ? 1 : 0.3,
    }))

    // Create force simulation
    const simulation = d3.forceSimulation<Node>(nodes)
      .force('link', d3.forceLink<Node, Link>(links)
        .id(d => d.id)
        .distance(200)
        .strength(d => d.strength))
      .force('charge', d3.forceManyBody().strength(-1000))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(60))

    simulationRef.current = simulation

    // Create arrow markers for links
    svg.append('defs').selectAll('marker')
      .data(['online', 'offline'])
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
      .attr('fill', d => d === 'online' ? '#10b981' : '#64748b')

    // Create links
    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .enter().append('line')
      .attr('stroke', d => {
        const sourceNode = nodes.find(n => n.id === (typeof d.source === 'string' ? d.source : d.source.id))
        return sourceNode?.status === 'online' ? '#10b981' : '#64748b'
      })
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0.6)
      .attr('marker-end', d => {
        const sourceNode = nodes.find(n => n.id === (typeof d.source === 'string' ? d.source : d.source.id))
        return `url(#arrow-${sourceNode?.status || 'offline'})`
      })

    // Create node groups
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .call(d3.drag<SVGGElement, Node>()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended) as any)
      .on('click', (event, d) => {
        event.stopPropagation()
        setSelectedNode(d)
      })

    // Add circles for nodes
    node.append('circle')
      .attr('r', d => d.type === 'server' ? 40 : 30)
      .attr('fill', d => {
        if (d.type === 'server') return '#8b5cf6'
        return d.status === 'online' ? '#10b981' : '#ef4444'
      })
      .attr('stroke', d => {
        if (d.type === 'server') return '#a78bfa'
        return d.status === 'online' ? '#34d399' : '#f87171'
      })
      .attr('stroke-width', 3)
      .style('cursor', 'pointer')

    // Add status pulse animation for online nodes
    node.filter(d => d.status === 'online' && d.type === 'agent')
      .append('circle')
      .attr('r', 30)
      .attr('fill', 'none')
      .attr('stroke', '#10b981')
      .attr('stroke-width', 2)
      .attr('opacity', 0)
      .call(pulse)

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

    // Add metrics badge
    node.filter(d => d.type === 'agent' && !!d.metrics)
      .append('circle')
      .attr('cx', 20)
      .attr('cy', -20)
      .attr('r', 12)
      .attr('fill', '#3b82f6')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)

    node.filter(d => d.type === 'agent' && !!d.metrics)
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
        .attr('x1', d => (d.source as Node).x || 0)
        .attr('y1', d => (d.source as Node).y || 0)
        .attr('x2', d => (d.target as Node).x || 0)
        .attr('y2', d => (d.target as Node).y || 0)

      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })

    // Drag functions
    function dragstarted(event: any, d: Node) {
      if (!event.active) simulation.alphaTarget(0.3).restart()
      d.fx = d.x
      d.fy = d.y
    }

    function dragged(event: any, d: Node) {
      d.fx = event.x
      d.fy = event.y
    }

    function dragended(event: any, d: Node) {
      if (!event.active) simulation.alphaTarget(0)
      d.fx = null
      d.fy = null
    }

    // Pulse animation
    function pulse(selection: any) {
      (function repeat() {
        selection
          .transition()
          .duration(2000)
          .attr('r', 40)
          .attr('opacity', 0)
          .transition()
          .duration(0)
          .attr('r', 30)
          .attr('opacity', 0.8)
          .on('end', repeat)
      })()
    }

    // Cleanup
    return () => {
      simulation.stop()
    }
  }, [agents, filteredData])

  const handleZoomIn = () => {
    d3.select(svgRef.current).transition().call(
      d3.zoom<SVGSVGElement, unknown>().scaleBy as any,
      1.3
    )
  }

  const handleZoomOut = () => {
    d3.select(svgRef.current).transition().call(
      d3.zoom<SVGSVGElement, unknown>().scaleBy as any,
      0.7
    )
  }

  const handleReset = () => {
    d3.select(svgRef.current).transition().call(
      d3.zoom<SVGSVGElement, unknown>().transform as any,
      d3.zoomIdentity
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
                <span className="text-slate-300">DCIM Server</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-green-500 border-2 border-green-400" />
                <span className="text-slate-300">Agent (Online)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-red-500 border-2 border-red-400" />
                <span className="text-slate-300">Agent (Offline)</span>
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
                <span className="text-slate-300">Active Alerts</span>
              </div>
            </div>
          </div>

          {/* Stats */}
          <div className="absolute top-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <Server className="w-4 h-4 text-purple-400" />
                <span className="text-slate-300">Total Agents: <span className="font-bold text-white">{agents?.length || 0}</span></span>
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

              {selectedNode.type === 'agent' && (
                <Link
                  to={`/app/agents/${selectedNode.id}`}
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
