import { useState, useEffect } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import type { RedfishDevice, RedfishLogEntry } from '../../api/types'

const IconPlay = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
    <polygon points="6 4 20 12 6 20 6 4" />
  </svg>
)
const IconStop = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
    <rect x="6" y="6" width="12" height="12" rx="1" />
  </svg>
)
const IconEye = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
)
const IconEyeOff = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </svg>
)

function StatRow({ label, value, labelColor, valueColor }: {
  label: string; value: number | string
  labelColor?: string; valueColor?: string
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, lineHeight: 1.85 }}>
      <span style={{ color: labelColor ?? 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: valueColor ?? 'var(--text)' }}>{value}</span>
    </div>
  )
}

function Field({ label, type, value, onChange, disabled, rightSlot }: {
  label: string; type: 'text' | 'number' | 'password'
  value: string | number
  onChange: (v: string) => void
  disabled: boolean
  rightSlot?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
      <span style={{ width: 80, fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', gap: 4,
        background: disabled ? 'var(--bg-base)' : 'var(--bg-card)',
        border: '1px solid var(--border)', borderRadius: 4,
        padding: '0 6px', opacity: disabled ? 0.6 : 1,
      }}>
        <input
          type={type}
          value={value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
          style={{
            flex: 1, minWidth: 0, background: 'transparent', border: 'none',
            outline: 'none', color: 'var(--text)', fontSize: 10,
            fontFamily: 'Consolas, monospace', padding: '4px 0',
          }}
        />
        {rightSlot}
      </div>
    </div>
  )
}

function ServerOps({ d, onChanged }: { d: RedfishDevice; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [logOpen, setLogOpen] = useState(false)
  const [log, setLog] = useState<RedfishLogEntry[]>([])

  async function act(action: string) {
    setBusy(action)
    try { await api.redfishAction(d.ip, action); onChanged() } catch { /* ignore */ }
    finally { setBusy(null) }
  }
  async function toggleLog() {
    if (logOpen) { setLogOpen(false); return }
    try {
      const r = await api.redfishLog(d.ip) as { entries: RedfishLogEntry[] }
      setLog(r.entries || []); setLogOpen(true)
    } catch { /* ignore */ }
  }

  const on = d.power_state === 'On'
  const led = !!d.indicator_led && d.indicator_led !== 'Off'
  const btn = (danger?: boolean): React.CSSProperties => ({
    fontSize: 9, padding: '2px 6px', borderRadius: 3, cursor: 'pointer',
    border: '1px solid var(--border)', background: 'var(--bg-card)',
    color: danger ? 'var(--red)' : 'var(--text)',
  })
  const B = ({ a, label, danger }: { a: string; label: string; danger?: boolean }) => (
    <button onClick={() => act(a)} disabled={!!busy} title={label}
            style={{ ...btn(danger), opacity: busy === a ? 0.4 : 1 }}>{label}</button>
  )

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 4, padding: '5px 6px', marginBottom: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
          background: on ? 'var(--green)' : 'var(--text-muted)',
          boxShadow: on ? '0 0 4px var(--green)' : 'none' }} />
        <span style={{ fontSize: 10, color: 'var(--text)', fontWeight: 600 }}>{d.name}</span>
        <span style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'Consolas, monospace' }}>{d.ip}:{d.port}</span>
        <span style={{ marginLeft: 'auto', fontSize: 9, color: led ? '#f0c000' : 'var(--text-muted)' }}>
          {led ? '◉ LED' : '○ LED'}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
        <B a="power_on" label="On" />
        <B a="power_off" label="Off" />
        <B a="reboot" label="Reboot" />
        <B a="power_cycle" label="Cycle" />
        <B a={led ? 'led_off' : 'led_on'} label={led ? 'LED Off' : 'LED On'} />
        <B a="refresh" label="Refresh" />
        <button onClick={toggleLog} style={btn()}>
          Log{typeof d.sel_count === 'number' ? ` (${d.sel_count})` : ''}
        </button>
        <B a="clear_log" label="Clear" danger />
      </div>
      {logOpen && (
        <div style={{ marginTop: 4, maxHeight: 120, overflowY: 'auto', background: 'var(--bg-base)',
          border: '1px solid var(--border)', borderRadius: 3, padding: '4px 6px' }}>
          {log.length === 0
            ? <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>(empty)</div>
            : log.map(e => (
              <div key={e.Id} style={{ fontSize: 9, fontFamily: 'Consolas, monospace',
                color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                <span style={{ color: e.Severity === 'Critical' ? 'var(--red)'
                  : e.Severity === 'Warning' ? '#f0c000' : 'var(--green)' }}>{e.Severity}</span>{' '}
                <span style={{ color: 'var(--text)' }}>{e.Message}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

// Sync the port field from a running server once per page load (not per mount).
let _portSyncedFromServer = false

export default function RedfishPanel() {
  const {
    devices, binding, redfish: status, fetchRedfish,
    redfishPort: port, redfishUsername: username, redfishPassword: password,
    setRedfishPort: setPort, setRedfishUsername: setUsername,
    setRedfishPassword: setPassword,
  } = useStore()
  const [busy,      setBusy]      = useState(false)
  const [operation, setOperation] = useState<'start' | 'stop' | null>(null)
  const [showPass,  setShowPass]  = useState(false)

  const running = status?.running ?? false

  // Redfish runs on server BMCs only
  const serverCount = devices.filter(d => d.device_type === 'server').length

  useEffect(() => { fetchRedfish() }, [])
  useEffect(() => {
    if (!_portSyncedFromServer && status?.running) {
      setPort(status.port)
      _portSyncedFromServer = true
    }
  }, [status])

  async function start() {
    setBusy(true); setOperation('start')
    try {
      await api.redfishStart({ port, username, password })
      await fetchRedfish()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function stop() {
    setBusy(true); setOperation('stop')
    try { await api.redfishStop(); await fetchRedfish() }
    catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (operation === 'start')      badge = { cls: 'ready',   dot: 'yellow', text: 'Starting' }
  else if (operation === 'stop')  badge = { cls: 'ready',   dot: 'yellow', text: 'Stopping' }
  else if (running)               badge = { cls: 'running', dot: 'green',  text: 'Running' }
  else                            badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }

  const validPort = port >= 1 && port <= 65535
  const validUser = username.trim().length > 0
  const validPass = password.length > 0
  const ipsBound  = (binding?.bound_count ?? 0) > 0
  const canStart  = !busy && !running && validPort && validUser && validPass && serverCount > 0 && ipsBound

  const startTip = serverCount === 0 ? 'No servers in topology'
                 : !ipsBound         ? 'Bind IPs first (Binding panel → Bind IPs)'
                 : !validPort        ? 'Port out of range (1-65535)'
                 : !validUser        ? 'Username required'
                 : !validPass        ? 'Password required'
                 : 'Start Redfish/BMC on all servers'

  const bmcs = status?.devices ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">Redfish Simulator</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', padding: '12px 10px 0',
                    display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* ── BMC Service ────────────────────────────────────── */}
        <div className="group-box" style={{ marginTop: 6, flexShrink: 0 }}>
          <span className="group-box-label">BMC Service</span>
          <Field label="HTTP Port" type="number" value={port}
                 onChange={v => setPort(parseInt(v) || 443)} disabled={running || busy} />
          <Field label="Username" type="text" value={username}
                 onChange={setUsername} disabled={running || busy} />
          <Field label="Password" type={showPass ? 'text' : 'password'} value={password}
                 onChange={setPassword} disabled={running || busy}
                 rightSlot={
                   <button
                     type="button"
                     onClick={() => setShowPass(v => !v)}
                     title={showPass ? 'Hide password' : 'Show password'}
                     style={{
                       display: 'flex', alignItems: 'center', justifyContent: 'center',
                       background: 'transparent', border: 'none', cursor: 'pointer',
                       padding: 0, color: showPass ? 'var(--accent)' : 'var(--text-muted)',
                       flexShrink: 0,
                     }}
                   >
                     {showPass ? <IconEyeOff /> : <IconEye />}
                   </button>
                 } />
        </div>

        {/* ── Targets (idle/ready) ───────────────────────────── */}
        {!running && (
          <div className="group-box" style={{ marginTop: 6, flexShrink: 0 }}>
            <span className="group-box-label">Targets</span>
            <StatRow label="Servers:" value={serverCount} labelColor="#a371f7" valueColor="#a371f7" />
          </div>
        )}

        {/* ── Active BMCs (when running) — only this list scrolls ── */}
        {running && (
          <div className="group-box" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <span className="group-box-label">Active BMCs</span>
            <StatRow label="Endpoints:" value={status?.active_devices ?? 0} labelColor="#a371f7" valueColor="#a371f7" />
            <StatRow label="Sessions:"  value={status?.sessions ?? 0} />
            <div style={{ marginTop: 6, flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 2 }}>
              {bmcs.map(d => (
                <ServerOps key={d.ip} d={d} onChanged={() => fetchRedfish()} />
              ))}
            </div>
          </div>
        )}

        {/* ── Endpoint hint ──────────────────────────────────── */}
        {running && (
          <div style={{
            fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6, flexShrink: 0,
            padding: '6px 8px', background: 'var(--bg-base)',
            border: '1px solid var(--border)', borderRadius: 4,
            fontFamily: 'Consolas, monospace',
          }}>
            <span style={{ color: 'var(--green)' }}>→</span>{' '}
            <span style={{ color: 'var(--text)' }}>http://&lt;server-ip&gt;:{status?.port ?? port}/redfish/v1/</span>
          </div>
        )}

      </div>

      {/* ── Pinned footer: Start/Stop always visible ─────────── */}
      <div style={{ flexShrink: 0, padding: '8px 10px 12px',
                    borderTop: '1px solid var(--border)' }}>
        <div className="snmp-actions">
          {running ? (
            <button className="btn-action btn-stop" onClick={stop} disabled={busy} title="Stop Redfish">
              <IconStop />
              <span>Stop Redfish</span>
            </button>
          ) : (
            <button className="btn-action btn-start" onClick={start} disabled={!canStart} title={startTip}>
              <IconPlay />
              <span>Start Redfish</span>
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
