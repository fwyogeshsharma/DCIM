import { useState, useMemo, useEffect, useRef } from 'react'
import { useStore } from '../store/useStore'
import type { DeviceInfo } from '../api/types'
import NodeContextMenu, { EditDeviceDialog, DeviceInfoModal } from './TopologyCanvas/NodeContextMenu'

const MIN_WIDTH = 200
const MAX_WIDTH = 900
const DEFAULT_WIDTH = 260
const STORAGE_KEY = 'dcim:device-list-width'

const TYPE_COLOR: Record<string, string> = {
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

const TYPE_LABEL: Record<string, string> = {
  router:        'Router',
  switch:        'Switch',
  server:        'Server',
  firewall:      'Firewall',
  load_balancer: 'LB',
  oob_switch:    'OOB Sw',
  pdu:           'PDU',
  floor_pdu:     'Floor PDU',
  rpp:           'RPP',
  generator:     'Generator',
  ups:           'UPS',
  sensor:        'Sensor',
}

type SortKey = 'name' | 'device_type' | 'vendor' | 'mgmt_ip' | 'ip_address' | 'interface_count' | 'snmp_port' | 'sys_location'
type SortDir = 'asc' | 'desc'

const COLUMNS: { key: SortKey; label: string; width?: number; align?: 'right' }[] = [
  { key: 'name',            label: 'Name',       width: 110 },
  { key: 'device_type',     label: 'Type',       width: 70  },
  { key: 'vendor',          label: 'Vendor',     width: 60  },
  { key: 'mgmt_ip',         label: 'Mgmt IP',    width: 95  },
  { key: 'ip_address',      label: 'Prod IP',    width: 95  },
  { key: 'interface_count', label: 'Iface',      width: 44, align: 'right' },
  { key: 'snmp_port',       label: 'SNMP',       width: 50, align: 'right' },
  { key: 'sys_location',    label: 'Location',   width: 180 },
]

function compareDevices(a: DeviceInfo, b: DeviceInfo, key: SortKey, dir: SortDir): number {
  const av = a[key], bv = b[key]
  let cmp: number
  if (typeof av === 'number' && typeof bv === 'number') {
    cmp = av - bv
  } else {
    cmp = String(av ?? '').localeCompare(String(bv ?? ''))
  }
  return dir === 'asc' ? cmp : -cmp
}

const IconSortAsc = () => (
  <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><polygon points="12 5 5 16 19 16" /></svg>
)
const IconSortDesc = () => (
  <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><polygon points="12 19 5 8 19 8" /></svg>
)
const IconSearch = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="7" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
)

