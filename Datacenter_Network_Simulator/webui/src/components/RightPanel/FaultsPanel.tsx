import { useCallback, useEffect, useMemo, useState } from 'react'
import { useStore } from '../../store/useStore'
import { api, errorMessage } from '../../api/client'
import type { ActiveFault, LedgerEntry, WeatherSite } from '../../api/types'
import NumberInput from '../NumberInput'
import {
  DegradeForm, SensorFaultForm, fmtDur, fmtLocal, isoMs, unitOf, useArm, DEGRADATION_KINDS,
} from './FaultInjectForms'

/**
 * Fault Injection — anomaly ground truth (doc 32 §12, task S2-1).
 *
 * Everything an operator injects here is ledgered server-side
 * (GET /api/faults/ledger), which is what the platform's detectors are scored
 * against. Two families, and the UI keeps them visibly apart:
 *   sensor fault  — the READING lies, the process is true   (rail --info)
 *   degradation   — the MACHINE changed, physics follows    (rail --warn)
 * Weather, setpoints and ramps are context the scorer excludes (rail grey).
 */

type View = 'active' | 'sensor' | 'degrade' | 'weather' | 'ledger'
const VIEWS: { id: View; label: string }[] = [
  { id: 'active', label: 'Active' },
  { id: 'sensor', label: 'Sensor' },
  { id: 'degrade', label: 'Degrade' },
  { id: 'weather', label: 'Weather' },
  { id: 'ledger', label: 'Ledger' },
]
const POLL_MS = 5000

// core/fault_ledger.py KINDS — the ledger's own vocabulary.
const LEDGER_KINDS = ['sensor', 'degradation', 'maintenance', 'override', 'ramp', 'setpoint',
  'fault_menu', 'pin', 'electrical', 'fire', 'link', 'power', 'weather', 'context']
const PAGE_SIZE = 50

export default function FaultsPanel() {
  const { devices, selectedDeviceId } = useStore()
  const [view, setView] = useState<View>('active')
  const [campaign, setCampaign] = useState('')

  const [active, setActive] = useState<ActiveFault[] | null>(null)
  const [activeErr, setActiveErr] = useState<string | null>(null)
  const refreshActive = useCallback(() => {
    api.faultsActive()
      .then(r => { setActive(r.active); setActiveErr(null) })
      .catch(e => setActiveErr(errorMessage(e)))
  }, [])
  useEffect(() => {
    refreshActive()
    const t = window.setInterval(refreshActive, POLL_MS)
    return () => window.clearInterval(t)
  }, [refreshActive])

  // Seed + air mode ride on every ledger record (core/fault_ledger.sim_info);
  // there is no status endpoint for them, so read the newest entry.
  const [sim, setSim] = useState<LedgerEntry['sim'] | null>(null)
  const refreshSim = useCallback(() => {
    api.ledger({ limit: 1 }).then(r => setSim(r.entries[0]?.sim ?? null)).catch(() => {})
  }, [])
  useEffect(() => { refreshSim() }, [refreshSim])

  const afterChange = () => { refreshActive(); refreshSim() }
  const count = active?.length ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">Fault Injection</span>
        <SimChip sim={sim} />
      </div>

      <div className="fx-seg" role="tablist" aria-label="Fault injection sections">
        {VIEWS.map(v => (
          <button key={v.id} role="tab" id={`fx-tab-${v.id}`} aria-controls="fx-view"
            aria-selected={view === v.id} onClick={() => setView(v.id)}>
            {v.label}
            {v.id === 'active' && count > 0 && <span className="fx-count" aria-label={`${count} active`}>{count}</span>}
          </button>
        ))}
      </div>

      <div className="fx-body" id="fx-view" role="tabpanel" aria-labelledby={`fx-tab-${view}`}>
        {(view === 'sensor' || view === 'degrade' || view === 'weather') && (
          <div style={{ padding: '10px 10px 0' }}>
            <label className="fx-field" htmlFor="fx-campaign">
              <span className="lbl">Campaign tag (optional)</span>
              <input id="fx-campaign" value={campaign} placeholder="e.g. crah-supply-bias"
                autoComplete="off" spellCheck={false} onChange={e => setCampaign(e.target.value)} />
              <span className="help">Written on the ledger entry so a run can be filtered and scored as one.</span>
            </label>
          </div>
        )}
        {view === 'active' && (
          <ActiveView active={active} err={activeErr} devices={devices}
            onRetry={refreshActive} onChanged={afterChange} onGo={setView} />
        )}
        {view === 'sensor' && (
          <SensorFaultForm devices={devices} initialDeviceId={selectedDeviceId} campaign={campaign}
            onInjected={afterChange} />
        )}
        {view === 'degrade' && (
          <DegradeForm devices={devices} initialDeviceId={selectedDeviceId} campaign={campaign}
            active={active ?? []} onStarted={afterChange} />
        )}
        {view === 'weather' && <WeatherView campaign={campaign} onChanged={afterChange} />}
        {view === 'ledger' && <LedgerView devices={devices} />}
      </div>
    </div>
  )
}

