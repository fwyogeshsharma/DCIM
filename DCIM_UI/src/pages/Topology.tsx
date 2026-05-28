import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import * as d3 from 'd3'
import {
  Activity,
  ZoomIn,
  ZoomOut,
  Maximize2,
  RefreshCw,
  Box,
  Edit3,
  X,
  Network,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { computeLayeredLayout } from '@/lib/topology-layered'
import { useSSE } from '@/hooks/useSSE'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TreeNode {
  device_id: string
  hostname: string
  device_type: string
  mgmt_ip: string | null
  network_id: string
  group_id: string
  parent_device_id: string | null
  parent_hostname: string | null
  depth: number
  is_root: boolean
  status: 'online' | 'offline'
  active_alerts: number
  critical_alerts: number
}

interface TreeLink {
  id: string
  src_device_id: string
  dst_device_id: string
  is_active: boolean
  relation: string
  link_speed_mbps: number | null
  src_hostname: string
  dst_hostname: string
  src_status: 'online' | 'offline'
  dst_status: 'online' | 'offline'
}

interface TopoNode extends d3.SimulationNodeDatum {
  id: string
  name: string
  deviceType: string
  depth: number
  isRoot: boolean
  status: 'online' | 'offline'
  ip: string | null
  networkId: string
  groupId: string
  alerts: number
  criticalAlerts: number
  parentId: string | null
  parentName: string | null
}

interface TopoLink extends d3.SimulationLinkDatum<TopoNode> {
  source: string | TopoNode
  target: string | TopoNode
  online: boolean
}

interface TrapAlert {
  trapType: string
  severity: string
  description: string
  deviceName: string
  timestamp: string
}

interface TrapFeedItem {
  id: number
  sourceIp: string
  deviceName: string
  trapType: string
  severity: string
  description: string
  timestamp: string
}

// ── Device visual properties by type ─────────────────────────────────────────

function deviceVisuals(deviceType: string) {
  const dt = (deviceType || '').toLowerCase()
  if (/router|rtr|edge.?router|cluster.?router/.test(dt))
    return { icon: '🔀', fill: '#1e3a8a', stroke: '#3b82f6', radius: 28 }
  if (/firewall|fw/.test(dt))
    return { icon: '🛡️', fill: '#7c2d12', stroke: '#f97316', radius: 26 }
  if (/load.?bal|^lb$/.test(dt))
    return { icon: '⚖️', fill: '#134e4a', stroke: '#2dd4bf', radius: 24 }
  if (/super.?spine|spine|fabric/.test(dt))
    return { icon: '🕸️', fill: '#3b0764', stroke: '#a855f7', radius: 26 }
  if (/aggregat|agg.?switch/.test(dt))
    return { icon: '🔗', fill: '#1e3a5f', stroke: '#818cf8', radius: 24 }
  if (/leaf|tor|top.?of.?rack/.test(dt))
    return { icon: '🌿', fill: '#14532d', stroke: '#22c55e', radius: 22 }
  if (/gpu/.test(dt))
    return { icon: '🖥️', fill: '#500724', stroke: '#f43f5e', radius: 20 }
  if (/storage|nas|san/.test(dt))
    return { icon: '💾', fill: '#3b2200', stroke: '#eab308', radius: 20 }
  if (/compute|server|host/.test(dt))
    return { icon: '💻', fill: '#0c4a6e', stroke: '#38bdf8', radius: 20 }
  if (/ups/.test(dt))
    return { icon: '🔋', fill: '#1a2e05', stroke: '#84cc16', radius: 18 }
  if (/pdu|power/.test(dt))
    return { icon: '⚡', fill: '#422006', stroke: '#f59e0b', radius: 18 }
  if (/oob|out.?of.?band/.test(dt))
    return { icon: '🔌', fill: '#1e293b', stroke: '#64748b', radius: 18 }
  if (/switch/.test(dt))
    return { icon: '🔌', fill: '#1e3a5f', stroke: '#60a5fa', radius: 22 }
  return { icon: '📡', fill: '#1e293b', stroke: '#475569', radius: 18 }
}

// A trap is a link-down event if its type normalizes to "linkdown". Collectors
// emit this interchangeably as linkDown / LINK_DOWN / link-down, so compare on
// the letters only.
const isLinkDownType = (s?: string) =>
  (s ?? '').toUpperCase().replace(/[^A-Z]/g, '').includes('LINKDOWN')

// ── Component ─────────────────────────────────────────────────────────────────

export default function Topology() {
  const navigate = useNavigate()
  const svgRef = useRef<SVGSVGElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const prevNodeCountRef = useRef(0)
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [selectedNode, setSelectedNode] = useState<TopoNode | null>(null)
  const [showLegend, setShowLegend] = useState(true)
  const [showStats, setShowStats] = useState(true)
  const [showTrapFeed, setShowTrapFeed] = useState(true)
  const [networkFilter, setNetworkFilter] = useState<string>('all')

  const [trapAlerts, setTrapAlerts] = useState<Map<string, TrapAlert>>(new Map())
  const [linkDownAlerts, setLinkDownAlerts] = useState<Map<string, TrapAlert>>(new Map())
  const [trapFeed, setTrapFeed] = useState<TrapFeedItem[]>([])
  const trapTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  // ── Data fetching ──────────────────────────────────────────────────────────

  const { data: topoData, isLoading } = useQuery({
    queryKey: ['topology-tree'],
    queryFn: () => api.getTopologyTree(),
    staleTime: 15000,
    refetchInterval: 30000,
  })

  const rawTree: TreeNode[] = topoData?.nodes ?? []
  const rawLinks: TreeLink[] = topoData?.links ?? []

  const { data: activeTraps } = useQuery({
    queryKey: ['active-traps-seed'],
    queryFn: () => api.getTraps({ limit: 200 }),
    staleTime: Infinity,
    refetchInterval: 30000,
  })

  // Seed trap state from DB on mount
  useEffect(() => {
    const traps = (activeTraps as any[]) ?? []
    if (!traps.length) return
    setTrapAlerts(prev => {
      const next = new Map(prev)
      traps.forEach(t => {
        const alert: TrapAlert = {
          trapType: t.trap_type ?? '', severity: t.severity ?? 'critical',
          description: t.description ?? '', deviceName: t.device_name ?? '',
          timestamp: t.timestamp ?? new Date().toISOString(),
        }
        if (t.source_ip) next.set(t.source_ip, alert)
        if (t.device_name) next.set(t.device_name, alert)
      })
      return next
    })
    setLinkDownAlerts(prev => {
      const next = new Map(prev)
      traps.filter(t => isLinkDownType(t.trap_type)).forEach(t => {
        const alert: TrapAlert = {
          trapType: t.trap_type ?? '', severity: t.severity ?? 'critical',
          description: t.description ?? '', deviceName: t.device_name ?? '',
          timestamp: t.timestamp ?? new Date().toISOString(),
        }
        if (t.source_ip) next.set(t.source_ip, alert)
        if (t.device_name) next.set(t.device_name, alert)
      })
      return next
    })
    setTrapFeed(traps.slice(0, 30).map((t, i) => ({
      id: i, sourceIp: t.source_ip ?? '', deviceName: t.device_name ?? '',
      trapType: t.trap_type ?? '', severity: t.severity ?? 'critical',
      description: t.description ?? '', timestamp: t.timestamp ?? new Date().toISOString(),
    })))
  }, [activeTraps])

  // SSE live trap handler
  const handleTrapEvent = useCallback((data: any) => {
    const ip: string | undefined = data.source_ip
    const name: string | undefined = data.device_name
    if (!ip && !name) return
    const alert: TrapAlert = {
      trapType: data.trap_type ?? '', severity: data.severity ?? 'critical',
      description: data.description ?? '', deviceName: name ?? '',
      timestamp: data.timestamp ?? new Date().toISOString(),
    }
    const keys = [ip, name].filter(Boolean) as string[]
    keys.forEach(k => { const ex = trapTimeoutsRef.current.get(k); if (ex) clearTimeout(ex) })
    setTrapAlerts(prev => { const next = new Map(prev); if (ip) next.set(ip, alert); if (name) next.set(name, alert); return next })
    const isLinkDown = isLinkDownType(data.trap_type)
    if (isLinkDown) {
      setLinkDownAlerts(prev => { const next = new Map(prev); if (ip) next.set(ip, alert); if (name) next.set(name, alert); return next })
    }
    setTrapFeed(prev => [{
      id: Date.now(), sourceIp: ip ?? '', deviceName: name ?? '',
      trapType: data.trap_type ?? '', severity: data.severity ?? 'critical',
      description: data.description ?? '', timestamp: data.timestamp ?? new Date().toISOString(),
    }, ...prev].slice(0, 30))
    const timeout = setTimeout(() => {
      setTrapAlerts(prev => { const next = new Map(prev); keys.forEach(k => next.delete(k)); return next })
      if (isLinkDown) setLinkDownAlerts(prev => { const next = new Map(prev); keys.forEach(k => next.delete(k)); return next })
      keys.forEach(k => trapTimeoutsRef.current.delete(k))
    }, 5 * 60 * 1000)
    keys.forEach(k => trapTimeoutsRef.current.set(k, timeout))
  }, [])

  useSSE('trap', handleTrapEvent, true)

  // ── Derived data ───────────────────────────────────────────────────────────

  const networks = useMemo(() => {
    return [...new Set(rawTree.map(n => n.network_id))].sort()
  }, [rawTree])

  const tree = useMemo(() => {
    return networkFilter === 'all' ? rawTree : rawTree.filter(n => n.network_id === networkFilter)
  }, [rawTree, networkFilter])

  // Filter links to only those whose both endpoints are in the current tree view
  const treeLinks = useMemo(() => {
    const nodeIds = new Set(tree.map(n => n.device_id))
    return rawLinks.filter(l => nodeIds.has(l.src_device_id) && nodeIds.has(l.dst_device_id))
  }, [rawLinks, tree])

  const stats = useMemo(() => ({
    total: tree.length,
    online: tree.filter(n => n.status === 'online').length,
    offline: tree.filter(n => n.status === 'offline').length,
    networks: new Set(tree.map(n => n.network_id)).size,
    withAlerts: tree.filter(n => n.active_alerts > 0).length,
  }), [tree])

  // ── D3 rendering ───────────────────────────────────────────────────────────

  useEffect(() => {
    if (!svgRef.current) return

    const prevZoom = d3.zoomTransform(svgRef.current)
    d3.select(svgRef.current).selectAll('*').remove()

    if (!tree || tree.length === 0) return

    // Expose treeLinks to the effect via closure (avoids stale ref issues)
    const effectLinks = treeLinks

    const width = svgRef.current.clientWidth
    const height = svgRef.current.clientHeight
    const svg = d3.select(svgRef.current).attr('width', width).attr('height', height)
    const g = svg.append('g')

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.05, 4])
      .on('zoom', e => g.attr('transform', e.transform))
    svg.call(zoom).on('dblclick.zoom', null)
    zoomRef.current = zoom

    // Build nodes from tree rows
    const nodes: TopoNode[] = tree.map(row => ({
      id: row.device_id,
      name: row.hostname,
      deviceType: row.device_type,
      depth: row.depth,
      isRoot: row.is_root,
      status: row.status,
      ip: row.mgmt_ip,
      networkId: row.network_id,
      groupId: row.group_id,
      alerts: row.active_alerts,
      criticalAlerts: row.critical_alerts,
      parentId: row.parent_device_id,
      parentName: row.parent_hostname,
    }))

    // Build edges from ALL topology_links (not just parent→child).
    // This draws every physical cable: uplinks, downlinks, AND peer/spine-mesh.
    const nodeIds = new Set(nodes.map(n => n.id))
    const nodeById = new Map(nodes.map(n => [n.id, n]))
    const seenPairs = new Set<string>()
    const links: TopoLink[] = []
    for (const tl of effectLinks) {
      if (!nodeIds.has(tl.src_device_id) || !nodeIds.has(tl.dst_device_id)) continue
      // Deduplicate bidirectional duplicates (A→B and B→A drawn once)
      const pairKey = [tl.src_device_id, tl.dst_device_id].sort().join('|')
      if (seenPairs.has(pairKey)) continue
      seenPairs.add(pairKey)
      links.push({
        source: tl.src_device_id,
        target: tl.dst_device_id,
        online: tl.src_status === 'online' && tl.dst_status === 'online',
      })
    }

    // Guarantee every device stays attached to its parent. A physical
    // topology_links row can be missing or stale for a device that has gone
    // offline (its agent stopped reporting the cable), which previously left
    // the node floating with no edge. We always add the hierarchy parent→child
    // edge from the tree — deduped against the physical links above — so
    // offline devices remain connected to their parent just like online ones.
    for (const row of tree) {
      if (!row.parent_device_id || !nodeIds.has(row.parent_device_id)) continue
      const pairKey = [row.device_id, row.parent_device_id].sort().join('|')
      if (seenPairs.has(pairKey)) continue
      seenPairs.add(pairKey)
      const child = nodeById.get(row.device_id)
      const parent = nodeById.get(row.parent_device_id)
      links.push({
        source: row.device_id,
        target: row.parent_device_id,
        online: child?.status === 'online' && parent?.status === 'online',
      })
    }

    // Layered layout: depth directly maps to layer (root=0 at top)
    const layeredInput = nodes.map(n => ({ id: n.id, layer: n.depth }))
    const layeredEdges = links.map(l => ({
      source: typeof l.source === 'string' ? l.source : (l.source as TopoNode).id,
      target: typeof l.target === 'string' ? l.target : (l.target as TopoNode).id,
    }))

    const layered = computeLayeredLayout(layeredInput, layeredEdges, {
      nodeGapX: 100,
      layerGapY: 160,
      iterations: 28,
      originX: width / 2,
      originY: 90,
    })

    nodes.forEach(n => {
      const p = layered.positions.get(n.id)
      if (p) { n.x = p.x; n.y = p.y }
    })

    // Drop unpositioned nodes
    const unpositioned = new Set(
      nodes.filter(n => typeof n.x !== 'number' || !isFinite(n.x!)).map(n => n.id)
    )
    const visNodes = nodes.filter(n => !unpositioned.has(n.id))
    const visLinks = links.filter(l => {
      const s = typeof l.source === 'string' ? l.source : (l.source as TopoNode).id
      const t = typeof l.target === 'string' ? l.target : (l.target as TopoNode).id
      return !unpositioned.has(s) && !unpositioned.has(t)
    })

    // Resolve link source/target strings to node objects
    const nodeMap = new Map(visNodes.map(n => [n.id, n]))
    visLinks.forEach(l => {
      if (typeof l.source === 'string') l.source = nodeMap.get(l.source) ?? l.source
      if (typeof l.target === 'string') l.target = nodeMap.get(l.target) ?? l.target
    })

    // Helpers
    const hasTrap = (d: TopoNode) =>
      (!!d.ip && trapAlerts.has(d.ip)) || trapAlerts.has(d.name)

    const isLinkDownTrap = (l: TopoLink) => {
      const sn = l.source as TopoNode
      const tn = l.target as TopoNode
      return linkDownAlerts.has(sn.ip ?? '') || linkDownAlerts.has(sn.name) ||
             linkDownAlerts.has(tn.ip ?? '') || linkDownAlerts.has(tn.name)
    }

    // Auto-fit on first load or node count change
    const nodeCountChanged = visNodes.length !== prevNodeCountRef.current
    prevNodeCountRef.current = visNodes.length
    const isDefaultZoom = prevZoom.k === 1 && prevZoom.x === 0 && prevZoom.y === 0
    if (visNodes.length > 0) {
      const xs = visNodes.map(n => n.x!)
      const ys = visNodes.map(n => n.y!)
      const pad = 80
      const contentW = (Math.max(...xs) - Math.min(...xs)) + pad * 2
      const contentH = (Math.max(...ys) - Math.min(...ys)) + pad * 2
      const fitScale = Math.min(width / contentW, height / contentH, 1)
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2
      const cy = (Math.min(...ys) + Math.max(...ys)) / 2
      if (nodeCountChanged || isDefaultZoom) {
        svg.call(zoom.transform as any,
          d3.zoomIdentity.translate(width / 2 - cx * fitScale, height / 2 - cy * fitScale).scale(fitScale))
      } else {
        svg.call(zoom.transform as any, prevZoom)
      }
    }

    // Defs (glow filter)
    const defs = svg.append('defs')
    const glow = defs.append('filter').attr('id', 'glow-red')
      .attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%')
    glow.append('feGaussianBlur').attr('stdDeviation', '4').attr('result', 'blur')
    glow.append('feFlood').attr('flood-color', '#ef4444').attr('flood-opacity', '0.6').attr('result', 'color')
    glow.append('feComposite').attr('in', 'color').attr('in2', 'blur').attr('operator', 'in').attr('result', 'glow')
    const merge = glow.append('feMerge')
    merge.append('feMergeNode').attr('in', 'glow')
    merge.append('feMergeNode').attr('in', 'SourceGraphic')

    // Links (parent–child edges)
    const linkSel = g.append('g').selectAll('line').data(visLinks).enter().append('line')
      .attr('stroke', l => {
        if (isLinkDownTrap(l)) return '#ef4444'
        return l.online ? '#10b981' : '#ef4444'
      })
      .attr('stroke-width', l => isLinkDownTrap(l) ? 2.5 : 1.5)
      .attr('stroke-opacity', l => l.online && !isLinkDownTrap(l) ? 0.5 : 0.75)
      .attr('stroke-dasharray', l => {
        if (isLinkDownTrap(l)) return '6,4'
        return l.online ? 'none' : '8,6'
      })
      .attr('x1', l => (l.source as TopoNode).x ?? 0)
      .attr('y1', l => (l.source as TopoNode).y ?? 0)
      .attr('x2', l => (l.target as TopoNode).x ?? 0)
      .attr('y2', l => (l.target as TopoNode).y ?? 0)

    // Tooltip positioning
    const moveTip = (event: MouseEvent) => {
      const tip = tooltipRef.current
      if (!tip) return
      const rect = svgRef.current?.parentElement?.getBoundingClientRect()
      if (!rect) return
      const tipW = 290
      let left = event.clientX - rect.left + 16
      let top = event.clientY - rect.top - 10
      if (left + tipW > rect.width) left -= tipW + 32
      if (top < 0) top = 10
      tip.style.left = `${left}px`
      tip.style.top = `${top}px`
    }

    // Node groups
    const nodeG = g.append('g').selectAll<SVGGElement, TopoNode>('g').data(visNodes).enter().append('g')
      .attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`)
      .style('cursor', 'pointer')

    // Main circle
    nodeG.append('circle')
      .attr('r', d => {
        const v = deviceVisuals(d.deviceType)
        return hasTrap(d) ? v.radius + 2 : v.radius
      })
      .attr('fill', d => d.status === 'offline' ? '#0f172a' : deviceVisuals(d.deviceType).fill)
      .attr('stroke', d => {
        if (hasTrap(d)) return '#ef4444'
        if (d.status === 'offline') return '#ef4444'
        return deviceVisuals(d.deviceType).stroke
      })
      .attr('stroke-width', d => (hasTrap(d) || d.status === 'offline') ? 3 : 2)
      .attr('filter', d => (d.status === 'offline' || hasTrap(d)) ? 'url(#glow-red)' : null)

    // Icon text
    nodeG.append('text')
      .attr('text-anchor', 'middle').attr('dy', '0.35em')
      .attr('font-size', d => `${Math.max(10, deviceVisuals(d.deviceType).radius - 8)}px`)
      .attr('fill', 'white')
      .text(d => {
        if (hasTrap(d)) return '⚠️'
        if (d.status === 'offline') return '💤'
        return deviceVisuals(d.deviceType).icon
      })

    // Hostname label
    nodeG.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => deviceVisuals(d.deviceType).radius + 14)
      .attr('font-size', '10px')
      .attr('font-weight', 'bold')
      .attr('fill', d => d.status === 'offline' ? '#6b7280' : '#e2e8f0')
      .text(d => d.name)

    // Network sub-label on root nodes
    nodeG.filter(d => d.isRoot)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => deviceVisuals(d.deviceType).radius + 26)
      .attr('font-size', '9px')
      .attr('fill', '#94a3b8')
      .text(d => d.networkId.replace(/_/g, ' '))

    // Alert badge
    nodeG.filter(d => d.alerts > 0).append('circle')
      .attr('cx', d => deviceVisuals(d.deviceType).radius - 4)
      .attr('cy', d => -(deviceVisuals(d.deviceType).radius - 4))
      .attr('r', 9)
      .attr('fill', d => d.criticalAlerts > 0 ? '#ef4444' : '#f59e0b')
      .attr('stroke', '#0f172a').attr('stroke-width', 1.5)

    nodeG.filter(d => d.alerts > 0).append('text')
      .attr('x', d => deviceVisuals(d.deviceType).radius - 4)
      .attr('y', d => -(deviceVisuals(d.deviceType).radius - 4))
      .attr('text-anchor', 'middle').attr('dy', '0.35em')
      .attr('font-size', '8px').attr('fill', 'white').attr('font-weight', 'bold')
      .text(d => d.alerts > 99 ? '99+' : String(d.alerts))

    // Pulse ring for offline nodes (skip for very large graphs)
    if (visNodes.length <= 200) {
      nodeG.filter(d => d.status === 'offline').append('circle')
        .attr('r', d => deviceVisuals(d.deviceType).radius)
        .attr('fill', 'none').attr('stroke', '#ef4444').attr('stroke-width', 2).attr('opacity', 0.9)
        .each(function (d) {
          const r = deviceVisuals(d.deviceType).radius
          const el = d3.select(this)
          el.append('animate').attr('attributeName', 'r').attr('values', `${r};${r + 14};${r}`).attr('dur', '1.5s').attr('repeatCount', 'indefinite')
          el.append('animate').attr('attributeName', 'opacity').attr('values', '0.9;0;0.9').attr('dur', '1.5s').attr('repeatCount', 'indefinite')
        })
    }

    // Hover tooltip
    nodeG.on('mouseenter', function (event, d) {
      const tip = tooltipRef.current
      if (!tip) return
      const activeTrap = (d.ip ? trapAlerts.get(d.ip) : undefined) ?? trapAlerts.get(d.name)
      tip.innerHTML = `
        <div class="font-bold text-base mb-1.5 ${activeTrap ? 'text-red-300' : 'text-white'}">${d.name}</div>
        <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <span class="text-slate-400">Status</span>
          <span class="${d.status === 'online' ? 'text-green-400' : 'text-red-400'} font-semibold">${d.status === 'online' ? 'Online' : 'Offline'}</span>
          <span class="text-slate-400">Type</span>
          <span class="text-slate-200">${d.deviceType || '—'}</span>
          <span class="text-slate-400">Network</span>
          <span class="text-blue-300">${d.networkId.replace(/_/g, ' ')}</span>
          ${d.ip ? `<span class="text-slate-400">IP</span><span class="text-slate-200 font-mono text-[11px]">${d.ip}</span>` : ''}
          ${d.parentName
            ? `<span class="text-slate-400">Parent</span><span class="text-purple-300">${d.parentName}</span>`
            : `<span class="text-slate-400">Role</span><span class="text-yellow-300">Root / Gateway</span>`}
          <span class="text-slate-400">Depth</span>
          <span class="text-slate-300">${d.depth}</span>
          ${d.alerts > 0 ? `<span class="text-slate-400">Alerts</span><span class="${d.criticalAlerts > 0 ? 'text-red-400 font-bold' : 'text-yellow-400'}">${d.alerts} (${d.criticalAlerts} critical)</span>` : ''}
        </div>
        ${activeTrap ? `
          <div class="mt-2 pt-2 border-t border-red-500/40">
            <div class="text-red-400 font-bold text-xs mb-1">⚠ SNMP TRAP</div>
            <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
              <span class="text-slate-400">Trap</span><span class="text-red-300">${activeTrap.trapType}</span>
              <span class="text-slate-400">Severity</span><span class="text-red-300 font-bold">${activeTrap.severity?.toUpperCase()}</span>
              ${activeTrap.description ? `<span class="text-slate-400">Detail</span><span class="text-slate-300">${activeTrap.description}</span>` : ''}
            </div>
          </div>` : ''}
      `
      tip.style.opacity = '1'
      tip.style.pointerEvents = 'none'
      moveTip(event)
    })
    nodeG.on('mousemove', (_event, _d) => { moveTip(_event) })
    nodeG.on('mouseleave', () => { if (tooltipRef.current) tooltipRef.current.style.opacity = '0' })

    // Click → select node
    nodeG.on('click', (event, d) => {
      event.stopPropagation()
      if (clickTimerRef.current) { clearTimeout(clickTimerRef.current); clickTimerRef.current = null }
      clickTimerRef.current = setTimeout(() => { setSelectedNode(d); clickTimerRef.current = null }, 180)
    })

    // Drag support
    nodeG.call(
      d3.drag<SVGGElement, TopoNode>()
        .on('start', function (event) { d3.select(this).raise(); event.sourceEvent.stopPropagation() })
        .on('drag', function (event, d) {
          const [x, y] = d3.pointer(event.sourceEvent, g.node())
          d.x = x; d.y = y
          d3.select(this).attr('transform', `translate(${x},${y})`)
          linkSel
            .attr('x1', l => (l.source as TopoNode).x ?? 0)
            .attr('y1', l => (l.source as TopoNode).y ?? 0)
            .attr('x2', l => (l.target as TopoNode).x ?? 0)
            .attr('y2', l => (l.target as TopoNode).y ?? 0)
        })
        .on('end', () => {})
    )

    return () => {
      if (clickTimerRef.current) { clearTimeout(clickTimerRef.current); clickTimerRef.current = null }
    }
  }, [tree, treeLinks, trapAlerts, linkDownAlerts])

  // ── Zoom controls ──────────────────────────────────────────────────────────

  const handleZoomIn = () => {
    if (svgRef.current && zoomRef.current)
      d3.select(svgRef.current).transition().call(zoomRef.current.scaleBy, 1.3)
  }
  const handleZoomOut = () => {
    if (svgRef.current && zoomRef.current)
      d3.select(svgRef.current).transition().call(zoomRef.current.scaleBy, 0.7)
  }
  const handleReset = () => {
    if (svgRef.current && zoomRef.current)
      d3.select(svgRef.current).transition().duration(500).call(zoomRef.current.transform, d3.zoomIdentity)
  }

  // ── Loading state ──────────────────────────────────────────────────────────

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

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-4xl font-bold text-white">Network Topology</h1>
          <p className="text-slate-400 mt-1 text-lg">
            {stats.total} devices · {stats.online} online · {stats.offline} offline · {stats.networks} network{stats.networks !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Network filter */}
          {networks.length > 1 && (
            <select
              value={networkFilter}
              onChange={e => setNetworkFilter(e.target.value)}
              className="px-3 py-2 bg-slate-800/60 border border-white/10 text-white text-sm rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="all">All Networks</option>
              {networks.map(n => (
                <option key={n} value={n}>{n.replace(/_/g, ' ')}</option>
              ))}
            </select>
          )}
          {!showTrapFeed && trapFeed.length > 0 && (
            <button
              onClick={() => setShowTrapFeed(true)}
              className="flex items-center gap-2 px-3 py-2 bg-red-900/40 hover:bg-red-900/60 border border-red-500/40 text-red-300 rounded-lg transition-colors text-sm font-medium"
            >
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              Traps ({trapFeed.length})
            </button>
          )}
          <button
            onClick={() => navigate('/app/topology-3d')}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors text-sm font-medium"
          >
            <Box className="w-4 h-4" />
            3D View
          </button>
          <button
            onClick={() => navigate('/app/topology-editor')}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors text-sm font-medium"
          >
            <Edit3 className="w-4 h-4" />
            Editor
          </button>
          <button onClick={handleZoomIn} className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors" title="Zoom in">
            <ZoomIn className="w-5 h-5 text-slate-300" />
          </button>
          <button onClick={handleZoomOut} className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors" title="Zoom out">
            <ZoomOut className="w-5 h-5 text-slate-300" />
          </button>
          <button onClick={handleReset} className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors" title="Reset view">
            <Maximize2 className="w-5 h-5 text-slate-300" />
          </button>
          <button
            onClick={() => { /* refetch */ }}
            className="p-2 bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 rounded-lg transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-5 h-5 text-slate-300" />
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-4 min-h-0">
        {/* Canvas */}
        <div className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden relative">
          {tree.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <Network className="w-16 h-16 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400 text-lg font-medium">No topology data</p>
                <p className="text-slate-500 text-sm mt-1">Topology links appear after simulators start syncing with relation data</p>
              </div>
            </div>
          ) : (
            <svg ref={svgRef} className="w-full h-full" />
          )}

          {/* Hover tooltip */}
          <div
            ref={tooltipRef}
            className="absolute z-50 w-[290px] bg-slate-900/95 border border-white/15 rounded-xl px-4 py-3 backdrop-blur-md shadow-2xl transition-opacity duration-150"
            style={{ opacity: 0, pointerEvents: 'none', top: 0, left: 0 }}
          />

          {/* Stats overlay */}
          {showStats && tree.length > 0 && (
            <div className="absolute top-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
              <div className="flex justify-end mb-1">
                <button onClick={() => setShowStats(false)} className="text-slate-400 hover:text-white transition-colors">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <Network className="w-4 h-4 text-blue-400" />
                  <span className="text-slate-300">Networks: <span className="font-bold text-white">{stats.networks}</span></span>
                </div>
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-slate-400" />
                  <span className="text-slate-300">Devices: <span className="font-bold text-white">{stats.total}</span></span>
                </div>
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-green-400" />
                  <span className="text-slate-300">Online: <span className="font-bold text-green-400">{stats.online}</span></span>
                </div>
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-red-400" />
                  <span className="text-slate-300">Offline: <span className="font-bold text-red-400">{stats.offline}</span></span>
                </div>
                {stats.withAlerts > 0 && (
                  <div className="flex items-center gap-2">
                    <Activity className="w-4 h-4 text-yellow-400" />
                    <span className="text-slate-300">With alerts: <span className="font-bold text-yellow-400">{stats.withAlerts}</span></span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Legend */}
          {showLegend && tree.length > 0 && (
            <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm max-w-[220px]">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-white">Legend</h3>
                <button onClick={() => setShowLegend(false)} className="ml-4 text-slate-400 hover:text-white transition-colors">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="space-y-2 text-xs">
                {[
                  { fill: '#1e3a8a', stroke: '#3b82f6', icon: '🔀', label: 'Router / Gateway' },
                  { fill: '#7c2d12', stroke: '#f97316', icon: '🛡️', label: 'Firewall' },
                  { fill: '#3b0764', stroke: '#a855f7', icon: '🕸️', label: 'Spine / Fabric' },
                  { fill: '#14532d', stroke: '#22c55e', icon: '🌿', label: 'Leaf / ToR' },
                  { fill: '#0c4a6e', stroke: '#38bdf8', icon: '💻', label: 'Compute / Server' },
                  { fill: '#422006', stroke: '#f59e0b', icon: '⚡', label: 'PDU / Power' },
                ].map(item => (
                  <div key={item.label} className="flex items-center gap-2">
                    <div className="w-5 h-5 rounded-full flex items-center justify-center text-[9px] flex-shrink-0"
                      style={{ background: item.fill, border: `2px solid ${item.stroke}` }}>
                      {item.icon}
                    </div>
                    <span className="text-slate-300">{item.label}</span>
                  </div>
                ))}
                <div className="mt-2 pt-2 border-t border-white/10 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-0.5 bg-green-500" />
                    <span className="text-slate-300">Link up</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-0.5 border-t-2 border-dashed border-red-500" />
                    <span className="text-slate-300">Link down</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-5 h-5 rounded-full bg-red-500 flex items-center justify-center text-white font-bold" style={{ fontSize: '9px' }}>!</div>
                    <span className="text-slate-300">Alert badge</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Live trap feed */}
          {showTrapFeed && (
            <div className="absolute top-4 right-4 w-72 max-h-80 bg-slate-900/95 border border-red-500/30 rounded-lg backdrop-blur-sm flex flex-col overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 shrink-0">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-xs font-semibold text-white">Live SNMP Traps</span>
                  {trapFeed.length > 0 && (
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-red-500/20 text-red-400 border border-red-500/30">
                      {trapFeed.length}
                    </span>
                  )}
                </div>
                <button onClick={() => setShowTrapFeed(false)} className="text-slate-400 hover:text-white transition-colors">
                  <X className="w-3 h-3" />
                </button>
              </div>
              <div className="overflow-y-auto flex-1">
                {trapFeed.length === 0 ? (
                  <p className="text-slate-500 text-xs text-center py-4">Waiting for traps…</p>
                ) : (
                  trapFeed.map(t => (
                    <div key={t.id} className="px-3 py-2 border-b border-white/5 hover:bg-white/5 transition-colors">
                      <div className="flex items-center justify-between gap-2">
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                          t.severity.toLowerCase() === 'critical' ? 'bg-red-500/20 text-red-400' :
                          t.severity.toLowerCase() === 'warning' ? 'bg-amber-500/20 text-amber-400' :
                          'bg-blue-500/20 text-blue-400'
                        }`}>{t.severity.toUpperCase()}</span>
                        <span className="text-[10px] text-slate-500 shrink-0">
                          {new Date(t.timestamp).toLocaleTimeString()}
                        </span>
                      </div>
                      <p className="text-xs font-medium text-white mt-0.5 truncate">{t.deviceName || t.sourceIp}</p>
                      <p className="text-[10px] text-slate-400 truncate">{t.trapType}</p>
                      {t.description && <p className="text-[10px] text-slate-500 truncate">{t.description}</p>}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* Node detail panel */}
        {selectedNode && (
          <div className="w-80 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 space-y-4 overflow-y-auto">
            <div className="flex items-center justify-between">
              <h3 className="text-xl font-bold text-white">Device Details</h3>
              <button onClick={() => setSelectedNode(null)} className="text-slate-400 hover:text-white transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Hostname</p>
                <p className="text-lg font-semibold text-white">{selectedNode.name}</p>
              </div>

              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Status</p>
                <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${
                  selectedNode.status === 'online'
                    ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                    : 'bg-red-500/20 text-red-400 border border-red-500/30'
                }`}>
                  {selectedNode.status.toUpperCase()}
                </span>
              </div>

              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Device Type</p>
                <p className="text-sm text-white">{selectedNode.deviceType || '—'}</p>
              </div>

              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Network</p>
                <p className="text-sm text-blue-300">{selectedNode.networkId.replace(/_/g, ' ')}</p>
              </div>

              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Group</p>
                <p className="text-sm text-slate-300">{selectedNode.groupId}</p>
              </div>

              {selectedNode.ip && (
                <div>
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Management IP</p>
                  <p className="text-sm font-mono text-white">{selectedNode.ip}</p>
                </div>
              )}

              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">
                  {selectedNode.isRoot ? 'Role' : 'Parent'}
                </p>
                <p className="text-sm text-purple-300">
                  {selectedNode.isRoot ? 'Root / Gateway' : (selectedNode.parentName ?? '—')}
                </p>
              </div>

              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Hierarchy Depth</p>
                <p className="text-sm text-slate-300">{selectedNode.depth} ({selectedNode.isRoot ? 'root' : `${selectedNode.depth} level${selectedNode.depth !== 1 ? 's' : ''} from root`})</p>
              </div>

              {selectedNode.alerts > 0 && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Active Alerts</p>
                  <p className="text-2xl font-bold text-red-400">{selectedNode.alerts}</p>
                  {selectedNode.criticalAlerts > 0 && (
                    <p className="text-xs text-red-300 mt-0.5">{selectedNode.criticalAlerts} critical</p>
                  )}
                </div>
              )}

              <Link
                to={`/app/agents/${selectedNode.id}`}
                className="block w-full text-center px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 text-blue-400 rounded-lg transition-colors text-sm"
              >
                View Device Details →
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
