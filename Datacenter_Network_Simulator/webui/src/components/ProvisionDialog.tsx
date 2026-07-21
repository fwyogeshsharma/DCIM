import { useEffect, useMemo, useState } from 'react'
import { api, errorMessage } from '../api/client'
import { useStore } from '../store/useStore'

/**
 * Provision Capacity — user-driven, senior-architect-style capacity growth that
 * mirrors what the fleet lifecycle does automatically, but on demand for a chosen
 * datacenter. Lives on the Floor-Plan page (you provision where you can see the
 * floor + free space). Two actions, both hot-commissioned onto the live sims:
 *
 *   • Add Rack   — one empty compute rack (leaf + A/B rack PDUs, wired to the pod
 *                  fabric + RPP feeds) into a hall that still has grid space.
 *   • Open Hall  — a brand-new server hall (own pod: spines + OOB, RPP pair + EV2
 *                  meters, back-wall CRAH complement, sensors) cloned from the DC's
 *                  busiest hall, with its first compute rack placed.
 *
 * Add Device (placement-only) deep-links here when a DC has no rack with free U.
 */

type RackOcc = { room: string; floor: string; rack_row: number; rack_num: number
                 used: number; total: number; free_units: number[]
                 next_free: number | null; full: boolean }

type ProvResult = { message: string
                    rack: { datacenter: string; room: string; floor: string
                            rack_row: number; rack_num: number } }

interface Props { onClose: () => void }

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
}
const dialog: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 6, width: 440, maxHeight: '90vh',
  display: 'flex', flexDirection: 'column',
  boxShadow: '0 16px 48px rgba(0,0,0,0.8)',
}
const sectionHeader: React.CSSProperties = {
  fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
  textTransform: 'uppercase', letterSpacing: '0.8px',
  borderBottom: '1px solid var(--border)',
  paddingBottom: 4, marginBottom: 6, marginTop: 10,
}

const uniq = (xs: (string | undefined)[]) =>
  [...new Set(xs.filter((x): x is string => !!x))].sort()