function SimChip({ sim }: { sim: LedgerEntry['sim'] | null }) {
  if (!sim) return null
  const spatial = sim.air_mode === 'spatial'
  return (
    <span style={{ display: 'flex', gap: 4 }}>
      <span className={`fx-chip ${spatial ? 'ok' : 'warn'}`}
        title={spatial ? 'Spatial air model: per-rack CRAH influence is real.'
          : 'Not the spatial air model. Restart the simulator with DCIM_SIM_AIR_MODEL=spatial before a scored campaign.'}>
        air {sim.air_mode ?? '?'}{spatial ? '' : ' (not spatial)'}
      </span>
      <span className="fx-chip" title="Global seed, from the newest ledger entry">seed {sim.seed ?? '?'}</span>
    </span>
  )
}

// ── Active ───────────────────────────────────────────────────────────────────

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), ms); return () => window.clearInterval(t) }, [ms])
  return now
}

const num = (v: unknown, d = 2) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—'
const signed = (v: number, d = 2) => (v > 0 ? '+' : '') + v.toFixed(d)

function ActiveView({ active, err, devices, onRetry, onChanged, onGo }: {
  active: ActiveFault[] | null; err: string | null
  devices: { id: string; name: string }[]
  onRetry: () => void; onChanged: () => void; onGo: (v: View) => void
}) {
  const now = useNow()
  const [armed, arm] = useArm()
  const [busy, setBusy] = useState<string | null>(null)
  const [actErr, setActErr] = useState<string | null>(null)
  const [weather, setWeather] = useState<WeatherSite[]>([])
  useEffect(() => { api.weather().then(r => setWeather(r.weather)).catch(() => {}) }, [active?.length])

  if (err && !active) {
    return (
      <div className="fx-empty" role="alert">
        Could not load active faults. {err}
        <div className="fx-actions"><button onClick={onRetry}>Retry</button></div>
      </div>
    )
  }
  if (!active) return <div className="fx-empty" aria-busy="true">Loading active faults…</div>
  if (active.length === 0) {
    return (
      <div className="fx-empty">
        Nothing injected. The simulator is reporting the truth everywhere.
        <div className="fx-actions">
          <button onClick={() => onGo('sensor')}>Inject a sensor fault</button>
          <button onClick={() => onGo('degrade')}>Start a degradation</button>
        </div>
      </div>
    )
  }

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key); setActErr(null)
    try { await fn(); onChanged() }
    catch (e) { setActErr(errorMessage(e)) }
    finally { setBusy(null) }
  }

  const idOf = (a: ActiveFault) => a.device_id ?? devices.find(d => d.name === a.device)?.id ?? null
  const rows = [...active].sort((a, b) => (isoMs(b.start) ?? 0) - (isoMs(a.start) ?? 0))

  return (
    <div>
      {err && <div className="fx-callout crit" role="alert" style={{ margin: 8 }}>Refresh failed: {err}</div>}
      {actErr && <div className="fx-callout crit" role="alert" style={{ margin: 8 }}>{actErr}</div>}
      {rows.map((a, i) => {
        const key = `${a.kind}:${a.id ?? `${a.device}/${a.point}/${a.city}/${i}`}`
        const p = a.params as Record<string, unknown>
        const started = isoMs(a.start)
        const age = started !== null ? (now - started) / 1000 : null
        let family = 'k-context', kindLabel = a.kind as string, live: React.ReactNode = null
        let when: string | null = null, frac: number | null = null
        let actions: React.ReactNode = null

        if (a.kind === 'sensor') {
          family = 'k-sensor'; kindLabel = 'Sensor'
          const u = a.point ? unitOf(a.point) : ''
          const unitTxt = u === 'point units' ? '' : ` ${u}`
          const bits: string[] = []
          if (a.current?.stuck || p.stuck_value !== undefined) bits.push(`stuck at ${num(a.current?.held ?? p.stuck_value)}${unitTxt}`)
          if ((p.bias as number) || (p.drift_per_h as number)) bits.push(`bias now ${signed(a.current?.bias ?? 0)}${unitTxt}`)
          if ((p.noise_mult as number) !== 1) bits.push(`noise ×${num(p.noise_mult, 1)}`)
          if ((p.dropout_pct as number) > 0) bits.push(`${num(p.dropout_pct, 0)} % dropout${a.current?.in_dropout ? ' (dropped now)' : ''}`)
          live = <>{bits.join(', ')}{p.affects_control ? <b> — controller acting on it</b> : null}</>
          const dur = p.duration_s as number | null
          if (dur && started !== null) {
            const left = started / 1000 + dur - now / 1000
            when = left > 0 ? `${fmtDur(left)} left` : 'ending'
            frac = Math.min(1, Math.max(0, 1 - left / dur))
          } else when = 'until stopped'
          actions = (
            <button className="danger" disabled={busy === key || !a.id}
              onClick={() => a.id && run(key, () => api.deleteFault(a.id!))}>
              {busy === key ? 'Stopping…' : 'Stop'}
            </button>
          )
        } else if (a.kind === 'degradation') {
          family = 'k-degradation'; kindLabel = 'Degradation'
          const dk = p.degradation as string
          const spec = DEGRADATION_KINDS[dk]
          const held = !!p.held
          const v = a.current?.value
          live = <>{spec?.label ?? dk}: <b>{num(v, 3)}</b>{held ? '' : ` → ${num(p.target, 3)}`} {spec?.short ?? ''}</>
          if (held) when = 'stopped, still degraded'
          else if (a.current?.reached) when = 'holding at target'
          else if (a.current?.eta_ts) {
            const left = a.current.eta_ts - now / 1000
            when = left > 0 ? `target in ${fmtDur(left)}` : 'reaching target'
            if (spec && typeof v === 'number' && typeof p.from === 'number' && typeof p.target === 'number' && p.target !== p.from)
              frac = Math.min(1, Math.max(0, (v - (p.from as number)) / ((p.target as number) - (p.from as number))))
          }
          const restoreKey = key + ':restore'
          actions = (
            <>
              {!held && (
                <button className="warn" disabled={!!busy}
                  title="Stops the wear. The unit keeps the value it has reached until it is restored."
                  onClick={() => run(key, () => api.deleteFault(a.id!))}>
                  {busy === key ? 'Stopping…' : 'Stop (stays degraded)'}
                </button>
              )}
              <button className={armed === restoreKey ? 'danger' : ''} disabled={!!busy}
                title="Maintenance: clean or repair the unit back to healthy. Ledgered as a separate maintenance event."
                onClick={() => { if (arm(restoreKey)) run(restoreKey, () => api.deleteFault(a.id!, true)) }}>
                {busy === restoreKey ? 'Restoring…' : armed === restoreKey ? 'Confirm restore to healthy' : 'Stop and restore (maintenance)'}
              </button>
            </>
          )
        } else if (a.kind === 'weather') {
          kindLabel = 'Weather'
          const site = weather.find(w => w.city === a.city)
          const until = isoMs(a.current?.until)
          live = <>dry {signed(p.dry_bulb_offset_c as number, 1)} K, wet {signed(p.wet_bulb_offset_c as number, 1)} K
            {site ? <>, outdoor now <b>{site.dry_bulb_c.toFixed(1)} °C</b></> : null}</>
          when = until !== null ? (until > now ? `${fmtDur((until - now) / 1000)} left` : 'ending') : null
          actions = (
            <button disabled={busy === key || !site}
              onClick={() => site && run(key, () => api.clearWeather(site.datacenter))}>
              {busy === key ? 'Clearing…' : 'Clear'}
            </button>
          )
        } else {
          kindLabel = a.kind === 'setpoint' ? 'Setpoint' : 'Ramp'
          live = <>value <b>{num(a.current?.value)}</b>{a.current?.reached ? ' (reached)' : ''}</>
          const devId = idOf(a)
          actions = (
            <button disabled={busy === key || !devId || !a.point}
              title="Release the operator override; the point returns to its own control."
              onClick={() => devId && a.point && run(key, () => api.setPlantOverride(devId, a.point!, null))}>
              {busy === key ? 'Releasing…' : 'Release override'}
            </button>
          )
        }

        return (
          <div key={key} className={`fx-row ${family}`}>
            <div className="fx-row-head">
              <span className="fx-kind">{kindLabel}</span>
              <span className="fx-target">
                {a.kind === 'weather' ? a.city : a.device ?? '—'}
                {a.point && <> <span className="pt">{a.point}</span></>}
              </span>
            </div>
            {live && <div className="fx-live">{live}</div>}
            <div className="fx-live">
              {when}{age !== null ? `${when ? '; ' : ''}started ${fmtDur(age)} ago` : ''}
            </div>
            {frac !== null && <div className="fx-meter" aria-hidden="true"><span style={{ width: `${frac * 100}%` }} /></div>}
            {a.kind === 'degradation' && armed === `${key}:restore` && (
              <div className="fx-note warn">Restoring resets the unit to healthy as if it was serviced. Click again to confirm.</div>
            )}
            <div className="fx-actions">{actions}</div>
          </div>
        )
      })}
      <p className="fx-note" style={{ padding: '8px 10px' }}>
        Stopping a degradation does not repair the equipment — a fouled coil does not unfoul.
        Use "Stop and restore (maintenance)" when the unit has been serviced.
      </p>
    </div>
  )
}

