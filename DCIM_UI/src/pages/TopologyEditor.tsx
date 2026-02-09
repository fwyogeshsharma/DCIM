import { useEffect, useRef, useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import * as d3 from 'd3'
import {
  Plus,
  Trash2,
  Link as LinkIcon,
  Edit3,
  Save,
  Download,
  Upload,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Grid3x3,
  Circle,
  GitBranch,
  Workflow,
  Target,
  Layers,
  Network as NetworkIcon,
} from 'lucide-react'

interface Node extends d3.SimulationNodeDatum {
  id: string
  name: string
  type: 'server' | 'agent' | 'switch' | 'router' | 'device' | 'custom'
  status?: 'online' | 'offline'
  color?: string
  x?: number
  y?: number
  fx?: number | null
  fy?: number | null
}

interface Link {
  source: string | Node
  target: string | Node
  id: string
}

type LayoutType = 'force' | 'star' | 'chain' | 'hierarchy' | 'circle' | 'concentric' | 'grid' | 'tree'

export default function TopologyEditor() {
  const { data: agents } = useAgents()
  const svgRef = useRef<SVGSVGElement>(null)
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Link[]>([])
  const [layout, setLayout] = useState<LayoutType>('force')
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [editingNode, setEditingNode] = useState<Node | null>(null)
  const [linkMode, setLinkMode] = useState(false)
  const [linkSource, setLinkSource] = useState<Node | null>(null)
  const [editName, setEditName] = useState('')
  const simulationRef = useRef<d3.Simulation<Node, any> | null>(null)

  // Initialize with agent data
  useEffect(() => {
    if (!agents || nodes.length > 0) return

    const initialNodes: Node[] = [
      {
        id: 'server',
        name: 'DCIM Server',
        type: 'server',
        status: 'online',
        color: '#8b5cf6',
      },
      ...agents.slice(0, 5).map((agent) => ({
        id: agent.agent_id,
        name: agent.hostname,
        type: 'agent' as const,
        status: agent.status as 'online' | 'offline',
        color: agent.status === 'online' ? '#10b981' : '#ef4444',
      })),
    ]

    const initialLinks: Link[] = agents.slice(0, 5).map(agent => ({
      source: agent.agent_id,
      target: 'server',
      id: `${agent.agent_id}-server`,
    }))

    setNodes(initialNodes)
    setLinks(initialLinks)
  }, [agents])

  // Apply layout
  const applyLayout = (layoutType: LayoutType, nodeList: Node[], linkList: Link[]) => {
    if (!svgRef.current) return

    const width = svgRef.current.clientWidth
    const height = svgRef.current.clientHeight
    const centerX = width / 2
    const centerY = height / 2

    // Create new node objects to ensure React detects changes
    let updatedNodes = nodeList.map(n => ({ ...n }))

    switch (layoutType) {
      case 'star': {
        const center = updatedNodes.find(n => n.type === 'server') || updatedNodes[0]
        const others = updatedNodes.filter(n => n.id !== center.id)
        const angleStep = (2 * Math.PI) / others.length
        const radius = Math.min(width, height) * 0.3

        center.x = centerX
        center.y = centerY
        center.fx = centerX
        center.fy = centerY

        others.forEach((node, i) => {
          const x = centerX + radius * Math.cos(i * angleStep)
          const y = centerY + radius * Math.sin(i * angleStep)
          node.x = x
          node.y = y
          node.fx = x
          node.fy = y
        })
        break
      }

      case 'chain': {
        const spacing = Math.min(width, height) / (updatedNodes.length + 1)
        updatedNodes.forEach((node, i) => {
          const x = spacing + (i * (width - 2 * spacing)) / (updatedNodes.length - 1 || 1)
          const y = centerY
          node.x = x
          node.y = y
          node.fx = x
          node.fy = y
        })
        break
      }

      case 'hierarchy':
      case 'tree': {
        const levels: Node[][] = []
        const visited = new Set<string>()
        const root = updatedNodes.find(n => n.type === 'server') || updatedNodes[0]

        const buildLevels = (node: Node, level: number) => {
          if (visited.has(node.id)) return
          visited.add(node.id)

          if (!levels[level]) levels[level] = []
          levels[level].push(node)

          const connected = linkList
            .filter(l =>
              (typeof l.source === 'string' ? l.source : l.source.id) === node.id ||
              (typeof l.target === 'string' ? l.target : l.target.id) === node.id
            )
            .map(l => {
              const targetId = (typeof l.target === 'string' ? l.target : l.target.id)
              const sourceId = (typeof l.source === 'string' ? l.source : l.source.id)
              return targetId === node.id ? sourceId : targetId
            })
            .map(id => updatedNodes.find(n => n.id === id))
            .filter((n): n is Node => n !== undefined && !visited.has(n.id))

          connected.forEach(n => buildLevels(n, level + 1))
        }

        buildLevels(root, 0)

        const levelHeight = height / (levels.length + 1)
        levels.forEach((levelNodes, level) => {
          const levelWidth = width / (levelNodes.length + 1)
          levelNodes.forEach((node, i) => {
            const x = levelWidth * (i + 1)
            const y = levelHeight * (level + 1)
            node.x = x
            node.y = y
            node.fx = x
            node.fy = y
          })
        })
        break
      }

      case 'circle': {
        const radius = Math.min(width, height) * 0.35
        const angleStep = (2 * Math.PI) / updatedNodes.length

        updatedNodes.forEach((node, i) => {
          const x = centerX + radius * Math.cos(i * angleStep)
          const y = centerY + radius * Math.sin(i * angleStep)
          node.x = x
          node.y = y
          node.fx = x
          node.fy = y
        })
        break
      }

      case 'concentric': {
        const maxRadius = Math.min(width, height) * 0.4
        const types = Array.from(new Set(updatedNodes.map(n => n.type)))
        const ringRadius = maxRadius / types.length

        types.forEach((type, ringIndex) => {
          const nodesInRing = updatedNodes.filter(n => n.type === type)
          const angleStep = (2 * Math.PI) / nodesInRing.length
          const radius = ringRadius * (ringIndex + 1)

          nodesInRing.forEach((node, i) => {
            const x = centerX + radius * Math.cos(i * angleStep)
            const y = centerY + radius * Math.sin(i * angleStep)
            node.x = x
            node.y = y
            node.fx = x
            node.fy = y
          })
        })
        break
      }

      case 'grid': {
        const cols = Math.ceil(Math.sqrt(updatedNodes.length))
        const rows = Math.ceil(updatedNodes.length / cols)
        const cellWidth = width / (cols + 1)
        const cellHeight = height / (rows + 1)

        updatedNodes.forEach((node, i) => {
          const col = i % cols
          const row = Math.floor(i / cols)
          const x = cellWidth * (col + 1)
          const y = cellHeight * (row + 1)
          node.x = x
          node.y = y
          node.fx = x
          node.fy = y
        })
        break
      }

      case 'force':
      default: {
        updatedNodes.forEach(node => {
          node.fx = null
          node.fy = null
        })
        break
      }
    }

    setNodes(updatedNodes)
  }

  // Render visualization
  useEffect(() => {
    if (!svgRef.current || nodes.length === 0) return

    const width = svgRef.current.clientWidth
    const height = svgRef.current.clientHeight

    // Clear previous
    d3.select(svgRef.current).selectAll('*').remove()

    const svg = d3.select(svgRef.current)
    const g = svg.append('g')

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => g.attr('transform', event.transform))

    svg.call(zoom)

    // Create simulation
    const simulation = d3.forceSimulation<Node>(nodes)
      .force('link', d3.forceLink<Node, Link>(links)
        .id(d => d.id)
        .distance(150))
      .force('charge', d3.forceManyBody().strength(-500))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(40))

    if (layout !== 'force') {
      // For static layouts, run one tick to initialize positions then stop
      simulation.tick()
      simulation.stop()
    }

    simulationRef.current = simulation

    // Render links
    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .enter().append('line')
      .attr('stroke', '#64748b')
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0.6)

    // Render nodes
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .call(d3.drag<SVGGElement, Node>()
        .on('start', function(event, d) {
          if (!event.active) simulation.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
          setSelectedNode(d)
        })
        .on('drag', function(event, d) {
          d.fx = event.x
          d.fy = event.y
        })
        .on('end', function(event, d) {
          if (!event.active) simulation.alphaTarget(0)
          if (layout === 'force') {
            d.fx = null
            d.fy = null
          }
        }) as any)
      .on('click', (event, d) => {
        event.stopPropagation()

        if (linkMode) {
          if (!linkSource) {
            setLinkSource(d)
          } else if (linkSource.id !== d.id) {
            const newLink: Link = {
              source: linkSource.id,
              target: d.id,
              id: `${linkSource.id}-${d.id}`,
            }
            setLinks([...links, newLink])
            setLinkSource(null)
            setLinkMode(false)
          }
        } else {
          setSelectedNode(d)
        }
      })

    node.append('circle')
      .attr('r', 25)
      .attr('fill', d => d.color || '#6366f1')
      .attr('stroke', d => d.status === 'online' ? '#34d399' : '#64748b')
      .attr('stroke-width', 3)
      .style('cursor', 'pointer')

    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 35)
      .attr('font-size', '11px')
      .attr('fill', '#e2e8f0')
      .text(d => d.name)

    // Update positions
    const updatePositions = () => {
      link
        .attr('x1', d => (d.source as Node).x || 0)
        .attr('y1', d => (d.source as Node).y || 0)
        .attr('x2', d => (d.target as Node).x || 0)
        .attr('y2', d => (d.target as Node).y || 0)

      node.attr('transform', d => `translate(${d.x},${d.y})`)
    }

    simulation.on('tick', updatePositions)

    // For static layouts, update positions immediately
    if (layout !== 'force') {
      updatePositions()
    }

    return () => {
      simulation.stop()
    }
  }, [nodes, links, layout, linkMode, linkSource])

  // Layout buttons
  const layouts: { type: LayoutType; icon: any; label: string }[] = [
    { type: 'force', icon: NetworkIcon, label: 'Force' },
    { type: 'star', icon: Target, label: 'Star' },
    { type: 'chain', icon: GitBranch, label: 'Chain' },
    { type: 'hierarchy', icon: Layers, label: 'Hierarchy' },
    { type: 'tree', icon: Workflow, label: 'Tree' },
    { type: 'circle', icon: Circle, label: 'Circle' },
    { type: 'concentric', icon: Target, label: 'Concentric' },
    { type: 'grid', icon: Grid3x3, label: 'Grid' },
  ]

  const addNode = () => {
    const newNode: Node = {
      id: `node-${Date.now()}`,
      name: `Node ${nodes.length + 1}`,
      type: 'custom',
      color: '#6366f1',
      x: svgRef.current ? svgRef.current.clientWidth / 2 : 400,
      y: svgRef.current ? svgRef.current.clientHeight / 2 : 300,
    }
    setNodes([...nodes, newNode])
  }

  const deleteNode = (nodeId: string) => {
    setNodes(nodes.filter(n => n.id !== nodeId))
    setLinks(links.filter(l =>
      (typeof l.source === 'string' ? l.source : l.source.id) !== nodeId &&
      (typeof l.target === 'string' ? l.target : l.target.id) !== nodeId
    ))
    setSelectedNode(null)
  }

  const deleteLink = (linkId: string) => {
    setLinks(links.filter(l => l.id !== linkId))
  }

  const startEditNode = (node: Node) => {
    setEditingNode(node)
    setEditName(node.name)
  }

  const saveNodeEdit = () => {
    if (editingNode) {
      setNodes(nodes.map(n =>
        n.id === editingNode.id ? { ...n, name: editName } : n
      ))
      setEditingNode(null)
    }
  }

  const exportTopology = () => {
    const data = { nodes, links }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'topology.json'
    a.click()
  }

  const importTopology = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) {
      const reader = new FileReader()
      reader.onload = (e) => {
        try {
          const data = JSON.parse(e.target?.result as string)
          setNodes(data.nodes)
          setLinks(data.links)
        } catch (error) {
          alert('Invalid topology file')
        }
      }
      reader.readAsText(file)
    }
  }

  return (
    <div className="h-full flex flex-col space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-4xl font-bold text-white">Topology Editor</h1>
          <p className="text-slate-400 mt-2">Create and customize your network topology</p>
        </div>
      </div>

      {/* Layout Selection */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-white mb-3">Layout Algorithms</h3>
        <div className="flex flex-wrap gap-2">
          {layouts.map(({ type, icon: Icon, label }) => (
            <button
              key={type}
              onClick={() => {
                setLayout(type)
                applyLayout(type, nodes, links)
              }}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                layout === type
                  ? 'bg-blue-500 text-white'
                  : 'bg-slate-700/50 text-slate-300 hover:bg-slate-600/50'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span className="text-sm font-medium">{label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Tools */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={addNode}
            className="flex items-center gap-2 px-4 py-2 bg-green-500/20 hover:bg-green-500/30 border border-green-500/30 text-green-400 rounded-lg transition-colors"
          >
            <Plus className="w-4 h-4" />
            Add Node
          </button>

          <button
            onClick={() => setLinkMode(!linkMode)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
              linkMode
                ? 'bg-blue-500 text-white'
                : 'bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 text-blue-400'
            }`}
          >
            <LinkIcon className="w-4 h-4" />
            {linkMode ? 'Click nodes to link' : 'Link Mode'}
          </button>

          <button
            onClick={exportTopology}
            className="flex items-center gap-2 px-4 py-2 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 text-purple-400 rounded-lg transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>

          <label className="flex items-center gap-2 px-4 py-2 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 text-purple-400 rounded-lg transition-colors cursor-pointer">
            <Upload className="w-4 h-4" />
            Import
            <input
              type="file"
              accept=".json"
              onChange={importTopology}
              className="hidden"
            />
          </label>

          <div className="ml-auto flex gap-2">
            <button
              onClick={() => {
                d3.select(svgRef.current).transition().call(
                  d3.zoom<SVGSVGElement, unknown>().scaleBy as any, 1.3
                )
              }}
              className="p-2 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg"
            >
              <ZoomIn className="w-5 h-5" />
            </button>
            <button
              onClick={() => {
                d3.select(svgRef.current).transition().call(
                  d3.zoom<SVGSVGElement, unknown>().scaleBy as any, 0.7
                )
              }}
              className="p-2 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg"
            >
              <ZoomOut className="w-5 h-5" />
            </button>
            <button
              onClick={() => {
                d3.select(svgRef.current).transition().call(
                  d3.zoom<SVGSVGElement, unknown>().transform as any,
                  d3.zoomIdentity
                )
              }}
              className="p-2 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg"
            >
              <Maximize2 className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 flex gap-4">
        {/* Canvas */}
        <div className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden relative">
          <svg ref={svgRef} className="w-full h-full" />

          {linkMode && linkSource && (
            <div className="absolute top-4 left-1/2 transform -translate-x-1/2 bg-blue-500/90 text-white px-4 py-2 rounded-lg">
              Click another node to create link from: {linkSource.name}
            </div>
          )}
        </div>

        {/* Properties Panel */}
        {selectedNode && (
          <div className="w-80 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xl font-bold text-white">Node Properties</h3>
              <button
                onClick={() => setSelectedNode(null)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            {editingNode?.id === selectedNode.id ? (
              <div className="space-y-3">
                <div>
                  <label className="block text-sm text-slate-400 mb-2">Name</label>
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-white"
                    autoFocus
                  />
                </div>
                <button
                  onClick={saveNodeEdit}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-500/20 hover:bg-green-500/30 border border-green-500/30 text-green-400 rounded-lg"
                >
                  <Save className="w-4 h-4" />
                  Save
                </button>
              </div>
            ) : (
              <>
                <div>
                  <p className="text-sm text-slate-400">Name</p>
                  <div className="flex items-center gap-2">
                    <p className="text-lg font-semibold text-white flex-1">{selectedNode.name}</p>
                    <button
                      onClick={() => startEditNode(selectedNode)}
                      className="p-2 hover:bg-white/5 rounded"
                    >
                      <Edit3 className="w-4 h-4 text-slate-400" />
                    </button>
                  </div>
                </div>

                <div>
                  <p className="text-sm text-slate-400">Type</p>
                  <p className="text-base text-white capitalize">{selectedNode.type}</p>
                </div>

                <div>
                  <p className="text-sm text-slate-400">ID</p>
                  <p className="text-xs font-mono text-slate-300">{selectedNode.id}</p>
                </div>

                <div>
                  <p className="text-sm text-slate-400 mb-2">Connected Links</p>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {links
                      .filter(l =>
                        (typeof l.source === 'string' ? l.source : l.source.id) === selectedNode.id ||
                        (typeof l.target === 'string' ? l.target : l.target.id) === selectedNode.id
                      )
                      .map(link => (
                        <div key={link.id} className="flex items-center justify-between bg-slate-900/50 rounded px-2 py-1">
                          <span className="text-xs text-slate-300">{link.id}</span>
                          <button
                            onClick={() => deleteLink(link.id)}
                            className="p-1 hover:bg-red-500/20 rounded"
                          >
                            <Trash2 className="w-3 h-3 text-red-400" />
                          </button>
                        </div>
                      ))}
                  </div>
                </div>

                <button
                  onClick={() => deleteNode(selectedNode.id)}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 text-red-400 rounded-lg"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete Node
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
