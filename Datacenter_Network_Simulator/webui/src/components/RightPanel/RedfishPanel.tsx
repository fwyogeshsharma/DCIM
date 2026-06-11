import { useState, useEffect, useRef } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import type { RedfishStatus } from '../../api/types'

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

function Field({ label, type, value, onChange, disabled }: {
  label: string; type: 'text' | 'number' | 'password'
  value: string | number
  onChange: (v: string) => void
  disabled: boolean
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
      </div>
    </div>
  )
}

export default function RedfishPanel() {
  const { devices } = useStore()
  const [status,    setStatus]    = useState<RedfishStatus | null>(null)
  const [busy,      setBusy]      = useState(false)
  const [operation, setOperation] = useState<'start' | 'stop' | null>(null)
  const [port,      setPort]      = useState(443)
  const [username,  setUsername]  = useState('admin')
  const [password,  setPassword]  = useState('password')

  const running = status?.running ?? false
  const configLoaded = useRef(false)

  // Redfish runs on server BMCs only
  const serverCount = devices.filter(d => d.device_type === 'server').length

  function fetchStatus(syncForm = false) {
    api.redfishStatus()
      .then(d => {
        const s = d as RedfishStatus
        setStatus(s)
        if (syncForm) {
          setPort(s.port)
          configLoaded.current = true
        }
      })
      .catch(() => {})
  }

  useEffect(() => { fetchStatus(!configLoaded.current) }, [])
  useEffect(() => {
    const t = window.setInterval(() => fetchStatus(false), 4000)
    return () => window.clearInterval(t)
  }, [running])

  async function start() {
    setBusy(true); setOperation('start')
    try {
      await api.redfishStart({ port, username, password })
      fetchStatus()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function stop() {
    setBusy(true); setOperation('stop')
    try { await api.redfishStop(); fetchStatus() }
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
  const canStart  = !busy && !running && validPort && validUser && validPass && serverCount > 0

  const startTip = serverCount === 0 ? 'No servers in topology'
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

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* ── BMC Service ────────────────────────────────────── */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">BMC Service</span>
          <Field label="HTTP Port" type="number" value={port}
                 onChange={v => setPort(parseInt(v) || 443)} disabled={running || busy} />
          <Field label="Username" type="text" value={username}
                 onChange={setUsername} disabled={running || busy} />
          <Field label="Password" type="password" value={password}
                 onChange={setPassword} disabled={running || busy} />
        </div>

        {/* ── Targets (idle/ready) ───────────────────────────── */}
        {!running && (
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Targets</span>
            <StatRow label="Servers:" value={serverCount} labelColor="#a371f7" valueColor="#a371f7" />
          </div>
        )}

        {/* ── Active BMCs (when running) ─────────────────────── */}
        {running && (
          <div className="group-box">
            <span className="group-box-label">Active BMCs</span>
            <StatRow label="Endpoints:" value={status?.active_devices ?? 0} labelColor="#a371f7" valueColor="#a371f7" />
            <StatRow label="Sessions:"  value={status?.sessions ?? 0} />
            <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
              {bmcs.slice(0, 12).map(d => (
                <div key={d.ip} style={{
                  fontSize: 9.5, fontFamily: 'Consolas, monospace',
                  color: 'var(--text-muted)', whiteSpace: 'nowrap',
                  overflow: 'hidden', textOverflow: 'ellipsis',
                }} title={d.url}>
                  <span style={{ color: 'var(--text)' }}>{d.name}</span>
                  {' · '}{d.ip}:{d.port}
                </div>
              ))}
              {bmcs.length > 12 && (
                <div style={{ fontSize: 9.5, color: 'var(--text-muted)' }}>
                  +{bmcs.length - 12} more…
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Endpoint hint ──────────────────────────────────── */}
        {running && (
          <div style={{
            fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6,
            padding: '6px 8px', background: 'var(--bg-base)',
            border: '1px solid var(--border)', borderRadius: 4,
            fontFamily: 'Consolas, monospace',
          }}>
            <span style={{ color: 'var(--green)' }}>→</span>{' '}
            <span style={{ color: 'var(--text)' }}>http://&lt;server-ip&gt;:{status?.port ?? port}/redfish/v1/</span>
          </div>
        )}

        {/* ── Actions ────────────────────────────────────────── */}
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