// ── Weather ──────────────────────────────────────────────────────────────────

function WeatherView({ campaign, onChanged }: { campaign: string; onChanged: () => void }) {
  const [sites, setSites] = useState<WeatherSite[] | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const load = useCallback(() => {
    api.weather().then(r => { setSites(r.weather); setLoadErr(null) }).catch(e => setLoadErr(errorMessage(e)))
  }, [])
  useEffect(() => { load(); const t = window.setInterval(load, POLL_MS); return () => window.clearInterval(t) }, [load])

  const [dc, setDc] = useState('')            // '' = every site
  const [dry, setDry] = useState(5)
  const [followDry, setFollowDry] = useState(true)
  const [wet, setWet] = useState(3)
  const [minutes, setMinutes] = useState(120)
  const [busy, setBusy] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const wetEff = followDry ? dry : wet
  const problem =
    Math.abs(dry) > 25 || Math.abs(wetEff) > 25 ? 'Offsets must stay within ±25 K.'
    : dry === 0 && wetEff === 0 ? 'Set a dry or wet bulb offset.'
    : !(minutes > 0) ? 'Duration must be above zero.'
    : null

  async function apply(e: React.FormEvent) {
    e.preventDefault()
    if (problem) return
    setBusy('apply'); setOk(null); setErr(null)
    try {
      await api.setWeather({ datacenter: dc || null, dry_bulb_offset_c: dry,
        wet_bulb_offset_c: followDry ? null : wet, duration_s: minutes * 60,
        campaign: campaign.trim() || null })
      setOk(`Weather override set on ${dc || 'every site'} for ${fmtDur(minutes * 60)}.`)
      load(); onChanged()
    } catch (e2) { setErr(errorMessage(e2)) }
    finally { setBusy(null) }
  }
  async function clear(datacenter?: string) {
    const k = datacenter ?? '*'
    setBusy(k); setOk(null); setErr(null)
    try { await api.clearWeather(datacenter); load(); onChanged() }
    catch (e2) { setErr(errorMessage(e2)) }
    finally { setBusy(null) }
  }

  const anyOverride = sites?.some(s => s.override)

  return (
    <div className="fx-pad">
      <p className="fx-note">
        Moves the outdoor air for a site. Chiller kW, the economiser, condenser water and the
        tower's outdoor-air points all follow. Ledgered as context: the platform should explain
        the shift, not alarm on it.
      </p>

      {loadErr && !sites && <div className="fx-callout crit" role="alert">Could not load weather. {loadErr} <button onClick={load}>Retry</button></div>}
      {!sites && !loadErr && <div className="fx-note" aria-busy="true">Loading sites…</div>}
      {sites && sites.length === 0 && <div className="fx-note">No datacenters in the topology.</div>}
      {sites && sites.length > 0 && (
        <table className="fx-sites">
          <caption className="fx-note" style={{ textAlign: 'left', paddingBottom: 4 }}>Outdoor air now</caption>
          <thead><tr><th scope="col">Site</th><th scope="col">Dry</th><th scope="col">Wet</th><th scope="col">Override</th></tr></thead>
          <tbody>
            {sites.map(s => (
              <tr key={s.datacenter}>
                <td>{s.datacenter}<div className="fx-note">{s.city}</div></td>
                <td>{s.dry_bulb_c.toFixed(1)} °C</td>
                <td>{s.wet_bulb_c.toFixed(1)} °C</td>
                <td className={s.override ? 'off' : ''}>
                  {s.override
                    ? <>
                        {signed(s.override.dry_bulb_offset_c, 1)} / {signed(s.override.wet_bulb_offset_c, 1)} K
                        <div className="fx-note">until {fmtLocal(s.override.until)}</div>
                        <button style={{ fontSize: 10, padding: '2px 8px', marginTop: 3 }} disabled={busy === s.datacenter}
                          aria-label={`Clear the weather override on ${s.datacenter}`}
                          onClick={() => clear(s.datacenter)}>{busy === s.datacenter ? 'Clearing…' : 'Clear'}</button>
                      </>
                    : <span style={{ color: 'var(--text-dim)' }}>none</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form onSubmit={apply} noValidate className="group-box" aria-label="Set a weather override"
        style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
        <span className="group-box-label">Set override</span>
        <label className="fx-field" htmlFor="fx-wx-dc">
          <span className="lbl">Site</span>
          <select id="fx-wx-dc" value={dc} onChange={e => setDc(e.target.value)}>
            <option value="">Every site</option>
            {sites?.map(s => <option key={s.datacenter} value={s.datacenter}>{s.datacenter} ({s.city})</option>)}
          </select>
        </label>
        <div className="fx-grid2">
          <label className="fx-field">
            <span className="lbl">Dry bulb offset</span>
            <span className="fx-unit"><NumberInput value={dry} onChange={setDry} min={-25} max={25} step={0.5} /><span className="u">K</span></span>
          </label>
          <label className="fx-field">
            <span className="lbl">Wet bulb offset</span>
            <span className="fx-unit"><NumberInput value={wetEff} onChange={setWet} min={-25} max={25} step={0.5} disabled={followDry} /><span className="u">K</span></span>
          </label>
        </div>
        <label className="fx-check">
          <input type="checkbox" checked={followDry} onChange={e => { setFollowDry(e.target.checked); if (!e.target.checked) setWet(dry) }} />
          <span>Wet bulb moves with the dry bulb (same depression). Untick for a humid or dry spell.</span>
        </label>
        <label className="fx-field">
          <span className="lbl">For</span>
          <span className="fx-unit"><NumberInput value={minutes} onChange={setMinutes} fallback={120} min={1} step={15} /><span className="u">min</span></span>
          <span className="help">Ends at about {new Date(Date.now() + minutes * 60000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })} local time.</span>
        </label>
        {err && <div className="fx-callout crit" role="alert">{err}</div>}
        {ok && <div className="fx-callout ok" role="status">{ok}</div>}
        <div className="fx-actions">
          <button type="submit" className="primary" disabled={!!problem || !!busy}>{busy === 'apply' ? 'Applying…' : 'Apply override'}</button>
          {anyOverride && <button type="button" disabled={!!busy} onClick={() => clear()}>{busy === '*' ? 'Clearing…' : 'Clear every site'}</button>}
        </div>
        {problem && <span className="fx-note">{problem}</span>}
      </form>
    </div>
  )
}

// ── Ledger ───────────────────────────────────────────────────────────────────

function LedgerView({ devices }: { devices: { id: string; name: string }[] }) {
  const [kind, setKind] = useState('')
  const [device, setDevice] = useState('')
  const [campaign, setCampaign] = useState('')
  // Cursor stack: [0] is the newest page (no cursor); "Older" pushes next_cursor.
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [page, setPage] = useState<{ entries: LedgerEntry[]; next: string | null } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // Device filter matches the ledger's NAME or id exactly; resolve a typed
  // name so a partial string does not silently empty the table.
  const devKey = useMemo(() => {
    const t = device.trim()
    return t ? (devices.find(d => d.name === t || d.id === t)?.name ?? t) : ''
  }, [device, devices])

  const cursor = cursors[cursors.length - 1]
  const load = useCallback(() => {
    setLoading(true)
    api.ledger({ kind: kind || undefined, device: devKey || undefined,
                 campaign: campaign.trim() || undefined, limit: PAGE_SIZE, cursor })
      .then(r => { setPage({ entries: r.entries, next: r.next_cursor }); setErr(null) })
      .catch(e => setErr(errorMessage(e)))
      .finally(() => setLoading(false))
  }, [kind, devKey, campaign, cursor])
  useEffect(() => { load() }, [load])
  // A filter change starts again from the newest page.
  useEffect(() => { setCursors([null]) }, [kind, devKey, campaign])

  async function copy() {
    if (!page) return
    try {
      await navigator.clipboard.writeText(JSON.stringify(page.entries, null, 2))
      setCopied(true); window.setTimeout(() => setCopied(false), 2000)
    } catch { setErr('The browser blocked clipboard access.') }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="fx-pad" style={{ paddingBottom: 6 }}>
        <div className="fx-grid2">
          <label className="fx-field" htmlFor="fx-lg-kind">
            <span className="lbl">Kind</span>
            <select id="fx-lg-kind" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="">All kinds</option>
              {LEDGER_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>
          <label className="fx-field" htmlFor="fx-lg-cmp">
            <span className="lbl">Campaign</span>
            <input id="fx-lg-cmp" value={campaign} placeholder="Any" autoComplete="off" spellCheck={false}
              onChange={e => setCampaign(e.target.value)} />
          </label>
        </div>
        <label className="fx-field" htmlFor="fx-lg-dev">
          <span className="lbl">Device</span>
          <input id="fx-lg-dev" list="fx-lg-dev-list" value={device} placeholder="Any device" autoComplete="off"
            spellCheck={false} onChange={e => setDevice(e.target.value)} />
          <datalist id="fx-lg-dev-list">{devices.map(d => <option key={d.id} value={d.name} />)}</datalist>
        </label>
      </div>

      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {err && <div className="fx-callout crit" role="alert" style={{ margin: 8 }}>Could not load the ledger. {err} <button onClick={load}>Retry</button></div>}
        {!page && !err && <div className="fx-empty" aria-busy="true">Loading ledger…</div>}
        {page && page.entries.length === 0 && (
          <div className="fx-empty">
            {kind || devKey || campaign ? 'No ledger entries match these filters.' : 'The ledger is empty. Every injection, from every channel, will be recorded here.'}
          </div>
        )}
        {page && page.entries.length > 0 && (
          <table className="fx-ledger" aria-busy={loading}>
            <thead>
              <tr>
                <th scope="col" style={{ width: 84 }}>Started</th>
                <th scope="col" style={{ width: 72 }}>Kind</th>
                <th scope="col">Device / point</th>
                <th scope="col" style={{ width: 70 }}>Ended</th>
              </tr>
            </thead>
            <tbody>
              {page.entries.map(e => {
                const isOpen = open === e.id
                const dur = e.end && e.start ? ((isoMs(e.end) ?? 0) - (isoMs(e.start) ?? 0)) / 1000 : null
                return [
                  <tr key={e.id} className="fx-lrow" tabIndex={0} aria-expanded={isOpen}
                    onClick={() => setOpen(isOpen ? null : e.id)}
                    onKeyDown={ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); setOpen(isOpen ? null : e.id) } }}>
                    <td className="mono" title={e.start ?? ''}>{fmtLocal(e.start, true)}</td>
                    <td>{e.kind}{e.campaign && <div className="fx-note" title={`campaign ${e.campaign}`} style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.campaign}</div>}</td>
                    <td title={`${e.device ?? ''} ${e.point ?? ''}`}>
                      {e.device ?? (e.params?.city as string) ?? (e.params?.context as string) ?? '—'}
                      {e.point && <div className="fx-note mono">{e.point}</div>}
                    </td>
                    <td className={e.end ? 'dim' : 'open'}>
                      {e.end ? <>{e.end_reason}{dur !== null && <div className="fx-note">{fmtDur(dur)}</div>}</> : 'open'}
                    </td>
                  </tr>,
                  isOpen && (
                    <tr key={e.id + ':d'}>
                      <td colSpan={4} style={{ whiteSpace: 'normal' }}>
                        <pre>{JSON.stringify(e, null, 2)}</pre>
                      </td>
                    </tr>
                  ),
                ]
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="fx-pager">
        <button disabled={cursors.length <= 1 || loading} onClick={() => setCursors(c => c.slice(0, -1))}>Newer</button>
        <button disabled={!page?.next || loading} onClick={() => page?.next && setCursors(c => [...c, page.next])}>Older</button>
        <span>Page {cursors.length}{loading ? ', loading…' : ''}</span>
        <span className="grow" />
        <button disabled={!page?.entries.length} onClick={copy} title="Copy this page of entries as JSON">
          {copied ? 'Copied' : 'Copy as JSON'}
        </button>
      </div>
    </div>
  )
}
