import { useState, useEffect, useRef } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

interface SFlowStatus {
  running: boolean
  collector_ip: string
  collector_port: number
  interval: number
  sample_rate: number
  active_devices: number
}

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

function Field({ label, suffix, prefix, type, value, onChange, disabled }: {
  label: string; suffix?: string; prefix?: string; type: 'text' | 'number'
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
        {prefix && <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'Consolas, monospace' }}>{prefix}</span>}
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
        {suffix && <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{suffix}</span>}
      </div>
    </div>
  )
}

export default function SFlowPanel() {
  const { devices } = useStore()
  const [status,       setStatus]       = useState<SFlowStatus | null>(null)
  const [busy,         setBusy]         = useState(false)
  const [operation,    setOperation]    = useState<'start' | 'stop' | null>(null)
  const [ip,           setIp]           = useState('127.0.0.1')
  const [port,         setPort]         = useState(6343)
  const [pollInterval, setPollInterval] = useState(30)
  const [rate,         setRate]         = useState(1000)

  const running = status?.running ?? false
  const configLoaded = useRef(false)

  // sFlow runs only on network devices (switches + routers)
  const tc: Record<string, number> = {}
  for (const d of devices) tc[d.device_type] = (tc[d.device_type] || 0) + 1
  const total = (tc['switch'] ?? 0) + (tc['router'] ?? 0)

  function fetchStatus(syncForm = false) {
    api.sflowStatus()
      .then(d => {
        const s = d as SFlowStatus
        setStatus(s)
        if (syncForm) {
          setIp(s.collector_ip)
          setPort(s.collector_port)
          setPollInterval(s.interval)
          setRate(s.sample_rate)
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
      await api.sflowStart({ collector_ip: ip, collector_port: port, interval: pollInterval, sample_rate: rate })
      fetchStatus()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function stop() {
    setBusy(true); setOperation('stop')
    try { await api.sflowStop(); fetchStatus() }
    catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  // Header badge
  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (operation === 'start')      badge = { cls: 'ready',   dot: 'yellow', text: 'Starting' }
  else if (operation === 'stop')  badge = { cls: 'ready',   dot: 'yellow', text: 'Stopping' }
  else if (running)               badge = { cls: 'running', dot: 'green',  text: 'Running' }
  else                            badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }

  // Form validation
  const validIp   = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(ip)
  const validPort = port >= 1 && port <= 65535
  const validInt  = pollInterval >= 5 && pollInterval <= 3600
  const validRate = rate >= 1 && rate <= 65535
  const canStart  = !busy && !running && validIp && validPort && validInt && validRate

  const startTip = !validIp   ? 'Collector IP invalid'
                 : !validPort ? 'UDP port out of range (1-65535)'
                 : !validInt  ? 'Interval must be 5-3600 seconds'
                 : !validRate ? 'Sample rate must be 1-65535'
                 : 'Start sFlow agent on all devices'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">sFlow Simulator</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* ── Collector ──────────────────────────────────────── */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Collector</span>

          <Field
            label="Collector IP"
            type="text"
            value={ip}
            onChange={setIp}
            disabled={running || busy}
          />
          <Field
            label="UDP Port"
            type="number"
            value={port}
            onChange={v => setPort(parseInt(v) || 6343)}
            disabled={running || busy}
          />
          <Field
            label="Interval"
            type="number"
            value={pollInterval}
            onChange={v => setPollInterval(parseInt(v) || 30)}
            disabled={running || busy}
            suffix="sec"
          />
          <Field
            label="Sample Rate"
            type="number"
            value={rate}
            onChange={v => setRate(parseInt(v) || 1000)}
            disabled={running || busy}
            prefix="1 in"
          />
        </div>

        {/* ── Targets (idle/ready) ───────────────────────────── */}
        {!running && total > 0 && (
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Targets</span>
            {(tc['switch'] ?? 0) > 0 && <StatRow label="Switches:" value={tc['switch'] ?? 0} />}
            {(tc['router'] ?? 0) > 0 && <StatRow label="Routers:"  value={tc['router'] ?? 0} />}
            <StatRow label="Total:" value={total} labelColor="#06b6d4" valueColor="#06b6d4" />
          </div>
        )}

        {/* ── Active Devices (when running) ──────────────────── */}
        {running && (
          <div className="group-box">
            <span className="group-box-label">Active Devices</span>
            {(tc['switch'] ?? 0) > 0 && <StatRow label="Switches:" value={tc['switch'] ?? 0} />}
            {(tc['router'] ?? 0) > 0 && <StatRow label="Routers:"  value={tc['router'] ?? 0} />}
            <StatRow
              label="Total Exporters:"
              value={status?.active_devices ?? total}
              labelColor="#06b6d4"
              valueColor="#06b6d4"
            />
          </div>
        )}

        {/* ── Export summary ─────────────────────────────────── */}
        {running && status && (
          <div style={{
            fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6,
            padding: '6px 8px', background: 'var(--bg-base)',
            border: '1px solid var(--border)', borderRadius: 4,
            fontFamily: 'Consolas, monospace',
          }}>
            <span style={{ color: 'var(--green)' }}>→</span>{' '}
            <span style={{ color: 'var(--text)' }}>{status.collector_ip}:{status.collector_port}</span>
            {' · every '}
            <span style={{ color: 'var(--text)' }}>{status.interval}s</span>
            {' · 1:'}
            <span style={{ color: 'var(--text)' }}>{status.sample_rate}</span>
          </div>
        )}

        {/* ── Actions ────────────────────────────────────────── */}
        <div className="snmp-actions">
          {running ? (
            <button
              className="btn-action btn-stop"
              onClick={stop}
              disabled={busy}
              title="Stop sFlow agent"
            >
              <IconStop />
              <span>Stop sFlow</span>
            </button>
          ) : (
            <button
              className="btn-action btn-start"
              onClick={start}
              disabled={!canStart}
              title={startTip}
            >
              <IconPlay />
              <span>Start sFlow</span>
            </button>
          )}
        </div>

      </div>
    </div>
  )
}
