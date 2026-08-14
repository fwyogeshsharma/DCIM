import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import TargetsBox, { StatRow } from './TargetsBox'
import NumberInput from '../NumberInput'

/**
 * Modbus/TCP — the facility electrical plane.
 *
 * The register browser is the reason this panel exists. Modbus has no
 * self-description: no MIB, no object list, no units, no discovery. A raw
 * register reads 4152 and means nothing until someone tells you it is volts
 * scaled by ten. This panel is that vendor register map, rendered live, which
 * is the one thing the SNMP and BACnet panels never have to do.
 *
 * Styling follows BACnetPanel: panel-header + badge, group-box sections, the
 * shared Field row, TargetsBox for the pre-start list and btn-action buttons,
 * so every protocol panel reads the same way.
 */

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

type Slave = {
  name: string; ip: string; unit_id: number; device_type: string
  map_id: string; vendor: string; product: string; word_order: string
  online: boolean; write_enabled: boolean; role: string; gateway_ip: string
  stats: { requests: number; exceptions: number; writes: number; last_exception: number }
}
type Point = {
  space: string; addr: number; name: string; dtype: string
  raw: number[]; value: number | string; units: string; key: string; writable: boolean
}
type Status = {
  running: boolean; available: boolean; port: number
  active_devices: number; devices: Slave[]
  stats: { requests?: number; exceptions?: number; connections?: number; refused?: number }
}
type Candidate = {
  name: string; device_type: string; ip: string; bound: boolean
  role?: string; unit_id?: number
}

const SPACE_LABEL: Record<string, string> = {
  input: 'Input Reg (FC04)',
  holding: 'Holding Reg (FC03)',
  discrete: 'Discrete In (FC02)',
  coil: 'Coil (FC01)',
}

