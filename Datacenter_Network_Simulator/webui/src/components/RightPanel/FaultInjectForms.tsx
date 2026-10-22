import { useEffect, useMemo, useRef, useState } from 'react'
import { api, errorMessage } from '../../api/client'
import type { ActiveFault, DeviceInfo, FaultPoint, FaultPointsResponse } from '../../api/types'
import NumberInput from '../NumberInput'

/**
 * Injection forms for the Fault Injection panel (doc 32 S2-1).
 *
 * Kept apart from FaultsPanel so the node context menu's "Sensor fault…" and
 * "Degrade…" dialogs (also S2-1) can wrap the same forms with a fixed device.
 * Validation here MIRRORS api/routers/faults.py so the operator sees the
 * reason before the round trip; the server stays the authority (422/409).
 */

// ── shared helpers ───────────────────────────────────────────────────────────

/** "45 s", "12 m", "3 h 10 m", "2 d 4 h". Simulator wall time, not tick count. */
export function fmtDur(sec: number): string {
  if (!Number.isFinite(sec)) return '—'
  const s = Math.max(0, Math.round(sec))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} m`
  const h = Math.floor(m / 60)
  if (h < 48) return m % 60 ? `${h} h ${m % 60} m` : `${h} h`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`
}

export function isoMs(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}

/** Local wall-clock rendering of a UTC ISO stamp. */
export function fmtLocal(iso: string | null | undefined, withDate = false): string {
  const t = isoMs(iso)
  if (t === null) return '—'
  const d = new Date(t)
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  if (!withDate) return time
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
}

/**
 * Best-effort engineering unit for a point name. A bias is applied in the
 * point's OWN unit, and the points endpoint does not carry units, so this only
 * names the unambiguous cases and otherwise says "point units" rather than
 * guess (a pressure here may be kPa or Pa depending on the device).
 */
export function unitOf(point: string): string {
  const p = point.toLowerCase()
  if (p === 'filter_dp') return 'Pa'
  if (p.includes('humidity') || p.endsWith('_rh_pct')) return '%RH'
  if (p.includes('temp') || p.endsWith('_c') || p.includes('bulb') || p === 'dewpoint') return '°C'
  if (p.endsWith('_pct') || p.includes('speed') || p.includes('valve') || p.includes('position')
      || p === 'compressor_load' || p === 'ups_output_load') return '%'
  if (p === 'fan_rpm') return 'rpm'
  if (p.includes('frequency')) return 'Hz'
  if (p.includes('voltage')) return 'V'
  return 'point units'
}

/**
 * MIRRORS core/fault_injection.py DEGRADATION_KINDS — types, healthy value,
 * physical band and unit per kind. The server re-checks all of it; change the
 * Python first. `label`, `explain` and the defaults are UI-only.
 */
export const DEGRADATION_KINDS: Record<string, {
  label: string; types: string[]; healthy: number; band: [number, number]; unit: string
  short: string; explain: string; defRate: number; defTarget: number; step: number
}> = {
  cop: {
    label: 'Compressor efficiency loss (COP)', types: ['chiller'], healthy: 1.0, band: [0.5, 1.0],
    unit: 'efficiency multiplier', short: '×', step: 0.01, defRate: 0.01, defTarget: 0.85,
    explain: 'Refrigerant loss, worn compressor or fouled tubes: the chiller draws more kW for the same cooling. 1.0 is healthy.',
  },
  tower_approach: {
    label: 'Tower fill fouling (approach)', types: ['cooling_tower'], healthy: 0.0, band: [0, 10],
    unit: 'K added to approach', short: 'K', step: 0.1, defRate: 0.5, defTarget: 3,
    explain: 'Scaled fill or a blocked nozzle: condenser water leaves warmer for the same wet bulb.',
  },
  hx_approach: {
    label: 'Heat-exchanger fouling (approach)', types: ['chiller', 'cdu'], healthy: 0.0, band: [0, 10],
    unit: 'K added to HX approach', short: 'K', step: 0.1, defRate: 0.5, defTarget: 3,
    explain: 'Fouled tubes or plates: the leaving water sits further from the other side of the exchanger.',
  },
  filter_loading: {
    label: 'Filter loading', types: ['crah', 'cdu'], healthy: 0.0, band: [0, 1.5],
    unit: 'loading fraction (1.0 = change-filter dP)', short: 'frac', step: 0.01, defRate: 0.05, defTarget: 1.0,
    explain: 'Dust builds on the filter: differential pressure rises, airflow or flow falls. 1.0 is the change-filter point.',
  },
  pump_derate: {
    label: 'Pump wear (head derate)', types: ['pump'], healthy: 1.0, band: [0.5, 1.0],
    unit: 'head multiplier', short: '×', step: 0.01, defRate: 0.02, defTarget: 0.85,
    explain: 'Impeller wear or a failing bearing: the pump makes less head at the same speed. 1.0 is healthy.',
  },
}
export const DEGRADABLE_TYPES = new Set(Object.values(DEGRADATION_KINDS).flatMap(k => k.types))

