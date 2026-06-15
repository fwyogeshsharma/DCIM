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

// ── Server Operations control cluster ──────────────────────────────────
// 4×2 grid of fixed-position, tone-coded buttons. Power verbs disable when
// they would be a no-op for the current chassis state, the busy action gets
// an inline spinner, and the LED/Log buttons reflect their toggled state.

const _ico = { width: 11, height: 11, viewBox: '0 0 24 24', fill: 'none' as const,
  stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const, style: { flexShrink: 0 } }

const IconPwr = () => (
  <svg {..._ico}>
    <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
    <line x1="12" y1="2" x2="12" y2="11" />
  </svg>
)
const IconCycleArrow = ({ ccw }: { ccw?: boolean }) => (
  <svg {..._ico} style={{ ..._ico.style, transform: ccw ? 'scaleX(-1)' : undefined }}>
    <polyline points="23 4 23 10 17 10" />
    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
  </svg>
)
const IconBulb = ({ lit }: { lit?: boolean }) => (
  <svg {..._ico} style={{ ..._ico.style,
    filter: lit ? 'drop-shadow(0 0 2px currentColor)' : undefined }}>
    {/* light rays — only when lit */}
    {lit && <>
      <line x1="4.2" y1="4.2" x2="2.4" y2="2.4" />
      <line x1="19.8" y1="4.2" x2="21.6" y2="2.4" />
    </>}
    <path d="M9 18h6M10 22h4" />
    <path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.4 1 2.3h6c0-.9.4-1.8 1-2.3A7 7 0 0 0 12 2z"
          fill={lit ? 'currentColor' : 'none'} />
  </svg>
)
const IconSync = () => (
  <svg {..._ico}>
    <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
    <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
    <path d="M16 16h5v5" />
  </svg>
)
const IconLines = () => (
  <svg {..._ico}>
    <line x1="4" y1="6" x2="20" y2="6" />
    <line x1="4" y1="12" x2="20" y2="12" />
    <line x1="4" y1="18" x2="14" y2="18" />
  </svg>
)
const IconBin = () => (
  <svg {..._ico}>
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    <path d="M10 11v6M14 11v6" />
  </svg>
)

type OpTone = 'ok' | 'danger' | 'warn' | 'neutral'

const OP_TONE: Record<OpTone, { fg: string; bg: string; border: string }> = {
  ok:      { fg: 'var(--green)', bg: 'rgba(63,185,80,0.10)',  border: 'rgba(63,185,80,0.45)' },
  danger:  { fg: 'var(--red)',   bg: 'rgba(248,81,73,0.10)',  border: 'rgba(248,81,73,0.45)' },
  warn:    { fg: '#d29922',      bg: 'rgba(210,153,34,0.10)', border: 'rgba(210,153,34,0.45)' },
  neutral: { fg: 'var(--text)',  bg: 'rgba(255,255,255,0.05)', border: 'var(--text-muted)' },
}

function OpBtn({ icon, label, tone = 'neutral', onClick, disabled, busy, active, title }: {
  icon: React.ReactNode
  label: string
  tone?: OpTone
  onClick: () => void
  disabled?: boolean
  busy?: boolean
  active?: boolean
  title?: string
}) {
  const [hov, setHov] = useState(false)
  const t = OP_TONE[tone]
  const dim = !!disabled && !busy
  const lit = active || (hov && !dim)
  return (
    <button
      onClick={onClick} disabled={disabled} title={title ?? label}
      onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
        height: 26, minWidth: 0, padding: '0 3px',
        borderRadius: 4,
        cursor: dim ? 'default' : 'pointer',
        border: `1px solid ${lit ? t.border : 'var(--border)'}`,
        background: lit ? t.bg : 'var(--bg-card)',
        color: dim ? 'var(--text-dim)' : t.fg,
        opacity: dim ? 0.45 : 1,
        fontSize: 9, fontWeight: 600, letterSpacing: '0.2px',
        transition: 'background 0.12s, border-color 0.12s, color 0.12s',
        boxShadow: active ? `inset 0 0 6px ${t.bg}` : 'none',
      }}
    >
      {busy
        ? <span style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
            border: '2px solid currentColor', borderTopColor: 'transparent',
            animation: 'spin 0.7s linear infinite' }} />
        : icon}
      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
    </button>
  )
}

