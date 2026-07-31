import { useState, useMemo } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

// Mirrors core/trap_definitions.py SEVERITY_COLOR — see src/theme.ts
import { SEVERITY_COLOR } from '../../theme'

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

// Severity colours are var() references now, so they can no longer be picked
// apart into RGB channels — color-mix does the fade in CSS instead.
function fade(color: string, alpha: number): string {
  return `color-mix(in srgb, ${color} ${Math.round(alpha * 100)}%, transparent)`
}

export default function TrapsPanel() {
  const { snmp, traps, fetchTraps } = useStore()
  const [ip,        setIp]        = useState('127.0.0.1')
  const [port,      setPort]      = useState('162')
  const [applying,  setApplying]  = useState(false)
  const [filterSev, setFilterSev] = useState<string | null>(null)

  const autonomousOn  = snmp?.autonomous_faults ?? false
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

  async function toggleAutonomous() {
    try { await api.setAutonomousFaults(!autonomousOn) }
    catch (e) { console.error(e) }
  }

  // Header badge — rule engine is always on; the badge reflects autonomous mode.
  type BadgeCfg = { cls: string; dot: string; text: string }
  const badge: BadgeCfg = autonomousOn
    ? { cls: 'running', dot: 'green',  text: `Auto faults · ${traps.length}` }
    : { cls: 'ready',   dot: 'grey',   text: `Manual · ${traps.length}` }

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

        {/* ── Autonomous Faults toggle ───────────────────────── */}
        <div className="field-row-split" title={
          autonomousOn
            ? 'Sim spontaneously breaches thresholds (live-monitoring demo)'
            : 'Devices stay healthy — traps fire only on user-injected faults'
        }>
          <span className="label" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <IconEngine />
            Autonomous Faults
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: autonomousOn ? 'var(--green)' : 'var(--text-dim)' }}>
              {autonomousOn ? 'ON' : 'OFF'}
            </span>
            <label className="toggle">
              <input type="checkbox" checked={autonomousOn} onChange={toggleAutonomous} />
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
                  background: active ? SEVERITY_COLOR[sev] : fade(SEVERITY_COLOR[sev], 0.25),
                  border: `1px solid ${active ? SEVERITY_COLOR[sev] : 'transparent'}`,
                  borderRadius: 3, color: 'var(--on-solid)',
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
                const sevColor = SEVERITY_COLOR[sev] || 'var(--text-muted)'
                const bg = fade(sevColor, 0.12)
                return (
                  <tr key={`${t.timestamp}-${i}`} style={{ background: bg }}>
                    <td className="mono dim">{t.timestamp.slice(11, 19)}</td>
                    <td title={t.device_name}>{t.device_name || '—'}</td>
                    <td className="mono">{t.device_ip || '—'}</td>
                    <td>
                      <span style={{
                        display: 'inline-block',
                        background: sevColor, color: 'var(--on-solid)',
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