export default function DeviceList() {
  const { devices, selectedDeviceId, setSelectedDevice, focusNode } = useStore()
  // Right-click context menu — reuses the topology-canvas NodeContextMenu so the
  // device list offers the same actions (Edit / Remove / Show Info / Simulate Fault)
  // as the graph, instead of falling through to the native browser menu.
  const [ctxMenu,     setCtxMenu]     = useState<{ nodeId: string; deviceType: string; deviceName: string; modelName: string; x: number; y: number } | null>(null)
  const [editDeviceId, setEditDeviceId] = useState<string | null>(null)
  const [infoDeviceId, setInfoDeviceId] = useState<string | null>(null)
  const [q,        setQ]        = useState('')
  const [typeFilt, setTypeFilt] = useState('')
  const [sortKey,  setSortKey]  = useState<SortKey>('name')
  const [sortDir,  setSortDir]  = useState<SortDir>('asc')
  const [width,    setWidth]    = useState<number>(() => {
    const saved = parseInt(localStorage.getItem(STORAGE_KEY) || '', 10)
    return Number.isFinite(saved) && saved >= MIN_WIDTH && saved <= MAX_WIDTH
      ? saved
      : DEFAULT_WIDTH
  })
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragRef.current) return
      const dx = e.clientX - dragRef.current.startX
      const next = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, dragRef.current.startW + dx))
      setWidth(next)
    }
    function onUp() {
      if (dragRef.current) {
        dragRef.current = null
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        localStorage.setItem(STORAGE_KEY, String(width))
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [width])

  function startDrag(e: React.MouseEvent) {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startW: width }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  // Available types (only those present in current device list)
  const availableTypes = useMemo(() => {
    const set = new Set(devices.map(d => d.device_type))
    return Array.from(set).sort()
  }, [devices])

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase()
    let list = devices
    if (typeFilt) list = list.filter(d => d.device_type === typeFilt)
    if (query) {
      list = list.filter(d =>
        d.name.toLowerCase().includes(query) ||
        d.ip_address.includes(query) ||
        (d.mgmt_ip || '').includes(query) ||
        d.device_type.includes(query) ||
        (d.vendor || '').toLowerCase().includes(query) ||
        (d.sys_location || '').toLowerCase().includes(query) ||
        String(d.snmp_port).includes(query)
      )
    }
    return [...list].sort((a, b) => compareDevices(a, b, sortKey, sortDir))
  }, [devices, q, typeFilt, sortKey, sortDir])

  function clickHeader(k: SortKey) {
    if (sortKey === k) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(k); setSortDir('asc') }
  }

  return (
    <div style={{
      width,
      flexShrink: 0,
      background: 'var(--bg-panel)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      position: 'relative',
    }}>
      <div className="panel-header">
        <span className="title">Device List</span>
        <span className="badge stopped">
          {filtered.length === devices.length
            ? devices.length
            : `${filtered.length} / ${devices.length}`}
        </span>
      </div>

      {/* ── Filters row ───────────────────────────────────────── */}
      <div style={{
        padding: '6px 8px', borderBottom: '1px solid var(--border)',
        display: 'flex', gap: 6, alignItems: 'center',
      }}>
        <div style={{
          flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 5,
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 4, padding: '0 8px',
        }}>
          <span style={{ color: 'var(--text-muted)', flexShrink: 0, display: 'flex' }}>
            <IconSearch />
          </span>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search name, IP, vendor, location…"
            style={{
              flex: 1, minWidth: 0, background: 'transparent', border: 'none',
              outline: 'none', color: 'var(--text)', fontSize: 11,
              padding: '5px 0',
            }}
          />
          {q && (
            <button
              onClick={() => setQ('')}
              style={{
                background: 'transparent', border: 'none', color: 'var(--text-muted)',
                cursor: 'pointer', padding: 0, fontSize: 12, lineHeight: 1,
              }}
              title="Clear search"
            >×</button>
          )}
        </div>
        <select
          value={typeFilt}
          onChange={e => setTypeFilt(e.target.value)}
          style={{ width: 96, fontSize: 10 }}
          title="Filter by device type"
        >
          <option value="">All Types</option>
          {availableTypes.map(t => (
            <option key={t} value={t}>{TYPE_LABEL[t] || t}</option>
          ))}
        </select>
      </div>

      {/* ── Table ─────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <table className="device-table">
          <thead>
            <tr>
              {COLUMNS.map(c => {
                const active = sortKey === c.key
                return (
                  <th
                    key={c.key}
                    onClick={() => clickHeader(c.key)}
                    style={{
                      width: c.width, textAlign: c.align ?? 'left',
                      cursor: 'pointer', userSelect: 'none',
                    }}
                    title={`Sort by ${c.label}`}
                  >
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', gap: 3,
                      color: active ? 'var(--text)' : undefined,
                    }}>
                      {c.label}
                      {active && (sortDir === 'asc' ? <IconSortAsc /> : <IconSortDesc />)}
                    </span>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {filtered.map(d => {
              const col = TYPE_COLOR[d.device_type] || '#4d4f52'
              const selected = selectedDeviceId === d.id
              return (
                <tr
                  key={d.id}
                  onClick={() => setSelectedDevice(selected ? null : d.id)}
                  onContextMenu={e => {
                    e.preventDefault()
                    setCtxMenu({
                      nodeId: d.id,
                      deviceType: d.device_type,
                      deviceName: d.name,
                      modelName: d.model_name || '',
                      x: e.clientX,
                      y: e.clientY,
                    })
                  }}
                  className={selected ? 'selected' : undefined}
                >
                  <td title={d.name}>
                    <span className="type-dot" style={{ background: col, marginRight: 5 }} />
                    {d.name}
                  </td>
                  <td>
                    <span style={{
                      display: 'inline-block', padding: '1px 6px',
                      borderRadius: 3, fontSize: 9, fontWeight: 600,
                      background: `${col}33`, color: col,
                      textTransform: 'uppercase', letterSpacing: '0.3px',
                    }}>
                      {TYPE_LABEL[d.device_type] || d.device_type}
                    </span>
                  </td>
                  <td className="muted" title={d.vendor}>{d.vendor || '—'}</td>
                  <td className="mono" title={d.mgmt_ip || ''}>{d.mgmt_ip || '—'}</td>
                  <td className="mono" title={d.ip_address}>{d.ip_address}</td>
                  <td className="mono right">{d.interface_count}</td>
                  <td className="mono right">{d.snmp_port}</td>
                  <td className="muted" title={d.sys_location || ''}>{d.sys_location || '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div style={{ padding: '24px 10px', textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
            {devices.length === 0
              ? 'No devices — open a topology'
              : q || typeFilt
                ? 'No devices match filter'
                : 'No devices'}
          </div>
        )}
      </div>

      {/* ── Right-click context menu + its dialogs (portaled to body) ── */}
      {ctxMenu && (
        <NodeContextMenu
          nodeId={ctxMenu.nodeId}
          deviceType={ctxMenu.deviceType}
          deviceName={ctxMenu.deviceName}
          modelName={ctxMenu.modelName}
          x={ctxMenu.x}
          y={ctxMenu.y}
          onClose={() => setCtxMenu(null)}
          onLocate={() => { setSelectedDevice(ctxMenu.nodeId); focusNode(ctxMenu.nodeId) }}
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

      {/* ── Resize handle ─────────────────────────────────────── */}
      <div
        onMouseDown={startDrag}
        onDoubleClick={() => { setWidth(DEFAULT_WIDTH); localStorage.setItem(STORAGE_KEY, String(DEFAULT_WIDTH)) }}
        title="Drag to resize · double-click to reset"
        style={{
          position: 'absolute',
          top: 0, right: -3, bottom: 0,
          width: 6, cursor: 'col-resize',
          zIndex: 10,
        }}
        className="resize-handle"
      />
    </div>
  )
}
