import { useEffect, useRef, useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import * as d3 from 'd3'
import { useNavigate } from 'react-router-dom'
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
  ArrowLeft,
  SlidersHorizontal,
  ChevronDown,
  ChevronUp,
  X,
} from 'lucide-react'

interface Node extends d3.SimulationNodeDatum {
  id: string
  name: string
  type: 'server' | 'agent' | 'switch' | 'router' | 'device' | 'custom' | 'firewall' | 'ap' | 'workstation' | 'printer' | 'camera'
  status?: 'online' | 'offline'
  color?: string
  x?: number
  y?: number
  fx?: number | null
  fy?: number | null
  // manual device fields
  isNew?: boolean
  isIoT?: boolean      // renders as square instead of circle
  isPDU?: boolean      // renders as hexagon
  isManual?: boolean
  isPending?: boolean  // form filled locally, not yet saved to DB
  pendingFormData?: {
    hostname: string; ip_address: string; server_id: string
    agent_group: string; certificate_cn: string; device_type: string
    location: string; description: string; color: string; mac_address: string; protocol: string
    specs: Record<string, string>
  }
  agentId?: string
  serverId?: string
  ip?: string
  protocol?: string
  agentGroup?: string
  certCN?: string
  deviceMeta?: {
    device_type?: string
    location?: string
    description?: string
    mac_address?: string
    protocol?: string
    specs?: Record<string, string>
  }
}

interface Link {
  source: string | Node
  target: string | Node
  id: string
}

type LayoutType = 'force' | 'star' | 'chain' | 'hierarchy' | 'circle' | 'concentric' | 'grid' | 'tree'

