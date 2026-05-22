import { useState, useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { DeviceInfo, IfaceStats } from '../api/types'

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
  pdu: '#065f46', floor_pdu: '#064e3b', oob_switch: '#4b5563', sensor: '#92400e',
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

function NumCell({ val, unit, warn, crit, decimals = 1 }: {
  val?: number; unit?: string; warn: number; crit: number; decimals?: number
}) {
  if (val == null) return <span style={{ color: 'var(--text-dim)' }}>—</span>
  const color = metricColor(val, warn, crit)
  return (
    <span style={{ color, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
      {val.toFixed(decimals)}{unit && <span style={{ color: 'var(--text-dim)', fontWeight: 400 }}>{unit}</span>}
    </span>
  )
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
      {d.ip_address}
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

function SortTH({ label, id, sort, align = 'left', minW }: {
  label: string; id: string; sort: ReturnType<typeof useSortState>; align?: string; minW?: number
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
      {label}
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
      case 'ip':     return a.ip_address.localeCompare(b.ip_address) * dir
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
          <SortTH label="CPU"      id="cpu"    sort={sort} align="right" minW={80} />
          <SortTH label="Memory"   id="mem"    sort={sort} align="right" minW={100} />
          <SortTH label="Disk"     id="disk"   sort={sort} align="right" minW={100} />
          <SortTH label="CPU Temp" id="ctemp"  sort={sort} align="right" minW={80} />
          <SortTH label="Uptime"   id="uptime" sort={sort} align="right" minW={80} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => {
          const cpuPct = Math.round(d.cpu_usage)
          return (
            <tr key={d.id} style={ROW_STYLE(i)}>
              <td style={{ width: 18 }} />
              <NameCell d={d} />
              <td style={{ padding: '6px 10px' }}><TypeBadge dt={d.device_type} /></td>
              <IpCell d={d} />
              <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                <span style={{ color: metricColor(cpuPct, 50, 80), fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{cpuPct}%</span>
                <MiniBar p={cpuPct} color={metricColor(cpuPct, 50, 80)} />
              </td>
              <PctCell used={d.memory_used} total={d.memory_total} warn={60} crit={85} />
              <PctCell used={d.disk_used}   total={d.disk_total}   warn={70} crit={90} />
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.cpu_temp} unit="°C" warn={75} crit={85} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtUptime(d.uptime)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Network table (expandable) ────────────────────────────────────────────────

const NET_COLS = 16

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
      case 'ip':       return a.ip_address.localeCompare(b.ip_address) * dir
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
          <SortTH label="IP"         id="ip"       sort={sort} />
          <SortTH label="CPU"        id="cpu"      sort={sort} align="right" minW={75} />
          <SortTH label="Memory"     id="mem"      sort={sort} align="right" minW={95} />
          <SortTH label="Disk"       id="disk"     sort={sort} align="right" minW={95} />
          <SortTH label="CPU Temp"   id="ctemp"    sort={sort} align="right" minW={75} />
          <SortTH label="Inlet Temp" id="itemp"    sort={sort} align="right" minW={75} />
          <SortTH label="Interfaces" id="ifaces"   sort={sort} align="right" minW={80} />
          <SortTH label="RX Total"   id="rx"       sort={sort} align="right" minW={85} />
          <SortTH label="TX Total"   id="tx"       sort={sort} align="right" minW={85} />
          <SortTH label="Errors"     id="errors"   sort={sort} align="right" minW={65} />
          <SortTH label="Discards"   id="discards" sort={sort} align="right" minW={65} />
          <SortTH label="BGP"        id="bgp"      sort={sort} align="right" minW={75} />
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
              <IpCell d={d} />
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

const SRV_COLS = 11

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
      case 'ip':     return a.ip_address.localeCompare(b.ip_address) * dir
      case 'cpu':    return (a.cpu_usage - b.cpu_usage) * dir
      case 'mem':    return (pct(a.memory_used, a.memory_total) - pct(b.memory_used, b.memory_total)) * dir
      case 'disk':   return (pct(a.disk_used, a.disk_total) - pct(b.disk_used, b.disk_total)) * dir
      case 'ctemp':  return ((a.cpu_temp ?? 0) - (b.cpu_temp ?? 0)) * dir
      case 'itemp':  return ((a.inlet_temp ?? 0) - (b.inlet_temp ?? 0)) * dir
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
          <SortTH label="IP"         id="ip"     sort={sort} />
          <SortTH label="CPU"        id="cpu"    sort={sort} align="right" minW={80} />
          <SortTH label="Memory"     id="mem"    sort={sort} align="right" minW={100} />
          <SortTH label="Disk"       id="disk"   sort={sort} align="right" minW={100} />
          <SortTH label="CPU Temp"   id="ctemp"  sort={sort} align="right" minW={80} />
          <SortTH label="Inlet Temp" id="itemp"  sort={sort} align="right" minW={80} />
          <SortTH label="Interfaces" id="ifaces" sort={sort} align="right" minW={80} />
          <SortTH label="RX Total"   id="rx"     sort={sort} align="right" minW={90} />
          <SortTH label="TX Total"   id="tx"     sort={sort} align="right" minW={90} />
          <SortTH label="Uptime"     id="uptime" sort={sort} align="right" minW={80} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => {
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
              <IpCell d={d} />
              <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                <span style={{ color: metricColor(cpuPct, 50, 80), fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{cpuPct}%</span>
                <MiniBar p={cpuPct} color={metricColor(cpuPct, 50, 80)} />
              </td>
              <PctCell used={d.memory_used} total={d.memory_total} warn={60} crit={85} />
              <PctCell used={d.disk_used}   total={d.disk_total}   warn={70} crit={90} />
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.cpu_temp}   unit="°C" warn={75} crit={85} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.inlet_temp} unit="°C" warn={35} crit={45} /></td>
              <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                {ifTot === 0
                  ? <span style={{ color: 'var(--text-dim)' }}>—</span>
                  : <span style={{ color: ifColor, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{ifUp}/{ifTot}</span>}
              </td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(d.total_rx_bytes)}</td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtBytes(d.total_tx_bytes)}</td>
              <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtUptime(d.uptime)}</td>
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
      case 'ip':     return a.ip_address.localeCompare(b.ip_address) * dir
      case 'status': return (a.ups_status ?? '').localeCompare(b.ups_status ?? '') * dir
      case 'load':   return ((a.ups_output_load ?? 0) - (b.ups_output_load ?? 0)) * dir
      case 'volt':   return ((a.ups_input_voltage ?? 0) - (b.ups_input_voltage ?? 0)) * dir
      case 'freq':   return ((a.ups_input_frequency ?? 0) - (b.ups_input_frequency ?? 0)) * dir
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
          <SortTH label="UPS Status"  id="status" sort={sort} />
          <SortTH label="Battery"     id="batt"   sort={sort} />
          <SortTH label="Output Load" id="load"   sort={sort} align="right" minW={90} />
          <SortTH label="Input V"     id="volt"   sort={sort} align="right" minW={80} />
          <SortTH label="Input Hz"    id="freq"   sort={sort} align="right" minW={80} />
          <SortTH label="Fan"         id="fan"    sort={sort} />
          <SortTH label="Charger"     id="chgr"   sort={sort} />
          <SortTH label="Rectifier"   id="rect"   sort={sort} />
          <SortTH label="Phase"       id="phase"  sort={sort} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => (
          <tr key={d.id} style={ROW_STYLE(i)}>
            <td style={{ width: 18 }} />
            <NameCell d={d} />
            <IpCell d={d} />
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_status}           okStates={['normal']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_battery_status}   okStates={['normal']} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_output_load}     unit="%" warn={70} crit={90} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_input_voltage}   unit="V" warn={235} crit={245} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.ups_input_frequency} unit="Hz" warn={50.3} crit={50.5} decimals={2} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_fan_status}       okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_charger_status}   okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_rectifier_status} okStates={['ok']} /></td>
            <td style={{ padding: '6px 10px' }}><StatePill val={d.ups_phase_status}     okStates={['ok']} /></td>
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
      case 'ip':    return a.ip_address.localeCompare(b.ip_address) * dir
      case 'load':  return ((a.pdu_load ?? 0) - (b.pdu_load ?? 0)) * dir
      case 'volt':  return ((a.pdu_voltage ?? 0) - (b.pdu_voltage ?? 0)) * dir
      case 'curr':  return ((a.pdu_outlet_current ?? 0) - (b.pdu_outlet_current ?? 0)) * dir
      case 'pf':    return ((a.pdu_power_factor ?? 0) - (b.pdu_power_factor ?? 0)) * dir
      case 'imbal': return ((a.pdu_phase_imbalance ?? 0) - (b.pdu_phase_imbalance ?? 0)) * dir
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
          <SortTH label="Load"         id="load"  sort={sort} align="right" minW={80} />
          <SortTH label="Voltage"      id="volt"  sort={sort} align="right" minW={80} />
          <SortTH label="Current"      id="curr"  sort={sort} align="right" minW={80} />
          <SortTH label="Power Factor" id="pf"    sort={sort} align="right" minW={90} />
          <SortTH label="Phase Imbal"  id="imbal" sort={sort} align="right" minW={90} />
          <SortTH label="Outlet"       id="outl"  sort={sort} />
          <SortTH label="Breaker"      id="brkr"  sort={sort} />
          <SortTH label="Failure"      id="fail"  sort={sort} />
          <SortTH label="Smoke"        id="smk"   sort={sort} />
          <SortTH label="Gnd Fault"    id="gnd"   sort={sort} />
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

function SensorTable({ rows }: { rows: DeviceInfo[] }) {
  const sort = useSortState('name')
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    switch (sort.col) {
      case 'name':     return a.name.localeCompare(b.name) * dir
      case 'ip':       return a.ip_address.localeCompare(b.ip_address) * dir
      case 'humidity': return ((a.humidity ?? 0) - (b.humidity ?? 0)) * dir
      case 'dewpoint': return ((a.dewpoint ?? 0) - (b.dewpoint ?? 0)) * dir
      case 'airflow':  return ((a.airflow ?? 0) - (b.airflow ?? 0)) * dir
      case 'itemp':    return ((a.inlet_temp ?? 0) - (b.inlet_temp ?? 0)) * dir
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
          <SortTH label="IP"         id="ip"       sort={sort} />
          <SortTH label="Humidity"   id="humidity" sort={sort} align="right" minW={90} />
          <SortTH label="Dew Point"  id="dewpoint" sort={sort} align="right" minW={90} />
          <SortTH label="Airflow"    id="airflow"  sort={sort} align="right" minW={90} />
          <SortTH label="Inlet Temp" id="itemp"    sort={sort} align="right" minW={90} />
          <SortTH label="Uptime"     id="uptime"   sort={sort} align="right" minW={80} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((d, i) => (
          <tr key={d.id} style={ROW_STYLE(i)}>
            <td style={{ width: 18 }} />
            <NameCell d={d} />
            <IpCell d={d} />
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.humidity}   unit="%" warn={70} crit={85} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.dewpoint}   unit="°C" warn={20} crit={25} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.airflow}    unit=" m/s" warn={3} crit={3.8} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right' }}><NumCell val={d.inlet_temp} unit="°C" warn={35} crit={45} /></td>
            <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{fmtUptime(d.uptime)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── tabs ──────────────────────────────────────────────────────────────────────

type Tab = 'all' | 'network' | 'server' | 'ups' | 'pdu' | 'sensor'

const TAB_LABELS: Record<Tab, string> = {
  all: 'All', network: 'Network', server: 'Server', ups: 'UPS', pdu: 'PDU', sensor: 'Sensor',
}

const TAB_TYPES: Record<Tab, string[]> = {
  all:     [],
  network: ['router', 'switch', 'firewall', 'load_balancer', 'oob_switch'],
  server:  ['server'],
  ups:     ['ups'],
  pdu:     ['pdu', 'floor_pdu'],
  sensor:  ['sensor'],
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function LiveMetricsPage() {
  const { devices, setActiveView } = useStore()
  const [tab,    setTab]    = useState<Tab>('all')
  const [search, setSearch] = useState('')

  const counts = useMemo((): Record<Tab, number> => ({
    all:     devices.length,
    network: devices.filter(d => TAB_TYPES.network.includes(d.device_type)).length,
    server:  devices.filter(d => TAB_TYPES.server.includes(d.device_type)).length,
    ups:     devices.filter(d => TAB_TYPES.ups.includes(d.device_type)).length,
    pdu:     devices.filter(d => TAB_TYPES.pdu.includes(d.device_type)).length,
    sensor:  devices.filter(d => TAB_TYPES.sensor.includes(d.device_type)).length,
  }), [devices])

  const filtered = useMemo(() => {
    let list = devices
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
  }, [devices, tab, search])

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
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto' }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
            {devices.length === 0 ? 'No topology loaded' : `No ${TAB_LABELS[tab].toLowerCase()} devices`}
          </div>
        ) : tab === 'all'     ? <AllTable     rows={filtered} />
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
