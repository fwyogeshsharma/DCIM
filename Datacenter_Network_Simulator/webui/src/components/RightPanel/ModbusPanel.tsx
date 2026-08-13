import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import NumberInput from '../NumberInput'

/**
 * Modbus/TCP — the facility electrical plane.
 *
 * The register browser is the reason this panel exists. Modbus has no
 * self-description: no MIB, no object list, no units, no discovery. A raw
 * register reads 4152 and means nothing until someone tells you it is volts
 * scaled by ten. This panel is that vendor register map, rendered live, which
 * is the one thing the SNMP and BACnet panels never have to do.
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
type Candidate = { name: string; device_type: string; ip: string; bound: boolean }

const SPACE_LABEL: Record<string, string> = {
  input: 'Input Reg (FC04)',
  holding: 'Holding Reg (FC03)',
  discrete: 'Discrete In (FC02)',
  coil: 'Coil (FC01)',
}

function StatRow({ label, value, valueColor }: {
  label: string; value: number | string; valueColor?: string
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, lineHeight: 1.85 }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: valueColor ?? 'var(--text)' }}>{value}</span>
    </div>
  )
}

function hex(n: number, w = 4) {
  return '0x' + n.toString(16).toUpperCase().padStart(w, '0')
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
  const canStart = !busy && !running && boundCount > 0 && port >= 1 && port <= 65535

  const start = async () => {
    setBusy(true); setErr('')
    try {
      await api.modbusStart({ port, write_enabled: writeEnabled })
      await refresh()
    } catch (e: any) { setErr(e?.message ?? String(e)) }
    finally { setBusy(false) }
  }
  const stop = async () => {
    setBusy(true); setErr('')
    try { await api.modbusStop(); setSelected(''); await refresh() }
    catch (e: any) { setErr(e?.message ?? String(e)) }
    finally { setBusy(false) }
  }

  let badge: { dot: string; text: string }
  if (busy)         badge = { dot: 'var(--warn)', text: 'Working' }
  else if (running) badge = { dot: 'var(--ok)',   text: 'Running' }
  else              badge = { dot: 'var(--text-dim)', text: 'Idle' }

  const startTip = boundCount === 0
    ? 'No Modbus-capable device has a bound IP (Binding panel → Bind IPs)'
    : `Serve ${boundCount} electrical device(s) on port ${port}`

  const slaves = status?.devices ?? []
  const sel = slaves.find(s => s.name === selected)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: 10 }}>
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 10px', borderBottom: '1px solid var(--border)',
      }}>
        {/* The official Modbus logo, unmodified. It is 3.14:1 art, so it only
            reads at a width the 44px icon rail cannot give it — here there is
            room, and the wordmark is legible at its true proportions. */}
        <img
          src="/assets/icons/modbus.png"
          alt="Modbus/TCP"
          title="Modbus/TCP"
          style={{ height: 24, width: 'auto', maxWidth: '65%',
                   objectFit: 'contain', display: 'block' }}
        />
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%', background: badge.dot,
          }} />
          <span style={{ color: 'var(--text-muted)' }}>{badge.text}</span>
        </span>
      </div>

      <div style={{ overflowY: 'auto', flex: 1, padding: '8px 10px' }}>
        {/* ── Config ───────────────────────────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
          <span style={{ width: 80, color: 'var(--text-muted)', flexShrink: 0 }}>TCP Port</span>
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center',
            background: running ? 'var(--bg-base)' : 'var(--bg-card)',
            border: '1px solid var(--border)', borderRadius: 4,
            padding: '0 6px', opacity: running ? 0.6 : 1,
          }}>
            <NumberInput
              value={port} onChange={setPort} fallback={502} int disabled={running}
              style={{
                flex: 1, minWidth: 0, background: 'transparent', border: 'none',
                outline: 'none', color: 'var(--text)', fontSize: 10,
                fontFamily: 'Consolas, monospace', padding: '4px 0',
              }}
            />
          </div>
        </div>
        {port < 1024 && (
          <div style={{ color: 'var(--text-dim)', marginBottom: 6, lineHeight: 1.5 }}>
            Port 502 is privileged — on Linux the process needs root or
            CAP_NET_BIND_SERVICE. Use 5020 if the bind is refused.
          </div>
        )}

        <label style={{
          display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8,
          color: 'var(--text-muted)', cursor: running ? 'default' : 'pointer',
        }}>
          <input
            type="checkbox" checked={writeEnabled} disabled={running}
            onChange={e => setWriteEnabled(e.target.checked)}
          />
          Arm write path (FC05/06/15/16)
        </label>
        <div style={{ color: 'var(--text-dim)', marginBottom: 8, lineHeight: 1.5 }}>
          No map declares a writable point yet, so every write is refused at the
          address check. Real sites run this plane read-only regardless.
        </div>

        {/* ── Start / Stop ─────────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          <button
            onClick={start} disabled={!canStart} title={startTip}
            style={{
              flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 5, padding: '6px 0', fontSize: 10, borderRadius: 4,
              border: '1px solid var(--border)',
              background: canStart ? 'var(--ok-bg)' : 'var(--bg-base)',
              color: canStart ? 'var(--ok)' : 'var(--text-dim)',
              cursor: canStart ? 'pointer' : 'default',
            }}
          ><IconPlay /> Start</button>
          <button
            onClick={stop} disabled={busy || !running}
            style={{
              flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 5, padding: '6px 0', fontSize: 10, borderRadius: 4,
              border: '1px solid var(--border)',
              background: running ? 'var(--crit-bg)' : 'var(--bg-base)',
              color: running ? 'var(--crit)' : 'var(--text-dim)',
              cursor: running ? 'pointer' : 'default',
            }}
          ><IconStop /> Stop</button>
        </div>

        {err && (
          <div style={{
            color: 'var(--crit)', background: 'var(--crit-bg)', padding: '5px 7px',
            borderRadius: 4, marginBottom: 8, lineHeight: 1.5,
          }}>{err}</div>
        )}

        {/* ── Stats ────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 10 }}>
          <StatRow label="Candidates (bound)" value={`${boundCount} / ${candidates.length}`} />
          <StatRow label="Active servers" value={status?.active_devices ?? 0} />
          <StatRow label="Requests" value={status?.stats?.requests ?? 0} />
          <StatRow
            label="Exceptions" value={status?.stats?.exceptions ?? 0}
            valueColor={(status?.stats?.exceptions ?? 0) > 0 ? 'var(--warn)' : undefined}
          />
          <StatRow label="Connections" value={status?.stats?.connections ?? 0} />
          <StatRow
            label="Refused" value={status?.stats?.refused ?? 0}
            valueColor={(status?.stats?.refused ?? 0) > 0 ? 'var(--warn)' : undefined}
          />
        </div>

        {/* ── Slave table ──────────────────────────────────────────── */}
        {running && slaves.length > 0 && (
          <>
            <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>Slaves</div>
            <div style={{
              border: '1px solid var(--border)', borderRadius: 4,
              marginBottom: 10, maxHeight: 190, overflowY: 'auto',
            }}>
              {slaves.map(s => (
                <div
                  key={s.name}
                  onClick={() => setSelected(s.name === selected ? '' : s.name)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '4px 6px', cursor: 'pointer',
                    borderBottom: '1px solid var(--border)',
                    background: s.name === selected ? 'var(--bg-selected)' : 'transparent',
                  }}
                >
                  <span style={{
                    width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
                    background: s.online ? 'var(--ok)' : 'var(--crit)',
                  }} />
                  <span style={{
                    flex: 1, minWidth: 0, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    fontFamily: 'Consolas, monospace', color: 'var(--text)',
                  }}>{s.name}</span>
                  <span style={{
                    fontSize: 9, padding: '1px 4px', borderRadius: 3,
                    background: 'var(--bg-base)', color: 'var(--text-muted)',
                  }}>{s.role === 'rtu_slave' ? 'RTU' : 'TCP'}</span>
                  <span style={{
                    fontFamily: 'Consolas, monospace', color: 'var(--text-dim)',
                    fontSize: 9,
                  }}>u{s.unit_id}</span>
                </div>
              ))}
            </div>
          </>
        )}

        {/* ── Register browser ─────────────────────────────────────── */}
        {sel && (
          <>
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              alignItems: 'baseline', marginBottom: 3,
            }}>
              <span style={{ color: 'var(--text-muted)' }}>Register map</span>
              <a
                href={api.modbusMapExportUrl(sel.device_type)}
                style={{ color: 'var(--accent)', fontSize: 9, textDecoration: 'none' }}
              >export CSV</a>
            </div>
            <div style={{
              color: 'var(--text-dim)', marginBottom: 6, lineHeight: 1.5,
              fontFamily: 'Consolas, monospace', fontSize: 9,
            }}>
              {mapMeta?.product} · {mapMeta?.map_id} · word order {mapMeta?.word_order}
              <br />
              Simulator addresses, not the vendor's published map.
            </div>

            <div style={{
              border: '1px solid var(--border)', borderRadius: 4,
              overflow: 'hidden', marginBottom: 10,
            }}>
              <div style={{
                display: 'grid', gridTemplateColumns: '58px 1fr 74px 58px',
                gap: 4, padding: '4px 6px', fontSize: 9,
                background: 'var(--bg-base)', color: 'var(--text-muted)',
              }}>
                <span>Addr</span><span>Point</span><span>Raw</span><span>Value</span>
              </div>
              <div style={{ maxHeight: 260, overflowY: 'auto' }}>
                {['input', 'holding', 'discrete', 'coil'].map(space => {
                  const rows = points.filter(p => p.space === space)
                  if (!rows.length) return null
                  return (
                    <div key={space}>
                      <div style={{
                        padding: '3px 6px', fontSize: 9, color: 'var(--text-dim)',
                        background: 'var(--bg-base)',
                      }}>{SPACE_LABEL[space] ?? space}</div>
                      {rows.map(p => (
                        <div key={space + p.addr} style={{
                          display: 'grid', gridTemplateColumns: '58px 1fr 74px 58px',
                          gap: 4, padding: '3px 6px',
                          borderBottom: '1px solid var(--border)',
                          fontFamily: 'Consolas, monospace',
                        }}>
                          <span style={{ color: 'var(--text-dim)' }}>
                            {space === 'input' || space === 'holding' ? hex(p.addr) : p.addr}
                          </span>
                          <span style={{
                            color: 'var(--text)', overflow: 'hidden',
                            textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }} title={`${p.name}  ←  ext["${p.key}"]`}>{p.name}</span>
                          <span style={{ color: 'var(--text-dim)' }}>
                            {p.raw.map(r => r.toString()).join(',')}
                          </span>
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
          </>
        )}

        {/* ── Unbound candidates ───────────────────────────────────── */}
        {!running && candidates.length > 0 && (
          <>
            <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>
              Candidate devices
            </div>
            <div style={{
              border: '1px solid var(--border)', borderRadius: 4,
              maxHeight: 200, overflowY: 'auto',
            }}>
              {candidates.map(c => (
                <div key={c.name} style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '3px 6px',
                  borderBottom: '1px solid var(--border)',
                  fontFamily: 'Consolas, monospace',
                }}>
                  <span style={{
                    width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
                    background: c.bound ? 'var(--ok)' : 'var(--text-dim)',
                  }} />
                  <span style={{
                    flex: 1, minWidth: 0, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text)',
                  }}>{c.name}</span>
                  <span style={{ color: 'var(--text-dim)', fontSize: 9 }}>{c.ip || 'unbound'}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