export default function TopologyEditor() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showTools, setShowTools] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved'>('idle')
  const [nodeAddMode, setNodeAddMode] = useState(false)
  const [iotAddMode, setIotAddMode] = useState(false)
  const [pduAddMode, setPduAddMode] = useState(false)
  const [nodeAddMsg, setNodeAddMsg] = useState('')
  const [formTab, setFormTab] = useState<'basic' | 'specs'>('basic')
  const [specsData, setSpecsData] = useState<Record<string, string>>({})
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: servers, isLoading: serversLoading } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })
  const dataLoading = agentsLoading || serversLoading
  const svgRef = useRef<SVGSVGElement>(null)
  const canvasContainerRef = useRef<HTMLDivElement>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const [resizeKey, setResizeKey] = useState(0)
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Link[]>([])
  const [layout, setLayout] = useState<LayoutType>('force')
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [editingNode, setEditingNode] = useState<Node | null>(null)
  const [linkMode, setLinkMode] = useState(false)
  const [linkSource, setLinkSource] = useState<Node | null>(null)
  const [linkAllToMode, setLinkAllToMode] = useState(false)
  const [editName, setEditName] = useState('')
  const simulationRef = useRef<d3.Simulation<Node, any> | null>(null)
  // Store original links so force layout can restore them
  const originalLinksRef = useRef<Link[]>([])
  // Track which node/link IDs came from live data so save only exports custom additions
  const liveLinkIdsRef = useRef<Set<string>>(new Set())
  // Track current zoom transform so form can be positioned near the node
  const zoomTransformRef = useRef({ x: 0, y: 0, k: 1 })
  // Track a newly added node that must be linked before the form opens
  const awaitingLinkNodeIdRef = useRef<string | null>(null)
  // Device form state
  const [formNode, setFormNode] = useState<Node | null>(null)
  const formNodeRef = useRef<Node | null>(null)
  const setFormNodeSynced = (n: Node | null) => { formNodeRef.current = n; setFormNode(n) }
  const [formNodePos, setFormNodePos] = useState({ x: 0, y: 0 })
  const [formDocked, setFormDocked] = useState(false)   // true = left-side slide panel
  const [formPanelVisible, setFormPanelVisible] = useState(true) // slide show/hide when docked
  const [formData, setFormData] = useState({
    hostname: '',
    ip_address: '',
    server_id: '',
    agent_group: 'manual',
    certificate_cn: '',
    device_type: 'device',
    location: '',
    description: '',
    color: '#6366f1',
    mac_address: '',
    protocol: '',
  })
  const [formError, setFormError] = useState<string | null>(null)
  const [formTouched, setFormTouched] = useState(false)

  // Initialize: mirrors the main Topology page — live data + any custom nodes from localStorage
  useEffect(() => {
    if (dataLoading || !agents || nodes.length > 0) return

    // Build server nodes
    const enabledServers = servers?.filter(s => s.enabled) || []
    const serverNodes: Node[] = enabledServers.map(s => ({
      id: `server-${s.id}`,
      name: s.name,
      type: 'server' as const,
      status: (s.health?.status === 'healthy' ? 'online' : 'offline') as 'online' | 'offline',
      color: s.metadata?.color || '#8b5cf6',
    }))

    // Fallback if no servers yet — derive from agents
    if (serverNodes.length === 0) {
      const uniqueServerIds = [...new Set(agents.map(a => a.server_id).filter(Boolean))]
      uniqueServerIds.forEach(sid => {
        const agentForServer = agents.find(a => a.server_id === sid)
        serverNodes.push({
          id: `server-${sid}`,
          name: agentForServer?.server_name || 'Server',
          type: 'server',
          status: 'online',
          color: '#8b5cf6',
        })
      })
    }

    const agentNodes: Node[] = agents.map(agent => ({
      id: `${agent.server_id}:${agent.agent_id}`,
      name: agent.hostname,
      type: (agent.metadata?.type || 'agent') as Node['type'],
      status: agent.status as 'online' | 'offline',
      color: agent.metadata?.color || (agent.status === 'online' ? '#10b981' : '#ef4444'),
      agentId: agent.agent_id,
      serverId: agent.server_id,
      isManual: agent.metadata?.manual === true,
      isIoT: agent.metadata?.isIoT === true,
      isPDU: agent.metadata?.isPDU === true,
      ip: agent.ip_address,
      protocol: agent.protocol,
      agentGroup: agent.group,
      certCN: agent.certificate_cn,
      deviceMeta: agent.metadata ? {
        device_type: agent.metadata.type,
        location: agent.metadata.location,
        description: agent.metadata.description,
        mac_address: agent.metadata.mac_address,
        protocol: agent.metadata.protocol,
        specs: agent.metadata.specs || {},
      } : undefined,
    }))

    const liveNodes: Node[] = [...serverNodes, ...agentNodes]

    // Agent → server links
    const liveLinks: Link[] = agents.map(agent => {
      const compoundId = `${agent.server_id}:${agent.agent_id}`
      const serverNodeId = serverNodes.find(s => s.id === `server-${agent.server_id}`)?.id || serverNodes[0]?.id
      return {
        source: compoundId,
        target: serverNodeId || 'server-unknown',
        id: `${compoundId}-${serverNodeId}`,
      }
    }).filter(l => l.target !== 'server-unknown')

    // Merge in custom nodes/links from localStorage (same source as main Topology)
    let extraNodes: Node[] = []
    let extraLinks: Link[] = []
    try {
      const raw = localStorage.getItem('dcim_custom_topology')
      if (raw) {
        const saved = JSON.parse(raw)
        const liveIds = new Set(liveNodes.map(n => n.id))
        extraNodes = ((saved.nodes as any[]) || [])
          .filter(cn => !liveIds.has(cn.id))
          .map(cn => ({
            id: cn.id,
            name: cn.name,
            type: (cn.type || 'custom') as Node['type'],
            status: cn.status as 'online' | 'offline' | undefined,
            color: cn.color,
          }))
        const allIds = new Set([...liveNodes.map(n => n.id), ...extraNodes.map(n => n.id)])
        const liveSet = new Set(liveLinks.map(l => l.id))
        extraLinks = ((saved.links as any[]) || [])
          .map(cl => ({
            source: typeof cl.source === 'string' ? cl.source : cl.source.id,
            target: typeof cl.target === 'string' ? cl.target : cl.target.id,
            id: cl.id,
          }))
          .filter(l => allIds.has(l.source as string) && allIds.has(l.target as string) && !liveSet.has(l.id))
      }
    } catch { /* ignore malformed data */ }

    const initialNodes = [...liveNodes, ...extraNodes]
    const initialLinks = [...liveLinks, ...extraLinks]

    liveLinkIdsRef.current = new Set(liveLinks.map(l => l.id))
    originalLinksRef.current = initialLinks
    setNodes(initialNodes)
    setLinks(initialLinks)
  }, [agents, servers, dataLoading])

  // Populate form data when a node is selected for editing
  useEffect(() => {
    if (!formNode) return
    setFormError(null)
    setFormTouched(false)
    setFormTab('basic')
    if (formNode.pendingFormData) {
      setFormData(formNode.pendingFormData)
      setSpecsData(formNode.pendingFormData.specs || {})
    } else if (formNode.isNew) {
      setFormData({
        hostname: '',
        ip_address: '',
        server_id: servers?.filter(s => s.enabled)[0]?.id || '',
        agent_group: 'manual',
        certificate_cn: '',
        device_type: 'device',
        location: '',
        description: '',
        color: formNode.isPDU ? '#eab308' : formNode.isIoT ? '#f97316' : '#6366f1',
        mac_address: '',
        protocol: '',
      })
      setSpecsData({})
    } else {
      setFormData({
        hostname: formNode.name || '',
        ip_address: formNode.ip || '',
        server_id: formNode.serverId || servers?.filter(s => s.enabled)[0]?.id || '',
        agent_group: formNode.agentGroup || 'manual',
        certificate_cn: formNode.certCN || '',
        device_type: formNode.deviceMeta?.device_type || (formNode.type !== 'agent' && formNode.type !== 'server' ? formNode.type : 'device'),
        location: formNode.deviceMeta?.location || '',
        description: formNode.deviceMeta?.description || '',
        color: formNode.color || '#6366f1',
        mac_address: formNode.deviceMeta?.mac_address || '',
        protocol: formNode.deviceMeta?.protocol || '',
      })
      setSpecsData(formNode.deviceMeta?.specs || {})
    }
  }, [formNode])

  // Exit nodeAddMode when no unfilled regular nodes remain
  useEffect(() => {
    if (nodeAddMode && nodes.filter(n => n.isNew && !n.isIoT && !n.isPDU).length === 0) {
      setNodeAddMode(false)
    }
  }, [nodes, nodeAddMode])

  // Exit iotAddMode when no unfilled IoT nodes remain
  useEffect(() => {
    if (iotAddMode && nodes.filter(n => n.isNew && n.isIoT).length === 0) {
      setIotAddMode(false)
    }
  }, [nodes, iotAddMode])

  // Exit pduAddMode when no unfilled PDU nodes remain
  useEffect(() => {
    if (pduAddMode && nodes.filter(n => n.isNew && n.isPDU).length === 0) {
      setPduAddMode(false)
    }
  }, [nodes, pduAddMode])

  // Apply layout — only repositions nodes, never replaces links.
  // Real topology connections (links) are always preserved across layout switches.
  const applyLayout = (layoutType: LayoutType, nodeList: Node[], linkList: Link[]) => {
    if (!svgRef.current) return

    const container = canvasContainerRef.current
    const width  = container ? container.clientWidth  : svgRef.current.clientWidth
    const height = container ? container.clientHeight : svgRef.current.clientHeight
    const centerX = width  / 2
    const centerY = height / 2
    const pad = 80 // padding from canvas edge

    // Work on copies so React detects changes; reset D3 velocities to prevent stale movement
    const updatedNodes = nodeList.map(n => ({ ...n, vx: 0, vy: 0 }))

    switch (layoutType) {
      case 'star': {
        // Server (or first node) at center; everything else in a ring around it
        const center = updatedNodes.find(n => n.type === 'server') || updatedNodes[0]
        const others = updatedNodes.filter(n => n.id !== center.id)
        const radius = Math.min(width - pad * 2, height - pad * 2) * 0.4
        const step   = (2 * Math.PI) / (others.length || 1)
        center.x = centerX; center.y = centerY; center.fx = centerX; center.fy = centerY
        others.forEach((node, i) => {
          const angle = i * step - Math.PI / 2
          node.x = centerX + radius * Math.cos(angle)
          node.y = centerY + radius * Math.sin(angle)
          node.fx = node.x; node.fy = node.y
        })
        break
      }

      case 'chain': {
        // Evenly spaced horizontal chain
        const n    = updatedNodes.length
        const usableW = width  - pad * 2
        updatedNodes.forEach((node, i) => {
          node.x = pad + (n > 1 ? (usableW * i) / (n - 1) : usableW / 2)
          node.y = centerY
          node.fx = node.x; node.fy = node.y
        })
        break
      }

      case 'hierarchy':
      case 'tree': {
        // Build adjacency from actual links
        const normLinks = linkList.map(l => ({
          source: typeof l.source === 'string' ? l.source : (l.source as Node).id,
          target: typeof l.target === 'string' ? l.target : (l.target as Node).id,
        }))
        const adjMap = new Map<string, string[]>()
        updatedNodes.forEach(n => adjMap.set(n.id, []))
        normLinks.forEach(({ source, target }) => {
          adjMap.get(source)?.push(target)
          adjMap.get(target)?.push(source)
        })

        // BFS from root (prefer server node)
        const root    = updatedNodes.find(n => n.type === 'server') || updatedNodes[0]
        const levels: Node[][] = []
        const visited = new Set<string>([root.id])
        let queue     = [root]

        while (queue.length > 0) {
          const level: Node[] = []
          const next:  Node[] = []
          for (const node of queue) {
            level.push(node)
            for (const nbId of adjMap.get(node.id) || []) {
              if (!visited.has(nbId)) {
                visited.add(nbId)
                const nb = updatedNodes.find(n => n.id === nbId)
                if (nb) next.push(nb)
              }
            }
          }
          levels.push(level)
          queue = next
        }

        // Disconnected nodes appended as an extra level
        const isolated = updatedNodes.filter(n => !visited.has(n.id))
        if (isolated.length > 0) levels.push(isolated)

        // Fixed vertical gap between levels, centred in the canvas
        const LEVEL_GAP  = Math.min(130, (height - pad * 2) / Math.max(levels.length, 1))
        const totalH     = LEVEL_GAP * (levels.length - 1)
        const baseY      = centerY - totalH / 2

        levels.forEach((levelNodes, li) => {
          const n          = levelNodes.length
          // Max horizontal gap between nodes, centred
          const NODE_GAP   = Math.min(150, (width - pad * 2) / Math.max(n, 1))
          const totalW     = NODE_GAP * (n - 1)
          const baseX      = centerX - totalW / 2

          levelNodes.forEach((node, ni) => {
            node.x  = n === 1 ? centerX : baseX + ni * NODE_GAP
            node.y  = baseY + li * LEVEL_GAP
            node.fx = node.x
            node.fy = node.y
          })
        })
        break
      }

      case 'circle': {
        const radius = Math.min(width - pad * 2, height - pad * 2) * 0.42
        const step   = (2 * Math.PI) / updatedNodes.length
        updatedNodes.forEach((node, i) => {
          const angle = i * step - Math.PI / 2
          node.x = centerX + radius * Math.cos(angle)
          node.y = centerY + radius * Math.sin(angle)
          node.fx = node.x; node.fy = node.y
        })
        break
      }

      case 'concentric': {
        // Inner rings = servers; outer rings = agents/devices etc.
        const typeOrder = ['server', 'switch', 'router', 'firewall', 'ap', 'agent', 'workstation', 'printer', 'camera', 'device', 'custom']
        const types = Array.from(new Set(updatedNodes.map(n => n.type)))
          .sort((a, b) => typeOrder.indexOf(a) - typeOrder.indexOf(b))
        const maxRadius = Math.min(width - pad * 2, height - pad * 2) * 0.44
        const ringGap   = maxRadius / (types.length || 1)

        types.forEach((type, ri) => {
          const ring    = updatedNodes.filter(n => n.type === type)
          const radius  = ringGap * (ri + 1)
          const step    = (2 * Math.PI) / (ring.length || 1)
          ring.forEach((node, i) => {
            const angle = i * step - Math.PI / 2
            node.x = centerX + radius * Math.cos(angle)
            node.y = centerY + radius * Math.sin(angle)
            node.fx = node.x; node.fy = node.y
          })
        })
        break
      }

      case 'grid': {
        // Sort by type for visual grouping (servers → switches → agents → devices)
        const typeOrder = ['server','switch','router','firewall','ap','agent','workstation','printer','camera','device','custom']
        updatedNodes.sort((a, b) =>
          (typeOrder.indexOf(a.type) - typeOrder.indexOf(b.type)) || a.name.localeCompare(b.name)
        )

        const n    = updatedNodes.length
        const cols = Math.ceil(Math.sqrt(n))
        const rows = Math.ceil(n / cols)

        // Cap cell size so nodes don't spread to the canvas edges for small counts
        const cellW = Math.min(150, (width  - pad * 2) / cols)
        const cellH = Math.min(130, (height - pad * 2) / rows)

        // Centre the whole grid
        const gridW  = cols * cellW
        const gridH  = rows * cellH
        const startX = centerX - gridW / 2
        const startY = centerY - gridH / 2

        updatedNodes.forEach((node, i) => {
          const col   = i % cols
          const row   = Math.floor(i / cols)
          node.x  = startX + col * cellW + cellW / 2
          node.y  = startY + row * cellH + cellH / 2
          node.fx = node.x
          node.fy = node.y
        })
        break
      }

      case 'force':
      default: {
        // Release ALL position constraints so force simulation takes over.
        // Only keep a node pinned if its form is currently open (prevents form jumping).
        updatedNodes.forEach(node => {
          if (formNode?.id !== node.id) {
            node.fx = null
            node.fy = null
          }
        })
        break
      }
    }

    // Only update node positions — links are NEVER changed by layout switches.
    setNodes(updatedNodes)

    // Reposition an open device form to follow its node's new position
    if (formNode) {
      const fn = updatedNodes.find(n => n.id === formNode.id)
      if (fn) {
        const t  = zoomTransformRef.current
        const sx = t.x + (fn.x || 0) * t.k
        const sy = t.y + (fn.y || 0) * t.k
        const cW = svgRef.current?.clientWidth  || 800
        const cH = svgRef.current?.clientHeight || 600
        setFormNodePos({
          x: Math.min(Math.max(sx + 50, 10), cW - 360),
          y: Math.min(Math.max(sy - 160, 10), cH - 560),
        })
      }
    }
  }

  // ResizeObserver — keep SVG sized to its container and re-trigger D3 on resize
  useEffect(() => {
    const el = canvasContainerRef.current
    if (!el) return
    let timer: ReturnType<typeof setTimeout>
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      // Immediately resize the SVG element so there's no visual gap
      if (svgRef.current) {
        svgRef.current.setAttribute('width', String(Math.round(width)))
        svgRef.current.setAttribute('height', String(Math.round(height)))
      }
      // Debounce the full D3 re-render (recalculates forces & positions)
      clearTimeout(timer)
      timer = setTimeout(() => setResizeKey(k => k + 1), 150)
    })
    ro.observe(el)
    return () => { ro.disconnect(); clearTimeout(timer) }
  }, [])

  // Render visualization
  useEffect(() => {
    if (!svgRef.current) return
    // Always clear and re-setup SVG, even when empty (keeps zoom/pan working)
    if (nodes.length === 0) {
      d3.select(svgRef.current).selectAll('*').remove()
      return
    }

    // Prefer the canvas container's measured size; fall back to SVG client size
    const container = canvasContainerRef.current
    const width  = container ? container.clientWidth  : svgRef.current.clientWidth
    const height = container ? container.clientHeight : svgRef.current.clientHeight

    // Clear previous
    d3.select(svgRef.current).selectAll('*').remove()

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height)
    const g = svg.append('g')

    // Zoom — also track transform so form can be positioned near nodes
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
        zoomTransformRef.current = { x: event.transform.x, y: event.transform.y, k: event.transform.k }
      })

    svg.call(zoom)
    zoomRef.current = zoom

    // Always normalise links to string IDs before handing to D3.
    // D3 forceLink mutates link objects in place (source/target become node
    // references). If the same mutated objects are passed to a new simulation
    // (after a React state update), D3 can't re-resolve stale references to
    // replaced node objects → links detach. Fresh string IDs fix this.
    // Also filter out any links whose endpoints no longer exist in nodes
    // (e.g. a node deleted while its link removal was batched separately).
    const nodeIdSet = new Set(nodes.map(n => n.id))
    const freshLinks: Link[] = links
      .map(l => ({
        source: typeof l.source === 'string' ? l.source : (l.source as Node).id,
        target: typeof l.target === 'string' ? l.target : (l.target as Node).id,
        id: l.id,
      }))
      .filter(l => nodeIdSet.has(l.source as string) && nodeIdSet.has(l.target as string))

    // Create simulation
    const simulation = d3.forceSimulation<Node>(nodes)
      .force('link', d3.forceLink<Node, Link>(freshLinks)
        .id(d => d.id)
        .distance(150))
      .force('charge', d3.forceManyBody().strength(-500))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(40))

    if (layout !== 'force') {
      // Static layout: stop immediately (positions are already set via fx/fy)
      simulation.stop()
    }
    // Force layout: simulation auto-starts on creation — no explicit restart needed

    simulationRef.current = simulation

    // Build a live node lookup — used for both edge coloring and position rendering.
    // NOTE: forceLink.initialize() (called during .force('link',...)) has already resolved
    // freshLinks[i].source / .target from string IDs to Node objects at this point.
    // So we always extract the ID safely before doing a map lookup.
    const nodeMap = new Map(nodes.map(n => [n.id, n]))
    const nodeId  = (d: string | Node) => typeof d === 'string' ? d : d.id
    const nodePos = (d: string | Node) => nodeMap.get(nodeId(d)) ?? { x: 0, y: 0 }

    const link = g.append('g')
      .selectAll('line')
      .data(freshLinks)
      .enter().append('line')
      .attr('stroke', '#64748b')
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0.7)

    // Render nodes
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .call(d3.drag<SVGGElement, Node>()
        .on('start', function(event, d) {
          event.sourceEvent.stopPropagation()
          d3.select(this).raise()
          if (layout === 'force') {
            if (!event.active) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          }
          setSelectedNode(d)
        })
        .on('drag', function(event, d) {
          // Use d3.pointer with the zoom group so coordinates are correct at any zoom level
          const [x, y] = d3.pointer(event.sourceEvent, g.node())
          if (layout === 'force') {
            d.fx = x
            d.fy = y
          } else {
            // Static layout — move node directly, update links immediately
            d.x = x
            d.y = y
            d.fx = x
            d.fy = y
            d3.select(this).attr('transform', `translate(${x},${y})`)
            link
              .attr('x1', (l: any) => nodePos(l.source).x ?? 0)
              .attr('y1', (l: any) => nodePos(l.source).y ?? 0)
              .attr('x2', (l: any) => nodePos(l.target).x ?? 0)
              .attr('y2', (l: any) => nodePos(l.target).y ?? 0)
          }
          // Keep the device form tracking the node as it's dragged
          if (formNodeRef.current && d.id === formNodeRef.current.id) {
            const t = zoomTransformRef.current
            const sx = t.x + x * t.k
            const sy = t.y + y * t.k
            const cW = svgRef.current?.clientWidth || 800
            const cH = svgRef.current?.clientHeight || 600
            setFormNodePos({
              x: Math.min(Math.max(sx + 50, 10), cW - 300),
              y: Math.min(Math.max(sy - 160, 10), cH - 390),
            })
          }
        })
        .on('end', function(event, d) {
          if (layout === 'force') {
            if (!event.active) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          }
        }) as any)
      .on('click', (event, d) => {
        event.stopPropagation()

        if (linkAllToMode) {
          // Link every unfilled (isNew) node to this target, skipping self-links
          const newNodes = nodes.filter(n => n.isNew && n.id !== d.id)
          if (newNodes.length > 0) {
            const bulkLinks: Link[] = newNodes.map(n => ({
              source: n.id,
              target: d.id,
              id: `${n.id}-${d.id}`,
            }))
            setLinks(prev => {
              const existingIds = new Set(prev.map(l => l.id))
              return [...prev, ...bulkLinks.filter(l => !existingIds.has(l.id))]
            })
          }
          setLinkAllToMode(false)
          return
        }

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
            awaitingLinkNodeIdRef.current = null
          }
        } else {
          setSelectedNode(d)
          if (d.isNew || d.isManual || d.isPending) {
            openFormNearNode(d)
          } else {
            setFormNodeSynced(null)
          }
        }
      })

    // Regular nodes — circle
    node.filter(d => !d.isIoT && !d.isPDU)
      .append('circle')
      .attr('r', 25)
      .attr('fill', d => d.color || '#6366f1')
      .attr('stroke', d => d.status === 'online' ? '#34d399' : '#64748b')
      .attr('stroke-width', 3)
      .style('cursor', 'pointer')

    // IoT nodes — square
    node.filter(d => !!d.isIoT)
      .append('rect')
      .attr('width', 38)
      .attr('height', 38)
      .attr('x', -19)
      .attr('y', -19)
      .attr('rx', 5)
      .attr('fill', d => d.color || '#f97316')
      .attr('stroke', d => d.status === 'online' ? '#34d399' : '#64748b')
      .attr('stroke-width', 3)
      .style('cursor', 'pointer')

    // PDU nodes — vertical pill (tall rounded rectangle)
    node.filter(d => !!d.isPDU)
      .append('rect')
      .attr('width', 22)
      .attr('height', 44)
      .attr('x', -11)
      .attr('y', -22)
      .attr('rx', 11)
      .attr('ry', 11)
      .attr('fill', d => d.color || '#eab308')
      .attr('stroke', d => d.status === 'online' ? '#34d399' : '#64748b')
      .attr('stroke-width', 3)
      .style('cursor', 'pointer')

    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 35)
      .attr('font-size', '11px')
      .attr('fill', '#e2e8f0')
      .text(d => d.name)

    // Update positions — use nodePos() so both string IDs and resolved Node objects work
    const updatePositions = () => {
      link
        .attr('x1', d => nodePos(d.source).x ?? 0)
        .attr('y1', d => nodePos(d.source).y ?? 0)
        .attr('x2', d => nodePos(d.target).x ?? 0)
        .attr('y2', d => nodePos(d.target).y ?? 0)

      node.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`)
    }

    simulation.on('tick', updatePositions)

    // Always update positions immediately so edges are never stuck at (0,0)
    updatePositions()

    return () => {
      simulation.stop()
    }
  }, [nodes, links, layout, linkMode, linkSource, linkAllToMode, resizeKey])

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

  const openFormNearNode = (node: Node) => {
    const t = zoomTransformRef.current
    const sx = t.x + (node.x || 0) * t.k
    const sy = t.y + (node.y || 0) * t.k
    const canvasW = svgRef.current?.clientWidth || 800
    const canvasH = svgRef.current?.clientHeight || 600
    setFormNodePos({
      x: Math.min(Math.max(sx + 50, 10), canvasW - 300),
      y: Math.min(Math.max(sy - 160, 10), canvasH - 390),
    })
    setFormError(null)
    setFormTouched(false)
    // Pre-populate form from existing node data
    if (node.pendingFormData) {
      setFormData(node.pendingFormData)
    } else if (node.isManual || node.isPending) {
      setFormData({
        hostname: node.name || '',
        ip_address: node.ip || '',
        server_id: node.serverId || '',
        agent_group: node.agentGroup || 'manual',
        certificate_cn: node.certCN || '',
        device_type: node.deviceMeta?.device_type || 'device',
        location: node.deviceMeta?.location || '',
        description: node.deviceMeta?.description || '',
        color: node.color || '#6366f1',
        mac_address: node.deviceMeta?.mac_address || '',
        protocol: node.deviceMeta?.protocol || '',
      })
    } else {
      setFormData({ hostname: '', ip_address: '', server_id: '', agent_group: 'manual', certificate_cn: '', device_type: 'device', location: '', description: '', color: node.color || (node.isPDU ? '#eab308' : node.isIoT ? '#f97316' : '#6366f1'), mac_address: '', protocol: '' })
    }
    setFormNodeSynced(node)
  }

  const showNodeAddMsg = (msg: string) => {
    setNodeAddMsg(msg)
    setTimeout(() => setNodeAddMsg(''), 3000)
  }

  const addNode = () => {
    const unfilledCount = nodes.filter(n => n.isNew && !n.isIoT && !n.isPDU).length
    if (unfilledCount >= 15) {
      showNodeAddMsg('First fill the form of all 15 nodes before adding more')
      return
    }
    const cx = svgRef.current ? svgRef.current.clientWidth / 2 : 400
    const cy = svgRef.current ? svgRef.current.clientHeight / 2 : 300
    const tempId = `temp-${Date.now()}`
    const newNode: Node = {
      id: tempId,
      name: 'New Device',
      type: 'device',
      color: '#6366f1',
      isNew: true,
      isIoT: false,
      x: cx,
      y: cy,
      fx: cx,
      fy: cy,
    }
    setNodes(prev => [...prev, newNode])
    setSelectedNode(newNode)
    setNodeAddMode(true)
  }

  const addOneMoreNode = () => {
    addNode()
  }

  const removeLastNewNode = () => {
    setNodes(prev => {
      const lastNew = [...prev].reverse().find(n => n.isNew && !n.isIoT && !n.isPDU)
      if (!lastNew) return prev
      const id = lastNew.id
      if (awaitingLinkNodeIdRef.current === id) awaitingLinkNodeIdRef.current = null
      setLinks(lp => lp.filter(l =>
        (typeof l.source === 'string' ? l.source : (l.source as Node).id) !== id &&
        (typeof l.target === 'string' ? l.target : (l.target as Node).id) !== id
      ))
      if (linkMode && linkSource?.id === id) {
        setLinkMode(false)
        setLinkSource(null)
      }
      const next = prev.filter(n => n.id !== id)
      if (next.filter(n => n.isNew && !n.isIoT && !n.isPDU).length === 0) setNodeAddMode(false)
      return next
    })
  }

  const addIoTNode = () => {
    const unfilledCount = nodes.filter(n => n.isNew).length
    if (unfilledCount >= 15) {
      showNodeAddMsg('First fill the form of all 15 nodes before adding more')
      return
    }
    const cx = svgRef.current ? svgRef.current.clientWidth / 2 : 400
    const cy = svgRef.current ? svgRef.current.clientHeight / 2 : 300
    const tempId = `temp-iot-${Date.now()}`
    const newNode: Node = {
      id: tempId,
      name: 'New IoT',
      type: 'device',
      color: '#f97316',
      isNew: true,
      isIoT: true,
      x: cx,
      y: cy,
      fx: cx,
      fy: cy,
    }
    setNodes(prev => [...prev, newNode])
    setSelectedNode(newNode)
    setIotAddMode(true)
  }

  const addOneMoreIoTNode = () => {
    addIoTNode()
  }

  const removeLastNewIoTNode = () => {
    setNodes(prev => {
      const lastNew = [...prev].reverse().find(n => n.isNew && n.isIoT)
      if (!lastNew) return prev
      const id = lastNew.id
      if (awaitingLinkNodeIdRef.current === id) awaitingLinkNodeIdRef.current = null
      setLinks(lp => lp.filter(l =>
        (typeof l.source === 'string' ? l.source : (l.source as Node).id) !== id &&
        (typeof l.target === 'string' ? l.target : (l.target as Node).id) !== id
      ))
      if (linkMode && linkSource?.id === id) {
        setLinkMode(false)
        setLinkSource(null)
      }
      const next = prev.filter(n => n.id !== id)
      if (next.filter(n => n.isNew && n.isIoT).length === 0) setIotAddMode(false)
      return next
    })
  }

  const addPDUNode = () => {
    const unfilledCount = nodes.filter(n => n.isNew).length
    if (unfilledCount >= 15) {
      showNodeAddMsg('First fill the form of all 15 nodes before adding more')
      return
    }
    const cx = svgRef.current ? svgRef.current.clientWidth / 2 : 400
    const cy = svgRef.current ? svgRef.current.clientHeight / 2 : 300
    const tempId = `temp-pdu-${Date.now()}`
    const newNode: Node = {
      id: tempId,
      name: 'New PDU',
      type: 'device',
      color: '#eab308',
      isNew: true,
      isPDU: true,
      x: cx,
      y: cy,
      fx: cx,
      fy: cy,
    }
    setNodes(prev => [...prev, newNode])
    setSelectedNode(newNode)
    setPduAddMode(true)
  }

  const addOneMorePDUNode = () => {
    addPDUNode()
  }

  const removeLastNewPDUNode = () => {
    setNodes(prev => {
      const lastNew = [...prev].reverse().find(n => n.isNew && n.isPDU)
      if (!lastNew) return prev
      const id = lastNew.id
      if (awaitingLinkNodeIdRef.current === id) awaitingLinkNodeIdRef.current = null
      setLinks(lp => lp.filter(l =>
        (typeof l.source === 'string' ? l.source : (l.source as Node).id) !== id &&
        (typeof l.target === 'string' ? l.target : (l.target as Node).id) !== id
      ))
      if (linkMode && linkSource?.id === id) {
        setLinkMode(false)
        setLinkSource(null)
      }
      const next = prev.filter(n => n.id !== id)
      if (next.filter(n => n.isNew && n.isPDU).length === 0) setPduAddMode(false)
      return next
    })
  }

  const cancelForm = () => {
    if (formNode?.isNew) {
      setNodes(prev => prev.filter(n => n.id !== formNode.id))
      setLinks(prev => prev.filter(l =>
        (typeof l.source === 'string' ? l.source : (l.source as Node).id) !== formNode.id &&
        (typeof l.target === 'string' ? l.target : (l.target as Node).id) !== formNode.id
      ))
    }
    setFormNodeSynced(null)
    setSelectedNode(null)
    setFormDocked(false)
    setFormPanelVisible(true)
    setFormTab('basic')
    setSpecsData({})
  }

  const handleFormNext = () => {
    setFormTouched(true)
    const required: (keyof typeof formData)[] = ['hostname', 'ip_address', 'server_id', 'agent_group', 'device_type', 'location', 'description', 'mac_address']
    const missing = required.filter(f => !formData[f]?.trim())
    if (missing.length > 0) {
      setFormError('Please fill in all required fields.')
      return
    }
    setFormError(null)
    setFormTab('specs')
  }

  const saveDeviceNode = () => {
    setFormTouched(true)
    const required: (keyof typeof formData)[] = ['hostname', 'ip_address', 'server_id', 'agent_group', 'device_type', 'location', 'description', 'mac_address']
    const missing = required.filter(f => !formData[f]?.trim())
    if (missing.length > 0) {
      setFormError('Please fill in all required fields.')
      setFormTab('basic')
      return
    }
    if (!formNode) return

    // Store form data locally on the node — DB save happens on "Save Topology"
    const updatedNode: Node = {
      ...formNode,
      name: formData.hostname,
      type: formData.device_type as Node['type'],
      color: formData.color,
      isNew: false,
      isPending: true,
      ip: formData.ip_address,
      serverId: formData.server_id,
      agentGroup: formData.agent_group,
      certCN: formData.certificate_cn || undefined,
      deviceMeta: {
        device_type: formData.device_type,
        location: formData.location,
        description: formData.description,
        mac_address: formData.mac_address,
        protocol: formData.protocol,
      },
      pendingFormData: { ...formData, specs: { ...specsData } },
      fx: formNode.x ?? formNode.fx ?? null,
      fy: formNode.y ?? formNode.fy ?? null,
    }
    setNodes(prev => prev.map(n => n.id === formNode.id ? updatedNode : n))
    setFormNodeSynced(null)
    setSelectedNode(updatedNode)
  }

  const deleteNode = async (nodeId: string) => {
    const node = nodes.find(n => n.id === nodeId)

    if (node?.agentId) {
      // Node is saved in DB — delete it
      try {
        await api.deleteAgent(node.agentId)
        queryClient.invalidateQueries({ queryKey: ['agents'] })
      } catch (err: any) {
        alert(`Failed to delete from DB: ${err.message}`)
        return
      }
    }

    setNodes(prev => prev.filter(n => n.id !== nodeId))
    setLinks(prev => prev.filter(l =>
      (typeof l.source === 'string' ? l.source : (l.source as Node).id) !== nodeId &&
      (typeof l.target === 'string' ? l.target : (l.target as Node).id) !== nodeId
    ))
    setSelectedNode(null)
    if (formNode?.id === nodeId) setFormNodeSynced(null)

    // Remove from localStorage so it doesn't reappear on refresh
    try {
      const raw = localStorage.getItem('dcim_custom_topology')
      if (raw) {
        const saved = JSON.parse(raw)
        saved.nodes = (saved.nodes || []).filter((n: any) => n.id !== nodeId)
        saved.links = (saved.links || []).filter((l: any) => l.source !== nodeId && l.target !== nodeId)
        localStorage.setItem('dcim_custom_topology', JSON.stringify(saved))
      }
    } catch {}
  }

  const deleteLink = (linkId: string) => {
    setLinks(prev => prev.filter(l => l.id !== linkId))
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

  const fitView = () => {
    if (!svgRef.current || !zoomRef.current || nodes.length === 0) return
    const container = canvasContainerRef.current
    const w = container ? container.clientWidth  : svgRef.current.clientWidth
    const h = container ? container.clientHeight : svgRef.current.clientHeight
    const padding = 80

    const xs = nodes.map(n => n.x ?? 0)
    const ys = nodes.map(n => n.y ?? 0)
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)

    const bw = maxX - minX || 1
    const bh = maxY - minY || 1

    const scale = Math.min(
      (w - padding * 2) / bw,
      (h - padding * 2) / bh,
      1.5
    )

    const tx = w / 2 - (minX + bw / 2) * scale
    const ty = h / 2 - (minY + bh / 2) * scale

    d3.select(svgRef.current)
      .transition()
      .duration(600)
      .ease(d3.easeCubicInOut)
      .call(zoomRef.current.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))
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

  const saveTopology = async () => {
    setSaveStatus('saved')

    // Save pending nodes to DB first
    let finalNodes = [...nodes]
    let finalLinks = [...links]
    const pendingNodes = nodes.filter(n => n.isPending && n.pendingFormData)

    for (const node of pendingNodes) {
      try {
        const fd = node.pendingFormData!
        const saved = await api.createAgent({
          server_id: fd.server_id,
          hostname: fd.hostname,
          ip_address: fd.ip_address,
          agent_group: fd.agent_group,
          certificate_cn: fd.certificate_cn || undefined,
          protocol: fd.protocol || undefined,
          metadata: {
            manual: true,
            type: fd.device_type,
            location: fd.location,
            description: fd.description,
            color: fd.color,
            mac_address: fd.mac_address,
            isIoT: node.isIoT || false,
            isPDU: node.isPDU || false,
            device_category: node.isPDU ? 'pdu' : node.isIoT ? 'iot' : 'node',
            specs: fd.specs || {},
          },
        })
        const newId = `${saved.server_id}:${saved.agent_id}`
        const savedNode: Node = {
          ...node,
          id: newId,
          name: saved.hostname,
          type: (saved.metadata?.type || 'device') as Node['type'],
          color: saved.metadata?.color || '#6366f1',
          status: 'offline',
          isPending: false,
          pendingFormData: undefined,
          isNew: false,
          isManual: true,
          isIoT: node.isIoT || false,
          isPDU: node.isPDU || false,
          agentId: saved.agent_id,
          serverId: saved.server_id,
          ip: saved.ip_address,
          agentGroup: saved.agent_group,
        }
        finalNodes = finalNodes.map(n => n.id === node.id ? savedNode : n)
        finalLinks = finalLinks.map(l => ({
          ...l,
          source: (typeof l.source === 'string' ? l.source : (l.source as Node).id) === node.id ? newId : l.source,
          target: (typeof l.target === 'string' ? l.target : (l.target as Node).id) === node.id ? newId : l.target,
        }))
      } catch (err: any) {
        console.error('Failed to save node to DB:', err)
      }
    }

    if (pendingNodes.length > 0) {
      setNodes(finalNodes)
      setLinks(finalLinks)
      queryClient.invalidateQueries({ queryKey: ['agents'] })
    }

    // Save only custom links to localStorage
    const customLinks = finalLinks.filter(l => !liveLinkIdsRef.current.has(l.id))
    const data = {
      nodes: finalNodes.map(n => ({ id: n.id, name: n.name, type: n.type, status: n.status, color: n.color })),
      links: customLinks.map(l => ({
        source: typeof l.source === 'string' ? l.source : (l.source as Node).id,
        target: typeof l.target === 'string' ? l.target : (l.target as Node).id,
        id: l.id,
      })),
    }
    localStorage.setItem('dcim_custom_topology', JSON.stringify(data))
    setTimeout(() => setSaveStatus('idle'), 2500)
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
    <div className="h-full flex flex-col gap-2 overflow-hidden">

      {/* ── Compact top bar ── */}
      <div className="flex items-center gap-2 bg-slate-800/60 backdrop-blur-sm border border-white/10 rounded-xl px-3 py-2 flex-shrink-0">

        {/* Back */}
        <button
          onClick={() => navigate('/app/topology')}
          className="flex items-center gap-1.5 px-2.5 py-1.5 bg-slate-700/50 hover:bg-slate-600/50 border border-white/10 text-slate-300 hover:text-white rounded-lg transition-colors text-sm flex-shrink-0"
          title="Back to Network Topology"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back
        </button>

        <div className="w-px h-5 bg-white/10 flex-shrink-0" />

        {/* Title */}
        <div className="flex-shrink-0">
          <span className="text-sm font-semibold text-white">Topology Editor</span>
        </div>

        <div className="w-px h-5 bg-white/10 flex-shrink-0" />

        {/* Layout algorithm buttons */}
        <div className="flex items-center gap-1 flex-wrap flex-1 min-w-0">
          {layouts.map(({ type, icon: Icon, label }) => (
            <button
              key={type}
              onClick={() => {
                setLayout(type)
                applyLayout(type, nodes, links)
              }}
              title={label}
              className={`flex items-center gap-1 px-2 py-1 rounded-md transition-colors text-xs font-medium flex-shrink-0 ${
                layout === type
                  ? 'bg-blue-500 text-white'
                  : 'bg-slate-700/50 text-slate-300 hover:bg-slate-600/50 border border-white/5'
              }`}
            >
              <Icon className="w-3 h-3" />
              {label}
            </button>
          ))}
        </div>

        <div className="w-px h-5 bg-white/10 flex-shrink-0" />

        {/* Tools toggle */}
        <button
          onClick={() => setShowTools(v => !v)}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg transition-colors text-xs font-medium flex-shrink-0 ${
            showTools
              ? 'bg-indigo-500 text-white'
              : 'bg-slate-700/50 hover:bg-slate-600/50 border border-white/10 text-slate-300 hover:text-white'
          }`}
          title="Toggle tools panel"
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          Tools
          {showTools ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>

        <div className="w-px h-5 bg-white/10 flex-shrink-0" />

        {/* Zoom controls */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={() => zoomRef.current && d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 1.3)}
            className="p-1.5 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg border border-white/5 transition-colors"
            title="Zoom in"
          >
            <ZoomIn className="w-3.5 h-3.5 text-slate-300" />
          </button>
          <button
            onClick={() => zoomRef.current && d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 0.7)}
            className="p-1.5 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg border border-white/5 transition-colors"
            title="Zoom out"
          >
            <ZoomOut className="w-3.5 h-3.5 text-slate-300" />
          </button>
          <button
            onClick={fitView}
            className="p-1.5 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg border border-white/5 transition-colors"
            title="Fit all nodes in view"
          >
            <Target className="w-3.5 h-3.5 text-slate-300" />
          </button>
          <button
            onClick={() => zoomRef.current && d3.select(svgRef.current).transition().duration(400).call(zoomRef.current.transform, d3.zoomIdentity)}
            className="p-1.5 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg border border-white/5 transition-colors"
            title="Reset zoom & position"
          >
            <Maximize2 className="w-3.5 h-3.5 text-slate-300" />
          </button>
        </div>
      </div>

      {/* ── Collapsible tools strip ── */}
      {showTools && (
        <div className="flex items-center gap-2 bg-slate-800/60 backdrop-blur-sm border border-white/10 rounded-xl px-3 py-2 flex-shrink-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            {/* ── Add Node ── */}
            {nodes.filter(n => n.isNew && !n.isIoT && !n.isPDU).length < 2 && (
              <button
                onClick={addNode}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-green-500/15 hover:bg-green-500/25 border border-green-500/30 text-green-400 rounded-lg transition-colors text-xs font-medium"
              >
                <Plus className="w-3.5 h-3.5" />
                Add Node
              </button>
            )}
            {nodeAddMode && (
              <>
                <button
                  onClick={addOneMoreNode}
                  title="Add another node"
                  className="flex items-center justify-center w-7 h-7 bg-green-500/20 hover:bg-green-500/35 border border-green-500/40 text-green-300 rounded-lg transition-colors text-base font-bold leading-none"
                >
                  +
                </button>
                <button
                  onClick={removeLastNewNode}
                  title="Remove last unfilled node"
                  className="flex items-center justify-center w-7 h-7 bg-red-500/20 hover:bg-red-500/35 border border-red-500/40 text-red-300 rounded-lg transition-colors text-base font-bold leading-none"
                >
                  −
                </button>
              </>
            )}

            <div className="w-px h-4 bg-white/10" />

            {/* ── Add IoT ── */}
            {nodes.filter(n => n.isNew && n.isIoT).length < 2 && (
              <button
                onClick={addIoTNode}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500/15 hover:bg-orange-500/25 border border-orange-500/30 text-orange-400 rounded-lg transition-colors text-xs font-medium"
              >
                <Plus className="w-3.5 h-3.5" />
                Add IoT
              </button>
            )}
            {iotAddMode && (
              <>
                <button
                  onClick={addOneMoreIoTNode}
                  title="Add another IoT device"
                  className="flex items-center justify-center w-7 h-7 bg-orange-500/20 hover:bg-orange-500/35 border border-orange-500/40 text-orange-300 rounded-lg transition-colors text-base font-bold leading-none"
                >
                  +
                </button>
                <button
                  onClick={removeLastNewIoTNode}
                  title="Remove last unfilled IoT device"
                  className="flex items-center justify-center w-7 h-7 bg-red-500/20 hover:bg-red-500/35 border border-red-500/40 text-red-300 rounded-lg transition-colors text-base font-bold leading-none"
                >
                  −
                </button>
              </>
            )}

            <div className="w-px h-4 bg-white/10" />

            {/* ── Add PDU ── */}
            {nodes.filter(n => n.isNew && n.isPDU).length < 2 && (
              <button
                onClick={addPDUNode}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-yellow-500/15 hover:bg-yellow-500/25 border border-yellow-500/30 text-yellow-400 rounded-lg transition-colors text-xs font-medium"
              >
                <Plus className="w-3.5 h-3.5" />
                Add PDU
              </button>
            )}
            {pduAddMode && (
              <>
                <button
                  onClick={addOneMorePDUNode}
                  title="Add another PDU"
                  className="flex items-center justify-center w-7 h-7 bg-yellow-500/20 hover:bg-yellow-500/35 border border-yellow-500/40 text-yellow-300 rounded-lg transition-colors text-base font-bold leading-none"
                >
                  +
                </button>
                <button
                  onClick={removeLastNewPDUNode}
                  title="Remove last unfilled PDU"
                  className="flex items-center justify-center w-7 h-7 bg-red-500/20 hover:bg-red-500/35 border border-red-500/40 text-red-300 rounded-lg transition-colors text-base font-bold leading-none"
                >
                  −
                </button>
              </>
            )}

            {nodeAddMsg && (
              <span className="text-xs text-amber-400 animate-pulse">{nodeAddMsg}</span>
            )}
          </div>

          <button
            onClick={() => { setLinkMode(!linkMode); setLinkAllToMode(false) }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-colors text-xs font-medium ${
              linkMode
                ? 'bg-blue-500 text-white'
                : 'bg-blue-500/15 hover:bg-blue-500/25 border border-blue-500/30 text-blue-400'
            }`}
          >
            <LinkIcon className="w-3.5 h-3.5" />
            {linkMode ? 'Linking — pick target' : 'Link Mode'}
          </button>

          {nodes.some(n => n.isNew) && (
            <button
              onClick={() => { setLinkAllToMode(v => !v); setLinkMode(false); setLinkSource(null) }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-colors text-xs font-medium ${
                linkAllToMode
                  ? 'bg-violet-500 text-white'
                  : 'bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-400'
              }`}
              title="Link all new nodes to one target"
            >
              <LinkIcon className="w-3.5 h-3.5" />
              {linkAllToMode ? 'Pick target node…' : 'Link All To'}
            </button>
          )}

          <div className="w-px h-4 bg-white/10" />

          <button
            onClick={exportTopology}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-500/15 hover:bg-purple-500/25 border border-purple-500/30 text-purple-400 rounded-lg transition-colors text-xs font-medium"
          >
            <Download className="w-3.5 h-3.5" />
            Export
          </button>

          <label className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-500/15 hover:bg-purple-500/25 border border-purple-500/30 text-purple-400 rounded-lg transition-colors text-xs font-medium cursor-pointer">
            <Upload className="w-3.5 h-3.5" />
            Import
            <input type="file" accept=".json" onChange={importTopology} className="hidden" />
          </label>

          <div className="w-px h-4 bg-white/10" />

          <button
            onClick={saveTopology}
            disabled={nodes.some(n => n.isNew)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-all text-xs font-medium ${
              nodes.some(n => n.isNew)
                ? 'opacity-40 cursor-not-allowed bg-slate-700/50 border border-white/10 text-slate-400'
                : saveStatus === 'saved'
                ? 'bg-green-500 text-white'
                : 'bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-400'
            }`}
            title={nodes.some(n => n.isNew) ? 'Fill or delete the new device first' : 'Save topology to Network Topology view'}
          >
            <Save className="w-3.5 h-3.5" />
            {saveStatus === 'saved' ? 'Saved!' : 'Save to Topology'}
          </button>

          <div className="ml-auto">
            <span className="text-xs text-slate-500">{nodes.length} nodes · {links.length} links</span>
          </div>
        </div>
      )}

      {/* ── Canvas (fills all remaining space) ── */}
      <div ref={canvasContainerRef} className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden relative min-h-0">
        <svg ref={svgRef} className="w-full h-full" style={{ display: 'block' }} />

        {/* Loading state */}
        {dataLoading && nodes.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 pointer-events-none">
            <div className="w-8 h-8 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
            <p className="text-slate-400 text-sm">Loading topology...</p>
          </div>
        )}

        {/* Empty state — shown when data loaded but no agents/servers connected */}
        {!dataLoading && nodes.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 pointer-events-none">
            <div className="w-16 h-16 rounded-full bg-slate-700/60 border border-white/10 flex items-center justify-center">
              <NetworkIcon className="w-8 h-8 text-slate-500" />
            </div>
            <div className="text-center">
              <p className="text-slate-300 font-semibold text-base">No devices connected</p>
              <p className="text-slate-500 text-sm mt-1">
                Connect a DCIM Server to see your infrastructure here,
              </p>
              <p className="text-slate-500 text-sm">
                or use <span className="text-indigo-400 font-medium">Tools → Add Node</span> to build manually.
              </p>
            </div>
          </div>
        )}

        {/* Unfilled node warning banner — only shown at max capacity */}
        {!linkMode && nodes.filter(n => n.isNew).length >= 15 && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-amber-500/90 backdrop-blur-sm text-white text-xs font-medium px-4 py-2 rounded-full shadow-lg border border-amber-400/30 z-10">
            <span>⚠ 15 devices added — link them and fill all forms before adding more</span>
          </div>
        )}

        {/* Link All To mode banner */}
        {linkAllToMode && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-violet-600/90 backdrop-blur-sm text-white text-xs font-medium px-4 py-2 rounded-full shadow-lg border border-violet-400/30 z-10">
            <LinkIcon className="w-3.5 h-3.5" />
            <span>Click any node to link all {nodes.filter(n => n.isNew).length} new node(s) to it</span>
            <button onClick={() => setLinkAllToMode(false)} className="ml-1 hover:text-violet-200">
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Link mode hint banner */}
        {linkMode && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-blue-600/90 backdrop-blur-sm text-white text-xs font-medium px-4 py-2 rounded-full shadow-lg border border-blue-400/30">
            <LinkIcon className="w-3.5 h-3.5" />
            {linkSource
              ? `From "${linkSource.name}" — click target node`
              : 'Click a source node'}
            <button onClick={() => {
              if (awaitingLinkNodeIdRef.current) {
                const id = awaitingLinkNodeIdRef.current
                setNodes(prev => prev.filter(n => n.id !== id))
                setLinks(prev => prev.filter(l =>
                  (typeof l.source === 'string' ? l.source : (l.source as Node).id) !== id &&
                  (typeof l.target === 'string' ? l.target : (l.target as Node).id) !== id
                ))
                setSelectedNode(null)
                awaitingLinkNodeIdRef.current = null
              }
              setLinkMode(false)
              setLinkSource(null)
            }} className="ml-1 hover:text-blue-200">
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* ── Device form — floats near node OR docked as left slide panel ── */}
        {formNode && (
          <div
            className={formDocked
              ? `absolute z-20 w-72 bg-slate-900/98 backdrop-blur-md border border-white/10 shadow-2xl overflow-hidden rounded-r-xl transition-transform duration-300 ${formPanelVisible ? 'translate-x-0' : '-translate-x-full'}`
              : 'absolute z-20 w-72 bg-slate-900/98 backdrop-blur-md border border-white/10 rounded-xl shadow-2xl overflow-hidden'
            }
            style={formDocked ? { left: 0, top: 0, bottom: 0 } : { left: formNodePos.x, top: formNodePos.y }}
          >
            {/* Slide tab — only in docked mode */}
            {formDocked && (
              <button
                onClick={() => setFormPanelVisible(v => !v)}
                className="absolute -right-7 top-1/2 -translate-y-1/2 z-30 bg-slate-900/98 border border-white/10 border-l-0 rounded-r-lg px-1 py-3 text-slate-400 hover:text-white transition-colors"
                title={formPanelVisible ? 'Hide panel' : 'Show panel'}
              >
                <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
                  {formPanelVisible
                    ? <path d="M8 2L4 6l4 4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
                    : <path d="M4 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
                  }
                </svg>
              </button>
            )}
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 bg-slate-800/70">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: formData.color }} />
                <span className="text-xs font-semibold text-white">
                  {formNode.isNew
                    ? (formNode.isPDU ? 'New PDU' : formNode.isIoT ? 'New IoT Device' : 'New Device')
                    : (formNode.isPDU ? 'Edit PDU' : formNode.isIoT ? 'Edit IoT Device' : 'Edit Device')}
                </span>
              </div>
              <div className="flex items-center gap-1">
                {/* Dock / undock toggle */}
                <button
                  onClick={() => { setFormDocked(d => !d); setFormPanelVisible(true) }}
                  className="text-slate-400 hover:text-white transition-colors p-0.5"
                  title={formDocked ? 'Float near node' : 'Dock to left panel'}
                >
                  <svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    {formDocked
                      ? <><rect x="1" y="1" width="11" height="11" rx="1"/><path d="M5 1v11"/></>
                      : <><rect x="1" y="3" width="11" height="7" rx="1"/><path d="M4 1l2 2-2 2"/></>
                    }
                  </svg>
                </button>
                <button
                  onClick={() => { setFormNodeSynced(null); setFormDocked(false); setFormPanelVisible(true) }}
                  className="text-slate-400 hover:text-white transition-colors"
                  title="Close form"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Tab indicators */}
            <div className="flex border-b border-white/10">
              <div className={`flex-1 text-center text-[10px] py-1.5 font-medium transition-colors ${formTab === 'basic' ? 'text-white border-b-2 border-blue-500 bg-slate-800/40' : 'text-slate-500'}`}>
                1 · Basic
              </div>
              <div className={`flex-1 text-center text-[10px] py-1.5 font-medium transition-colors ${formTab === 'specs' ? 'text-white border-b-2 border-blue-500 bg-slate-800/40' : 'text-slate-500'}`}>
                2 · Specs
              </div>
            </div>

            {/* Tab content */}
            <div className="p-3 space-y-2 max-h-[420px] overflow-y-auto">
              {formError && (
                <div className="text-red-400 text-[10px] bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
                  {formError}
                </div>
              )}

              {formTab === 'basic' ? (
                <>
                  {/* Hostname + IP */}
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Hostname <span className="text-red-400">*</span></label>
                      <input type="text" value={formData.hostname}
                        onChange={e => setFormData(p => ({ ...p, hostname: e.target.value }))}
                        placeholder="Switch-01"
                        className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 ${formTouched && !formData.hostname ? 'border-red-500/60' : 'border-white/10'}`}
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">IP Address <span className="text-red-400">*</span></label>
                      <input type="text" value={formData.ip_address}
                        onChange={e => setFormData(p => ({ ...p, ip_address: e.target.value }))}
                        placeholder="192.168.1.1"
                        className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 ${formTouched && !formData.ip_address ? 'border-red-500/60' : 'border-white/10'}`}
                      />
                    </div>
                  </div>

                  {/* Server */}
                  <div>
                    <label className="block text-[10px] text-slate-400 mb-0.5">Server <span className="text-red-400">*</span></label>
                    <select value={formData.server_id}
                      onChange={e => setFormData(p => ({ ...p, server_id: e.target.value }))}
                      className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500 ${formTouched && !formData.server_id ? 'border-red-500/60' : 'border-white/10'}`}
                    >
                      <option value="">Select server...</option>
                      {servers?.filter(s => s.enabled).map(s => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </select>
                  </div>

                  {/* Type + Group */}
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Type <span className="text-red-400">*</span></label>
                      <select value={formData.device_type}
                        onChange={e => setFormData(p => ({ ...p, device_type: e.target.value }))}
                        className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500 ${formTouched && !formData.device_type ? 'border-red-500/60' : 'border-white/10'}`}
                      >
                        {formNode.isPDU ? (
                          <>
                            <option value="device">Basic PDU</option>
                            <option value="switch">Metered PDU</option>
                            <option value="router">Monitored PDU</option>
                            <option value="firewall">Switched PDU</option>
                            <option value="ap">Switched+Metered PDU</option>
                            <option value="workstation">ATS PDU</option>
                            <option value="custom">3-Phase PDU</option>
                          </>
                        ) : formNode.isIoT ? (
                          <>
                            <option value="device">Generic IoT</option>
                            <option value="camera">Temperature Sensor</option>
                            <option value="printer">Humidity Sensor</option>
                            <option value="workstation">Motion Sensor</option>
                            <option value="ap">IP Camera</option>
                            <option value="switch">Smart Meter</option>
                            <option value="router">Gateway / Hub</option>
                            <option value="firewall">Industrial Controller</option>
                            <option value="custom">Embedded Board</option>
                          </>
                        ) : (
                          <>
                            <option value="device">Device</option>
                            <option value="switch">Switch</option>
                            <option value="router">Router</option>
                            <option value="firewall">Firewall</option>
                            <option value="ap">Access Point</option>
                            <option value="workstation">Workstation</option>
                            <option value="printer">Printer</option>
                            <option value="camera">IP Camera</option>
                            <option value="server">Server</option>
                            <option value="custom">Custom</option>
                          </>
                        )}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Group <span className="text-red-400">*</span></label>
                      <input type="text" value={formData.agent_group}
                        onChange={e => setFormData(p => ({ ...p, agent_group: e.target.value }))}
                        placeholder="manual"
                        className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 ${formTouched && !formData.agent_group ? 'border-red-500/60' : 'border-white/10'}`}
                      />
                    </div>
                  </div>

                  {/* MAC + Location */}
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">MAC <span className="text-red-400">*</span></label>
                      <input type="text" value={formData.mac_address}
                        onChange={e => setFormData(p => ({ ...p, mac_address: e.target.value }))}
                        placeholder="AA:BB:CC:DD:EE:FF"
                        className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 ${formTouched && !formData.mac_address ? 'border-red-500/60' : 'border-white/10'}`}
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Location <span className="text-red-400">*</span></label>
                      <input type="text" value={formData.location}
                        onChange={e => setFormData(p => ({ ...p, location: e.target.value }))}
                        placeholder="Rack A / Floor 2"
                        className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 ${formTouched && !formData.location ? 'border-red-500/60' : 'border-white/10'}`}
                      />
                    </div>
                  </div>

                  {/* Description */}
                  <div>
                    <label className="block text-[10px] text-slate-400 mb-0.5">Description <span className="text-red-400">*</span></label>
                    <input type="text" value={formData.description}
                      onChange={e => setFormData(p => ({ ...p, description: e.target.value }))}
                      placeholder="Brief description"
                      className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 ${formTouched && !formData.description ? 'border-red-500/60' : 'border-white/10'}`}
                    />
                  </div>

                  {/* Color */}
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-slate-400">Color</span>
                    {['#6366f1','#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899','#14b8a6','#f97316','#eab308'].map(c => (
                      <button key={c} onClick={() => setFormData(p => ({ ...p, color: c }))}
                        className={`w-4 h-4 rounded-full transition-transform hover:scale-110 flex-shrink-0 ${formData.color === c ? 'ring-2 ring-white ring-offset-1 ring-offset-slate-900 scale-110' : ''}`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                  </div>

                  {/* Protocol + Cert CN */}
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Protocol</label>
                      <input type="text" value={formData.protocol}
                        onChange={e => setFormData(p => ({ ...p, protocol: e.target.value }))}
                        placeholder="SNMP, MQTT..."
                        className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Cert CN</label>
                      <input type="text" value={formData.certificate_cn}
                        onChange={e => setFormData(p => ({ ...p, certificate_cn: e.target.value }))}
                        placeholder="device-cn"
                        className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                </>
              ) : (
                /* ── Specs tab ── */
                <>
                  {formNode.isPDU ? (
                    /* PDU Specs */
                    <>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Power</p>
                      <div className="grid grid-cols-2 gap-2">
                        {[
                          { key: 'total_capacity', label: 'Total Capacity', placeholder: '32A / 7.2kW' },
                          { key: 'voltage', label: 'Voltage', placeholder: '220V / 208V' },
                          { key: 'frequency', label: 'Frequency', placeholder: '50Hz / 60Hz' },
                          { key: 'inlet_connector', label: 'Inlet Connector', placeholder: 'C20, L6-30P' },
                        ].map(({ key, label, placeholder }) => (
                          <div key={key}>
                            <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                            <input type="text" value={specsData[key] || ''}
                              onChange={e => setSpecsData(p => ({ ...p, [key]: e.target.value }))}
                              placeholder={placeholder}
                              className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                            />
                          </div>
                        ))}
                      </div>
                      <div className="grid grid-cols-2 gap-2 mt-2">
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Phase</label>
                          <select value={specsData['phase'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, phase: e.target.value }))}
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            <option value="1-phase">1-Phase</option>
                            <option value="3-phase">3-Phase</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Form Factor</label>
                          <select value={specsData['form_factor'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, form_factor: e.target.value }))}
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            <option value="0U">0U (Vertical)</option>
                            <option value="1U">1U</option>
                            <option value="2U">2U</option>
                          </select>
                        </div>
                      </div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mt-2 mb-1">Outlets</p>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Outlet Count</label>
                          <input type="text" value={specsData['outlet_count'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, outlet_count: e.target.value }))}
                            placeholder="24"
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                          />
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Outlet Type</label>
                          <select value={specsData['outlet_type'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, outlet_type: e.target.value }))}
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            <option value="C13">C13</option>
                            <option value="C19">C19</option>
                            <option value="NEMA 5-15">NEMA 5-15</option>
                            <option value="NEMA 5-20">NEMA 5-20</option>
                            <option value="Mixed">Mixed</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Per-Outlet Metering</label>
                          <select value={specsData['per_outlet_metering'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, per_outlet_metering: e.target.value }))}
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            <option value="yes">Yes</option>
                            <option value="no">No</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Per-Outlet Switching</label>
                          <select value={specsData['per_outlet_switching'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, per_outlet_switching: e.target.value }))}
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            <option value="yes">Yes</option>
                            <option value="no">No</option>
                          </select>
                        </div>
                      </div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mt-2 mb-1">Hardware</p>
                      <div className="grid grid-cols-2 gap-2">
                        {[
                          { key: 'model', label: 'Model', placeholder: 'APC AP8841' },
                          { key: 'serial_number', label: 'Serial Number', placeholder: 'SN123456' },
                        ].map(({ key, label, placeholder }) => (
                          <div key={key}>
                            <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                            <input type="text" value={specsData[key] || ''}
                              onChange={e => setSpecsData(p => ({ ...p, [key]: e.target.value }))}
                              placeholder={placeholder}
                              className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                            />
                          </div>
                        ))}
                      </div>
                    </>
                  ) : formNode.isIoT ? (
                    /* IoT Specs */
                    <>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Compute</p>
                      <div className="grid grid-cols-2 gap-2">
                        {[
                          { key: 'cpu_chip', label: 'CPU / Chip', placeholder: 'ESP32, ARM M4' },
                          { key: 'ram', label: 'RAM', placeholder: '520KB, 256MB' },
                          { key: 'storage_total', label: 'Storage Total', placeholder: '4MB, 8GB' },
                          { key: 'storage_used', label: 'Storage Used', placeholder: '1.2MB, 3GB' },
                          { key: 'firmware_version', label: 'Firmware Version', placeholder: 'v2.1.0' },
                          { key: 'ip_rating', label: 'IP Rating', placeholder: 'IP67, IP54' },
                        ].map(({ key, label, placeholder }) => (
                          <div key={key}>
                            <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                            <input type="text" value={specsData[key] || ''}
                              onChange={e => setSpecsData(p => ({ ...p, [key]: e.target.value }))}
                              placeholder={placeholder}
                              className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                            />
                          </div>
                        ))}
                      </div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mt-2 mb-1">Power & Sensing</p>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Power Source</label>
                          <select value={specsData['power_source'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, power_source: e.target.value }))}
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            <option value="Battery">Battery</option>
                            <option value="PoE">PoE</option>
                            <option value="AC">AC</option>
                            <option value="DC">DC</option>
                            <option value="Solar">Solar</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-400 mb-0.5">Battery Level</label>
                          <input type="text" value={specsData['battery_level'] || ''}
                            onChange={e => setSpecsData(p => ({ ...p, battery_level: e.target.value }))}
                            placeholder="85%"
                            className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                          />
                        </div>
                        {[
                          { key: 'sensor_type', label: 'Sensor Type', placeholder: 'Temperature, Motion' },
                          { key: 'reporting_interval', label: 'Reporting Interval', placeholder: '30s, 5min' },
                        ].map(({ key, label, placeholder }) => (
                          <div key={key}>
                            <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                            <input type="text" value={specsData[key] || ''}
                              onChange={e => setSpecsData(p => ({ ...p, [key]: e.target.value }))}
                              placeholder={placeholder}
                              className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                            />
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    /* Node (Network Device) Specs */
                    <>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Hardware</p>
                      <div className="grid grid-cols-2 gap-2">
                        {[
                          { key: 'cpu', label: 'CPU', placeholder: 'Intel Atom C3000' },
                          { key: 'ram', label: 'RAM', placeholder: '4GB, 512MB' },
                          { key: 'storage', label: 'Storage', placeholder: '32GB, 128MB' },
                          { key: 'firmware_version', label: 'Firmware Version', placeholder: '15.2(4)M' },
                          { key: 'model', label: 'Model', placeholder: 'Cisco C9200L' },
                          { key: 'serial_number', label: 'Serial Number', placeholder: 'FCZ1234A5BC' },
                        ].map(({ key, label, placeholder }) => (
                          <div key={key}>
                            <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                            <input type="text" value={specsData[key] || ''}
                              onChange={e => setSpecsData(p => ({ ...p, [key]: e.target.value }))}
                              placeholder={placeholder}
                              className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                            />
                          </div>
                        ))}
                      </div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mt-2 mb-1">Network</p>
                      <div className="grid grid-cols-2 gap-2">
                        {[
                          { key: 'port_count', label: 'Port Count', placeholder: '24, 48' },
                          { key: 'throughput', label: 'Throughput', placeholder: '10Gbps, 1Gbps' },
                          { key: 'uplink_speed', label: 'Uplink Speed', placeholder: '40Gbps' },
                          { key: 'snmp_community', label: 'SNMP Community', placeholder: 'public' },
                        ].map(({ key, label, placeholder }) => (
                          <div key={key}>
                            <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                            <input type="text" value={specsData[key] || ''}
                              onChange={e => setSpecsData(p => ({ ...p, [key]: e.target.value }))}
                              placeholder={placeholder}
                              className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                            />
                          </div>
                        ))}
                      </div>
                      <div className="mt-2">
                        <label className="block text-[10px] text-slate-400 mb-0.5">VLAN IDs</label>
                        <input type="text" value={specsData['vlan_ids'] || ''}
                          onChange={e => setSpecsData(p => ({ ...p, vlan_ids: e.target.value }))}
                          placeholder="10, 20, 100, 200"
                          className="w-full bg-slate-800 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                        />
                      </div>
                    </>
                  )}
                </>
              )}
            </div>

            {/* Footer */}
            <div className="flex gap-2 px-3 py-2 border-t border-white/10 bg-slate-800/40">
              <button onClick={cancelForm}
                className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 border border-white/10 text-slate-300 rounded text-xs transition-colors"
              >
                Cancel
              </button>
              {formTab === 'basic' ? (
                <button onClick={handleFormNext}
                  className="flex-1 flex items-center justify-center gap-1 px-2 py-1 bg-blue-500 hover:bg-blue-600 text-white rounded text-xs font-medium transition-colors"
                >
                  Next →
                </button>
              ) : (
                <>
                  <button onClick={() => setFormTab('basic')}
                    className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 border border-white/10 text-slate-300 rounded text-xs transition-colors"
                  >
                    ← Back
                  </button>
                  <button onClick={saveDeviceNode}
                    className="flex-1 flex items-center justify-center gap-1 px-2 py-1 bg-blue-500 hover:bg-blue-600 text-white rounded text-xs font-medium transition-colors"
                  >
                    <Save className="w-3 h-3" />
                    Save Device
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {/* ── Floating properties panel — hidden when form is open ── */}
        {selectedNode && !formNode && (
          <div className="absolute top-3 right-3 w-64 bg-slate-900/95 backdrop-blur-md border border-white/10 rounded-xl shadow-2xl overflow-hidden">
            {/* Panel header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/10 bg-slate-800/60">
              <span className="text-xs font-semibold text-slate-300 uppercase tracking-wide">Node Properties</span>
              <button onClick={() => setSelectedNode(null)} className="text-slate-500 hover:text-white transition-colors">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            <div className="p-4 space-y-3">
              {editingNode?.id === selectedNode.id ? (
                <>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Name</label>
                    <input
                      type="text"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="w-full bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                      autoFocus
                    />
                  </div>
                  <button
                    onClick={saveNodeEdit}
                    className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 bg-green-500/20 hover:bg-green-500/30 border border-green-500/30 text-green-400 rounded-lg text-xs font-medium transition-colors"
                  >
                    <Save className="w-3.5 h-3.5" />
                    Save
                  </button>
                </>
              ) : (
                <>
                  {/* Name row */}
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide">Name</p>
                      <p className="text-sm font-semibold text-white leading-tight">{selectedNode.name}</p>
                    </div>
                    <button onClick={() => startEditNode(selectedNode)} className="p-1.5 hover:bg-white/5 rounded-lg transition-colors">
                      <Edit3 className="w-3.5 h-3.5 text-slate-400" />
                    </button>
                  </div>

                  {/* Type + status */}
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide">Type</p>
                      <p className="text-xs text-white capitalize">{selectedNode.type}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide">Status</p>
                      <span className={`text-xs font-medium ${selectedNode.status === 'online' ? 'text-green-400' : 'text-slate-400'}`}>
                        {selectedNode.status ?? '—'}
                      </span>
                    </div>
                  </div>

                  {/* ID */}
                  <div>
                    <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">ID</p>
                    <p className="text-[10px] font-mono text-slate-400 break-all">{selectedNode.id}</p>
                  </div>

                  {/* Connected links */}
                  {(() => {
                    const connectedLinks = links.filter(l =>
                      (typeof l.source === 'string' ? l.source : l.source.id) === selectedNode.id ||
                      (typeof l.target === 'string' ? l.target : l.target.id) === selectedNode.id
                    )
                    return connectedLinks.length > 0 ? (
                      <div>
                        <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Links ({connectedLinks.length})</p>
                        <div className="space-y-1 max-h-28 overflow-y-auto">
                          {connectedLinks.map(link => (
                            <div key={link.id} className="flex items-center justify-between bg-slate-800/60 rounded px-2 py-1">
                              <span className="text-[10px] font-mono text-slate-400 truncate flex-1 mr-1">{link.id}</span>
                              <button onClick={() => deleteLink(link.id)} className="p-0.5 hover:bg-red-500/20 rounded transition-colors flex-shrink-0">
                                <Trash2 className="w-2.5 h-2.5 text-red-400" />
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null
                  })()}

                  {/* Delete */}
                  <button
                    onClick={() => deleteNode(selectedNode.id)}
                    className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 bg-red-500/15 hover:bg-red-500/25 border border-red-500/30 text-red-400 rounded-lg text-xs font-medium transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Delete Node
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