/** Second click within 4 s confirms. Returns [armedKey, arm(key) → true when confirmed]. */
export function useArm(): [string | null, (key: string) => boolean] {
  const [armed, setArmed] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const arm = (key: string) => {
    window.clearTimeout(timer.current)
    if (armed === key) { setArmed(null); return true }
    setArmed(key)
    timer.current = window.setTimeout(() => setArmed(null), 4000)
    return false
  }
  return [armed, arm]
}

// ── device field ─────────────────────────────────────────────────────────────

/** Type-to-search device picker. Holds the typed text; resolves to an id by
 *  exact name (or id) so a half-typed name never targets the wrong unit. */
export function DeviceField({
  id, label, devices, value, onChange, help,
}: {
  id: string; label: string; devices: DeviceInfo[]; value: string | null
  onChange: (deviceId: string | null) => void; help?: string
}) {
  const current = devices.find(d => d.id === value)
  const [text, setText] = useState(current?.name ?? '')
  useEffect(() => { if (current && current.name !== text) setText(current.name) }, [value, current?.name])
  const listId = `${id}-list`
  const resolve = (t: string) => {
    setText(t)
    const hit = devices.find(d => d.name === t) ?? devices.find(d => d.id === t)
    onChange(hit ? hit.id : null)
  }
  const unresolved = text !== '' && !devices.some(d => d.name === text || d.id === text)
  return (
    <label className="fx-field" htmlFor={id}>
      <span className="lbl">{label}</span>
      <input id={id} list={listId} value={text} placeholder="Type a device name…"
        autoComplete="off" spellCheck={false}
        aria-invalid={unresolved || undefined}
        aria-describedby={`${id}-help`}
        onChange={e => resolve(e.target.value)} />
      <datalist id={listId}>
        {devices.map(d => <option key={d.id} value={d.name}>{d.device_type}</option>)}
      </datalist>
      <span id={`${id}-help`} className="help">
        {unresolved ? 'No device has that exact name — pick one from the list.'
          : current ? `${current.device_type}${current.datacenter ? ` in ${current.datacenter}` : ''}`
          : (help ?? '')}
      </span>
    </label>
  )
}

function Result({ ok, err }: { ok: string | null; err: string | null }) {
  if (err) return <div className="fx-callout crit" role="alert">{err}</div>
  if (ok) return <div className="fx-callout ok" role="status">{ok}</div>
  return null
}

// ── sensor fault ─────────────────────────────────────────────────────────────

