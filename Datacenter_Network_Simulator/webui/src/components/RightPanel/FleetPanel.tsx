import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'

// ── Types ────────────────────────────────────────────────────────────────────

interface FleetConfig {
  minutes_per_day:      number
  provision_lambda:     number
  decommission_lambda:  number
  power_cap:            number
  max_racks_per_row:    number
  compute_rows_per_room: number
  max_total_servers:    number
}
interface DayLog {
  day: number; added: string[]; removed: string[]; expanded_racks: string[]; total_servers: number
}
interface FleetStatus {
  enabled: boolean
  day: number
  config: FleetConfig
  total_servers: number
  history: DayLog[]
}

const CFG_FIELDS: { key: keyof FleetConfig; label: string; step?: number; hint: string }[] = [
  { key: 'minutes_per_day',      label: 'Minutes / day',      step: 0.5, hint: 'wall-clock minutes that equal one sim-day' },
  { key: 'provision_lambda',     label: 'Provision / day',    hint: 'avg servers added on a normal day' },
  { key: 'decommission_lambda',  label: 'Decommission / day', hint: 'avg servers removed (kept below provision = net growth)' },
  { key: 'power_cap',            label: 'Power cap / rack',   hint: 'per-rack server cap (binds with leaf downlink ports); existing racks fill to min(downlinks, this) before any new rack opens' },
  { key: 'max_racks_per_row',    label: 'Racks / row (new hall)', hint: 'row width of a newly-opened hall; existing halls are never enlarged' },
  { key: 'compute_rows_per_room', label: 'Rows / new hall',   hint: 'compute rows per new hall; grid = racks/row × this, then the next hall opens' },
  { key: 'max_total_servers',    label: 'Max total servers',  hint: 'global ceiling — provisioning pauses here' },
]

// ── Component ────────────────────────────────────────────────────────────────

export default function FleetPanel() {
  const [status, setStatus] = useState<FleetStatus | null>(null)
  const [cfg, setCfg]       = useState<FleetConfig | null>(null)
  const [busy, setBusy]     = useState<string | null>(null)
  const [err, setErr]       = useState('')
  const seeded = useRef(false)

  const refresh = useCallback(() => {
    api.fleetStatus()
      .then(s => {
        const st = s as FleetStatus
        setStatus(st)
        if (!seeded.current) { setCfg(st.config); seeded.current = true }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name); setErr('')
    try { await fn(); refresh() }
    catch (e) { setErr(String(e)) }
    finally { setBusy(null) }
  }

  const running = status?.enabled ?? false
  const setField = (k: keyof FleetConfig, v: number) => setCfg(c => c ? { ...c, [k]: v } : c)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

      {/* Header */}
      <div className="panel-header">
        <span className="title">Fleet Lifecycle</span>
        <span className={`badge ${running ? 'running' : 'stopped'}`}>
          <span className={`status-dot ${running ? 'green' : 'grey'}`} />
          {running ? 'Running' : 'Idle'}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* Day + fleet size */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Status</span>
          <div className="field-row-split"><span className="label">Sim-day</span>
            <span style={{ fontFamily: 'monospace', fontWeight: 700, color: 'var(--text)' }}>{status?.day ?? 0}</span>
          </div>
          <div className="field-row-split"><span className="label">Servers</span>
            <span style={{ fontFamily: 'monospace', fontWeight: 700, color: 'var(--accent)' }}>{status?.total_servers ?? 0}</span>
          </div>
        </div>

        {/* Scheduler controls */}
        <div className="group-box">
          <span className="group-box-label">Scheduler</span>
          <div className="snmp-actions" style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <button
              className={`btn-action ${running ? 'btn-stop' : 'btn-start'}`}
              disabled={busy !== null}
              onClick={() => running
                ? act('stop', () => api.fleetStop())
                : act('start', () => api.fleetStart(cfg ?? {}))}
              style={{ flex: 1 }}
            >{busy === 'start' || busy === 'stop' ? '…' : running ? 'Stop' : 'Start'}</button>
            <button
              className="btn-action"
              disabled={busy !== null}
              onClick={() => act('advance', () => api.fleetAdvance())}
              title="Apply exactly one sim-day of churn now"
              style={{ flex: 1, border: '1px solid var(--border)' }}
            >{busy === 'advance' ? '…' : 'Advance Day'}</button>
          </div>
          <button
            className="btn-action"
            disabled={busy !== null}
            onClick={() => act('snmp', () => api.snmpReload())}
            title="Restart snmpsim so churned devices become SNMP-pollable (briefly drops existing agents)"
            style={{ width: '100%', border: '1px solid var(--border)' }}
          >{busy === 'snmp' ? '…' : 'Reload SNMP (serve new devices)'}</button>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', lineHeight: 1.5, marginTop: 6 }}>
            Adds/removes servers in-memory; the topology file is untouched. gNMI/Redfish hot-commission automatically;
            SNMP-poll needs this reload (snmpsim re-reads its data-dir on restart).
          </div>
        </div>

        {/* Config */}
        {cfg && (
          <div className="group-box">
            <span className="group-box-label">Cadence &amp; Caps</span>
            {CFG_FIELDS.map(f => (
              <div key={f.key} className="field-row-split" title={f.hint} style={{ marginBottom: 5 }}>
                <span className="label">{f.label}</span>
                <input
                  type="number" min={0} step={f.step ?? 1}
                  value={cfg[f.key]}
                  onChange={e => setField(f.key, parseFloat(e.target.value) || 0)}
                  style={{
                    width: 72, background: '#0d1117', border: '1px solid var(--border)', borderRadius: 4,
                    color: 'var(--text)', fontSize: 12, fontFamily: 'monospace', padding: '3px 7px', outline: 'none',
                  }}
                />
              </div>
            ))}
            <button
              className="btn-action"
              disabled={busy !== null}
              onClick={() => act('config', () => api.fleetConfig(cfg))}
              style={{ width: '100%', marginTop: 6, border: '1px solid var(--border)' }}
            >{busy === 'config' ? '…' : 'Apply Config'}</button>
            <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 4 }}>
              Interval change takes effect after the current day's wait.
            </div>
          </div>
        )}

        {/* Day log */}
        <div className="group-box">
          <span className="group-box-label">Activity</span>
          {(!status || status.history.length === 0) && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>No days elapsed yet.</div>
          )}
          {status && [...status.history].reverse().map(d => (
            <div key={d.day} style={{
              display: 'flex', alignItems: 'baseline', gap: 8, padding: '3px 0',
              fontSize: 11, fontFamily: 'monospace', borderBottom: '1px solid var(--border)',
            }}>
              <span style={{ color: 'var(--text-dim)', width: 38 }}>D{d.day}</span>
              <span style={{ color: '#3fb950' }}>+{d.added.length}</span>
              <span style={{ color: '#f87171' }}>-{d.removed.length}</span>
              {d.expanded_racks.length > 0 &&
                <span style={{ color: '#d29922' }} title={d.expanded_racks.join(', ')}>+{d.expanded_racks.length} rack</span>}
              <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>{d.total_servers} srv</span>
            </div>
          ))}
        </div>

        {err && <div style={{ color: 'var(--red)', fontSize: 10 }}>{err}</div>}
      </div>
    </div>
  )
}
