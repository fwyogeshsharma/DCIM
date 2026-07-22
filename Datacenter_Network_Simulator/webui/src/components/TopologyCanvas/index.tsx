import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import NodeContextMenu, { EditDeviceDialog, DeviceInfoModal } from './NodeContextMenu'

import {
  ReactFlow,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  ReactFlowProvider,
  useReactFlow,
  useViewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import DeviceNode from './DeviceNode'
import LinkEdge, { isHotFlow } from './LinkEdge'
import { useStore } from '../../store/useStore'
import { api, errorMessage } from '../../api/client'
import type { GraphDevice, GraphLink } from '../../api/types'

const nodeTypes = { device: DeviceNode }
const edgeTypes = { link: LinkEdge }

const LAYER_BTN: { id: string; label: string; color: string }[] = [
  { id: 'all',        label: 'All',   color: '#8b8d8c' },
  { id: 'production', label: 'Prod',  color: 'var(--layer-prod)' },
  { id: 'management', label: 'Mgmt',  color: 'var(--layer-mgmt)' },
  { id: 'power',      label: 'Power', color: 'var(--layer-power)' },
  { id: 'cooling',    label: 'Cool',  color: '#93b6bf' },
]

const NODE_TYPE_COLOR: Record<string, string> = {
  router:        'var(--node-router)',
  switch:        'var(--node-switch)',
  server:        'var(--node-server)',
  firewall:      'var(--node-firewall)',
  load_balancer: 'var(--node-lb)',
  oob_switch:    'var(--node-oob)',
  pdu:           'var(--node-pdu)',
  floor_pdu:     'var(--node-floor-pdu)',
  rpp:           'var(--node-rpp)',
  generator:     'var(--node-generator)',
  ups:           'var(--node-ups)',
  sensor:        'var(--node-sensor)',
}

// ── Icons ──────────────────────────────────────────────────
const I = {
  search: () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>,
  zoomIn: () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" /></svg>,
  zoomOut: () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="8" y1="11" x2="14" y2="11" /></svg>,
  fit: () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 8V5a2 2 0 0 1 2-2h3" /><path d="M21 8V5a2 2 0 0 0-2-2h-3" /><path d="M3 16v3a2 2 0 0 0 2 2h3" /><path d="M21 16v3a2 2 0 0 1-2 2h-3" /></svg>,
  reset: () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" /><path d="M16 16h5v5" /></svg>,
  close: () => <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>,
  info: () => <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" /></svg>,
}

function tierLayout(devices: GraphDevice[]): Map<string, { x: number; y: number }> {
  const pos = new Map<string, { x: number; y: number }>()
  const nonZero = devices.filter(d => d.x !== 0 || d.y !== 0)
  if (nonZero.length > devices.length * 0.3) {
    for (const d of devices) pos.set(d.id, { x: d.x, y: d.y })
    return pos
  }
  const TIER: Record<string, number> = {
    router: 0, firewall: 0,
    switch: 1, load_balancer: 1,
    oob_switch: 2, server: 2,
    ups: 3, pdu: 3, floor_pdu: 3, rpp: 3, generator: 3, sensor: 3,
  }
  const tiers: GraphDevice[][] = [[], [], [], []]
  for (const d of devices) tiers[TIER[d.device_type] ?? 2].push(d)
  const H_GAP = 130, V_GAP = 110
  let y = 40
  for (const tier of tiers) {
    if (tier.length === 0) continue
    const startX = -(tier.length * H_GAP) / 2 + H_GAP / 2
    tier.forEach((d, i) => pos.set(d.id, { x: startX + i * H_GAP, y }))
    y += V_GAP
  }
  return pos
}

function devicesToNodes(
  devices: GraphDevice[],
  positions: Map<string, { x: number; y: number }>,
  activeLayer: string,
  bacnetInstByIp: Record<string, number>,
): Node[] {
  return devices.map(d => {
    const p = positions.get(d.id) || { x: d.x, y: d.y }
    const bacnetInstance = bacnetInstByIp[d.ip_address] ?? bacnetInstByIp[d.mgmt_ip || '']
    return {
      id: d.id,
      type: 'device',
      position: { x: p.x, y: p.y },
      data: {
        name: d.name,
        device_type: d.device_type,
        vendor: d.vendor,
        ip_address: d.ip_address,
        mgmt_ip: d.mgmt_ip || '',
        os_name: d.os_name || '',
        os_version: d.os_version || '',
        snmp_port: d.snmp_port ?? 161,
        gnmi_port: d.gnmi_port ?? 57400,
        bacnet_instance: bacnetInstance,
        activeLayer,
        cpu_usage: d.cpu_usage,
        memory_used: d.memory_used,
        power_state: d.power_state || 'On',
      },
    }
  })
}

function linksToEdges(links: GraphLink[], nameById: Record<string, string>): Edge[] {
  return links.map(l => {
    const isCool = l.layer === 'cooling'
    const hot = isCool && isHotFlow(nameById[l.src_id] || l.src_id, nameById[l.dst_id] || l.dst_id)
    return {
      id: l.id,
      source: l.src_id,
      target: l.dst_id,
      type: 'link',
      data: {
        layer: l.layer,
        broken: l.broken,
        src_iface: l.src_iface,
        dst_iface: l.dst_iface,
        outlet: l.outlet,
        psu: l.psu,
        srcName: nameById[l.src_id] || l.src_id,
        dstName: nameById[l.dst_id] || l.dst_id,
        flow: isCool ? (hot ? 'hot' : 'cold') : undefined,
      },
    }
  })
}

function ToolBtn({ title, onClick, active, children, disabled }: {
  title: string
  onClick?: () => void
  active?: boolean
  children: React.ReactNode
  disabled?: boolean
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`canvas-tool ${active ? 'active' : ''}`}
    >
      {children}
    </button>
  )
}