function ServerOps({ d, onChanged }: { d: RedfishDevice; onChanged: () => void | Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [logOpen, setLogOpen] = useState(false)
  const [log, setLog] = useState<RedfishLogEntry[]>([])
  // Authoritative state from the last action RESPONSE — the action endpoint
  // returns fresh power/LED/SEL values, so the icon flips the instant the
  // spinner stops instead of waiting for the next status poll. Cleared once
  // the store row catches up (any update from poll/SSE drops the override).
  const [ov, setOv] = useState<Partial<RedfishDevice> | null>(null)
  useEffect(() => { setOv(null) },
    [d.power_state, d.indicator_led, d.sel_count])

  async function act(action: string) {
    setBusy(action)
    try {
      const res = await api.redfishAction(d.ip, action) as Partial<RedfishDevice>
      setOv({ power_state: res.power_state, indicator_led: res.indicator_led,
              sel_count: res.sel_count })
      onChanged()   // background store refresh; rendering no longer waits on it
    } catch { /* ignore */ }
    finally { setBusy(null) }
  }
  async function toggleLog() {
    if (logOpen) { setLogOpen(false); return }
    try {
      const r = await api.redfishLog(d.ip) as { entries: RedfishLogEntry[] }
      setLog(r.entries || []); setLogOpen(true)
    } catch { /* ignore */ }
  }

  const eff = ov ? { ...d, ...ov } : d
  const on = eff.power_state === 'On'
  const led = !!eff.indicator_led && eff.indicator_led !== 'Off'

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 4, padding: '5px 6px', marginBottom: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
          background: on ? 'var(--green)' : 'var(--text-muted)',
          boxShadow: on ? '0 0 4px var(--green)' : 'none' }} />
        <span style={{ fontSize: 10, color: 'var(--text)', fontWeight: 600 }}>{d.name}</span>
        <span style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'Consolas, monospace' }}>{d.ip}:{d.port}</span>
        <span style={{ marginLeft: 'auto', fontSize: 9, fontWeight: 600,
          color: on ? 'var(--green)' : 'var(--text-muted)', letterSpacing: '0.4px' }}>
          {on ? 'ON' : 'OFF'}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 3 }}>
        <OpBtn icon={<IconPwr />} label="On" tone="ok"
               disabled={!!busy || on} busy={busy === 'power_on'}
               onClick={() => act('power_on')}
               title={on ? 'Chassis is already on' : 'Power on'} />
        <OpBtn icon={<IconPwr />} label="Off" tone="danger"
               disabled={!!busy || !on} busy={busy === 'power_off'}
               onClick={() => act('power_off')}
               title={!on ? 'Chassis is already off' : 'Graceful shutdown'} />
        <OpBtn icon={<IconCycleArrow />} label="Reboot" tone="warn"
               disabled={!!busy || !on} busy={busy === 'reboot'}
               onClick={() => act('reboot')}
               title={!on ? 'Power on first' : 'Graceful restart'} />
        <OpBtn icon={<IconCycleArrow ccw />} label="Cycle" tone="warn"
               disabled={!!busy || !on} busy={busy === 'power_cycle'}
               onClick={() => act('power_cycle')}
               title={!on ? 'Power on first' : 'Hard power cycle'} />
        <OpBtn icon={<IconBulb lit={led} />} label="LED" tone="warn" active={led}
               disabled={!!busy} busy={busy === 'led_on' || busy === 'led_off'}
               onClick={() => act(led ? 'led_off' : 'led_on')}
               title={led ? 'Turn identify LED off' : 'Light identify LED'} />
        <OpBtn icon={<IconSync />} label="Sync"
               disabled={!!busy} busy={busy === 'refresh'}
               onClick={() => act('refresh')}
               title="Refresh BMC inventory" />
        <OpBtn icon={<IconLines />} active={logOpen}
               label={typeof eff.sel_count === 'number' ? `Log·${eff.sel_count}` : 'Log'}
               onClick={toggleLog}
               title={logOpen ? 'Hide event log' : 'Show event log (SEL)'} />
        <OpBtn icon={<IconBin />} label="Clear" tone="danger"
               disabled={!!busy} busy={busy === 'clear_log'}
               onClick={() => act('clear_log')}
               title="Clear the event log" />
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
    redfishBusy: busy, redfishOp: operation, startRedfish: start, stopRedfish: stop,
  } = useStore()
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

        {/* ── Start button sits just below Targets while idle ──── */}
        {!running && (
          <div className="snmp-actions" style={{ flexShrink: 0 }}>
            <button className="btn-action btn-start" onClick={start} disabled={!canStart} title={startTip}>
              <IconPlay />
              <span>Start Redfish</span>
            </button>
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

      {/* ── Pinned footer: Stop sticks to the bottom while running ── */}
      {running && (
        <div style={{ flexShrink: 0, padding: '8px 10px 12px',
                      borderTop: '1px solid var(--border)' }}>
          <div className="snmp-actions">
            <button className="btn-action btn-stop" onClick={stop} disabled={busy} title="Stop Redfish">
              <IconStop />
              <span>Stop Redfish</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