export default function ProvisionDialog({ onClose }: Props) {
  const { devices, fetchGraph, fetchDevices } = useStore()

  const datacenters = useMemo(() => uniq(devices.map(d => d.datacenter)), [devices])
  const [dc, setDc] = useState('')
  const [room, setRoom] = useState('')   // target hall for Add Rack; '' = busiest
  // Rack type. A rack either has a coolant manifold or it does not, and that is
  // decided when the cabinet is built — not later, per server. Picking 'liquid'
  // installs an in-rack CDU alongside the leaf and PDUs and plumbs it to the hall's
  // chilled-water headers, which is what makes direct-to-chip servers rackable here.
  const [rackKind, setRackKind] = useState<'air' | 'liquid'>('air')
  const [mode, setMode] = useState<'rack' | 'hall'>('rack')

  const [racks, setRacks] = useState<RackOcc[]>([])
  const [loadingCap, setLoadingCap] = useState(false)

  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState<ProvResult | null>(null)

  // Default the datacenter as soon as inventory arrives (single DC → auto-pick).
  useEffect(() => {
    if (!dc && datacenters.length > 0) setDc(datacenters[0])
  }, [datacenters, dc])

  // Load the chosen DC's rack occupancy so we can show its capacity headroom and
  // steer the operator (all racks full → Open Hall). Reloaded after a provision.
  const loadCapacity = useMemo(() => async (target: string) => {
    if (!target) { setRacks([]); return }
    setLoadingCap(true)
    try {
      const r = await api.rackOccupancy(target) as { racks?: RackOcc[] }
      setRacks(r.racks || [])
    } catch { setRacks([]) }
    finally { setLoadingCap(false) }
  }, [])

  useEffect(() => { loadCapacity(dc) }, [dc, loadCapacity])

  const totalRacks    = racks.length
  const racksWithSpace = racks.filter(r => r.free_units.length > 0).length
  const freeUnits      = racks.reduce((n, r) => n + r.free_units.length, 0)
  const allFull        = totalRacks > 0 && racksWithSpace === 0

  // Add Rack builds a COMPUTE rack, so it can only target a server hall (white
  // space with a pod fabric + RPP + CRAH). Facility rooms (Mechanical/Electrical)
  // can't hold one and the backend rejects them (no _hall_infra). Restrict the
  // picker to rooms in this DC that actually contain servers — in this topology
  // the network gear lives inside those same halls (each hall is its own pod), so
  // there is no separate network hall to list. NOT filtered by free U: a hall whose
  // existing racks are full may still have empty GRID slots for a new rack; the
  // backend enforces the per-hall grid/fabric cap.
  const computeRooms = useMemo(() => new Set(
    devices.filter(d => d.device_type === 'server' && d.datacenter === dc)
      .map(d => d.room || '')), [devices, dc])
  const halls = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of racks) if (computeRooms.has(r.room)) m.set(r.room, (m.get(r.room) || 0) + 1)
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [racks, computeRooms])

  // Add-Rack has nowhere to go when every rack in the DC is full → nudge to Hall.
  useEffect(() => { if (allFull && mode === 'rack') setMode('hall') }, [allFull, mode])

  async function submit() {
    if (!dc) { setErr('Pick a datacenter'); return }
    setBusy(true); setErr(''); setResult(null)
    try {
      const r = (mode === 'rack'
        ? await api.provisionRack(dc, room || undefined, rackKind === 'liquid')
        : await api.provisionHall(dc)) as ProvResult
      setResult(r)
      await Promise.all([fetchGraph(), fetchDevices(), loadCapacity(dc)])
    } catch (e: unknown) {
      setErr(errorMessage(e))
    } finally { setBusy(false) }
  }

  return (
    <div style={overlay} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div style={dialog}>
        <div className="panel-header" style={{ borderRadius: '6px 6px 0 0', flexShrink: 0 }}>
          <span className="title">Provision Capacity</span>
          <button onClick={onClose} style={{ border: 'none', background: 'none', color: 'var(--text-muted)', fontSize: 16, cursor: 'pointer', padding: '0 4px' }}>✕</button>
        </div>

        <div style={{ padding: '10px 16px 14px', display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
          <div style={sectionHeader}>Target</div>
          <Row label="Datacenter">
            <select style={{ flex: 1 }} value={dc} onChange={e => { setDc(e.target.value); setRoom(''); setResult(null); setErr('') }}>
              {datacenters.length === 0 && <option value="">— no datacenters —</option>}
              {datacenters.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </Row>

          {/* Capacity headroom for the chosen DC. */}
          {dc && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', paddingLeft: 100, marginTop: 2, marginBottom: 2 }}>
              {loadingCap ? 'reading rack capacity…'
                : `${totalRacks} racks · ${racksWithSpace} with free U · ${freeUnits} free server-U total`}
              {allFull && !loadingCap && (
                <span style={{ color: '#f0a020' }}> — every rack full; open a new hall.</span>
              )}
            </div>
          )}

          <div style={sectionHeader}>Action</div>
          <Row label="Provision">
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <Radio checked={mode === 'rack'} disabled={allFull}
                     onSelect={() => setMode('rack')}
                     title="Add a rack"
                     desc="One empty compute rack (leaf + A/B PDUs) into a hall that still has grid space. Fabric & rack-power caps are enforced." />
              <Radio checked={mode === 'hall'}
                     onSelect={() => setMode('hall')}
                     title="Open a new hall"
                     desc="A brand-new server hall — own pod (spines + OOB), RPP pair + EV2 meters, back-wall CRAHs, sensors — cloned from the DC's busiest hall, first rack placed." />
            </div>
          </Row>

          {/* Which hall to drop the new rack into (Add Rack only). The row/rack
              number + starting U are assigned by the hall's grid packing — you
              choose the hall, the floor grid chooses the slot. */}
          {mode === 'rack' && (
            <Row label="Hall">
              <select style={{ flex: 1 }} value={room} onChange={e => { setRoom(e.target.value); setResult(null); setErr('') }}>
                <option value="">Auto — most-utilized hall</option>
                {halls.map(([name, n]) => (
                  <option key={name} value={name}>{name} · {n} racks</option>
                ))}
              </select>
            </Row>
          )}

          {/* Rack type — only meaningful for Add Rack; a new hall's first rack is
              always air (its CDU story starts when a liquid rack is added to it). */}
          {mode === 'rack' && (
            <Row label="Rack type">
              <select style={{ flex: 1 }} value={rackKind}
                onChange={e => { setRackKind(e.target.value as 'air' | 'liquid'); setResult(null); setErr('') }}>
                <option value="air">Compute (air-cooled) — leaf + A/B PDUs</option>
                <option value="liquid">Compute (liquid / DLC) — leaf + A/B PDUs + in-rack CDU</option>
              </select>
            </Row>
          )}
          {mode === 'rack' && rackKind === 'liquid' && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', paddingLeft: 100, marginTop: -4, marginBottom: 4 }}>
              CDU is cloned from this DC's standard unit, takes U37–U40, and is plumbed to
              the hall's CHW supply/return headers. Costs 4U of server space.
            </div>
          )}

          {result && (
            <div style={{ marginTop: 10, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 4, background: 'var(--bg-hover)' }}>
              <div style={{ fontSize: 11, color: 'var(--green, #35c26b)', fontWeight: 600 }}>✓ {result.message}</div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 3 }}>
                {result.rack.datacenter} · {result.rack.room} · Row {result.rack.rack_row} · Rack {result.rack.rack_num}
                {' — now selectable in Add Device.'}
              </div>
            </div>
          )}

          {err && <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 8 }}>{err}</div>}
        </div>

        <div style={{ display: 'flex', gap: 8, padding: '8px 16px 12px', flexShrink: 0, borderTop: '1px solid var(--border)' }}>
          <button className="primary" style={{ flex: 1 }} onClick={submit} disabled={busy || !dc}>
            {busy ? 'Provisioning…' : mode === 'rack' ? 'Add Rack' : 'Open Hall'}
          </button>
          <button style={{ flex: 1 }} onClick={onClose} disabled={busy}>Close</button>
        </div>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 11, width: 96, textAlign: 'right', flexShrink: 0 }}>{label}</span>
      {children}
    </div>
  )
}

function Radio({ checked, disabled, onSelect, title, desc }: {
  checked: boolean; disabled?: boolean; onSelect: () => void; title: string; desc: string
}) {
  return (
    <label style={{
      display: 'flex', gap: 8, cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.45 : 1, alignItems: 'flex-start',
    }}>
      <input type="radio" checked={checked} disabled={disabled} onChange={onSelect}
             style={{ accentColor: 'var(--accent)', marginTop: 2 }} />
      <div>
        <div style={{ fontSize: 11, color: 'var(--text)', fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.4 }}>{desc}</div>
      </div>
    </label>
  )
}