function ZoomIndicator() {
  const { zoom } = useViewport()
  return (
    <div style={{
      fontSize: 9, color: 'var(--text-muted)',
      fontFamily: 'Consolas, monospace',
      padding: '2px 4px', textAlign: 'center',
      fontVariantNumeric: 'tabular-nums',
      lineHeight: 1.2,
    }}>
      {Math.round(zoom * 100)}%
    </div>
  )
}

function Canvas() {
  const {
    graphDevices, graphLinks, activeLayer, setLayer, fetchGraph,
    fitViewTrigger, focusNodeId, focusNodeNonce, layoutAlgo, setLayoutAlgo, bacnet,
  } = useStore()

  // Map BACnet device IP → instance number (e.g. 40001) for tooltip display
  const bacnetInstByIp = useMemo(() => {
    const m: Record<string, number> = {}
    for (const d of bacnet?.devices ?? []) m[d.ip] = d.instance
    return m
  }, [bacnet?.devices])

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const { fitView, setNodes: rfSetNodes, zoomIn, zoomOut } = useReactFlow()
  const initialFit = useRef(false)
  const repositionPending = useRef(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [ctxMenu,      setCtxMenu]      = useState<{ nodeId: string; deviceType: string; deviceName: string; modelName: string; x: number; y: number } | null>(null)
  const [editDeviceId, setEditDeviceId] = useState<string | null>(null)
  const [infoDeviceId, setInfoDeviceId] = useState<string | null>(null)

  const searchRef = useRef<HTMLInputElement>(null)

  const positions = useMemo(() => tierLayout(graphDevices), [graphDevices])

  // Link counts per layer
  const layerCounts = useMemo(() => {
    const c: Record<string, number> = { all: graphLinks.length, production: 0, management: 0, power: 0, cooling: 0 }
    for (const l of graphLinks) if (l.layer in c) c[l.layer]++
    return c
  }, [graphLinks])

  // Sync graph data → React Flow nodes/edges.
  // Preserve user-dragged positions across data refreshes (e.g. periodic SSE
  // topology sync) so a node doesn't snap back. Reset Layout sets
  // repositionPending to force a rebuild from server positions.
  useEffect(() => {
    const nameById: Record<string, string> = {}
    for (const dev of graphDevices) nameById[dev.id] = dev.name
    const built = devicesToNodes(graphDevices, positions, activeLayer, bacnetInstByIp)
    if (repositionPending.current) {
      repositionPending.current = false
      setNodes(built)
    } else {
      setNodes(prev => {
        // Carry over BOTH the dragged position and the selection. Rebuilding from
        // `built` alone would drop `selected` on every SSE topology sync, so a
        // multi-node selection would silently evaporate mid-drag-setup.
        const prevState = new Map(prev.map(n => [n.id, { position: n.position, selected: n.selected }]))
        return built.map(n => {
          const p = prevState.get(n.id)
          return p ? { ...n, position: p.position, selected: p.selected } : n
        })
      })
    }
    setEdges(linksToEdges(graphLinks, nameById))
    if (!initialFit.current && graphDevices.length > 0) {
      initialFit.current = true
      setTimeout(() => fitView({ padding: 0.1, duration: 400 }), 100)
    }
  }, [graphDevices, graphLinks, activeLayer, bacnetInstByIp])

  useEffect(() => {
    if (fitViewTrigger > 0) fitView({ padding: 0.1, duration: 400 })
  }, [fitViewTrigger])

  // Focus-single-node: pan/zoom to one node (e.g. Locate on Graph from the device
  // list). Nonce-gated so re-locating the same node still fires.
  useEffect(() => {
    if (focusNodeNonce > 0 && focusNodeId) {
      fitView({ nodes: [{ id: focusNodeId }], padding: 0.4, duration: 400 })
    }
  }, [focusNodeNonce])

  useEffect(() => {
    if (!layoutAlgo) return
    setLayoutAlgo(null)
    if (layoutAlgo === 'default') {
      initialFit.current = false
      repositionPending.current = true
      fetchGraph()
      return
    }
    api.layoutGraph(layoutAlgo)
      .then(({ positions: serverPos }) => {
        rfSetNodes(nds => nds.map(n => {
          const p = serverPos[n.id]
          return p ? { ...n, position: { x: p.x * 1, y: p.y * 1 } } : n
        }))
        setTimeout(() => fitView({ padding: 0.1, duration: 400 }), 50)
      })
      .catch(console.error)
  }, [layoutAlgo])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.ctrlKey && e.shiftKey && e.key === 'F') { e.preventDefault(); fitView({ padding: 0.1, duration: 400 }) }
      if (e.key === '/' && !e.ctrlKey) { e.preventDefault(); setSearchOpen(true) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Search: dim non-matching nodes
  useEffect(() => {
    if (!searchText.trim()) {
      rfSetNodes(nds => nds.map(n => ({ ...n, style: { ...n.style, opacity: 1 } })))
      return
    }
    const q = searchText.toLowerCase()
    rfSetNodes(nds => nds.map(n => ({
      ...n,
      style: { ...n.style, opacity: String(n.data.name).toLowerCase().includes(q) ? 1 : 0.15 },
    })))
  }, [searchText])

  useEffect(() => {
    if (searchOpen) setTimeout(() => searchRef.current?.focus(), 50)
    else setSearchText('')
  }, [searchOpen])

  // React Flow hands us every node that moved in this drag — one node normally,
  // the whole selection when several were shift-clicked. Persist them in a single
  // request so a 40-node move is one round trip, not forty.
  const onNodeDragStop = useCallback((_: React.MouseEvent, _node: Node, dragged: Node[]) => {
    if (!dragged?.length) return
    const positions: Record<string, { x: number; y: number }> = {}
    for (const n of dragged) {
      positions[n.id] = { x: Math.round(n.position.x), y: Math.round(n.position.y) }
    }
    api.savePositions(positions).catch(console.error)
  }, [])

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault()
    setCtxMenu({
      nodeId: node.id,
      deviceType: String(node.data.device_type || ''),
      deviceName: String(node.data.name || ''),
      modelName: String(node.data.model_name || ''),
      x: e.clientX,
      y: e.clientY,
    })
  }, [])

  const onEdgeDoubleClick = useCallback(async (_: React.MouseEvent, edge: Edge) => {
    const data = edge.data as { layer: string; broken: boolean }
    // Only production links are breakable — ignore mgmt/power/cooling edges.
    if ((data?.layer ?? 'production') !== 'production') return
    try {
      if (data?.broken) await api.restoreLink(edge.source, edge.target, 'production')
      else              await api.breakLink(edge.source, edge.target, 'production')
      fetchGraph()
    } catch (e) { console.error(e) }
  }, [fetchGraph])

  // Pause the cooling flow-dash animation while the user pans/zooms — the
  // per-frame stroke-dashoffset repaint otherwise fights the viewport transform
  // and causes jank. Toggle a class directly (no React re-render).
  const wrapperRef = useRef<HTMLDivElement>(null)
  const moveIdle = useRef<number | undefined>(undefined)
  const onMoveStart = useCallback(() => {
    if (moveIdle.current) { clearTimeout(moveIdle.current); moveIdle.current = undefined }
    wrapperRef.current?.classList.add('rf-interacting')
  }, [])
  const onMoveEnd = useCallback(() => {
    // Debounce: wheel-zoom fires many start/end pairs in quick succession.
    moveIdle.current = window.setTimeout(
      () => wrapperRef.current?.classList.remove('rf-interacting'), 150)
  }, [])

  return (
    <div ref={wrapperRef} style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>

      {/* ── Top-left toolbar ─────────────────────────────────── */}
      <div className="canvas-toolbar">
        <ToolBtn title="Search devices  ( / )" onClick={() => setSearchOpen(o => !o)} active={searchOpen}><I.search /></ToolBtn>
        <div className="canvas-tool-divider" />
        <ToolBtn title="Zoom In"  onClick={() => zoomIn({ duration: 200 })}><I.zoomIn /></ToolBtn>
        <ZoomIndicator />
        <ToolBtn title="Zoom Out" onClick={() => zoomOut({ duration: 200 })}><I.zoomOut /></ToolBtn>
        <ToolBtn title="Fit to View  (Ctrl+Shift+F)" onClick={() => fitView({ padding: 0.1, duration: 400 })}><I.fit /></ToolBtn>
        <div className="canvas-tool-divider" />
        <ToolBtn title="Reset Layout" onClick={() => {
          // Restore the canonical layout server-side FIRST, then refetch — the
          // graph comes back with canonical coords, and repositionPending forces
          // a rebuild from them so any hand-drags are dropped.
          api.resetPositions()
            .then(() => { initialFit.current = false; repositionPending.current = true; return fetchGraph() })
            .catch(console.error)
        }}><I.reset /></ToolBtn>

      </div>

      {/* Search input panel */}
      {searchOpen && (
        <div style={{
          position: 'absolute', top: 8, left: 46, zIndex: 10,
          background: 'var(--bg-panel)', border: '1px solid var(--border)',
          borderRadius: 6, padding: '4px 8px',
          display: 'flex', alignItems: 'center', gap: 6,
          boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
        }}>
          <span style={{ color: 'var(--text-muted)', display: 'flex' }}><I.search /></span>
          <input
            ref={searchRef}
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Escape') setSearchOpen(false) }}
            placeholder="Search devices…"
            style={{
              fontSize: 11, background: 'transparent', border: 'none',
              outline: 'none', color: 'var(--text)', width: 180,
            }}
          />
          {searchText && (
            <button onClick={() => setSearchText('')}
              style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0, display: 'flex' }}>
              <I.close />
            </button>
          )}
        </div>
      )}

      {/* ── Layer filter (centered) ──────────────────────────── */}
      <div style={{
        position: 'absolute', top: 8, left: '50%', transform: 'translateX(-50%)',
        zIndex: 10, display: 'flex', gap: 3,
        background: 'var(--bg-panel)', border: '1px solid var(--border)',
        borderRadius: 6, padding: 3,
        boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
      }}>
        <span style={{
          fontSize: 9, color: 'var(--text-muted)',
          padding: '0 8px', alignSelf: 'center',
          textTransform: 'uppercase', letterSpacing: '0.5px',
        }}>Layer</span>
        {LAYER_BTN.map(b => {
          const isActive = activeLayer === b.id
          return (
            <button key={b.id} onClick={() => setLayer(b.id)} style={{
              padding: '3px 10px', fontSize: 10,
              background: isActive ? b.color : 'transparent',
              border: `1px solid ${isActive ? b.color : 'var(--border)'}`,
              color: isActive ? 'var(--accent-on)' : 'var(--text-muted)',
              borderRadius: 4, cursor: 'pointer',
              transition: 'background 0.15s, color 0.15s',
            }}>
              {b.label}
            </button>
          )
        })}
      </div>

      {/* ── Empty state ──────────────────────────────────────── */}
      {graphDevices.length === 0 && (
        <div style={{
          position: 'absolute', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          textAlign: 'center', color: 'var(--text-dim)',
          fontSize: 12, lineHeight: 1.7,
          padding: 24,
          border: '1px dashed var(--border)',
          borderRadius: 8,
          background: 'rgba(19, 20, 23, 0.6)',
          backdropFilter: 'blur(4px)',
          zIndex: 5,
        }}>
          <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.5 }}>⌬</div>
          <div style={{ color: 'var(--text)', fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
            No topology loaded
          </div>
          <div>
            Open <span style={{ color: 'var(--accent)' }}>File → Open Topology</span>
            {' '}or add devices from <span style={{ color: 'var(--accent)' }}>Devices menu</span>
          </div>
        </div>
      )}

      {/* ── Bottom hint ──────────────────────────────────────── */}
      {graphLinks.length > 0 && (
        <div style={{
          position: 'absolute', bottom: 8, left: '50%', transform: 'translateX(-50%)',
          zIndex: 10, fontSize: 9, color: 'var(--text-muted)',
          background: 'var(--bg-panel)', padding: '3px 10px', borderRadius: 4,
          border: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          <I.info />
          Double-click a production link to break / restore it
          <span style={{ opacity: 0.5 }}>·</span>
          <span><b>Shift</b>+click or <b>Shift</b>+drag to select several nodes, then drag to move them together</span>
        </div>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={onNodeDragStop}
        onNodeContextMenu={onNodeContextMenu}
        onEdgeDoubleClick={onEdgeDoubleClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        minZoom={0.05}
        maxZoom={2}
        onlyRenderVisibleElements
        onMoveStart={onMoveStart}
        onMoveEnd={onMoveEnd}
        defaultEdgeOptions={{ type: 'link' }}
        style={{
          background: 'radial-gradient(ellipse at center, #191b1f 0%, var(--bg-base) 70%)',
        }}
        proOptions={{ hideAttribution: true }}
        // Shift+click adds a node to the selection (React Flow defaults this to
        // Ctrl/Cmd, which collides with browser and OS shortcuts). Shift+drag on
        // empty canvas still draws a selection box — same key, different target,
        // so both gestures build the same selection. Dragging any selected node
        // then moves the whole set.
        multiSelectionKeyCode="Shift"
        selectionKeyCode="Shift"
      >
        <Background variant={BackgroundVariant.Dots} color="#3d434c40" gap={20} size={1} />
      </ReactFlow>

      {ctxMenu && (
        <NodeContextMenu
          nodeId={ctxMenu.nodeId}
          deviceType={ctxMenu.deviceType}
          deviceName={ctxMenu.deviceName}
          modelName={ctxMenu.modelName}
          x={ctxMenu.x}
          y={ctxMenu.y}
          onClose={() => setCtxMenu(null)}
          onLocate={() => fitView({ nodes: [{ id: ctxMenu.nodeId }], padding: 0.4, duration: 400 })}
          onEditDevice={() => setEditDeviceId(ctxMenu.nodeId)}
          onShowInfo={() => setInfoDeviceId(ctxMenu.nodeId)}
        />
      )}
      {editDeviceId && (
        <EditDeviceDialog deviceId={editDeviceId} onClose={() => setEditDeviceId(null)} />
      )}
      {infoDeviceId && (
        <DeviceInfoModal deviceId={infoDeviceId} onClose={() => setInfoDeviceId(null)} />
      )}
    </div>
  )
}

export default function TopologyCanvas() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  )
}
