import { useState, useMemo } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

// Mirrors core/trap_definitions.py SEVERITY_COLOR
const SEVERITY_COLOR: Record<string, string> = {
  informational: '#2ecc71',
  minor:         '#f39c12',
  major:         '#e67e22',
  critical:      '#e74c3c',
}
const SEVERITY_ORDER = ['informational', 'minor', 'major', 'critical'] as const

const IconApply = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
)
const IconTrash = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
  </svg>
)
const IconEngine = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
)

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export default function TrapsPanel() {
  const { snmp, traps, fetchTraps } = useStore()
  const [ip,        setIp]        = useState('127.0.0.1')
  const [port,      setPort]      = useState('162')
  const [applying,  setApplying]  = useState(false)
  const [filterSev, setFilterSev] = useState<string | null>(null)

  const engineOn      = snmp?.rule_engine_enabled ?? false
  const engineAvail   = snmp?.running             ?? false
  const trapReceiver  = `${snmp?.trap_receiver_ip ?? '—'}:${snmp?.trap_receiver_port ?? '—'}`

  // Severity counts
  const sevCounts = useMemo(() => {
    const c: Record<string, number> = { informational: 0, minor: 0, major: 0, critical: 0 }
    for (const t of traps) {
      const s = t.severity || 'informational'
      c[s] = (c[s] || 0) + 1
    }
    return c
  }, [traps])

  const visible = useMemo(() => {
    const list = filterSev
      ? traps.filter(t => (t.severity || 'informational') === filterSev)
      : traps
    return [...list].reverse().slice(0, 200)
  }, [traps, filterSev])

  async function applyReceiver() {
    setApplying(true)
    try { await api.setTrapReceiver(ip, parseInt(port) || 162) }
    catch (e) { console.error(e) }
    finally { setApplying(false) }
  }

  async function clearTraps() {
    try { await api.clearTraps(); fetchTraps() }
    catch (e) { console.error(e) }
  }

  async function toggleEngine() {
    if (!engineAvail) return
    try {
      if (engineOn) await api.disableEngine()
      else          await api.enableEngine()
    } catch (e) { console.error(e) }
  }

  // Header badge
  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (!engineAvail)   badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }
  else if (engineOn)  badge = { cls: 'running', dot: 'green',  text: `Traps ON · ${traps.length}` }
  else                badge = { cls: 'ready',   dot: 'yellow', text: `Traps OFF · ${traps.length}` }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">SNMP Traps</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12, flexShrink: 0 }}>

        {/* ── Receiver ───────────────────────────────────────── */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Trap Receiver</span>
          <div style={{ display: 'flex', gap: 5 }}>
            <input
              value={ip}
              onChange={e => setIp(e.target.value)}
              placeholder="IP address"
              style={{ flex: 1, minWidth: 0, fontSize: 10, fontFamily: 'Consolas, monospace' }}
            />
            <input
              type="number"
              value={port}
              onChange={e => setPort(e.target.value)}
              placeholder="Port"
              style={{ width: 62, fontSize: 10, fontFamily: 'Consolas, monospace', textAlign: 'right' }}
            />
          </div>
          <button
            className="btn-apply-receiver"
            onClick={applyReceiver}
            disabled={applying}
          >
            <IconApply />
            <span>{applying ? 'Setting…' : 'Set Receiver'}</span>
          </button>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 5,
            fontSize: 9, color: 'var(--text-dim)', marginTop: 6,
            fontFamily: 'Consolas, monospace',
          }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.3px' }}>Active:</span>
            <span style={{ color: 'var(--green)', fontWeight: 600 }}>{trapReceiver}</span>
          </div>
        </div>

        {/* ── Rule Engine toggle ─────────────────────────────── */}
        <div className="field-row-split" title={
          !engineAvail ? 'Start SNMP simulator first'
          : engineOn   ? 'Disable rule-driven trap generation'
          : 'Enable rule-driven trap generation'
        }>
          <span className="label" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <IconEngine />
            Trap Simulation
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: engineOn ? 'var(--green)' : 'var(--text-dim)' }}>
              {engineOn ? 'ON' : 'OFF'}
            </span>
            <label className="toggle" style={{ opacity: engineAvail ? 1 : 0.4, pointerEvents: engineAvail ? 'auto' : 'none' }}>
              <input type="checkbox" checked={engineOn} onChange={toggleEngine} disabled={!engineAvail} />
              <span className="toggle-slider" />
            </label>
          </div>
        </div>

        {/* ── Severity counter badges ────────────────────────── */}
        <div style={{ display: 'flex', gap: 4 }}>
          {SEVERITY_ORDER.map(sev => {
            const c = sevCounts[sev] || 0
            const active = filterSev === sev
            return (
              <button
                key={sev}
                onClick={() => setFilterSev(active ? null : sev)}
                title={`${sev.charAt(0).toUpperCase() + sev.slice(1)} — click to filter`}
                style={{
                  flex: 1, padding: '4px 6px',
                  background: active ? SEVERITY_COLOR[sev] : hexToRgba(SEVERITY_COLOR[sev], 0.25),
                  border: `1px solid ${active ? SEVERITY_COLOR[sev] : 'transparent'}`,
                  borderRadius: 3, color: '#fff',
                  fontSize: 10, fontWeight: 700, lineHeight: 1.2,
                  fontVariantNumeric: 'tabular-nums', cursor: 'pointer',
                  textTransform: 'uppercase', letterSpacing: '0.3px',
                }}
              >
                <div style={{ fontSize: 8, opacity: 0.85 }}>{sev.slice(0, 4)}</div>
                <div>{c}</div>
              </button>
            )
          })}
        </div>

        {/* ── Trap log info + clear ──────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: 10, color: 'var(--text-muted)',
        }}>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {filterSev ? (
              <>
                Showing <span style={{ color: SEVERITY_COLOR[filterSev], fontWeight: 600 }}>
                  {visible.length}
                </span> {filterSev}
                {' · '}
                <button
                  onClick={() => setFilterSev(null)}
                  style={{
                    background: 'transparent', border: 'none', color: 'var(--accent)',
                    cursor: 'pointer', padding: 0, fontSize: 10, textDecoration: 'underline',
                  }}
                >clear filter</button>
              </>
            ) : (
              <>Trap log · <span style={{ color: 'var(--text)' }}>{traps.length}</span></>
            )}
          </span>

          <button
            onClick={clearTraps}
            disabled={traps.length === 0}
            title="Delete all trap history"
            className="btn-clear-traps"
          >
            <IconTrash />
            <span>Clear All</span>
          </button>
        </div>
      </div>

      {/* ── Trap table (5 cols matching desktop) ─────────────── */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 10px 10px', minHeight: 0 }}>
        {visible.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-dim)', fontSize: 10 }}>
            {filterSev ? `No ${filterSev} traps` : 'No traps received'}
          </div>
        ) : (
          <table className="trap-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Device</th>
                <th>IP</th>
                <th>Trap Type</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((t, i) => {
                const sev = t.severity || 'informational'
                const sevColor = SEVERITY_COLOR[sev] || '#888'
                const bg = hexToRgba(sevColor, 0.12)
                return (
                  <tr key={`${t.timestamp}-${i}`} style={{ background: bg }}>
                    <td className="mono dim">{t.timestamp.slice(11, 19)}</td>
                    <td title={t.device_name}>{t.device_name || '—'}</td>
                    <td className="mono">{t.device_ip || '—'}</td>
                    <td>
                      <span style={{
                        display: 'inline-block',
                        background: sevColor, color: '#fff',
                        padding: '1px 6px', borderRadius: 3,
                        fontSize: 9, fontWeight: 700,
                        whiteSpace: 'nowrap',
                      }} title={`${sev} · ${t.trap_type ?? ''}`}>
                        {t.display_name || t.trap_type || '—'}
                      </span>
                    </td>
                    <td title={t.details} className="details">{t.details || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