export function SensorFaultForm({
  devices, initialDeviceId, campaign, onInjected,
}: {
  devices: DeviceInfo[]; initialDeviceId?: string | null; campaign: string
  onInjected?: () => void
}) {
  const [deviceId, setDeviceId] = useState<string | null>(initialDeviceId ?? null)
  const [points, setPoints] = useState<FaultPointsResponse | null>(null)
  const [ptState, setPtState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [ptErr, setPtErr] = useState<string | null>(null)
  const [point, setPoint] = useState('')

  const [bias, setBias] = useState(0)
  const [drift, setDrift] = useState(0)
  const [noise, setNoise] = useState(1)
  const [stuck, setStuck] = useState(false)
  const [holdValue, setHoldValue] = useState(false)
  const [stuckValue, setStuckValue] = useState(0)
  const [dropout, setDropout] = useState(0)
  const [dropWin, setDropWin] = useState(30)
  const [untilStopped, setUntilStopped] = useState(false)
  const [durMin, setDurMin] = useState(30)
  const [affects, setAffects] = useState(false)

  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // Follow the canvas selection while the operator has not picked by hand.
  useEffect(() => { if (initialDeviceId) setDeviceId(initialDeviceId) }, [initialDeviceId])

  useEffect(() => {
    setPoints(null); setPoint(''); setPtErr(null)
    if (!deviceId) { setPtState('idle'); return }
    let live = true
    setPtState('loading')
    api.faultPoints(deviceId)
      .then(r => { if (!live) return; setPoints(r); setPtState('idle'); setPoint(r.points[0]?.point ?? '') })
      .catch(e => { if (!live) return; setPtState('error'); setPtErr(errorMessage(e)) })
    return () => { live = false }
  }, [deviceId])

  const info: FaultPoint | undefined = points?.points.find(p => p.point === point)
  useEffect(() => { if (!info?.control) setAffects(false) }, [info?.control])
  const binary = !!info?.binary
  const unit = point ? unitOf(point) : 'point units'

  const noop = bias === 0 && drift === 0 && noise === 1 && !stuck && !holdValue && dropout === 0
  const problem =
    !deviceId ? 'Pick a device.'
    : !info ? 'Pick a point.'
    : noop ? 'Set at least one of bias, drift, noise, stuck or dropout.'
    : binary && (bias !== 0 || drift !== 0 || noise !== 1) ? `${point} is a binary point: only stuck or dropout apply.`
    : dropout < 0 || dropout > 100 ? 'Dropout must be 0–100 %.'
    : dropWin < 1 ? 'Dropout window must be at least 1 s.'
    : noise < 0 ? 'Noise multiplier cannot be negative.'
    : !untilStopped && !(durMin > 0) ? 'Duration must be above zero, or choose "until stopped".'
    : null

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (problem || !deviceId || !info) return
    setBusy(true); setOk(null); setErr(null)
    try {
      const r = await api.addSensorFault({
        device: deviceId, point,
        bias: binary ? 0 : bias, drift_per_h: binary ? 0 : drift, noise_mult: binary ? 1 : noise,
        stuck: stuck || holdValue, stuck_value: holdValue ? stuckValue : null,
        dropout_pct: dropout, dropout_window_s: dropWin,
        duration_s: untilStopped ? null : durMin * 60,
        affects_control: info.control && affects,
        campaign: campaign.trim() || null,
      })
      setOk(`Injected ${r.id} on ${points?.name}/${point}.`)
      onInjected?.()
    } catch (e2) { setErr(errorMessage(e2)) }
    finally { setBusy(false) }
  }

  const controlPts = points?.points.filter(p => p.control) ?? []
  const monitorPts = points?.points.filter(p => !p.control) ?? []

  return (
    <form className="fx-pad" onSubmit={submit} noValidate aria-label="Inject a sensor fault">
      <p className="fx-note">
        The reading lies; the process stays true. The rack, the room and the plant carry on
        exactly as before, unless you let a control sensor's error drive its controller.
      </p>

      <DeviceField id="fx-sf-dev" label="Device" devices={devices} value={deviceId}
        onChange={setDeviceId} help="Defaults to the device selected on the canvas." />

      <label className="fx-field" htmlFor="fx-sf-pt">
        <span className="lbl">Point</span>
        {ptState === 'loading' ? <span className="help">Loading points…</span>
          : ptState === 'error' ? <span className="fx-callout crit" role="alert">{ptErr}</span>
          : points && points.points.length === 0
            ? <span className="help">{points.name} has no injectable sensor points (network gear reports
                no process readings). Pick a server, sensor, power or plant device.</span>
          : <select id="fx-sf-pt" value={point} disabled={!points} onChange={e => setPoint(e.target.value)}>
              {!points && <option value="">Pick a device first</option>}
              {controlPts.length > 0 && (
                <optgroup label="Control sensors (in a control loop)">
                  {controlPts.map(p => <option key={p.point} value={p.point}>{p.point}</option>)}
                </optgroup>
              )}
              {monitorPts.length > 0 && (
                <optgroup label={controlPts.length ? 'Monitoring points' : 'Points'}>
                  {monitorPts.map(p => <option key={p.point} value={p.point}>{p.point}{p.binary ? ' (binary)' : ''}</option>)}
                </optgroup>
              )}
            </select>}
        {info && (
          <span className="help">
            {info.control ? 'Control sensor. ' : ''}
            Served over {info.protocols.join(', ') || 'no protocol'}.
            {info.metric_hint ? ` Platform metric ${info.metric_hint.metric}${info.metric_hint.instance ? `/${info.metric_hint.instance}` : ''}.` : ''}
            {info.store_published && !info.control ? ' The store rewrites this point every tick; the fault still reaches the wire.' : ''}
          </span>
        )}
      </label>

      {info && (
        <>
          <div className="fx-grid2">
            <label className="fx-field">
              <span className="lbl">Bias</span>
              <span className="fx-unit">
                <NumberInput value={bias} onChange={setBias} step={0.1} disabled={binary} title="Constant offset added to the reading" />
                <span className="u">{unit}</span>
              </span>
            </label>
            <label className="fx-field">
              <span className="lbl">Drift</span>
              <span className="fx-unit">
                <NumberInput value={drift} onChange={setDrift} step={0.1} disabled={binary} title="Offset that grows from zero at this rate" />
                <span className="u">{unit === 'point units' ? 'per h' : `${unit}/h`}</span>
              </span>
            </label>
            <label className="fx-field">
              <span className="lbl">Noise</span>
              <span className="fx-unit">
                <NumberInput value={noise} onChange={setNoise} fallback={1} min={0} step={0.5} disabled={binary} title="1 = the point's normal noise" />
                <span className="u">× normal</span>
              </span>
            </label>
            <label className="fx-field">
              <span className="lbl">Dropout</span>
              <span className="fx-unit">
                <NumberInput value={dropout} onChange={setDropout} min={0} max={100} step={5} title="Share of windows with no reading" />
                <span className="u">% of</span>
                <NumberInput value={dropWin} onChange={setDropWin} fallback={30} min={1} step={5} title="Window length" style={{ maxWidth: 56 }} />
                <span className="u">s</span>
              </span>
            </label>
          </div>

          <div className="fx-field">
            <label className="fx-check">
              <input type="checkbox" checked={stuck} onChange={e => setStuck(e.target.checked)} />
              <span>Stuck — freeze at the value it reads when the fault starts</span>
            </label>
            <label className="fx-check">
              <input type="checkbox" checked={holdValue} onChange={e => setHoldValue(e.target.checked)} />
              <span>Stuck at a chosen value</span>
            </label>
            {holdValue && (
              <span className="fx-unit" style={{ paddingLeft: 20 }}>
                <NumberInput value={stuckValue} onChange={setStuckValue} step={binary ? 1 : 0.1} title="Value the reading holds" />
                <span className="u">{binary ? '0 / 1' : unit}</span>
              </span>
            )}
          </div>

          <div className="fx-field">
            <span className="lbl">Duration</span>
            <span className="fx-unit">
              <NumberInput value={durMin} onChange={setDurMin} fallback={30} min={1} step={5} disabled={untilStopped} title="Minutes until the fault ends by itself" />
              <span className="u">min</span>
            </span>
            <label className="fx-check">
              <input type="checkbox" checked={untilStopped} onChange={e => setUntilStopped(e.target.checked)} />
              <span>Until stopped by hand</span>
            </label>
          </div>

          {info.control && (
            <div className="fx-field">
              <label className="fx-check">
                <input type="checkbox" checked={affects} onChange={e => setAffects(e.target.checked)} />
                <span>Let the controller act on the false reading</span>
              </label>
              {affects
                ? <div className="fx-callout" role="note">
                    The unit will control to the wrong number. A supply sensor reading 3 K high makes the
                    unit drive the real discharge about 3 K low — the room, the plant and every protocol
                    change, and the platform sees process symptoms, not only a bad sensor.
                  </div>
                : <span className="help">Off: only the reported value lies; the controller still sees the truth.</span>}
            </div>
          )}
        </>
      )}

      <Result ok={ok} err={err} />
      <div className="fx-actions">
        <button type="submit" className="primary" disabled={!!problem || busy} aria-busy={busy}>
          {busy ? 'Injecting…' : 'Inject sensor fault'}
        </button>
      </div>
      {problem && info && <span className="fx-note">{problem}</span>}
    </form>
  )
}

