import { useState, useEffect, useRef } from 'react'
import { api, errorMessage } from '../../api/client'
import type { BacnetStatus, BacnetDevice } from '../../api/types'

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

function Field({ label, suffix, type, value, onChange, disabled, min, max }: {
  label: string; suffix?: string; type: 'text' | 'number'
  value: string | number
  onChange: (v: string) => void
  disabled: boolean
  min?: number; max?: number
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
      <span style={{ width: 90, fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', gap: 4,
        background: disabled ? 'var(--bg-base)' : 'var(--bg-card)',
        border: '1px solid var(--border)', borderRadius: 4,
        padding: '0 6px', opacity: disabled ? 0.6 : 1,
      }}>
        <input
          type={type}
          value={value}
          min={min}
          max={max}
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

function Select({ label, value, onChange, disabled, options }: {
  label: string
  value: number
  onChange: (v: number) => void
  disabled: boolean
  options: { label: string; value: number }[]
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
      <span style={{ width: 90, fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
      <select
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        disabled={disabled}
        style={{
          flex: 1, background: disabled ? 'var(--bg-base)' : 'var(--bg-card)',
          border: '1px solid var(--border)', borderRadius: 4,
          color: 'var(--text)', fontSize: 10, padding: '4px 6px',
          outline: 'none', opacity: disabled ? 0.6 : 1,
        }}
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}

// Map a device `kind` to a human-friendly type label.
function typeLabel(kind?: string): string {
  if (!kind || kind === 'ev2') return 'Verdigris EV2'
  if (kind.startsWith('plant:')) return kind.slice('plant:'.length)
  return kind
}

function DeviceCounts({ devices }: { devices: BacnetDevice[] }) {
  // Count active devices grouped by type label, preserving first-seen order.
  const order: string[] = []
  const counts = new Map<string, number>()
  let total = 0
  for (const d of devices) {
    if (d.status !== 'Active') continue
    const label = typeLabel(d.kind)
    if (!counts.has(label)) order.push(label)
    counts.set(label, (counts.get(label) ?? 0) + 1)
    total++
  }

  if (order.length === 0) {
    return (
      <div style={{ padding: 10, textAlign: 'center', color: 'var(--text-dim)', fontSize: 10 }}>
        No active devices
      </div>
    )
  }

  return (
    <>
      {order.map(label => (
        <StatRow key={label} label={`${label}:`} value={counts.get(label) ?? 0} />
      ))}
      <div style={{ height: 4 }} />
      <StatRow label="Total:" value={total} labelColor="#06b6d4" valueColor="#06b6d4" />
    </>
  )
}

const FREQ_OPTIONS = [
  { label: '50 Hz (EU/Asia)', value: 50.0 },
  { label: '60 Hz (US/CA)',   value: 60.0 },
]

export default function BACnetPanel() {
  const [status,       setStatus]       = useState<BacnetStatus | null>(null)
  const [busy,         setBusy]         = useState(false)
  const [operation,    setOperation]    = useState<'start' | 'stop' | null>(null)
  const [baseInstance, setBaseInstance] = useState(40001)
  const [freqHz,       setFreqHz]       = useState(50.0)
  const [port,         setPort]         = useState(47808)
  const [error,        setError]        = useState<string | null>(null)

  const running      = status?.running ?? false
  const configLoaded = useRef(false)

  function fetchStatus(syncForm = false) {
    api.bacnetStatus()
      .then(d => {
        const s = d as BacnetStatus
        setStatus(s)
        if (syncForm && !configLoaded.current) {
          setBaseInstance(s.base_instance)
          setFreqHz(s.frequency_hz)
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
    setBusy(true); setOperation('start'); setError(null)
    try {
      await api.bacnetStart({ base_instance: baseInstance, frequency_hz: freqHz, port })
      fetchStatus()
    } catch (e: unknown) {
      setError(errorMessage(e))
    }
    finally { setBusy(false); setOperation(null) }
  }

  async function stop() {
    setBusy(true); setOperation('stop'); setError(null)
    try { await api.bacnetStop(); fetchStatus() }
    catch (e: unknown) {
      setError(errorMessage(e))
    }
    finally { setBusy(false); setOperation(null) }
  }

  // Header badge
  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (operation === 'start')     badge = { cls: 'ready',   dot: 'yellow', text: 'Starting' }
  else if (operation === 'stop') badge = { cls: 'ready',   dot: 'yellow', text: 'Stopping' }
  else if (running)              badge = { cls: 'running', dot: 'green',  text: 'Running' }
  else                           badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }

  const validInstance = baseInstance >= 1 && baseInstance <= 4194302
  const validPort     = port >= 1024 && port <= 65535
  const canStart      = !busy && !running && validInstance && validPort

  const startTip = !validInstance ? 'Instance must be 1–4194302'
                 : !validPort     ? 'Port must be 1024–65535'
                 : 'Start BACnet/IP simulator'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">BACnet/IP Simulator</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* ── Configuration ──────────────────────────────────── */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Configuration</span>

          <Field
            label="Base Instance"
            type="number"
            value={baseInstance}
            min={1}
            max={4194302}
            onChange={v => setBaseInstance(parseInt(v) || 40001)}
            disabled={running || busy}
          />
          <Select
            label="Mains Frequency"
            value={freqHz}
            onChange={setFreqHz}
            disabled={running || busy}
            options={FREQ_OPTIONS}
          />
          <Field
            label="UDP Port"
            type="number"
            value={port}
            min={1024}
            max={65535}
            onChange={v => setPort(parseInt(v) || 47808)}
            disabled={running || busy}
            suffix="0xBAC0"
          />
        </div>

        {/* ── Running summary ────────────────────────────────── */}
        {running && status && (
          <div style={{
            fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6,
            padding: '6px 8px', background: 'var(--bg-base)',
            border: '1px solid var(--border)', borderRadius: 4,
            fontFamily: 'Consolas, monospace',
          }}>
            <StatRow
              label="Base instance:"
              value={status.base_instance}
              valueColor="var(--text)"
            />
            <StatRow
              label="Mains frequency:"
              value={`${status.frequency_hz} Hz`}
              valueColor="var(--text)"
            />
            <StatRow
              label="UDP port:"
              value={status.port}
              valueColor="var(--text)"
            />
          </div>
        )}

        {/* ── Active Devices table ───────────────────────────── */}
        {running && (
          <div className="group-box">
            <span className="group-box-label">Active Devices</span>
            <DeviceCounts devices={status?.devices ?? []} />
          </div>
        )}

        {/* ── Error ──────────────────────────────────────────── */}
        {error && (
          <div style={{
            fontSize: 10, color: 'var(--red)', padding: '6px 8px',
            background: 'color-mix(in srgb, var(--red) 10%, var(--bg-base))',
            border: '1px solid color-mix(in srgb, var(--red) 30%, transparent)',
            borderRadius: 4, lineHeight: 1.5,
          }}>
            {error}
          </div>
        )}

        {/* ── Actions ────────────────────────────────────────── */}
        <div className="snmp-actions">
          {running ? (
            <button
              className="btn-action btn-stop"
              onClick={stop}
              disabled={busy}
              title="Stop BACnet simulator"
            >
              <IconStop />
              <span>Stop BACnet</span>
            </button>
          ) : (
            <button
              className="btn-action btn-start"
              onClick={start}
              disabled={!canStart}
              title={startTip}
            >
              <IconPlay />
              <span>Start BACnet</span>
            </button>
          )}
        </div>

      </div>
    </div>
  )
}