// Same 90px label column as BACnetPanel's Field, so the two panels line up.
function Field({ label, suffix, value, onChange, disabled, min, max, fallback }: {
  label: string; suffix?: string
  value: number
  onChange: (n: number) => void
  disabled: boolean
  min?: number; max?: number; fallback?: number
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
        <NumberInput
          value={value} onChange={onChange} fallback={fallback} int
          min={min} max={max} disabled={disabled}
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

function hex(n: number) {
  return '0x' + n.toString(16).toUpperCase().padStart(4, '0')
}

export default function ModbusPanel() {
  const [status, setStatus] = useState<Status | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [port, setPort] = useState(502)
  const [writeEnabled, setWriteEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [selected, setSelected] = useState('')
  const [points, setPoints] = useState<Point[]>([])
  const [mapMeta, setMapMeta] = useState<{ map_id: string; word_order: string; product: string } | null>(null)

  const running = status?.running ?? false

  const refresh = async () => {
    try { setStatus(await api.modbusStatus() as Status) } catch { /* transient */ }
  }

  useEffect(() => { refresh() }, [])
  useEffect(() => {
    api.modbusCandidates()
      .then(r => setCandidates((r as { devices?: Candidate[] }).devices ?? []))
      .catch(() => {})
  }, [running])

  // Live poll while running. 1 s matches the store tick — polling faster shows
  // the same register image twice and just burns requests.
  useEffect(() => {
    if (!running) return
    const t = setInterval(refresh, 1000)
    return () => clearInterval(t)
  }, [running])

  useEffect(() => {
    if (!running || !selected) { setPoints([]); return }
    let alive = true
    const pull = async () => {
      try {
        const r = await api.modbusRegisters(selected) as {
          points?: Point[]; map_id: string; word_order: string; product: string
        }
        if (!alive) return
        setPoints(r.points ?? [])
        setMapMeta({ map_id: r.map_id, word_order: r.word_order, product: r.product })
      } catch { /* device may have gone */ }
    }
    pull()
    const t = setInterval(pull, 1000)
    return () => { alive = false; clearInterval(t) }
  }, [running, selected])

  const boundCount = candidates.filter(c => c.bound).length
  const validPort = port >= 1 && port <= 65535
  const canStart = !busy && !running && boundCount > 0 && validPort

  const start = async () => {
    setBusy(true); setErr('')
    try { await api.modbusStart({ port, write_enabled: writeEnabled }); await refresh() }
    catch (e: any) { setErr(e?.message ?? String(e)) }
    finally { setBusy(false) }
  }
  const stop = async () => {
    setBusy(true); setErr('')
    try { await api.modbusStop(); setSelected(''); await refresh() }
    catch (e: any) { setErr(e?.message ?? String(e)) }
    finally { setBusy(false) }
  }

  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (busy)         badge = { cls: 'ready',   dot: 'yellow', text: running ? 'Stopping' : 'Starting' }
  else if (running) badge = { cls: 'running', dot: 'green',  text: 'Running' }
  else              badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }

  const startTip = boundCount === 0
    ? 'Bind IPs first (Binding panel → Bind IPs)'
    : !validPort ? 'Port must be 1–65535'
    : `Serve ${boundCount} device(s) on port ${port}`

  const slaves = status?.devices ?? []
  const sel = slaves.find(s => s.name === selected)

  // Pre-start target rows, grouped by device type like the other panels.
  const byType = new Map<string, number>()
  for (const c of candidates) byType.set(c.device_type, (byType.get(c.device_type) ?? 0) + 1)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">
          {/* The official lockup, unmodified — the panel has the width the 44px
              icon rail cannot give it, so the wordmark is legible here. */}
          <img
            src="/assets/icons/modbus.png"
            alt="Modbus/TCP Simulator"
            style={{ height: 24, width: 'auto', maxWidth: '62%',
                     objectFit: 'contain', display: 'block' }}
          />
        </span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px',
                    display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* ── Configuration ──────────────────────────────────── */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Configuration</span>

          <Field
            label="TCP Port"
            value={port}
            onChange={setPort}
            fallback={502}
            min={1}
            max={65535}
            disabled={running || busy}
            suffix="502"
          />

          <label style={{
            display: 'flex', alignItems: 'center', gap: 6, fontSize: 10,
            color: 'var(--text-muted)', marginTop: 2,
            cursor: running || busy ? 'default' : 'pointer',
          }}>
            <input
              type="checkbox" checked={writeEnabled} disabled={running || busy}
              onChange={e => setWriteEnabled(e.target.checked)}
            />
            Arm write path (FC05/06/15/16)
          </label>

          {port < 1024 && (
            <div style={{ fontSize: 9, color: 'var(--text-dim)', lineHeight: 1.5, marginTop: 6 }}>
              Port 502 is privileged — on Linux the process needs root or
              CAP_NET_BIND_SERVICE. Use 5020 if the bind is refused.
            </div>
          )}
        </div>

        {/* ── Targets — pre-start, mirrors the other protocol panels ── */}
        {!running && candidates.length > 0 && (
          <TargetsBox
            rows={[...byType.entries()].map(([t, n]) => ({ label: `${t}:`, value: n }))}
            total={candidates.length}
            footer={
              <StatRow
                label="Bound:"
                value={`${boundCount} / ${candidates.length}`}
                labelColor={boundCount === candidates.length ? undefined : 'var(--warn)'}
                valueColor={boundCount === candidates.length ? undefined : 'var(--warn)'}
              />
            }
          />
        )}

        {/* ── Running summary ────────────────────────────────── */}
        {running && status && (
          <div style={{
            fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6,
            padding: '6px 8px', background: 'var(--bg-base)',
            border: '1px solid var(--border)', borderRadius: 4,
            fontFamily: 'Consolas, monospace',
          }}>
            <StatRow label="TCP port:" value={status.port} valueColor="var(--text)" />
            <StatRow label="Active slaves:" value={status.active_devices} valueColor="var(--text)" />
            <StatRow label="Requests:" value={status.stats?.requests ?? 0} valueColor="var(--text)" />
            <StatRow
              label="Exceptions:"
              value={status.stats?.exceptions ?? 0}
              valueColor={(status.stats?.exceptions ?? 0) > 0 ? 'var(--warn)' : 'var(--text)'}
            />
            <StatRow label="Connections:" value={status.stats?.connections ?? 0} valueColor="var(--text)" />
            <StatRow
              label="Refused:"
              value={status.stats?.refused ?? 0}
              valueColor={(status.stats?.refused ?? 0) > 0 ? 'var(--warn)' : 'var(--text)'}
            />
          </div>
        )}

        {/* ── Active slaves ──────────────────────────────────── */}
        {running && slaves.length > 0 && (
          <div className="group-box">
            <span className="group-box-label">Active Slaves</span>
            <div style={{ maxHeight: 190, overflowY: 'auto', margin: '0 -4px' }}>
              {slaves.map(s => (
                <div
                  key={s.name}
                  onClick={() => setSelected(s.name === selected ? '' : s.name)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '3px 4px', cursor: 'pointer', borderRadius: 3,
                    fontSize: 10,
                    background: s.name === selected ? 'var(--bg-selected)' : 'transparent',
                  }}
                >
                  <span className={`status-dot ${s.online ? 'green' : 'red'}`} />
                  <span style={{
                    flex: 1, minWidth: 0, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    fontFamily: 'Consolas, monospace', color: 'var(--text)',
                  }}>{s.name}</span>
                  <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>
                    {s.role === 'rtu_slave' ? 'RTU' : 'TCP'}
                  </span>
                  <span style={{
                    fontFamily: 'Consolas, monospace', color: 'var(--text-dim)', fontSize: 9,
                  }}>u{s.unit_id}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Register browser ───────────────────────────────── */}
        {sel && (
          <div className="group-box">
            <span className="group-box-label">Register Map</span>

            <div style={{
              fontSize: 9, color: 'var(--text-dim)', lineHeight: 1.5,
              fontFamily: 'Consolas, monospace', marginBottom: 6,
              display: 'flex', justifyContent: 'space-between', gap: 6,
            }}>
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {mapMeta?.product} · {mapMeta?.map_id} · word order {mapMeta?.word_order}
              </span>
              <a
                href={api.modbusMapExportUrl(sel.device_type)}
                style={{ color: 'var(--accent)', textDecoration: 'none', flexShrink: 0 }}
              >CSV</a>
            </div>
            <div style={{ fontSize: 9, color: 'var(--text-dim)', marginBottom: 6 }}>
              Simulator addresses, not the vendor's published map.
            </div>

            <div style={{
              display: 'grid', gridTemplateColumns: '54px 1fr 66px 56px',
              gap: 4, fontSize: 9, color: 'var(--text-muted)',
              borderBottom: '1px solid var(--border)', paddingBottom: 3,
            }}>
              <span>Addr</span><span>Point</span><span>Raw</span><span>Value</span>
            </div>

            <div style={{ maxHeight: 240, overflowY: 'auto' }}>
              {['input', 'holding', 'discrete', 'coil'].map(space => {
                const rows = points.filter(p => p.space === space)
                if (!rows.length) return null
                return (
                  <div key={space}>
                    <div style={{
                      fontSize: 9, color: 'var(--text-dim)', padding: '4px 0 2px',
                    }}>{SPACE_LABEL[space] ?? space}</div>
                    {rows.map(p => (
                      <div key={space + p.addr} style={{
                        display: 'grid', gridTemplateColumns: '54px 1fr 66px 56px',
                        gap: 4, fontSize: 10, padding: '2px 0',
                        fontFamily: 'Consolas, monospace',
                      }}>
                        <span style={{ color: 'var(--text-dim)' }}>
                          {space === 'input' || space === 'holding' ? hex(p.addr) : p.addr}
                        </span>
                        <span
                          style={{
                            color: 'var(--text)', overflow: 'hidden',
                            textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}
                          title={`${p.name}  ←  ext["${p.key}"]`}
                        >{p.name}</span>
                        <span style={{ color: 'var(--text-dim)' }}>{p.raw.join(',')}</span>
                        <span style={{ color: 'var(--text)' }}>
                          {p.value}{p.units ? ' ' + p.units : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {err && (
          <div style={{
            fontSize: 10, color: 'var(--crit)', background: 'var(--crit-bg)',
            padding: '5px 7px', borderRadius: 4, lineHeight: 1.5,
          }}>{err}</div>
        )}

        {/* ── Start / Stop ───────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 6 }}>
          {running ? (
            <button
              className="btn-action btn-stop"
              onClick={stop}
              disabled={busy}
              title="Stop Modbus simulator"
            >
              <IconStop />
              <span>Stop Modbus</span>
            </button>
          ) : (
            <button
              className="btn-action btn-start"
              onClick={start}
              disabled={!canStart}
              title={startTip}
            >
              <IconPlay />
              <span>Start Modbus</span>
            </button>
          )}
        </div>

      </div>
    </div>
  )
}