// ── degradation ──────────────────────────────────────────────────────────────

export function DegradeForm({
  devices, initialDeviceId, campaign, active, onStarted,
}: {
  devices: DeviceInfo[]; initialDeviceId?: string | null; campaign: string
  active: ActiveFault[]; onStarted?: () => void
}) {
  const eligible = useMemo(() => devices.filter(d => DEGRADABLE_TYPES.has(d.device_type)), [devices])
  const startId = initialDeviceId && eligible.some(d => d.id === initialDeviceId) ? initialDeviceId : null
  const [deviceId, setDeviceId] = useState<string | null>(startId)
  useEffect(() => { if (startId) setDeviceId(startId) }, [startId])

  const dev = eligible.find(d => d.id === deviceId)
  const kinds = dev ? Object.entries(DEGRADATION_KINDS).filter(([, k]) => k.types.includes(dev.device_type)) : []
  const [kind, setKind] = useState('')
  useEffect(() => {
    if (!kinds.some(([k]) => k === kind)) setKind(kinds[0]?.[0] ?? '')
  }, [deviceId])
  const spec = kind ? DEGRADATION_KINDS[kind] : undefined

  const [rate, setRate] = useState(0)
  const [target, setTarget] = useState(0)
  useEffect(() => { if (spec) { setRate(spec.defRate); setTarget(spec.defTarget) } }, [kind])

  // Where the machine is NOW: an active or held degradation of this kind on
  // this unit, else healthy. Equipment does not heal itself, so a target on
  // the healthy side of this is refused.
  const existing = active.find(a => a.kind === 'degradation' && a.device === dev?.name
    && (a.params as { degradation?: string }).degradation === kind)
  const ramping = !!existing && !(existing.params as { held?: boolean }).held
  const currentVal = existing?.current?.value ?? spec?.healthy ?? 0

  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const problem =
    !dev ? 'Pick a chiller, cooling tower, CDU, CRAH or pump.'
    : !spec ? 'Pick a kind.'
    : ramping ? `${dev.name} already has a ${kind} degradation running. Stop it on the Active tab first.`
    : !(rate > 0) ? 'Rate must be above zero.'
    : target < spec.band[0] || target > spec.band[1] ? `Target must sit within ${spec.band[0]}–${spec.band[1]} (${spec.unit}).`
    : Math.abs(target - spec.healthy) < Math.abs(currentVal - spec.healthy) - 1e-9
      ? `The unit is already at ${currentVal.toFixed(3)}; a target nearer healthy would mean it healed itself. Restore it instead.`
    : null

  const etaS = spec && rate > 0 ? Math.abs(target - currentVal) / rate * 3600 : NaN

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (problem || !dev || !spec) return
    setBusy(true); setOk(null); setErr(null)
    try {
      const r = await api.addDegradation({ device: dev.id, kind, rate_per_h: rate, target,
                                           campaign: campaign.trim() || null })
      setOk(`Started ${r.id}: ${dev.name} ${kind} toward ${target}.`)
      onStarted?.()
    } catch (e2) { setErr(errorMessage(e2)) }
    finally { setBusy(false) }
  }

  return (
    <form className="fx-pad" onSubmit={submit} noValidate aria-label="Start a degradation">
      <p className="fx-note">
        The machine itself wears. Physics changes, so every protocol shows the consequence —
        higher kW, a wider approach, a climbing filter dP. It advances on simulator time
        toward the target, then holds there.
      </p>

      <DeviceField id="fx-dg-dev" label="Device" devices={eligible} value={deviceId}
        onChange={setDeviceId} help="Chillers, cooling towers, CDUs, CRAHs and pumps." />

      {dev && (
        <label className="fx-field" htmlFor="fx-dg-kind">
          <span className="lbl">Kind</span>
          <select id="fx-dg-kind" value={kind} onChange={e => setKind(e.target.value)}>
            {kinds.map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
          </select>
          {spec && <span className="help">{spec.explain}</span>}
        </label>
      )}

      {spec && (
        <>
          <div className="fx-grid2">
            <label className="fx-field">
              <span className="lbl">Rate</span>
              <span className="fx-unit">
                <NumberInput value={rate} onChange={setRate} min={0} step={spec.step} title={`${spec.unit} per hour`} />
                <span className="u">{spec.short}/h</span>
              </span>
            </label>
            <label className="fx-field">
              <span className="lbl">Target</span>
              <span className="fx-unit">
                <NumberInput value={target} onChange={setTarget} min={spec.band[0]} max={spec.band[1]} step={spec.step}
                  fallback={spec.defTarget} title={`${spec.band[0]}–${spec.band[1]} ${spec.unit}`} />
                <span className="u">{spec.short}</span>
              </span>
            </label>
          </div>
          <span className="fx-note">
            {spec.unit}; healthy is {spec.healthy}. Now {existing ? `held at ${currentVal.toFixed(3)}` : 'healthy'}.
            {Number.isFinite(etaS) && !problem ? ` Reaches the target in about ${fmtDur(etaS)} of simulator time.` : ''}
          </span>
        </>
      )}

      <Result ok={ok} err={err} />
      <div className="fx-actions">
        <button type="submit" className="warn" disabled={!!problem || busy} aria-busy={busy}>
          {busy ? 'Starting…' : 'Start degradation'}
        </button>
      </div>
      {problem && spec && <span className="fx-note">{problem}</span>}
      <span className="fx-note">
        Stopping it later leaves the unit degraded at whatever value it reached. Only
        "Restore (maintenance)" returns it to healthy, and that is ledgered as maintenance.
      </span>
    </form>
  )
}
