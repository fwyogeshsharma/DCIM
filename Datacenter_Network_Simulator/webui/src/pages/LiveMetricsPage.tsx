import { useState, useMemo, useEffect } from 'react'
import { useStore } from '../store/useStore'
import type { DeviceInfo, IfaceStats, EV2DeviceSnapshot, EV2CircuitMetrics, PlantDeviceSnapshot } from '../api/types'

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtBytes(b?: number): string {
  if (b == null || b === 0) return '—'
  if (b >= 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b >= 1e9)  return (b / 1e9).toFixed(1) + ' GB'
  if (b >= 1e6)  return (b / 1e6).toFixed(1) + ' MB'
  return (b / 1e3).toFixed(0) + ' KB'
}

function fmtSpeed(bps: number): string {
  if (bps >= 1e12) return (bps / 1e12).toFixed(0) + ' Tbps'
  if (bps >= 1e9)  return (bps / 1e9).toFixed(0) + ' Gbps'
  if (bps >= 1e6)  return (bps / 1e6).toFixed(0) + ' Mbps'
  return (bps / 1e3).toFixed(0) + ' Kbps'
}

function fmtNum(n?: number): string {
  if (n == null) return '—'
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return String(n)
}

function fmtUptime(cs: number): string {
  if (!cs) return '—'
  const s = Math.floor(cs / 100)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function pct(used: number, total: number): number {
  if (!total) return 0
  return Math.min(100, Math.round(used / total * 100))
}

function metricColor(v: number, warn: number, crit: number): string {
  if (v >= crit) return '#f85149'
  if (v >= warn) return '#d29922'
  return '#3fb950'
}

const TYPE_BADGE: Record<string, string> = {
  switch: '#1e6ec8', router: '#8b5cf6', server: '#059669',
  firewall: '#dc2626', load_balancer: '#d97706', ups: '#0891b2',
  pdu: '#065f46', floor_pdu: '#064e3b', rpp: '#4a044e', generator: '#713f12', oob_switch: '#4b5563', sensor: '#92400e',
}

const ROW_STYLE = (i: number): React.CSSProperties => ({
  background: i % 2 ? 'rgba(255,255,255,0.02)' : 'transparent',
  borderBottom: '1px solid rgba(255,255,255,0.04)',
})

// ── shared cell components ────────────────────────────────────────────────────

function MiniBar({ p, color }: { p: number; color: string }) {
  return (
    <div style={{ width: '100%', height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2, marginTop: 3 }}>
      <div style={{ width: `${p}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.4s' }} />
    </div>
  )
}

function StatePill({ val, okStates }: { val?: string; okStates: string[] }) {
  if (!val) return <span style={{ color: 'var(--text-dim)' }}>—</span>
  const ok = okStates.includes(val)
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 3, fontSize: 9,
      fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.3px',
      background: ok ? '#3fb95022' : '#f8514922',
      border: `1px solid ${ok ? '#3fb95044' : '#f8514944'}`,
      color: ok ? '#3fb950' : '#f85149',
    }}>{val}</span>
  )
}

function NumCell({ val, unit, warn, crit, decimals = 1, invert = false }: {
  val?: number; unit?: string; warn?: number; crit?: number; decimals?: number; invert?: boolean
}) {
  if (val == null) return <span style={{ color: 'var(--text-dim)' }}>—</span>
  const color = warn == null || crit == null
    ? 'var(--text)'
    : invert
      ? metricColor(-val, -warn, -crit)   // lower value is worse
      : metricColor(val, warn, crit)
  return (
    <span style={{ color, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
      {val.toFixed(decimals)}{unit && <span style={{ color: 'var(--text-dim)', fontWeight: 400 }}>{unit}</span>}
    </span>
  )
}

// OS-level metric with no reading (e.g. server chassis powered off)
function DashCell() {
  return <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-dim)' }}>—</td>
}

function PctCell({ used, total, warn, crit }: { used: number; total: number; warn: number; crit: number }) {
  if (!total) return <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-dim)' }}>—</td>
  const p = pct(used, total)
  const color = metricColor(p, warn, crit)
  return (
    <td style={{ padding: '6px 10px', textAlign: 'right', minWidth: 90 }}>
      <span style={{ color, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{p}%</span>
      <span style={{ color: 'var(--text-dim)', marginLeft: 4, fontSize: 9 }}>{fmtBytes(used)}</span>
      <MiniBar p={p} color={color} />
    </td>
  )
}

function NameCell({ d }: { d: DeviceInfo }) {
  return (
    <td style={{ padding: '6px 10px', maxWidth: 160 }}>
      <div style={{ fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={d.name}>{d.name}</div>
      <div style={{ fontSize: 9, color: 'var(--text-dim)', marginTop: 1 }}>{d.vendor}</div>
    </td>
  )
}

function TypeBadge({ dt }: { dt: string }) {
  const c = TYPE_BADGE[dt] ?? '#4b5563'
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 3,
      background: c + '33', border: `1px solid ${c}55`,
      color: c, fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px',
    }}>{dt.replace(/_/g, ' ')}</span>
  )
}

function IpCell({ d }: { d: DeviceInfo }) {
  return (
    <td style={{ padding: '6px 10px', fontFamily: 'monospace', color: 'var(--text-muted)', whiteSpace: 'nowrap', fontSize: 10 }}>
      {d.mgmt_ip || d.ip_address}
    </td>
  )
}

function IpVal({ ip }: { ip?: string }) {
  return (
    <td style={{ padding: '6px 10px', fontFamily: 'monospace', color: 'var(--text-muted)', whiteSpace: 'nowrap', fontSize: 10 }}>
      {ip || <span style={{ color: 'var(--text-dim)' }}>—</span>}
    </td>
  )
}

// ── sort hook ─────────────────────────────────────────────────────────────────

type SortDir = 'asc' | 'desc'
function useSortState(init: string) {
  const [col, setCol] = useState(init)
  const [dir, setDir] = useState<SortDir>('asc')
  function toggle(c: string) {
    if (c === col) setDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setCol(c); setDir('asc') }
  }
  return { col, dir, toggle }
}

function SortTH({ label, id, sort, align = 'left', minW, title }: {
  label: string; id: string; sort: ReturnType<typeof useSortState>; align?: string; minW?: number; title?: string
}) {
  const active = sort.col === id
  return (
    <th onClick={() => sort.toggle(id)} style={{
      padding: '7px 8px', fontSize: 9, fontWeight: 600, cursor: 'pointer',
      userSelect: 'none', whiteSpace: 'nowrap',
      textAlign: align as React.CSSProperties['textAlign'],
      color: active ? 'var(--accent)' : 'var(--text-muted)',
      textTransform: 'uppercase', letterSpacing: '0.6px',
      borderBottom: '1px solid var(--border)', minWidth: minW,
      background: 'var(--bg-panel)',
    }}>
      {title
        ? <span title={title} style={{ cursor: 'help' }}>
            {label}<span style={{ marginLeft: 3, fontSize: 8, color: '#58a6ff', fontStyle: 'normal', textTransform: 'none', letterSpacing: 0 }}>ⓘ</span>
          </span>
        : label}
      <span style={{ marginLeft: 3, opacity: active ? 1 : 0.3, fontSize: 8 }}>
        {active ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}
      </span>
    </th>
  )
}

// ── per-interface sub-table ───────────────────────────────────────────────────

function IfaceSubTable({ ifaces, colSpan }: { ifaces: IfaceStats[]; colSpan: number }) {
  return (
    <tr>
      <td colSpan={colSpan} style={{ padding: 0, background: '#0d1117' }}>
        <div style={{ borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
          {/* sub-header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '28px 120px 60px 80px 100px 100px 90px 90px 70px 70px 70px 70px',
            padding: '4px 0',
            background: '#161b22',
            borderBottom: '1px solid var(--border)',
          }}>
            {['', 'Interface', 'Status', 'Speed', 'In Octets', 'Out Octets', 'In Pkts', 'Out Pkts', 'In Err', 'Out Err', 'In Disc', 'Out Disc']
              .map((h, i) => (
                <div key={i} style={{
                  fontSize: 8, fontWeight: 700, color: 'var(--text-muted)',
                  textTransform: 'uppercase', letterSpacing: '0.5px',
                  padding: '0 8px', textAlign: i >= 4 ? 'right' : 'left',
                }}>{h}</div>
              ))}
          </div>
          {/* rows */}
          {ifaces.map((iface, idx) => {
            const up = iface.oper_status === 1
            return (
              <div key={iface.index} style={{
                display: 'grid',
                gridTemplateColumns: '28px 120px 60px 80px 100px 100px 90px 90px 70px 70px 70px 70px',
                padding: '3px 0',
                background: idx % 2 ? 'rgba(255,255,255,0.015)' : 'transparent',
                borderBottom: '1px solid rgba(255,255,255,0.03)',
                alignItems: 'center',
              }}>
                <div />
                <div style={{ padding: '0 8px', fontSize: 10, fontFamily: 'monospace', color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {iface.name}
                </div>
                <div style={{ padding: '0 8px' }}>
                  <span style={{
                    fontSize: 9, fontWeight: 600, padding: '1px 5px', borderRadius: 3,
                    background: up ? '#3fb95022' : '#f8514922',
                    border: `1px solid ${up ? '#3fb95044' : '#f8514944'}`,
                    color: up ? '#3fb950' : '#f85149',
                  }}>{up ? 'UP' : 'DOWN'}</span>
                </div>
                <div style={{ padding: '0 8px', fontSize: 9, color: 'var(--text-muted)' }}>{fmtSpeed(iface.speed)}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(iface.in_octets)}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(iface.out_octets)}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(iface.in_unicast_pkts)}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(iface.out_unicast_pkts)}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: iface.in_errors > 0 ? '#d29922' : 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>{iface.in_errors > 0 ? fmtNum(iface.in_errors) : '—'}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: iface.out_errors > 0 ? '#d29922' : 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>{iface.out_errors > 0 ? fmtNum(iface.out_errors) : '—'}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: iface.in_discards > 0 ? '#d29922' : 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>{iface.in_discards > 0 ? fmtNum(iface.in_discards) : '—'}</div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 10, color: iface.out_discards > 0 ? '#d29922' : 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>{iface.out_discards > 0 ? fmtNum(iface.out_discards) : '—'}</div>
              </div>
            )
          })}
        </div>
      </td>
    </tr>
  )
}

// ── expand toggle cell ────────────────────────────────────────────────────────

function ExpandCell({ expanded, hasIfaces }: { expanded: boolean; hasIfaces: boolean }) {
  return (
    <td style={{ padding: '6px 6px 6px 10px', width: 18, color: hasIfaces ? 'var(--text-muted)' : 'transparent', fontSize: 9 }}>
      {hasIfaces ? (expanded ? '▼' : '▶') : ''}
    </td>
  )
}

// ── All table ─────────────────────────────────────────────────────────────────

function AllTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    switch (sort.col) {
      case 'name':   return a.name.localeCompare(b.name) * dir
      case 'type':   return a.device_type.localeCompare(b.device_type) * dir
      case 'ip':     return (a.mgmt_ip || a.ip_address).localeCompare(b.mgmt_ip || b.ip_address) * dir
      case 'cpu':    return (a.cpu_usage - b.cpu_usage) * dir
      case 'mem':    return (pct(a.memory_used, a.memory_total) - pct(b.memory_used, b.memory_total)) * dir
      case 'disk':   return (pct(a.disk_used, a.disk_total) - pct(b.disk_used, b.disk_total)) * dir
      case 'ctemp':  return ((a.cpu_temp ?? 0) - (b.cpu_temp ?? 0)) * dir
      case 'uptime': return (a.uptime - b.uptime) * dir
      default: return 0
    }
  }), [rows, sort.col, sort.dir])

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <tr>
          <th style={{ width: 18, background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }} />
          <SortTH label="Name"     id="name"   sort={sort} minW={140} />
          <SortTH label="Type"     id="type"   sort={sort} />
          <SortTH label="IP"       id="ip"     sort={sort} />
          <SortTH label="CPU Usage" id="cpu"    sort={sort} align="right" minW={80} />
          <SortTH label="Memory"   id="mem"    sort={sort} align="right" minW={100} />
          <SortTH label="Disk"     id="disk"   sort={sort} align="right" minW={100} />
          <SortTH label="CPU Temp" id="ctemp"  sort={sort} align="right" minW={80} />
          <SortTH label="Uptime"   id="uptime" sort={sort} align="right" minW={80} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => {
          const off    = d.power_state === 'Off'
          const cpuPct = Math.round(d.cpu_usage)
          return (
            <tr key={d.id} style={ROW_STYLE(i)}>
              <td style={{ width: 18 }} />
              <NameCell d={d} />
              <td style={{ padding: '6px 10px' }}><TypeBadge dt={d.device_type} /></td>
              <IpCell d={d} />
              {off ? <DashCell /> : (
                <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                  <span style={{ color: metricColor(cpuPct, 50, 80), fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{cpuPct}%</span>
                  <MiniBar p={cpuPct} color={metricColor(cpuPct, 50, 80)} />
                </td>
              )}
              {off ? <DashCell /> : <PctCell used={d.memory_used} total={d.memory_total} warn={60} crit={85} />}
              {off ? <DashCell /> : <PctCell used={d.disk_used}   total={d.disk_total}   warn={70} crit={90} />}
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.cpu_temp} unit="°C" warn={75} crit={85} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{off ? '—' : fmtUptime(d.uptime)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Network table (expandable) ────────────────────────────────────────────────

const NET_COLS = 17

function NetworkTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    switch (sort.col) {
      case 'name':     return a.name.localeCompare(b.name) * dir
      case 'type':     return a.device_type.localeCompare(b.device_type) * dir
      case 'pip':      return (a.ip_address || '').localeCompare(b.ip_address || '') * dir
      case 'mip':      return (a.mgmt_ip || '').localeCompare(b.mgmt_ip || '') * dir
      case 'cpu':      return (a.cpu_usage - b.cpu_usage) * dir
      case 'mem':      return (pct(a.memory_used, a.memory_total) - pct(b.memory_used, b.memory_total)) * dir
      case 'disk':     return (pct(a.disk_used, a.disk_total) - pct(b.disk_used, b.disk_total)) * dir
      case 'ctemp':    return ((a.cpu_temp ?? 0) - (b.cpu_temp ?? 0)) * dir
      case 'itemp':    return ((a.inlet_temp ?? 0) - (b.inlet_temp ?? 0)) * dir
      case 'ifaces':   return ((a.interfaces_up ?? 0) - (b.interfaces_up ?? 0)) * dir
      case 'rx':       return ((a.total_rx_bytes ?? 0) - (b.total_rx_bytes ?? 0)) * dir
      case 'tx':       return ((a.total_tx_bytes ?? 0) - (b.total_tx_bytes ?? 0)) * dir
      case 'errors':   return ((a.total_errors ?? 0) - (b.total_errors ?? 0)) * dir
      case 'discards': return ((a.total_discards ?? 0) - (b.total_discards ?? 0)) * dir
      case 'flap':     return ((a.flapping_count ?? 0) - (b.flapping_count ?? 0)) * dir
      case 'bgp':      return ((a.bgp_sessions_up ?? 0) - (b.bgp_sessions_up ?? 0)) * dir
      case 'uptime':   return (a.uptime - b.uptime) * dir
      default: return 0
    }
  }), [rows, sort.col, sort.dir])

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <tr>
          <th style={{ width: 18, background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }} />
          <SortTH label="Name"       id="name"     sort={sort} minW={140} />
          <SortTH label="Type"       id="type"     sort={sort} />
          <SortTH label="Prod IP"    id="pip"      sort={sort} minW={95} />
          <SortTH label="Mgmt IP"    id="mip"      sort={sort} minW={95} />
          <SortTH label="CPU"        id="cpu"      sort={sort} align="right" minW={75} />
          <SortTH label="Memory"     id="mem"      sort={sort} align="right" minW={95} />
          <SortTH label="Disk"       id="disk"     sort={sort} align="right" minW={95} />
          <SortTH label="CPU Temp"   id="ctemp"    sort={sort} align="right" minW={75} />
          <SortTH label="Inlet Temp" id="itemp"    sort={sort} align="right" minW={75} />
          <SortTH label="Interfaces" id="ifaces"   sort={sort} align="right" minW={80} />
          <SortTH label="RX Total"   id="rx"       sort={sort} align="right" minW={85} />
          <SortTH label="TX Total"   id="tx"       sort={sort} align="right" minW={85} />
          <SortTH label="Error Counters"   id="errors"   sort={sort} align="right" minW={90} />
          <SortTH label="Discard Counters" id="discards" sort={sort} align="right" minW={100} />
          <SortTH label="BGP Sessions" id="bgp"     sort={sort} align="right" minW={90} />
          <SortTH label="Uptime"     id="uptime"   sort={sort} align="right" minW={75} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => {
          const cpuPct   = Math.round(d.cpu_usage)
          const bgpUp    = d.bgp_sessions_up ?? 0
          const bgpTot   = d.bgp_sessions_total ?? 0
          const errors   = d.total_errors ?? 0
          const discards = d.total_discards ?? 0
          const ifUp     = d.interfaces_up ?? 0
          const ifTot    = d.interfaces_total ?? 0
          const bgpColor = bgpTot === 0 ? 'var(--text-muted)' : bgpUp === bgpTot ? '#3fb950' : bgpUp === 0 ? '#f85149' : '#d29922'
          const ifColor  = ifTot === 0 ? 'var(--text-muted)' : ifUp === ifTot ? '#3fb950' : ifUp === 0 ? '#f85149' : '#d29922'
          const hasIfaces = (d.iface_stats?.length ?? 0) > 0
          const isExpanded = expanded.has(d.id)

          return <>
            <tr
              key={d.id}
              onClick={() => hasIfaces && toggle(d.id)}
              style={{ ...ROW_STYLE(i), cursor: hasIfaces ? 'pointer' : 'default' }}
            >
              <ExpandCell expanded={isExpanded} hasIfaces={hasIfaces} />
              <NameCell d={d} />
              <td style={{ padding: '6px 8px' }}><TypeBadge dt={d.device_type} /></td>
              <IpVal ip={d.ip_address} />
              <IpVal ip={d.mgmt_ip} />
              <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                <span style={{ color: metricColor(cpuPct, 50, 80), fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{cpuPct}%</span>
                <MiniBar p={cpuPct} color={metricColor(cpuPct, 50, 80)} />
              </td>
              <PctCell used={d.memory_used} total={d.memory_total} warn={60} crit={85} />
              <PctCell used={d.disk_used}   total={d.disk_total}   warn={70} crit={90} />
              <td style={{ padding: '6px 8px', textAlign: 'right' }}><NumCell val={d.cpu_temp}   unit="°C" warn={75} crit={85} /></td>
              <td style={{ padding: '6px 8px', textAlign: 'right' }}><NumCell val={d.inlet_temp} unit="°C" warn={35} crit={45} /></td>
              <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                {ifTot === 0
                  ? <span style={{ color: 'var(--text-dim)' }}>—</span>
                  : <span style={{ color: ifColor, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{ifUp}/{ifTot}</span>}
              </td>
              <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(d.total_rx_bytes)}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(d.total_tx_bytes)}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                <span style={{ color: errors > 0 ? '#d29922' : 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{errors > 0 ? fmtNum(errors) : '—'}</span>
              </td>
              <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                <span style={{ color: discards > 0 ? '#d29922' : 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{discards > 0 ? fmtNum(discards) : '—'}</span>
              </td>
              <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                {bgpTot === 0
                  ? <span style={{ color: 'var(--text-dim)' }}>—</span>
                  : <span style={{ color: bgpColor, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{bgpUp}/{bgpTot}</span>}
              </td>
              <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtUptime(d.uptime)}</td>
            </tr>
            {isExpanded && d.iface_stats && d.iface_stats.length > 0 && (
              <IfaceSubTable ifaces={d.iface_stats} colSpan={NET_COLS} />
            )}
          </>
        })}
      </tbody>
    </table>
  )
}

// ── Server table (expandable) ─────────────────────────────────────────────────

const SRV_COLS = 16

function ServerTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    switch (sort.col) {
      case 'name':   return a.name.localeCompare(b.name) * dir
      case 'pip':    return (a.ip_address || '').localeCompare(b.ip_address || '') * dir
      case 'mip':    return (a.mgmt_ip || '').localeCompare(b.mgmt_ip || '') * dir
      case 'cpu':    return (a.cpu_usage - b.cpu_usage) * dir
      case 'mem':    return (pct(a.memory_used, a.memory_total) - pct(b.memory_used, b.memory_total)) * dir
      case 'disk':   return (pct(a.disk_used, a.disk_total) - pct(b.disk_used, b.disk_total)) * dir
      case 'ctemp':  return ((a.cpu_temp ?? 0) - (b.cpu_temp ?? 0)) * dir
      case 'itemp':  return ((a.inlet_temp ?? 0) - (b.inlet_temp ?? 0)) * dir
      case 'otemp':  return ((a.outlet_temp ?? 0) - (b.outlet_temp ?? 0)) * dir
      case 'watts':  return ((a.power_watts ?? 0) - (b.power_watts ?? 0)) * dir
      case 'fan':    return ((a.fan_rpm ?? 0) - (b.fan_rpm ?? 0)) * dir
      case 'pstate': return (a.power_state ?? '').localeCompare(b.power_state ?? '') * dir
      case 'cooling': return ((a.liquid_cooled ? 1 : 0) - (b.liquid_cooled ? 1 : 0)) * dir
      case 'ifaces': return ((a.interfaces_up ?? 0) - (b.interfaces_up ?? 0)) * dir
      case 'rx':     return ((a.total_rx_bytes ?? 0) - (b.total_rx_bytes ?? 0)) * dir
      case 'tx':     return ((a.total_tx_bytes ?? 0) - (b.total_tx_bytes ?? 0)) * dir
      case 'uptime': return (a.uptime - b.uptime) * dir
      default: return 0
    }
  }), [rows, sort.col, sort.dir])

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <tr>
          <th style={{ width: 18, background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }} />
          <SortTH label="Name"       id="name"   sort={sort} minW={140} />
          <SortTH label="Prod IP"    id="pip"    sort={sort} minW={95} title="Production NIC — OS SNMP agent answers here" />
          <SortTH label="Mgmt IP"    id="mip"    sort={sort} minW={95} title="BMC address — Redfish + BMC SNMP, alive even when powered off" />
          <SortTH label="Power State" id="pstate" sort={sort} minW={80} title="Live BMC power state — populated while the Redfish simulator runs" />
          <SortTH label="Cooling"    id="cooling" sort={sort} minW={70} title="Direct-to-chip liquid cooling (CDU cold-plate loop) vs air-cooled" />
          <SortTH label="CPU"        id="cpu"    sort={sort} align="right" minW={80} />
          <SortTH label="Memory"     id="mem"    sort={sort} align="right" minW={100} />
          <SortTH label="Disk"       id="disk"   sort={sort} align="right" minW={100} />
          <SortTH label="CPU Temp"    id="ctemp"  sort={sort} align="right" minW={80} />
          <SortTH label="Inlet Temp" id="itemp"  sort={sort} align="right" minW={80} />
          <SortTH label="Exhaust Temp" id="otemp" sort={sort} align="right" minW={80} title="Server chassis exhaust — Redfish Thermal 'Exhaust'; inlet + ΔT from power/airflow" />
          <SortTH label="Power"      id="watts"  sort={sort} align="right" minW={80} title="Derived: nominal power_draw_w × (0.55 + 0.45 × CPU load) — matches Redfish PowerConsumedWatts" />
          <SortTH label="Fan"        id="fan"    sort={sort} align="right" minW={80} title="Chassis fan speed (RPM) — rises with CPU temperature; matches Redfish Thermal Fans" />
          <SortTH label="Interfaces" id="ifaces" sort={sort} align="right" minW={80} />
          <SortTH label="RX Total"   id="rx"     sort={sort} align="right" minW={90} />
          <SortTH label="TX Total"   id="tx"     sort={sort} align="right" minW={90} />
          <SortTH label="Uptime"     id="uptime" sort={sort} align="right" minW={80} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => {
          const off      = d.power_state === 'Off'
          const cpuPct   = Math.round(d.cpu_usage)
          const ifUp     = d.interfaces_up ?? 0
          const ifTot    = d.interfaces_total ?? 0
          const ifColor  = ifTot === 0 ? 'var(--text-muted)' : ifUp === ifTot ? '#3fb950' : ifUp === 0 ? '#f85149' : '#d29922'
          const hasIfaces = (d.iface_stats?.length ?? 0) > 0
          const isExpanded = expanded.has(d.id)

          return <>
            <tr
              key={d.id}
              onClick={() => hasIfaces && toggle(d.id)}
              style={{ ...ROW_STYLE(i), cursor: hasIfaces ? 'pointer' : 'default' }}
            >
              <ExpandCell expanded={isExpanded} hasIfaces={hasIfaces} />
              <NameCell d={d} />
              <IpVal ip={d.ip_address} />
              <IpVal ip={d.mgmt_ip} />
              <td style={{ padding: '6px 10px' }}><StatePill val={d.power_state} okStates={['On']} /></td>
              <td style={{ padding: '6px 10px' }}>
                {d.liquid_cooled
                  ? <span style={{
                      display: 'inline-block', padding: '1px 6px', borderRadius: 3, fontSize: 9,
                      fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.3px',
                      background: '#0891b222', border: '1px solid #0891b255', color: '#22d3ee',
                    }}>Liquid</span>
                  : <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>Air</span>}
              </td>
              {/* OS-level metrics have no reading while the chassis is off;
                  temps stay live — the BMC reads sensors on standby power. */}
              {off ? <DashCell /> : (
                <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                  <span style={{ color: metricColor(cpuPct, 50, 80), fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{cpuPct}%</span>
                  <MiniBar p={cpuPct} color={metricColor(cpuPct, 50, 80)} />
                </td>
              )}
              {off ? <DashCell /> : <PctCell used={d.memory_used} total={d.memory_total} warn={60} crit={85} />}
              {off ? <DashCell /> : <PctCell used={d.disk_used}   total={d.disk_total}   warn={70} crit={90} />}
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.cpu_temp}   unit="°C" warn={75} crit={85} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.inlet_temp} unit="°C" warn={35} crit={45} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.outlet_temp} unit="°C" warn={42} crit={52} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.power_watts} unit=" W" decimals={0} /></td>
              {off ? <DashCell /> : <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.fan_rpm} unit=" RPM" decimals={0} warn={12000} crit={14000} /></td>}
              <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                {ifTot === 0
                  ? <span style={{ color: 'var(--text-dim)' }}>—</span>
                  : <span style={{ color: ifColor, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{ifUp}/{ifTot}</span>}
              </td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(d.total_rx_bytes)}</td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(d.total_tx_bytes)}</td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{off ? '—' : fmtUptime(d.uptime)}</td>
            </tr>
            {isExpanded && d.iface_stats && d.iface_stats.length > 0 && (
              <IfaceSubTable ifaces={d.iface_stats} colSpan={SRV_COLS} />
            )}
          </>
        })}
      </tbody>
    </table>
  )
}

// ── UPS table ─────────────────────────────────────────────────────────────────

function UpsTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    switch (sort.col) {
      case 'name':   return a.name.localeCompare(b.name) * dir
      case 'ip':     return (a.mgmt_ip || a.ip_address).localeCompare(b.mgmt_ip || b.ip_address) * dir
      case 'status': return (a.ups_status ?? '').localeCompare(b.ups_status ?? '') * dir
      case 'mode':   return (a.ups_operating_mode ?? '').localeCompare(b.ups_operating_mode ?? '') * dir
      case 'load':   return ((a.ups_output_load ?? 0) - (b.ups_output_load ?? 0)) * dir
      case 'health':  return ((a.ups_battery_health ?? 0) - (b.ups_battery_health ?? 0)) * dir
      case 'battvolt':return ((a.ups_battery_voltage ?? 0) - (b.ups_battery_voltage ?? 0)) * dir
      case 'energy':  return ((a.ups_energy_kwh ?? 0) - (b.ups_energy_kwh ?? 0)) * dir
      case 'volt':    return ((a.ups_input_voltage ?? 0) - (b.ups_input_voltage ?? 0)) * dir
      case 'inc':     return ((a.ups_input_current ?? 0) - (b.ups_input_current ?? 0)) * dir
      case 'inw':     return ((a.ups_input_power ?? 0) - (b.ups_input_power ?? 0)) * dir
      case 'freq':    return ((a.ups_input_frequency ?? 0) - (b.ups_input_frequency ?? 0)) * dir
      case 'outv':    return ((a.ups_output_voltage ?? 0) - (b.ups_output_voltage ?? 0)) * dir
      case 'outc':    return ((a.ups_output_current ?? 0) - (b.ups_output_current ?? 0)) * dir
      case 'outw':    return ((a.ups_output_power ?? 0) - (b.ups_output_power ?? 0)) * dir
      default: return 0
    }
  }), [rows, sort.col, sort.dir])

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <tr>
          <th style={{ width: 18, background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }} />
          <SortTH label="Name"        id="name"   sort={sort} minW={140} />
          <SortTH label="IP"          id="ip"     sort={sort} />
          <SortTH label="Power Status"    id="status" sort={sort} />
          <SortTH label="Operating Mode"  id="mode"   sort={sort} title="Derived: online when normal, battery when on_battery/low_battery, bypass when bypass_status=on" />
          <SortTH label="Battery Status"  id="batt"   sort={sort} />
          <SortTH label="Battery Health"   id="health"  sort={sort} align="right" minW={100} />
          <SortTH label="Battery Voltage"  id="battvolt" sort={sort} align="right" minW={100} />
          <SortTH label="Output Load"      id="load"    sort={sort} align="right" minW={90} />
          <SortTH label="Output Voltage"   id="outv"    sort={sort} align="right" minW={90} />
          <SortTH label="Output Current"   id="outc"    sort={sort} align="right" minW={90} />
          <SortTH label="Output Power"     id="outw"    sort={sort} align="right" minW={90} />
          <SortTH label="Input Voltage"    id="volt"    sort={sort} align="right" minW={90} />
          <SortTH label="Input Current"    id="inc"     sort={sort} align="right" minW={90} />
          <SortTH label="Input Power"      id="inw"     sort={sort} align="right" minW={90} />
          <SortTH label="Input Freq"       id="freq"    sort={sort} align="right" minW={80} />
          <SortTH label="Fan Status"      id="fan"    sort={sort} />
          <SortTH label="Charger Status"  id="chgr"   sort={sort} />
          <SortTH label="Rectifier Status" id="rect"  sort={sort} />
          <SortTH label="Phase Status"    id="phase"  sort={sort} />
          <SortTH label="Energy (kWh)"    id="energy" sort={sort} align="right" minW={90} title="Derived: cumulative ∫(output_load% × 3kW) dt per tick" />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => (
          <tr key={d.id} style={ROW_STYLE(i)}>
            <td style={{ width: 18 }} />
            <NameCell d={d} />
            <IpCell d={d} />
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_status}           okStates={['normal']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_operating_mode}   okStates={['online']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_battery_status}   okStates={['normal']} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_battery_health}   unit="%" warn={80} crit={50} invert /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_battery_voltage}  unit="V" warn={200} crit={185} invert /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_output_load}      unit="%" warn={70} crit={90} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_output_voltage}   unit="V" /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_output_current}   unit="A" decimals={2} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_output_power}     unit="W" decimals={0} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_input_voltage}    unit="V" warn={235} crit={245} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_input_current}    unit="A" decimals={2} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_input_power}      unit="W" decimals={0} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_input_frequency}  unit="Hz" warn={50.3} crit={50.5} decimals={2} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_fan_status}       okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_charger_status}   okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_rectifier_status} okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_phase_status}     okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_energy_kwh}      unit=" kWh" decimals={1} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── PDU table ─────────────────────────────────────────────────────────────────

function PduTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    switch (sort.col) {
      case 'name':  return a.name.localeCompare(b.name) * dir
      case 'type':  return a.device_type.localeCompare(b.device_type) * dir
      case 'ip':    return (a.mgmt_ip || a.ip_address).localeCompare(b.mgmt_ip || b.ip_address) * dir
      case 'load':   return ((a.pdu_load ?? 0) - (b.pdu_load ?? 0)) * dir
      case 'volt':   return ((a.pdu_voltage ?? 0) - (b.pdu_voltage ?? 0)) * dir
      case 'curr':   return ((a.pdu_outlet_current ?? 0) - (b.pdu_outlet_current ?? 0)) * dir
      case 'pf':     return ((a.pdu_power_factor ?? 0) - (b.pdu_power_factor ?? 0)) * dir
      case 'imbal':  return ((a.pdu_phase_imbalance ?? 0) - (b.pdu_phase_imbalance ?? 0)) * dir
      case 'realw':  return ((a.pdu_real_power ?? 0) - (b.pdu_real_power ?? 0)) * dir
      case 'va':     return ((a.pdu_apparent_power ?? 0) - (b.pdu_apparent_power ?? 0)) * dir
      case 'outw':   return ((a.pdu_outlet_power ?? 0) - (b.pdu_outlet_power ?? 0)) * dir
      case 'freq':   return ((a.pdu_frequency ?? 0) - (b.pdu_frequency ?? 0)) * dir
      case 'temp':   return ((a.pdu_temperature ?? 0) - (b.pdu_temperature ?? 0)) * dir
      case 'humid':  return ((a.pdu_humidity ?? 0) - (b.pdu_humidity ?? 0)) * dir
      case 'energy': return ((a.pdu_energy_kwh ?? 0) - (b.pdu_energy_kwh ?? 0)) * dir
      default: return 0
    }
  }), [rows, sort.col, sort.dir])

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <tr>
          <th style={{ width: 18, background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }} />
          <SortTH label="Name"         id="name"  sort={sort} minW={140} />
          <SortTH label="Type"         id="type"  sort={sort} />
          <SortTH label="IP"           id="ip"    sort={sort} />
          <SortTH label="PDU Load"         id="load"  sort={sort} align="right" minW={80} />
          <SortTH label="Input Voltage"   id="volt"  sort={sort} align="right" minW={90} />
          <SortTH label="Outlet Current"  id="curr"  sort={sort} align="right" minW={90} />
          <SortTH label="Power Factor"    id="pf"    sort={sort} align="right" minW={90} />
          <SortTH label="Phase Imbalance" id="imbal" sort={sort} align="right" minW={100} />
          <SortTH label="Real Power (W)"  id="realw" sort={sort} align="right" minW={90}  title="Derived: Input Voltage × Outlet Current × Power Factor" />
          <SortTH label="Apparent (VA)"   id="va"    sort={sort} align="right" minW={90}  title="Derived: Input Voltage × Outlet Current" />
          <SortTH label="Outlet Power (W)" id="outw" sort={sort} align="right" minW={90}  title="Derived: Input Voltage × Outlet Current × Power Factor (per-outlet)" />
          <SortTH label="Input Freq"      id="freq"  sort={sort} align="right" minW={80} />
          <SortTH label="Ambient Temp"    id="temp"  sort={sort} align="right" minW={90} />
          <SortTH label="Ambient Humidity" id="humid" sort={sort} align="right" minW={100} />
          <SortTH label="Energy (kWh)"    id="energy" sort={sort} align="right" minW={90}  title="Derived: cumulative ∫(Real Power kW) dt per tick" />
          <SortTH label="Outlet Status"   id="outl"  sort={sort} />
          <SortTH label="Breaker Status"  id="brkr"  sort={sort} />
          <SortTH label="Outlet Failure"  id="fail"  sort={sort} />
          <SortTH label="Smoke Detection" id="smk"   sort={sort} />
          <SortTH label="Ground Fault"    id="gnd"   sort={sort} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => (
          <tr key={d.id} style={ROW_STYLE(i)}>
            <td style={{ width: 18 }} />
            <NameCell d={d} />
            <td style={{ padding: '6px 10px' }}><TypeBadge dt={d.device_type} /></td>
            <IpCell d={d} />
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_load}            unit="%" warn={70} crit={85} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_voltage}         unit="V" warn={230} crit={240} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_outlet_current}  unit="A" warn={15} crit={20} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_power_factor}    unit="" warn={0} crit={0} decimals={2} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_phase_imbalance} unit="%" warn={10} crit={15} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_real_power}      unit=" W"   warn={4000} crit={5000} decimals={0} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_apparent_power}  unit=" VA"  decimals={0} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_outlet_power}    unit=" W"   warn={3500} crit={4500} decimals={0} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_frequency}       unit=" Hz"  warn={50.3} crit={50.5} decimals={2} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_temperature}     unit="°C"   warn={35} crit={40} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_humidity}        unit="%"    warn={70} crit={85} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.pdu_energy_kwh}      unit=" kWh" decimals={1} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.pdu_outlet_status}  okStates={['on']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.pdu_breaker_status} okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.pdu_outlet_failure} okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.pdu_smoke}          okStates={['no']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.pdu_ground_fault}   okStates={['no']} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Sensor table ──────────────────────────────────────────────────────────────

type SensorMetricCfg = { key: keyof DeviceInfo; label: string; unit: string; warn: number; crit: number }

const SENSOR_MODEL_METRICS: Record<string, SensorMetricCfg[]> = {
  'Raritan DPX2-T3H1': [
    { key: 'inlet_temp',  label: 'Inlet Temp',   unit: '°C',   warn: 35, crit: 45 },
    { key: 'mid_temp',    label: 'Mid-Rack Temp', unit: '°C',   warn: 38, crit: 48 },
    { key: 'outlet_temp', label: 'Exhaust Temp',  unit: '°C',   warn: 42, crit: 52 },
    { key: 'humidity',    label: 'Humidity',       unit: '%',    warn: 70, crit: 85 },
  ],
  'Raritan DPX2-CC2': [
    { key: 'inlet_temp', label: 'Floor Temp', unit: '°C', warn: 35, crit: 45 },
  ],
  'Vertiv Geist GTHD': [
    { key: 'inlet_temp', label: 'Inlet Temp', unit: '°C', warn: 35, crit: 45 },
    { key: 'humidity',   label: 'Humidity',   unit: '%',  warn: 70, crit: 85 },
    { key: 'dewpoint',   label: 'Dewpoint',   unit: '°C', warn: 20, crit: 25 },
  ],
  'APC NetBotz 355': [
    { key: 'inlet_temp', label: 'Inlet Temp', unit: '°C',   warn: 35, crit: 45 },
    { key: 'humidity',   label: 'Humidity',   unit: '%',    warn: 70, crit: 85 },
    { key: 'airflow',    label: 'Airflow',    unit: ' m/s', warn: 3,  crit: 3.8 },
  ],
  'APC NetBotz 250': [
    { key: 'inlet_temp', label: 'Inlet Temp', unit: '°C',   warn: 35, crit: 45 },
    { key: 'humidity',   label: 'Humidity',   unit: '%',    warn: 70, crit: 85 },
    { key: 'airflow',    label: 'Airflow',    unit: ' m/s', warn: 3,  crit: 3.8 },
  ],
}

function getSensorMetrics(model: string): SensorMetricCfg[] {
  return SENSOR_MODEL_METRICS[model] ?? [
    { key: 'inlet_temp', label: 'Inlet Temp', unit: '°C', warn: 35, crit: 45 },
  ]
}

function SensorTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')

  const groups = useMemo(() => {
    const map = new Map<string, DeviceInfo[]>()
    for (const d of rows) {
      const model = d.model_name ?? 'Unknown Sensor'
      if (!map.has(model)) map.set(model, [])
      map.get(model)!.push(d)
    }
    const dir = sort.dir === 'asc' ? 1 : -1
    for (const devices of map.values()) {
      devices.sort((a, b) => {
        if (sort.col === 'ip')     return (a.mgmt_ip || a.ip_address).localeCompare(b.mgmt_ip || b.ip_address) * dir
        if (sort.col === 'uptime') return (a.uptime - b.uptime) * dir
        return a.name.localeCompare(b.name) * dir
      })
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [rows, sort.col, sort.dir])

  return (
    <div>
      {groups.map(([model, devices], gi) => {
        const metrics = getSensorMetrics(model)
        return (
          <div key={model} style={{ marginBottom: gi < groups.length - 1 ? 20 : 0 }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', padding: '4px 10px', background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
              {model} &nbsp;·&nbsp; {devices.length} device{devices.length !== 1 ? 's' : ''}
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  <th style={{ width: 18, background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }} />
                  <SortTH label="Name"   id="name"   sort={sort} minW={140} />
                  <SortTH label="IP"     id="ip"     sort={sort} />
                  {metrics.map(m => (
                    <SortTH key={String(m.key)} label={m.label} id={String(m.key)} sort={sort} align="right" minW={90} />
                  ))}
                  <SortTH label="Uptime" id="uptime" sort={sort} align="right" minW={80} />
                </tr>
              </thead>
              <tbody>
                {devices.map((d, i) => (
                  <tr key={d.id} style={ROW_STYLE(i)}>
                    <td style={{ width: 18 }} />
                    <NameCell d={d} />
                    <IpCell d={d} />
                    {metrics.map(m => (
                      <td key={String(m.key)} style={{ padding: '6px 10px', textAlign: 'right' }}>
                        <NumCell val={d[m.key] as number | undefined} unit={m.unit} warn={m.warn} crit={m.crit} />
                      </td>
                    ))}
                    <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtUptime(d.uptime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}
    </div>
  )
}

// ── EV2 energy monitor tab ────────────────────────────────────────────────────

function fmtVal(v: number | undefined, decimals = 1): string {
  return v == null ? '—' : v.toFixed(decimals)
}

function AlarmPill({ label, active }: { label: string; active: boolean }) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 7px', borderRadius: 10, fontSize: 9,
      fontWeight: 600, marginRight: 4,
      background: active ? '#c0392b22' : 'var(--bg-card)',
      color: active ? '#e74c3c' : 'var(--text-muted)',
      border: `1px solid ${active ? '#e74c3c' : 'var(--border)'}`,
    }}>{label}</span>
  )
}

function StatCell({ label, value, unit, warn, crit }: { label: string; value?: number; unit: string; warn?: number; crit?: number }) {
  const color = value == null ? 'var(--text-muted)'
    : crit != null && value >= crit ? '#e74c3c'
    : warn != null && value >= warn ? '#f39c12'
    : 'var(--text)'
  return (
    <div style={{ minWidth: 80, padding: '6px 10px', borderRight: '1px solid var(--border)' }}>
      <div style={{ fontSize: 8, color: 'var(--text-muted)', marginBottom: 2, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
      <div style={{ fontSize: 12, fontWeight: 600, color, fontVariantNumeric: 'tabular-nums' }}>
        {value == null ? '—' : value.toFixed(unit === ' kWh' ? 1 : unit === ' kW' ? 2 : unit === ' Hz' ? 2 : unit === ' PF' ? 3 : 1)}{value != null ? unit : ''}
      </div>
    </div>
  )
}

function EV2CircuitTable({ circuits }: { circuits: EV2CircuitMetrics[] }) {
  const [sortCol, setSortCol] = useState<string>('circuit')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const sorted = useMemo(() => {
    return [...circuits].sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      if (sortCol === 'circuit')     return (a.circuit - b.circuit) * dir
      if (sortCol === 'device_name') return (a.device_name ?? '').localeCompare(b.device_name ?? '') * dir
      if (sortCol === 'current')     return ((a.current ?? 0) - (b.current ?? 0)) * dir
      if (sortCol === 'kw')          return ((a.kw ?? 0) - (b.kw ?? 0)) * dir
      if (sortCol === 'kwh')         return ((a.kwh ?? 0) - (b.kwh ?? 0)) * dir
      if (sortCol === 'pf')          return ((a.pf ?? 0) - (b.pf ?? 0)) * dir
      if (sortCol === 'thd')         return ((a.thd ?? 0) - (b.thd ?? 0)) * dir
      return 0
    })
  }, [circuits, sortCol, sortDir])

  function th(label: string, col: string, align: 'left' | 'right' = 'right') {
    const active = sortCol === col
    return (
      <th onClick={() => { if (active) setSortDir(d => d === 'asc' ? 'desc' : 'asc'); else { setSortCol(col); setSortDir('asc') } }}
        style={{ padding: '4px 8px', textAlign: align, fontSize: 9, fontWeight: 600, color: active ? 'var(--accent)' : 'var(--text-muted)', cursor: 'pointer', background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap', userSelect: 'none' }}>
        {label}{active ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
      </th>
    )
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead>
        <tr>
          {th('Circuit', 'circuit', 'left')}
          {th('Device', 'device_name', 'left')}
          {th('Current (A)', 'current')}
          {th('kW', 'kw')}
          {th('kWh', 'kwh')}
          {th('PF', 'pf')}
          {th('THD %', 'thd')}
        </tr>
      </thead>
      <tbody>
        {sorted.map((c, i) => (
          <tr key={c.circuit} style={{ background: i % 2 === 0 ? 'transparent' : 'var(--bg-stripe, rgba(255,255,255,0.02))' }}>
            <td style={{ padding: '3px 8px', fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap' }}>{c.label}</td>
            <td style={{ padding: '3px 8px', color: 'var(--text-muted)', whiteSpace: 'nowrap', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.device_name ?? '—'}</td>
            <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}><NumCell val={c.current} unit=" A"  warn={16} crit={20} /></td>
            <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}><NumCell val={c.kw}      unit=" kW" warn={3}  crit={4}  /></td>
            <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-muted)' }}>{fmtVal(c.kwh, 1)}</td>
            <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}><NumCell val={c.pf}      unit=""    warn={0}  crit={0}  /></td>
            <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}><NumCell val={c.thd}     unit="%"   warn={5}  crit={7}  /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function EV2MetricsTab({ snapshots }: { snapshots: EV2DeviceSnapshot[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  if (snapshots.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
        BACnet offline — start BACnet simulator to see EV2 metrics
      </div>
    )
  }

  return (
    <div>
      {snapshots.map((snap) => {
        const p = snap.panel
        const isExpanded = expanded.has(snap.instance)
        const anyAlarm = p.alarm_overcurrent || p.alarm_voltage_imbalance || p.alarm_high_thd || p.alarm_phase_loss || p.alarm_sensor_fault
        const activeCircuits = snap.circuit_list.filter(c => (c.current ?? 0) > 0 || (c.device_name ?? '').length > 0)
        return (
          <div key={snap.instance} style={{ marginBottom: 16, border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden' }}>
            {/* Device header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontWeight: 700, fontSize: 11, color: 'var(--text)' }}>{snap.name}</span>
              <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{snap.ip}</span>
              {snap.monitored_pdu_name && (
                <span style={{ fontSize: 9, padding: '1px 6px', borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--accent)' }}>
                  PDU: {snap.monitored_pdu_name}
                </span>
              )}
              <span style={{ fontSize: 9, padding: '1px 6px', borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>{snap.circuits} circuits</span>
              <span style={{ fontSize: 9, padding: '1px 6px', borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>inst {snap.instance}</span>
              {anyAlarm && <span style={{ fontSize: 9, padding: '1px 6px', borderRadius: 8, background: '#c0392b22', border: '1px solid #e74c3c', color: '#e74c3c', fontWeight: 700 }}>ALARM</span>}
            </div>

            {/* Panel summary */}
            <div style={{ display: 'flex', flexWrap: 'wrap', borderBottom: '1px solid var(--border)', background: 'var(--bg-base)' }}>
              <StatCell label="Total kW"  value={p.total_kw}     unit=" kW"  warn={150} crit={180} />
              <StatCell label="Total kWh" value={p.total_kwh}    unit=" kWh" />
              <StatCell label="V Phase A" value={p.voltage_pha}  unit=" V"   warn={245} crit={255} />
              <StatCell label="V Phase B" value={p.voltage_phb}  unit=" V"   warn={245} crit={255} />
              <StatCell label="V Phase C" value={p.voltage_phc}  unit=" V"   warn={245} crit={255} />
              <StatCell label="I Phase A" value={p.current_pha}  unit=" A"   warn={70}  crit={85}  />
              <StatCell label="I Phase B" value={p.current_phb}  unit=" A"   warn={70}  crit={85}  />
              <StatCell label="I Phase C" value={p.current_phc}  unit=" A"   warn={70}  crit={85}  />
              <StatCell label="Frequency" value={p.frequency}    unit=" Hz"  warn={51}  crit={52}  />
              <StatCell label="Power Factor" value={p.power_factor} unit=" PF" />
            </div>

            {/* Power quality */}
            <div style={{ display: 'flex', flexWrap: 'wrap', borderBottom: '1px solid var(--border)', background: 'var(--bg-base)' }}>
              <StatCell label="V THD"  value={p.voltage_thd} unit="%" warn={5}  crit={8}  />
              <StatCell label="I THD"  value={p.current_thd} unit="%" warn={5}  crit={7}  />
              <StatCell label="H3 %" value={p.harmonic_3}    unit="%" warn={10} crit={15} />
              <StatCell label="H5 %" value={p.harmonic_5}    unit="%" warn={10} crit={15} />
              <StatCell label="H7 %" value={p.harmonic_7}    unit="%" warn={10} crit={15} />
              <StatCell label="H9 %" value={p.harmonic_9}    unit="%" warn={10} crit={15} />
            </div>

            {/* Alarms — only show active ones; "No alarms" when all clear */}
            <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
              {anyAlarm ? (
                <>
                  {p.alarm_overcurrent       && <AlarmPill label="Overcurrent"       active />}
                  {p.alarm_voltage_imbalance && <AlarmPill label="Voltage Imbalance" active />}
                  {p.alarm_high_thd          && <AlarmPill label="High THD"          active />}
                  {p.alarm_phase_loss        && <AlarmPill label="Phase Loss"        active />}
                  {p.alarm_sensor_fault      && <AlarmPill label="Sensor Fault"      active />}
                </>
              ) : (
                <span style={{ fontSize: 9, padding: '2px 7px', borderRadius: 10, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                  No alarms
                </span>
              )}
            </div>

            {/* Circuits toggle */}
            <div style={{ background: 'var(--bg-panel)' }}>
              <button
                onClick={() => setExpanded(s => { const n = new Set(s); isExpanded ? n.delete(snap.instance) : n.add(snap.instance); return n })}
                style={{ width: '100%', background: 'transparent', border: 'none', borderBottom: isExpanded ? '1px solid var(--border)' : 'none', padding: '5px 12px', textAlign: 'left', cursor: 'pointer', fontSize: 10, color: 'var(--text-muted)' }}>
                {isExpanded ? '▼' : '▶'} Circuits ({activeCircuits.length} / {snap.circuits})
              </button>
              {isExpanded && <EV2CircuitTable circuits={activeCircuits} />}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Chiller-plant tabs (CRAH / Chiller / Pump / Cooling Tower / Valve) ─────────
// Columns mirror PLANT_SPEC in core/bacnet_plant_generator.py. bin: 'run' =
// 1 is good (running); bin: 'alarm' = 1 is bad (alarm active).

type PlantCol = {
  key: string; label: string; unit?: string
  warn?: number; crit?: number; decimals?: number; bin?: 'run' | 'alarm'
}

const PLANT_COLUMNS: Record<string, PlantCol[]> = {
  crah: [
    { key: 'Supply_Air_Temp', label: 'Supply Air', unit: '°C', warn: 26, crit: 30, decimals: 1 },
    { key: 'Return_Air_Temp', label: 'Return Air', unit: '°C', decimals: 1 },
    { key: 'Setpoint',        label: 'Setpoint',   unit: '°C', decimals: 1 },
    { key: 'Fan_Speed',       label: 'Fan Speed',  unit: '%',  decimals: 0 },
    { key: 'CHW_Valve',       label: 'CHW Valve',  unit: '%',  decimals: 0 },
    { key: 'Cooling_Capacity',label: 'Capacity',   unit: '%',  decimals: 0 },
    { key: 'Supply_Humidity', label: 'Humidity',   unit: '%',  warn: 60, crit: 70, decimals: 0 },
    { key: 'Airflow',         label: 'Airflow',    unit: '%',  decimals: 0 },
    { key: 'Fan_Power',       label: 'Fan Power',  unit: ' kW', decimals: 2 },
    { key: 'Run_Hours',       label: 'Run Hrs',    unit: ' h', decimals: 0 },
    { key: 'Unit_Running',     label: 'Running',    bin: 'run' },
    { key: 'Alarm_HighTemp',   label: 'Hi Temp',    bin: 'alarm' },
    { key: 'Alarm_AirflowLoss',label: 'Airflow Loss', bin: 'alarm' },
    { key: 'Filter_Dirty',     label: 'Filter',     bin: 'alarm' },
  ],
  chiller: [
    { key: 'CHW_Supply_Temp', label: 'CHW Supply', unit: '°C', warn: 9,  crit: 11, decimals: 1 },
    { key: 'CHW_Return_Temp', label: 'CHW Return', unit: '°C', decimals: 1 },
    { key: 'CHW_Setpoint',    label: 'Setpoint',   unit: '°C', decimals: 1 },
    { key: 'CHW_Flow',        label: 'CHW Flow',   unit: ' L/s', decimals: 1 },
    { key: 'Cond_Supply_Temp',label: 'Cond Supply',unit: '°C', decimals: 1 },
    { key: 'Cond_Return_Temp',label: 'Cond Return',unit: '°C', decimals: 1 },
    { key: 'Compressor_Load', label: 'Compressor', unit: '%',  warn: 85, crit: 95, decimals: 0 },
    { key: 'Active_Power',    label: 'Power',      unit: ' kW', decimals: 1 },
    { key: 'Cooling_Capacity',label: 'Capacity',   unit: ' kW', decimals: 0 },
    { key: 'COP',             label: 'COP',        decimals: 2 },
    { key: 'Evap_Pressure',   label: 'Evap P',     unit: ' kPa', decimals: 0 },
    { key: 'Cond_Pressure',   label: 'Cond P',     unit: ' kPa', decimals: 0 },
    { key: 'Run_Hours',       label: 'Run Hrs',    unit: ' h', decimals: 0 },
    { key: 'Chiller_Running',   label: 'Running',  bin: 'run' },
    { key: 'Alarm_HighPressure',label: 'Hi Press', bin: 'alarm' },
    { key: 'Alarm_LowEvapTemp', label: 'Lo Evap',  bin: 'alarm' },
    { key: 'Alarm_FlowLoss',    label: 'Flow Loss',bin: 'alarm' },
  ],
  pump: [
    { key: 'Speed',             label: 'Speed',      unit: '%',  decimals: 0 },
    { key: 'Flow',              label: 'Flow',       unit: ' L/s', decimals: 1 },
    { key: 'Discharge_Pressure',label: 'Discharge P',unit: ' kPa', decimals: 0 },
    { key: 'Suction_Pressure',  label: 'Suction P',  unit: ' kPa', decimals: 0 },
    { key: 'Diff_Pressure',     label: 'Diff P',     unit: ' kPa', decimals: 0 },
    { key: 'Motor_Power',       label: 'Motor Power',unit: ' kW', decimals: 2 },
    { key: 'Motor_Temp',        label: 'Motor Temp', unit: '°C', warn: 70, crit: 85, decimals: 1 },
    { key: 'VFD_Frequency',     label: 'VFD Freq',   unit: ' Hz', decimals: 1 },
    { key: 'Run_Hours',         label: 'Run Hrs',    unit: ' h', decimals: 0 },
    { key: 'Run_Status',   label: 'Running',  bin: 'run' },
    { key: 'Alarm_Fault',  label: 'Fault',    bin: 'alarm' },
    { key: 'Alarm_LowFlow',label: 'Low Flow', bin: 'alarm' },
  ],
  cooling_tower: [
    { key: 'Fan_Speed',      label: 'Fan Speed',   unit: '%',  decimals: 0 },
    { key: 'Basin_Temp',     label: 'Basin Temp',  unit: '°C', decimals: 1 },
    { key: 'Cond_Water_In',  label: 'Water In',    unit: '°C', decimals: 1 },
    { key: 'Cond_Water_Out', label: 'Water Out',   unit: '°C', decimals: 1 },
    { key: 'Fan_Power',      label: 'Fan Power',   unit: ' kW', decimals: 2 },
    { key: 'Basin_Level',    label: 'Basin Level', unit: '%',  warn: 40, crit: 25, decimals: 0, },
    { key: 'Makeup_Flow',    label: 'Makeup Flow', unit: ' L/min', decimals: 1 },
    { key: 'Vibration',      label: 'Vibration',   unit: ' mm/s', warn: 4, crit: 6, decimals: 2 },
    { key: 'Run_Hours',      label: 'Run Hrs',     unit: ' h', decimals: 0 },
    { key: 'Fan_Status',         label: 'Running',  bin: 'run' },
    { key: 'Alarm_HighVibration',label: 'Hi Vibration', bin: 'alarm' },
    { key: 'Alarm_LowBasin',     label: 'Low Basin',bin: 'alarm' },
  ],
  valve: [
    { key: 'Position',          label: 'Position',   unit: '%', decimals: 0 },
    { key: 'Commanded_Position',label: 'Commanded',  unit: '%', decimals: 0 },
    { key: 'Actuator_Temp',     label: 'Actuator Temp', unit: '°C', warn: 60, crit: 75, decimals: 1 },
    { key: 'Status_Modulating',  label: 'Modulating', bin: 'run' },
    { key: 'Alarm_ActuatorFault',label: 'Fault',      bin: 'alarm' },
  ],
  cdu: [
    { key: 'TCS_Supply_Temp',    label: 'TCS Supply', unit: '°C', warn: 36, crit: 40, decimals: 1 },
    { key: 'TCS_Return_Temp',    label: 'TCS Return', unit: '°C', decimals: 1 },
    { key: 'TCS_Setpoint',       label: 'Setpoint',   unit: '°C', decimals: 1 },
    { key: 'TCS_Flow',           label: 'TCS Flow',   unit: ' L/s', decimals: 1 },
    { key: 'Facility_CHW_Valve', label: 'CHW Valve',  unit: '%',  decimals: 0 },
    { key: 'Facility_CHW_Flow',  label: 'CHW Flow',   unit: ' L/s', decimals: 1 },
    { key: 'TCS_Loop_Pressure',  label: 'Loop Press', unit: ' kPa', warn: 180, crit: 150, decimals: 0 },
    { key: 'Heat_Load',          label: 'Heat Load',  unit: ' kW', decimals: 0 },
    { key: 'Pump_Power',         label: 'Pump Power', unit: ' kW', decimals: 2 },
    { key: 'Pump_Speed',         label: 'Pump Speed', unit: '%',  decimals: 0 },
    { key: 'Approach_Temp',      label: 'Approach',   unit: '°C', warn: 5, crit: 7, decimals: 1 },
    { key: 'Filter_DP',          label: 'Filter ΔP',  unit: ' kPa', warn: 55, crit: 70, decimals: 0 },
    { key: 'Run_Hours',          label: 'Run Hrs',    unit: ' h', decimals: 0 },
    { key: 'Unit_Running',         label: 'Running',    bin: 'run' },
    { key: 'Alarm_Leak',           label: 'Leak',       bin: 'alarm' },
    { key: 'Alarm_HighSupplyTemp', label: 'Hi Supply',  bin: 'alarm' },
    { key: 'Alarm_PumpFault',      label: 'Pump Fault', bin: 'alarm' },
    { key: 'Alarm_LowFlow',        label: 'Low Flow',   bin: 'alarm' },
  ],
}

function PlantBinCell({ val, mode }: { val?: number; mode: 'run' | 'alarm' }) {
  if (val == null) return <span style={{ color: 'var(--text-dim)' }}>—</span>
  const on = val >= 0.5
  // run: on = good (green); alarm: on = bad (red)
  const good = mode === 'run' ? on : !on
  const text = mode === 'run' ? (on ? 'ON' : 'OFF') : (on ? 'ALARM' : 'OK')
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 3, fontSize: 9,
      fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.3px',
      background: good ? '#3fb95022' : '#f8514922',
      border: `1px solid ${good ? '#3fb95044' : '#f8514944'}`,
      color: good ? '#3fb950' : '#f85149',
    }}>{text}</span>
  )
}

function PlantTable({ type, rows }: { type: string; rows: PlantDeviceSnapshot[] }) {
  const cols = PLANT_COLUMNS[type] ?? []
  const sort = useSortState('name')

  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    if (sort.col === 'name')     return a.name.localeCompare(b.name) * dir
    if (sort.col === 'ip')       return a.ip.localeCompare(b.ip) * dir
    if (sort.col === 'instance') return (a.instance - b.instance) * dir
    return ((a.values[sort.col] ?? 0) - (b.values[sort.col] ?? 0)) * dir
  }), [rows, sort.col, sort.dir])

  if (rows.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
        BACnet offline — start BACnet simulator to see {type.replace(/_/g, ' ')} metrics
      </div>
    )
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <tr>
          <SortTH label="Name"     id="name"     sort={sort} minW={140} />
          <SortTH label="IP"       id="ip"       sort={sort} />
          <SortTH label="Instance" id="instance" sort={sort} align="right" minW={70} />
          {cols.map(c => (
            <SortTH key={c.key} label={c.label} id={c.key} sort={sort}
              align={c.bin ? 'center' : 'right'} minW={c.bin ? 70 : 80} />
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => (
          <tr key={d.instance} style={ROW_STYLE(i)}>
            <td style={{ padding: '6px 10px', fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }} title={d.name}>{d.name}</td>
            <td style={{ padding: '6px 10px', fontFamily: 'monospace', color: 'var(--text-muted)', whiteSpace: 'nowrap', fontSize: 10 }}>{d.ip}</td>
            <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{d.instance}</td>
            {cols.map(c => (
              <td key={c.key} style={{ padding: '6px 10px', textAlign: c.bin ? 'center' : 'right' }}>
                {c.bin
                  ? <PlantBinCell val={d.values[c.key]} mode={c.bin} />
                  : <NumCell val={d.values[c.key]} unit={c.unit} warn={c.warn} crit={c.crit} decimals={c.decimals ?? 1} />}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── tabs ──────────────────────────────────────────────────────────────────────

type Tab = 'all' | 'network' | 'server' | 'ups' | 'pdu' | 'sensor' | 'energy'
        | 'crah' | 'chiller' | 'pump' | 'cooling_tower' | 'valve' | 'cdu'

const PLANT_TABS: Tab[] = ['crah', 'chiller', 'pump', 'cooling_tower', 'valve', 'cdu']

const TAB_LABELS: Record<Tab, string> = {
  all: 'All', network: 'Network', server: 'Server', ups: 'UPS', pdu: 'PDU', sensor: 'Sensor', energy: 'Energy (EV2)',
  crah: 'CRAH', chiller: 'Chiller', pump: 'Pump', cooling_tower: 'Cooling Tower', valve: 'Valve', cdu: 'CDU',
}

const TAB_TYPES: Record<Tab, string[]> = {
  all:     [],
  network: ['router', 'switch', 'firewall', 'load_balancer', 'oob_switch'],
  server:  ['server'],
  ups:     ['ups'],
  pdu:     ['pdu', 'floor_pdu'],
  sensor:  ['sensor'],
  energy:  [],   // data comes from ev2Metrics, not devices[]
  crah:    [], chiller: [], pump: [], cooling_tower: [], valve: [], cdu: [],  // data from plantMetrics
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function LiveMetricsPage() {
  const { devices, ev2Metrics, plantMetrics, tickSeq, setActiveView, fetchEV2Metrics, fetchPlantMetrics } = useStore()
  const [tab,    setTab]    = useState<Tab>('all')
  const [search, setSearch] = useState('')

  const isPlantTab = PLANT_TABS.includes(tab)

  // Refresh EV2 metrics on tab open + every simulator tick (matches tick interval)
  useEffect(() => {
    if (tab !== 'energy') return
    fetchEV2Metrics()
  }, [tab, tickSeq, fetchEV2Metrics])

  // Refresh chiller-plant metrics on tab open + every simulator tick
  useEffect(() => {
    if (!isPlantTab) return
    fetchPlantMetrics()
  }, [isPlantTab, tickSeq, fetchPlantMetrics])

  const counts = useMemo((): Record<Tab, number> => ({
    all:     devices.length,
    network: devices.filter(d => TAB_TYPES.network.includes(d.device_type)).length,
    server:  devices.filter(d => TAB_TYPES.server.includes(d.device_type)).length,
    ups:     devices.filter(d => TAB_TYPES.ups.includes(d.device_type)).length,
    pdu:     devices.filter(d => TAB_TYPES.pdu.includes(d.device_type)).length,
    sensor:  devices.filter(d => TAB_TYPES.sensor.includes(d.device_type)).length,
    energy:  ev2Metrics.length,
    crah:          plantMetrics.filter(p => p.device_type === 'crah').length,
    chiller:       plantMetrics.filter(p => p.device_type === 'chiller').length,
    pump:          plantMetrics.filter(p => p.device_type === 'pump').length,
    cooling_tower: plantMetrics.filter(p => p.device_type === 'cooling_tower').length,
    valve:         plantMetrics.filter(p => p.device_type === 'valve').length,
    cdu:           plantMetrics.filter(p => p.device_type === 'cdu').length,
  }), [devices, ev2Metrics, plantMetrics])

  const filtered = useMemo(() => {
    if (tab === 'energy' || isPlantTab) return []   // these tabs use ev2Metrics/plantMetrics directly
    let list = devices.filter(d => d.device_type !== 'rpp')
    const types = TAB_TYPES[tab]
    if (types.length > 0) list = list.filter(d => types.includes(d.device_type))
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(d =>
        d.name.toLowerCase().includes(q) ||
        d.ip_address.toLowerCase().includes(q) ||
        d.vendor.toLowerCase().includes(q) ||
        d.device_type.toLowerCase().includes(q)
      )
    }
    return list
  }, [devices, ev2Metrics, tab, search])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--bg-base)', overflow: 'hidden' }}>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '0 16px', height: 42, flexShrink: 0,
        background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)',
      }}>
        <button onClick={() => setActiveView('main')} style={{
          background: 'transparent', border: '1px solid var(--border)',
          borderRadius: 4, padding: '3px 10px', cursor: 'pointer',
          color: 'var(--text-muted)', fontSize: 10,
        }}>← Back</button>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>Live Metrics</span>
        <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 10, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
          {devices.length} devices
        </span>
      </div>

      {/* Tabs + search */}
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)', flexShrink: 0 }}>
        {(Object.keys(TAB_LABELS) as Tab[]).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            border: 'none', borderRadius: 0,
            borderRight: '1px solid var(--border)',
            borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
            background: tab === t ? 'var(--bg-selected)' : 'transparent',
            color: tab === t ? 'var(--text)' : 'var(--text-muted)',
            padding: '5px 14px', fontSize: 10, cursor: 'pointer', whiteSpace: 'nowrap',
          }}>
            {TAB_LABELS[t]}
            <span style={{ marginLeft: 5, fontSize: 9, opacity: 0.7, fontVariantNumeric: 'tabular-nums' }}>{counts[t]}</span>
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ padding: '0 10px' }}>
          <input
            type="text" placeholder="Search name, IP, vendor, type…"
            value={search} onChange={e => setSearch(e.target.value)}
            style={{
              width: 220, background: 'var(--bg-card)', border: '1px solid var(--border)',
              borderRadius: 4, color: 'var(--text)', fontSize: 10, padding: '3px 8px', outline: 'none',
            }}
          />
        </div>
      </div>

      {/* Hint for expandable tabs */}
      {(tab === 'network' || tab === 'server') && filtered.length > 0 && (
        <div style={{ padding: '4px 12px', fontSize: 9, color: 'var(--text-muted)', background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
          ▶ Click a row to expand per-interface counters
        </div>
      )}

      {/* Table */}
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto', padding: tab === 'energy' ? 12 : 0 }}>
        {tab === 'energy'
          ? <EV2MetricsTab snapshots={ev2Metrics} />
          : isPlantTab
          ? <PlantTable type={tab} rows={plantMetrics.filter(p => p.device_type === tab)} />
          : filtered.length === 0
            ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
                {devices.length === 0 ? 'No topology loaded' : `No ${TAB_LABELS[tab].toLowerCase()} devices`}
              </div>
            : tab === 'all'     ? <AllTable     rows={filtered} />
            : tab === 'network' ? <NetworkTable rows={filtered} />
            : tab === 'server'  ? <ServerTable  rows={filtered} />
            : tab === 'ups'     ? <UpsTable     rows={filtered} />
            : tab === 'pdu'     ? <PduTable     rows={filtered} />
            :                     <SensorTable  rows={filtered} />
        }
      </div>

    </div>
  )
}
