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
  Search,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { computeLayeredLayout } from '@/lib/topology-layered'
import { useSSE } from '@/hooks/useSSE'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TreeNode {
  device_id: string
  hostname: string
  device_type: string
  device_role: string | null
  role_confidence: number | null
  mgmt_ip: string | null
  network_id: string
  group_id: string
  datacenter_id: string
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
  deviceRole: string | null
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
  cooling: boolean   // cooling-loop pipe (chiller/CRAH/pump/tower) — rendered as flowing water
}

// Cooling-plant device roles. A link between two of these is a chilled/condenser
// water pipe rather than a network cable, so the topology draws it as flowing water.
const COOLING_ROLES = new Set(['pump', 'chiller', 'cooling_tower', 'crac', 'crah', 'cdu', 'valve', 'valves'])
function isCoolingDevice(role: string | null, deviceType: string, name: string): boolean {
  if (role && COOLING_ROLES.has(role.toLowerCase())) return true
  const s = `${deviceType || ''} ${name || ''}`.toLowerCase()
  return /chiller|cooling.?tower|cooling|\bcrac\b|\bcrah\b|\bcdu\b|\bchwp\b|\bcdwp\b|\bcwp\b|\bpump\b|valve/.test(s)
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

// ── Role → tier layer mapping ─────────────────────────────────────────────────
// Defines the fixed vertical row for each device_role.  Devices without a role
// fall back to their BFS depth so the topology still renders sensibly.

const ROLE_LAYERS: Record<string, number> = {
  edge_router:   0,
  firewall:      1,
  load_balancer: 2,
  core:          3,
  spine:         4,
  distribution:  4,
  leaf:          5,
  tor:           5,
  access:        5,
  oob_switch:    6,
  sensor:        7,
  server:        8,
  pdu:           9,
  ups:           10,
  floor_pdu:     11,
  pump:          12,
  chiller:       13,
  cooling_tower: 14,
  crah:          15,
  crac:          15,
  valve:         16,
  valves:        16,
  generator:     17,
  cdu:           18,   // Coolant Distribution Units — bottom-most tier
}

const ROLE_TIER_LABELS: Record<number, string> = {
  0:  'Edge Routers',
  1:  'Firewalls',
  2:  'Load Balancers',
  3:  'Core Switches',
  4:  'Spine Switches',
  5:  'Leaf / ToR Switches',
  6:  'OOB Switches',
  7:  'Sensors',
  8:  'Servers',
  9:  'PDUs',
  10: 'UPS',
  11: 'Floor PDUs',
  12: 'Pumps',
  13: 'Chillers',
  14: 'Cooling Towers',
  15: 'CRAH Units',
  16: 'Valves',
  17: 'Generators',
  18: 'CDUs',
}

const TIER_COLORS: Record<number, string> = {
  0:  '#3b82f6',
  1:  '#f97316',
  2:  '#2dd4bf',
  3:  '#6366f1',
  4:  '#a855f7',
  5:  '#22c55e',
  6:  '#64748b',
  7:  '#f43f5e',
  8:  '#38bdf8',
  9:  '#f59e0b',
  10: '#84cc16',
  11: '#fb923c',
  12: '#06b6d4',
  13: '#0ea5e9',
  14: '#14b8a6',
  15: '#5eead4',
  16: '#d946ef',
  17: '#fbbf24',
  18: '#8b5cf6',
}

function roleToLayer(role: string | null, name: string, fallback: number): number {
  if (role) {
    const l = ROLE_LAYERS[role.toLowerCase()]
    if (l !== undefined) return l
  }
  // Facility/plant tiers are often identified by hostname rather than a role.
  const n = (name || '').toLowerCase()
  if (/\bcdu\b|cdu-|^cdu/.test(n)) return 18
  if (/\bgenerator\b|\bgen-?\d|^gen-/.test(n)) return 17
  if (/\bvalve/.test(n)) return 16
  if (/\bcrah\b|\bcrac\b|crah-|crac-/.test(n)) return 15
  if (/cooling.?tower|\bct-?\d/.test(n)) return 14
  if (/\bchiller\b|chiller-|^chl-/.test(n)) return 13
  if (/\bpump\b|pump-|\bchwp\b|\bcdwp\b|\bcwp\b/.test(n)) return 12
  return fallback
}

// ── Device visual properties by role then type ────────────────────────────────

function deviceVisuals(deviceType: string, deviceRole?: string | null) {
  const role = (deviceRole || '').toLowerCase()
  const dt = (deviceType || '').toLowerCase()
  if (role === 'edge_router' || /router|rtr|edge.?router|cluster.?router/.test(dt))
    return { icon: '🔀', fill: '#1e3a8a', stroke: '#3b82f6', radius: 28 }
  if (role === 'firewall' || /firewall|fw/.test(dt))
    return { icon: '🛡️', fill: '#7c2d12', stroke: '#f97316', radius: 26 }
  if (role === 'load_balancer' || /load.?bal|^lb$/.test(dt))
    return { icon: '⚖️', fill: '#134e4a', stroke: '#2dd4bf', radius: 24 }
  if (role === 'core')
    return { icon: '🏗️', fill: '#1a1a4e', stroke: '#6366f1', radius: 26 }
  if (role === 'spine' || role === 'distribution' || /super.?spine|spine|fabric/.test(dt))
    return { icon: '🕸️', fill: '#3b0764', stroke: '#a855f7', radius: 26 }
  if (/aggregat|agg.?switch/.test(dt))
    return { icon: '🔗', fill: '#1e3a5f', stroke: '#818cf8', radius: 24 }
  if (role === 'leaf' || role === 'tor' || role === 'access' || /leaf|tor|top.?of.?rack/.test(dt))
    return { icon: '🌿', fill: '#14532d', stroke: '#22c55e', radius: 22 }
  if (/gpu/.test(dt))
    return { icon: '🖥️', fill: '#500724', stroke: '#f43f5e', radius: 20 }
  if (/storage|nas|san/.test(dt))
    return { icon: '💾', fill: '#3b2200', stroke: '#eab308', radius: 20 }
  if (role === 'server' || /compute|server|host/.test(dt))
    return { icon: '💻', fill: '#0c4a6e', stroke: '#38bdf8', radius: 20 }
  if (role === 'ups' || /ups/.test(dt))
    return { icon: '🔋', fill: '#1a2e05', stroke: '#84cc16', radius: 18 }
  if (role === 'floor_pdu')
    return { icon: '⚡', fill: '#2d1200', stroke: '#fb923c', radius: 16 }
  if (role === 'pdu' || /pdu|power/.test(dt))
    return { icon: '⚡', fill: '#422006', stroke: '#f59e0b', radius: 18 }
  if (role === 'generator' || /generator|\bgen-/.test(dt))
    return { icon: '🛢️', fill: '#451a03', stroke: '#fbbf24', radius: 18 }
  if (role === 'valve' || role === 'valves' || /valve/.test(dt))
    return { icon: '🚰', fill: '#3b0764', stroke: '#d946ef', radius: 14 }
  if (role === 'crah' || role === 'crac' || /\bcrah\b|\bcrac\b/.test(dt))
    return { icon: '🌬️', fill: '#042f2e', stroke: '#5eead4', radius: 18 }
  if (role === 'cooling_tower' || /cooling.?tower/.test(dt))
    return { icon: '💨', fill: '#042f2e', stroke: '#14b8a6', radius: 18 }
  if (role === 'chiller' || /chiller/.test(dt))
    return { icon: '❄️', fill: '#082f49', stroke: '#0ea5e9', radius: 18 }
  if (role === 'pump' || /pump/.test(dt))
    return { icon: '💧', fill: '#083344', stroke: '#06b6d4', radius: 16 }
  if (role === 'sensor' || /sensor|thermometer|hygrometer/.test(dt))
    return { icon: '🌡️', fill: '#1f1a2e', stroke: '#f43f5e', radius: 14 }
  if (role === 'oob_switch' || /oob|out.?of.?band/.test(dt))
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

// A trap is a link-up (restore) event if its type normalizes to "linkup" — the
// counterpart to linkDown. Same loose match (linkUp / LINK_UP / OOBSwitchLinkUp).
const isLinkUpType = (s?: string) =>
  (s ?? '').toUpperCase().replace(/[^A-Z]/g, '').includes('LINKUP')

// Alarm events carry alarm_state ("active" | "closed"), either as a top-level
// field (SSE) or inside event_payload (DB rows). "closed" means the alarm cleared
// → remove all alarms from that device; anything else ("active") keeps it shown.
const isAlarmClosed = (t: any): boolean =>
  String(t?.alarm_state ?? t?.event_payload?.alarm_state ?? '').toLowerCase() === 'closed'

// A link trap names BOTH endpoints (source + dst). To break only that one edge —
// not every cable on the source device — we key link-down state by the UNORDERED
// endpoint pair. pairKey() builds the canonical key for one identifier type;
// null when either side is missing.
const pairKey = (a?: string | null, b?: string | null): string | null => {
  const x = (a ?? '').trim().toLowerCase()
  const y = (b ?? '').trim().toLowerCase()
  if (!x || !y) return null
  return [x, y].sort().join('||')
}

// All candidate pair keys for a link trap / edge: device_id, hostname, and ip —
// whichever both ends happen to share. An edge is "down" if ANY of its keys is in
// the link-down set. If the trap carries no dst endpoint, this is empty and NO
// edge is broken (avoids the old behaviour of lighting up every link on a device).
const linkPairKeys = (
  src: { id?: string | null; name?: string | null; ip?: string | null },
  dst: { id?: string | null; name?: string | null; ip?: string | null },
): string[] =>
  [pairKey(src.id, dst.id), pairKey(src.name, dst.name), pairKey(src.ip, dst.ip)]
    .filter((k): k is string => !!k)

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
  const [showTierBands, setShowTierBands] = useState(true)
  const [networkFilter, setNetworkFilter] = useState<string>('all')
  const [datacenterFilter, setDatacenterFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  // The query that has been *applied* via the Apply button. When set, the graph
  // is filtered down to the matching device(s) plus every device reachable from
  // them through the link graph (their connected sub-network).
  const [appliedFocus, setAppliedFocus] = useState('')
  const prevSearchRef = useRef('')

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

  // Energy-monitor devices are surfaced under Power Management → PDU circuit
  // mapping, not the network topology — exclude them (and their links) here.
  const rawTree: TreeNode[] = (topoData?.nodes ?? []).filter(n => n.device_role !== 'energy_monitor')
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
      // Traps are ordered ts DESC, so the FIRST event seen per device key is the
      // latest — it decides the device's current alarm state. A closed alarm (or
      // a linkUp restore) clears all alarms on that device; an active one shows.
      const decided = new Set<string>()
      traps.forEach(t => {
        const keys = [t.source_ip, t.device_name].filter(Boolean) as string[]
        const cleared = isAlarmClosed(t) || isLinkUpType(t.trap_type) || t.resolved === true
        const alert: TrapAlert = {
          trapType: t.trap_type ?? '', severity: t.severity ?? 'critical',
          description: t.description ?? '', deviceName: t.device_name ?? '',
          timestamp: t.timestamp ?? new Date().toISOString(),
        }
        keys.forEach(k => {
          if (decided.has(k)) return
          decided.add(k)
          if (cleared) next.delete(k)
          else next.set(k, alert)
        })
      })
      return next
    })
    setLinkDownAlerts(prev => {
      const next = new Map(prev)
      // Only still-open (unresolved) linkDowns; a resolved one means the link is
      // back up and must not re-break on the 30s reseed.
      traps.filter(t => isLinkDownType(t.trap_type) && !t.resolved).forEach(t => {
        const alert: TrapAlert = {
          trapType: t.trap_type ?? '', severity: t.severity ?? 'critical',
          description: t.description ?? '', deviceName: t.device_name ?? '',
          timestamp: t.timestamp ?? new Date().toISOString(),
        }
        linkPairKeys(
          { id: t.device_id, name: t.device_name, ip: t.source_ip },
          { id: t.dst_device_id, name: t.dst_hostname, ip: t.dst_ip },
        ).forEach(k => next.set(k, alert))
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
    const keys = [ip, name].filter(Boolean) as string[]    // device-level keys → node glow
    const isLinkDown = isLinkDownType(data.trap_type)
    const isLinkUp = isLinkUpType(data.trap_type)
    // Pair keys identifying the SPECIFIC link this trap is about (source ↔ peer).
    const lKeys = linkPairKeys(
      { id: data.device_id, name, ip },
      { id: data.dst_device_id, name: data.dst_hostname, ip: data.dst_ip },
    )

    // A new transition for these keys supersedes any pending auto-expire timer.
    keys.forEach(k => { const ex = trapTimeoutsRef.current.get(k); if (ex) { clearTimeout(ex); trapTimeoutsRef.current.delete(k) } })

    // Always surface the trap in the live feed (including the restore).
    setTrapFeed(prev => [{
      id: Date.now(), sourceIp: ip ?? '', deviceName: name ?? '',
      trapType: data.trap_type ?? '', severity: data.severity ?? 'critical',
      description: data.description ?? '', timestamp: data.timestamp ?? new Date().toISOString(),
    }, ...prev].slice(0, 30))

    // linkUp = link restored, or alarm_state "closed" = alarm cleared: remove all
    // alarms from this device (node glow) and clear this link's down-state so the
    // node/cable render normal again immediately.
    if (isLinkUp || isAlarmClosed(data)) {
      if (lKeys.length) setLinkDownAlerts(prev => { const next = new Map(prev); lKeys.forEach(k => next.delete(k)); return next })
      setTrapAlerts(prev => { const next = new Map(prev); keys.forEach(k => next.delete(k)); return next })
      return
    }

    const alert: TrapAlert = {
      trapType: data.trap_type ?? '', severity: data.severity ?? 'critical',
      description: data.description ?? '', deviceName: name ?? '',
      timestamp: data.timestamp ?? new Date().toISOString(),
    }
    setTrapAlerts(prev => { const next = new Map(prev); if (ip) next.set(ip, alert); if (name) next.set(name, alert); return next })
    // Break only the affected edge. With no dst endpoint, lKeys is empty → no edge
    // is marked down (better than breaking every link on the device).
    if (isLinkDown && lKeys.length) {
      setLinkDownAlerts(prev => { const next = new Map(prev); lKeys.forEach(k => next.set(k, alert)); return next })
    }
    const timeout = setTimeout(() => {
      setTrapAlerts(prev => { const next = new Map(prev); keys.forEach(k => next.delete(k)); return next })
      if (isLinkDown && lKeys.length) setLinkDownAlerts(prev => { const next = new Map(prev); lKeys.forEach(k => next.delete(k)); return next })
      keys.forEach(k => trapTimeoutsRef.current.delete(k))
    }, 5 * 60 * 1000)
    keys.forEach(k => trapTimeoutsRef.current.set(k, timeout))
  }, [])

  useSSE('trap', handleTrapEvent, true)

  // ── Derived data ───────────────────────────────────────────────────────────

  const networks = useMemo(() => {
    return [...new Set(rawTree.map(n => n.network_id))].sort()
  }, [rawTree])

  const datacenters = useMemo(() => {
    return [...new Set(rawTree.map(n => n.datacenter_id).filter(Boolean))].sort()
  }, [rawTree])

  // Devices visible after the Datacenter + Network dropdown filters
  // (before any device focus).
  const baseTree = useMemo(() => {
    return rawTree.filter(n =>
      (datacenterFilter === 'all' || n.datacenter_id === datacenterFilter) &&
      (networkFilter === 'all' || n.network_id === networkFilter)
    )
  }, [rawTree, datacenterFilter, networkFilter])

  // Apply the device focus: when a query has been applied, keep only the matching
  // seed device(s) and everything connected to them (directly or transitively)
  // through the link graph — i.e. the whole sub-network around that device.
  const tree = useMemo(() => {
    const f = appliedFocus.trim().toLowerCase()
    if (!f) return baseTree

    const seeds = baseTree.filter(n =>
      n.hostname.toLowerCase().includes(f) || (n.mgmt_ip ?? '').toLowerCase().includes(f)
    )
    if (seeds.length === 0) return baseTree   // no match → leave the full graph as-is

    // Build an undirected adjacency over the visible devices from both the
    // physical links and the hierarchy parent→child relationships (matching the
    // edges the graph actually draws).
    const idSet = new Set(baseTree.map(n => n.device_id))
    const adj = new Map<string, Set<string>>()
    const addEdge = (a: string, b: string) => {
      if (a === b || !idSet.has(a) || !idSet.has(b)) return
      if (!adj.has(a)) adj.set(a, new Set())
      if (!adj.has(b)) adj.set(b, new Set())
      adj.get(a)!.add(b)
      adj.get(b)!.add(a)
    }
    rawLinks.forEach(l => addEdge(l.src_device_id, l.dst_device_id))
    baseTree.forEach(n => { if (n.parent_device_id) addEdge(n.device_id, n.parent_device_id) })

    // BFS outward from every seed to collect the entire connected component.
    const keep = new Set<string>(seeds.map(s => s.device_id))
    const queue = [...keep]
    while (queue.length) {
      const cur = queue.shift()!
      for (const nb of adj.get(cur) ?? []) {
        if (!keep.has(nb)) { keep.add(nb); queue.push(nb) }
      }
    }
    return baseTree.filter(n => keep.has(n.device_id))
  }, [baseTree, rawLinks, appliedFocus])

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

  // Count of devices matching the search query (by hostname or management IP)
  const matchCount = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return 0
    return baseTree.filter(n =>
      n.hostname.toLowerCase().includes(q) || (n.mgmt_ip ?? '').toLowerCase().includes(q)
    ).length
  }, [baseTree, searchQuery])

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
      deviceRole: row.device_role,
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
      const s = nodeById.get(tl.src_device_id)
      const t = nodeById.get(tl.dst_device_id)
      const cooling = !!s && !!t &&
        isCoolingDevice(s.deviceRole, s.deviceType, s.name) &&
        isCoolingDevice(t.deviceRole, t.deviceType, t.name)
      links.push({
        source: tl.src_device_id,
        target: tl.dst_device_id,
        online: tl.src_status === 'online' && tl.dst_status === 'online',
        cooling,
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
      const cooling = !!child && !!parent &&
        isCoolingDevice(child.deviceRole, child.deviceType, child.name) &&
        isCoolingDevice(parent.deviceRole, parent.deviceType, parent.name)
      links.push({
        source: row.device_id,
        target: row.parent_device_id,
        online: child?.status === 'online' && parent?.status === 'online',
        cooling,
      })
    }

    // Layered layout: device_role maps each node to a fixed tier row.
    // Devices without a role fall back to their BFS depth.
    const LAYER_GAP_Y = 160
    const layeredInput = nodes.map(n => ({
      id: n.id,
      layer: roleToLayer(n.deviceRole, n.name, n.depth),
    }))
    const layeredEdges = links.map(l => ({
      source: typeof l.source === 'string' ? l.source : (l.source as TopoNode).id,
      target: typeof l.target === 'string' ? l.target : (l.target as TopoNode).id,
    }))

    const layered = computeLayeredLayout(layeredInput, layeredEdges, {
      nodeGapX: 100,
      layerGapY: LAYER_GAP_Y,
      iterations: 28,
      originX: width / 2,
      originY: 90,
    })

    nodes.forEach(n => {
      const p = layered.positions.get(n.id)
      if (p) { n.x = p.x; n.y = p.y }
    })

    // Build layer → y mapping so we can draw tier band labels behind everything
    const layerYMap = new Map<number, number>()
    layeredInput.forEach(({ id, layer }) => {
      if (!layerYMap.has(layer)) {
        const p = layered.positions.get(id)
        if (p) layerYMap.set(layer, p.y)
      }
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

    // Down only when THIS edge's endpoint pair matches a link-down trap — so a
    // single linkDown breaks just its own cable, not every link on the device.
    const isLinkDownTrap = (l: TopoLink) => {
      const sn = l.source as TopoNode
      const tn = l.target as TopoNode
      return linkPairKeys(
        { id: sn.id, name: sn.name, ip: sn.ip },
        { id: tn.id, name: tn.name, ip: tn.ip },
      ).some(k => linkDownAlerts.has(k))
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

    // Tier bands — rendered first so they appear behind links and nodes
    if (showTierBands && layerYMap.size > 0) {
      const sortedLyrs = [...layerYMap.keys()].sort((a, b) => a - b)
      const allX = visNodes.map(n => n.x!).filter(x => isFinite(x))
      const xMin = allX.length ? Math.min(...allX) - 130 : -300
      const xMax = allX.length ? Math.max(...allX) + 80 : 300
      const bandG = g.append('g').attr('class', 'tier-bands')

      sortedLyrs.forEach((lyr, i) => {
        const y = layerYMap.get(lyr)!
        const prevY = i > 0 ? layerYMap.get(sortedLyrs[i - 1])! : y - LAYER_GAP_Y
        const nextY = i < sortedLyrs.length - 1 ? layerYMap.get(sortedLyrs[i + 1])! : y + LAYER_GAP_Y
        const bandTop = (prevY + y) / 2
        const bandBot = (y + nextY) / 2
        const color = TIER_COLORS[lyr] ?? '#1e293b'
        const label = (ROLE_TIER_LABELS[lyr] ?? `Tier ${lyr}`).toUpperCase()

        // Semi-transparent row background
        bandG.append('rect')
          .attr('x', xMin - 10)
          .attr('y', bandTop)
          .attr('width', xMax - xMin + 20)
          .attr('height', bandBot - bandTop)
          .attr('rx', 6)
          .attr('fill', color)
          .attr('opacity', 0.055)

        // Left-side tier label
        bandG.append('text')
          .attr('x', xMin - 14)
          .attr('y', y)
          .attr('text-anchor', 'end')
          .attr('dominant-baseline', 'middle')
          .attr('font-size', '9px')
          .attr('font-weight', '700')
          .attr('letter-spacing', '0.08em')
          .attr('fill', color)
          .attr('opacity', 0.75)
          .text(label)

        // Thin separator line at top of each band (skip the very first)
        if (i > 0) {
          bandG.append('line')
            .attr('x1', xMin - 10).attr('y1', bandTop)
            .attr('x2', xMax + 10).attr('y2', bandTop)
            .attr('stroke', color)
            .attr('stroke-width', 0.5)
            .attr('stroke-dasharray', '6,4')
            .attr('opacity', 0.25)
        }
      })
    }

    const lx1 = (l: TopoLink) => (l.source as TopoNode).x ?? 0
    const ly1 = (l: TopoLink) => (l.source as TopoNode).y ?? 0
    const lx2 = (l: TopoLink) => (l.target as TopoNode).x ?? 0
    const ly2 = (l: TopoLink) => (l.target as TopoNode).y ?? 0

    // Cooling-loop pipes (chiller ↔ tower ↔ CRAH ↔ pump). Drawn first, under the
    // network links: a thick cyan pipe wall with a dashed overlay whose moving
    // dash-offset animates the chilled water flowing through it.
    const coolingLinks = visLinks.filter(l => l.cooling)
    const pipeG = g.append('g').attr('class', 'cooling-pipes')
    pipeG.selectAll('line.pipe-wall').data(coolingLinks).enter().append('line')
      .attr('class', 'pipe-wall')
      .attr('stroke', l => isLinkDownTrap(l) ? '#ef4444' : '#155e75')
      .attr('stroke-width', 6)
      .attr('stroke-linecap', 'round')
      .attr('stroke-opacity', 0.55)
      .attr('x1', lx1).attr('y1', ly1).attr('x2', lx2).attr('y2', ly2)
    pipeG.selectAll('line.pipe-flow').data(coolingLinks).enter().append('line')
      .attr('class', l => `pipe-flow ${l.online && !isLinkDownTrap(l) ? 'pipe-flow--on' : ''}`)
      .attr('stroke', l => (l.online && !isLinkDownTrap(l)) ? '#67e8f9' : '#94a3b8')
      .attr('stroke-width', 3)
      .attr('stroke-linecap', 'round')
      .attr('stroke-dasharray', '2 14')
      .attr('stroke-opacity', 0.95)
      .attr('x1', lx1).attr('y1', ly1).attr('x2', lx2).attr('y2', ly2)

    // Links (parent–child edges) — network cables only; cooling pipes drawn above.
    const linkSel = g.append('g').selectAll('line').data(visLinks.filter(l => !l.cooling)).enter().append('line')
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
      .attr('x1', lx1).attr('y1', ly1).attr('x2', lx2).attr('y2', ly2)

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
      .classed('topo-node-group', true)
      .attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`)
      .style('cursor', 'pointer')

    // Search highlight ring (hidden until a search match — toggled by the search effect)
    nodeG.append('circle')
      .attr('class', 'search-ring')
      .attr('r', d => deviceVisuals(d.deviceType, d.deviceRole).radius + 8)
      .attr('fill', 'none')
      .attr('stroke', '#fbbf24')
      .attr('stroke-width', 3.5)
      .attr('opacity', 0)
      .style('pointer-events', 'none')

    // Main circle
    nodeG.append('circle')
      .attr('r', d => {
        const v = deviceVisuals(d.deviceType, d.deviceRole)
        return hasTrap(d) ? v.radius + 2 : v.radius
      })
      .attr('fill', d => d.status === 'offline' ? '#0f172a' : deviceVisuals(d.deviceType, d.deviceRole).fill)
      .attr('stroke', d => {
        if (hasTrap(d)) return '#ef4444'
        if (d.status === 'offline') return '#ef4444'
        return deviceVisuals(d.deviceType, d.deviceRole).stroke
      })
      .attr('stroke-width', d => (hasTrap(d) || d.status === 'offline') ? 3 : 2)
      .attr('filter', d => (d.status === 'offline' || hasTrap(d)) ? 'url(#glow-red)' : null)

    // Icon text
    nodeG.append('text')
      .attr('text-anchor', 'middle').attr('dy', '0.35em')
      .attr('font-size', d => `${Math.max(10, deviceVisuals(d.deviceType, d.deviceRole).radius - 8)}px`)
      .attr('fill', 'white')
      .text(d => {
        if (hasTrap(d)) return '⚠️'
        if (d.status === 'offline') return '💤'
        return deviceVisuals(d.deviceType, d.deviceRole).icon
      })

    // Hostname label
    nodeG.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => deviceVisuals(d.deviceType, d.deviceRole).radius + 14)
      .attr('font-size', '10px')
      .attr('font-weight', 'bold')
      .attr('fill', d => d.status === 'offline' ? '#6b7280' : '#e2e8f0')
      .text(d => d.name)

    // Network sub-label on root nodes
    nodeG.filter(d => d.isRoot)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', d => deviceVisuals(d.deviceType, d.deviceRole).radius + 26)
      .attr('font-size', '9px')
      .attr('fill', '#94a3b8')
      .text(d => d.networkId.replace(/_/g, ' '))

    // Alert badge
    nodeG.filter(d => d.alerts > 0).append('circle')
      .attr('cx', d => deviceVisuals(d.deviceType, d.deviceRole).radius - 4)
      .attr('cy', d => -(deviceVisuals(d.deviceType, d.deviceRole).radius - 4))
      .attr('r', 9)
      .attr('fill', d => d.criticalAlerts > 0 ? '#ef4444' : '#f59e0b')
      .attr('stroke', '#0f172a').attr('stroke-width', 1.5)

    nodeG.filter(d => d.alerts > 0).append('text')
      .attr('x', d => deviceVisuals(d.deviceType, d.deviceRole).radius - 4)
      .attr('y', d => -(deviceVisuals(d.deviceType, d.deviceRole).radius - 4))
      .attr('text-anchor', 'middle').attr('dy', '0.35em')
      .attr('font-size', '8px').attr('fill', 'white').attr('font-weight', 'bold')
      .text(d => d.alerts > 99 ? '99+' : String(d.alerts))

    // Pulse ring for offline nodes (skip for very large graphs)
    if (visNodes.length <= 200) {
      nodeG.filter(d => d.status === 'offline').append('circle')
        .attr('r', d => deviceVisuals(d.deviceType, d.deviceRole).radius)
        .attr('fill', 'none').attr('stroke', '#ef4444').attr('stroke-width', 2).attr('opacity', 0.9)
        .each(function (d) {
          const r = deviceVisuals(d.deviceType, d.deviceRole).radius
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
          ${d.deviceRole ? `<span class="text-slate-400">Tier</span><span class="text-indigo-300 font-semibold">${d.deviceRole.replace(/_/g, ' ')}</span>` : ''}
          <span class="text-slate-400">Network</span>
          <span class="text-blue-300">${d.networkId.replace(/_/g, ' ')}</span>
          ${d.ip ? `<span class="text-slate-400">IP</span><span class="text-slate-200 font-mono text-[11px]">${d.ip}</span>` : ''}
          ${d.parentName
            ? `<span class="text-slate-400">Parent</span><span class="text-purple-300">${d.parentName}</span>`
            : `<span class="text-slate-400">Role</span><span class="text-yellow-300">Root / Gateway</span>`}
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

    // Drag support — cooling-plant devices (those joined by water pipes) are
    // pinned to their layout position and excluded, so only network devices move.
    nodeG.filter(d => !isCoolingDevice(d.deviceRole, d.deviceType, d.name)).call(
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
  }, [tree, treeLinks, trapAlerts, linkDownAlerts, showTierBands])

  // ── Search highlight ─────────────────────────────────────────────────────────
  // Runs after the graph is (re)rendered. Highlights nodes whose hostname or
  // management IP contains the query, dims the rest, and pans to fit the matches.
  useEffect(() => {
    if (!svgRef.current) return
    const focus = appliedFocus.trim().toLowerCase()
    const q = searchQuery.trim().toLowerCase()
    const groups = d3.select(svgRef.current).selectAll<SVGGElement, TopoNode>('g.topo-node-group')
    if (groups.empty()) return

    // Once a focus is applied the graph is already filtered to the connected
    // sub-network, so every node stays fully visible and we only ring the seed
    // device(s). While the user is just typing (no focus yet), preview the match
    // by dimming the non-matching nodes.
    const highlightTerm = focus || q
    const dimOthers = !focus && q !== ''

    const matched: TopoNode[] = []
    groups.each(function (d) {
      const isMatch = highlightTerm !== '' &&
        (d.name.toLowerCase().includes(highlightTerm) || (d.ip ?? '').toLowerCase().includes(highlightTerm))
      if (isMatch) matched.push(d)
      const sel = d3.select(this)
      sel.style('opacity', dimOthers ? (isMatch ? 1 : 0.15) : 1)
      sel.select('.search-ring').attr('opacity', isMatch ? 1 : 0)
    })

    // Pan/zoom to the live-preview matches when the query changes. When a focus is
    // applied the render effect's auto-fit already frames the whole sub-network.
    const queryChanged = prevSearchRef.current !== searchQuery
    prevSearchRef.current = searchQuery
    if (!focus && queryChanged && q !== '' && matched.length > 0 && zoomRef.current) {
      const xs = matched.map(d => d.x!).filter(isFinite)
      const ys = matched.map(d => d.y!).filter(isFinite)
      if (xs.length) {
        const width = svgRef.current.clientWidth
        const height = svgRef.current.clientHeight
        const pad = 140
        const contentW = (Math.max(...xs) - Math.min(...xs)) + pad * 2
        const contentH = (Math.max(...ys) - Math.min(...ys)) + pad * 2
        const scale = Math.min(width / contentW, height / contentH, 1.4)
        const cx = (Math.min(...xs) + Math.max(...xs)) / 2
        const cy = (Math.min(...ys) + Math.max(...ys)) / 2
        d3.select(svgRef.current).transition().duration(450).call(
          zoomRef.current.transform as any,
          d3.zoomIdentity.translate(width / 2 - cx * scale, height / 2 - cy * scale).scale(scale)
        )
      }
    }
  }, [searchQuery, appliedFocus, tree, treeLinks, trapAlerts, linkDownAlerts, showTierBands])

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
          {/* Device search — type a name/IP, then Apply to focus on that device
              and its entire connected sub-network */}
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') setAppliedFocus(searchQuery) }}
                placeholder="Search name or IP…"
                className="w-56 pl-9 pr-16 py-2 bg-slate-800/60 border border-white/10 text-white text-sm rounded-lg focus:outline-none focus:border-blue-500 placeholder:text-slate-500"
              />
              {searchQuery.trim() !== '' && (
                <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5">
                  <span className={`text-[11px] font-medium ${matchCount > 0 ? 'text-amber-300' : 'text-slate-500'}`}>
                    {matchCount}
                  </span>
                  <button
                    onClick={() => { setSearchQuery(''); setAppliedFocus('') }}
                    className="text-slate-400 hover:text-white transition-colors"
                    title="Clear search"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>
            <button
              onClick={() => setAppliedFocus(searchQuery)}
              disabled={searchQuery.trim() === ''}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
              title="Show this device and everything connected to it"
            >
              Apply
            </button>
            {appliedFocus.trim() !== '' && (
              <button
                onClick={() => setAppliedFocus('')}
                className="px-3 py-2 bg-slate-800/60 hover:bg-slate-700 border border-white/10 text-slate-300 text-sm font-medium rounded-lg transition-colors"
                title="Show the full topology again"
              >
                Reset focus
              </button>
            )}
          </div>
          {/* Datacenter filter */}
          {datacenters.length > 0 && (
            <select
              value={datacenterFilter}
              onChange={e => setDatacenterFilter(e.target.value)}
              className="px-3 py-2 bg-slate-800/60 border border-white/10 text-white text-sm rounded-lg focus:outline-none focus:border-blue-500"
              title="Filter by datacenter"
            >
              <option value="all">All Components</option>
              {datacenters.map(dc => (
                <option key={dc} value={dc}>{dc}</option>
              ))}
            </select>
          )}
          {/* Network filter */}
          {networks.length > 1 && (
            <select
              value={networkFilter}
              onChange={e => setNetworkFilter(e.target.value)}
              className="px-3 py-2 bg-slate-800/60 border border-white/10 text-white text-sm rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="all">All Datacenters</option>
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
          <button
            onClick={() => setShowTierBands(v => !v)}
            title={showTierBands ? 'Hide tier bands' : 'Show tier bands'}
            className={`flex items-center gap-1.5 px-3 py-2 border rounded-lg transition-colors text-sm font-medium ${
              showTierBands
                ? 'bg-indigo-500/20 border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/30'
                : 'bg-slate-800/50 border-white/10 text-slate-400 hover:bg-slate-700/50'
            }`}
          >
            <span className="text-base leading-none">🏷️</span>
            Tiers
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
                  { fill: '#1e3a8a', stroke: '#3b82f6', icon: '🔀', label: 'Edge Router' },
                  { fill: '#7c2d12', stroke: '#f97316', icon: '🛡️', label: 'Firewall' },
                  { fill: '#134e4a', stroke: '#2dd4bf', icon: '⚖️', label: 'Load Balancer' },
                  { fill: '#1a1a4e', stroke: '#6366f1', icon: '🏗️', label: 'Core Switch' },
                  { fill: '#3b0764', stroke: '#a855f7', icon: '🕸️', label: 'Spine Switch' },
                  { fill: '#14532d', stroke: '#22c55e', icon: '🌿', label: 'Leaf / ToR' },
                  { fill: '#1e293b', stroke: '#64748b', icon: '🔌', label: 'OOB Switch' },
                  { fill: '#1f1a2e', stroke: '#f43f5e', icon: '🌡️', label: 'Sensor' },
                  { fill: '#0c4a6e', stroke: '#38bdf8', icon: '💻', label: 'Server' },
                  { fill: '#422006', stroke: '#f59e0b', icon: '⚡', label: 'PDU' },
                  { fill: '#1a2e05', stroke: '#84cc16', icon: '🔋', label: 'UPS' },
                  { fill: '#2d1200', stroke: '#fb923c', icon: '⚡', label: 'Floor PDU' },
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

              {selectedNode.deviceRole && (
                <div>
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Fabric Tier</p>
                  <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
                    {selectedNode.deviceRole.replace(/_/g, ' ')}
                  </span>
                </div>
              )}

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
