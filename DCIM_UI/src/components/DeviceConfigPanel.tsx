import { useEffect, useMemo, useState } from 'react'
import { useDeviceConfig, useUpdateDeviceConfig } from '@/hooks/useAgents'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Settings2, Save, RotateCcw, ServerCog, Lightbulb, Power, AlertTriangle } from 'lucide-react'
import type {
  DeviceConfig,
  DeviceConfigUpdate,
  DeviceConfigAsset,
  DeviceConfigIdentity,
  DeviceConfigRedfish,
} from '@/lib/api'

// ── Field metadata ───────────────────────────────────────────────────────────

const IDENTITY_FIELDS: { key: keyof DeviceConfigIdentity; label: string; oid: string }[] = [
  { key: 'sysName',     label: 'sysName',     oid: '1.3.6.1.2.1.1.5.0' },
]

const ASSET_FIELDS: { key: keyof DeviceConfigAsset; label: string; oid: string; numeric?: boolean; readonly?: boolean }[] = [
  { key: 'country',         label: 'Country',         oid: '…99999.4.1.0', readonly: true },
  { key: 'datacenter_city', label: 'Datacenter city', oid: '…99999.4.2.0', readonly: true },
  { key: 'datacenter',      label: 'Datacenter',      oid: '…99999.4.3.0' },
  { key: 'floor',           label: 'Floor',           oid: '…99999.4.4.0' },
  { key: 'room',            label: 'Room',            oid: '…99999.4.5.0' },
  { key: 'rack_row',        label: 'Rack row',        oid: '…99999.4.6.0', numeric: true },
  { key: 'rack_num',        label: 'Rack number',     oid: '…99999.4.7.0', numeric: true },
  { key: 'rack_unit',       label: 'Rack unit',       oid: '…99999.4.8.0', numeric: true },
  { key: 'model',           label: 'Model',           oid: '…99999.4.9.0' },
  { key: 'power_draw_w',    label: 'Power draw (W)',  oid: '…99999.4.10.0', numeric: true },
]

const THRESHOLD_FIELDS: { key: string; label: string; unit: string; oid: string }[] = [
  { key: 'HighCPU',                label: 'High CPU',              unit: '%',      oid: '…99999.3.1.0' },
  { key: 'HighCPUSustained',       label: 'High CPU (sustained)',  unit: '%',      oid: '…99999.3.2.0' },
  { key: 'CPUNormal',              label: 'CPU normal (recovery)', unit: '%',      oid: '…99999.3.3.0' },
  { key: 'HighMemory',             label: 'High Memory',           unit: '%',      oid: '…99999.3.4.0' },
  { key: 'HighTemperature',        label: 'High Temperature',      unit: '°C',     oid: '…99999.3.6.0' },
  { key: 'RackFailureMin',         label: 'Rack failure (min)',    unit: 'devices',oid: '…99999.3.9.0' },
  { key: 'SensorTempHigh',         label: 'Sensor temp high',      unit: '°C',     oid: '…99999.3.10' },
  { key: 'SensorTempCritical',     label: 'Sensor temp critical',  unit: '°C',     oid: '…99999.3.11' },
  { key: 'SensorHumidityHigh',     label: 'Sensor humidity high',  unit: '%',      oid: '…99999.3.14' },
  { key: 'SensorHumidityCritical', label: 'Humidity critical',     unit: '%',      oid: '…99999.3.15' },
  { key: 'SensorHumidityLow',      label: 'Humidity low',          unit: '%',      oid: '…99999.3.16' },
  { key: 'SensorDewpointHigh',     label: 'Dewpoint high',         unit: '°C',     oid: '…99999.3.18' },
  { key: 'SensorAirflowHigh',      label: 'Airflow high',          unit: 'm/s',    oid: '…99999.3.20' },
  { key: 'SensorAirflowLow',       label: 'Airflow low',           unit: 'm/s',    oid: '…99999.3.21' },
]

const POWER_ACTIONS = [
  '', 'On', 'ForceOn', 'ForceOff', 'GracefulShutdown',
  'GracefulRestart', 'ForceRestart', 'PowerCycle', 'PushPowerButton',
]
const INDICATOR_STATES = ['Off', 'Lit', 'Blinking']

// ── Local editable form shape ──────────────────────────────────────────────────

type FormState = {
  identity: DeviceConfigIdentity
  asset: Record<keyof DeviceConfigAsset, string>
  thresholds: Record<string, string>
  redfish: DeviceConfigRedfish
}

function toForm(cfg: DeviceConfig): FormState {
  const asStr = (v: unknown) => (v === null || v === undefined ? '' : String(v))
  return {
    identity: { ...cfg.identity },
    asset: Object.fromEntries(
      ASSET_FIELDS.map(f => [f.key, asStr(cfg.asset[f.key])])
    ) as Record<keyof DeviceConfigAsset, string>,
    thresholds: Object.fromEntries(
      THRESHOLD_FIELDS.map(f => [f.key, asStr(cfg.thresholds[f.key])])
    ),
    redfish: cfg.redfish ?? { power_action: '', indicator_led: 'Off' },
  }
}

// Build a minimal update payload carrying only fields that changed from `base`,
// so the change-feed pushed to DCS stays precise (no needless re-applies).
function diff(form: FormState, base: FormState, redfishCapable: boolean): DeviceConfigUpdate {
  const out: DeviceConfigUpdate = {}

  const identity: Partial<DeviceConfigIdentity> = {}
  for (const f of IDENTITY_FIELDS) {
    if (form.identity[f.key] !== base.identity[f.key]) identity[f.key] = form.identity[f.key]
  }
  if (Object.keys(identity).length) out.identity = identity

  const asset: Record<string, string | number | null> = {}
  for (const f of ASSET_FIELDS) {
    const cur = form.asset[f.key], prev = base.asset[f.key]
    if (cur !== prev) asset[f.key] = f.numeric ? (cur === '' ? null : Number(cur)) : cur
  }
  if (Object.keys(asset).length) out.asset = asset as DeviceConfigUpdate['asset']

  const thresholds: Record<string, number> = {}
  for (const f of THRESHOLD_FIELDS) {
    const cur = form.thresholds[f.key], prev = base.thresholds[f.key]
    if (cur !== prev && cur !== '') thresholds[f.key] = Number(cur)
  }
  if (Object.keys(thresholds).length) out.thresholds = thresholds

  if (redfishCapable) {
    const redfish: Partial<DeviceConfigRedfish> = {}
    if (form.redfish.power_action !== base.redfish.power_action) redfish.power_action = form.redfish.power_action
    if (form.redfish.indicator_led !== base.redfish.indicator_led) redfish.indicator_led = form.redfish.indicator_led
    if (Object.keys(redfish).length) out.redfish = redfish
  }

  return out
}

// ── Small presentational helpers ───────────────────────────────────────────────

const inputCls = 'h-9 bg-slate-900/60 border-white/10 text-white placeholder:text-slate-600 focus-visible:ring-blue-500/40'

function Field({ label, oid, children }: { label: string; oid: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between mb-1">
        <span className="text-xs font-medium text-slate-300">{label}</span>
        <span className="text-[10px] font-mono text-slate-600" title={oid}>{oid}</span>
      </span>
      {children}
    </label>
  )
}

function SectionCard({ title, icon, children, note }: {
  title: string; icon: React.ReactNode; children: React.ReactNode; note?: string
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-slate-900/30 p-4">
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <h4 className="text-sm font-semibold text-white">{title}</h4>
        {note && <span className="text-[11px] text-slate-500">· {note}</span>}
      </div>
      {children}
    </div>
  )
}

// ── Main panel ──────────────────────────────────────────────────────────────────

export default function DeviceConfigPanel({ agentId }: { agentId: string }) {
  const { data: cfg, isLoading, isError } = useDeviceConfig(agentId)
  const update = useUpdateDeviceConfig(agentId)

  const base = useMemo(() => (cfg ? toForm(cfg) : null), [cfg])
  const [form, setForm] = useState<FormState | null>(null)

  // Seed the editable form once the config arrives (and after each save refetch).
  const seededFor = useMemo(() => JSON.stringify(base), [base])
  const [seedKey, setSeedKey] = useState<string>('')
  if (base && seedKey !== seededFor) {
    setForm(base)
    setSeedKey(seededFor)
  }

  // Auto-dismiss the "saved" confirmation a few seconds after a successful save.
  const [showSaved, setShowSaved] = useState(false)
  useEffect(() => {
    if (!update.isSuccess) return
    setShowSaved(true)
    const t = setTimeout(() => setShowSaved(false), 6000)
    return () => clearTimeout(t)
  }, [update.isSuccess])

  if (isLoading || !form || !base || !cfg) {
    return (
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-white mb-2">Device Configuration</h3>
        <p className="text-slate-500 text-sm">{isError ? 'Failed to load configuration.' : 'Loading configuration…'}</p>
      </div>
    )
  }

  const payload = diff(form, base, cfg.redfish_capable)
  const dirty = Object.keys(payload).length > 0

  const setIdentity = (k: keyof DeviceConfigIdentity, v: string) =>
    setForm(f => f && { ...f, identity: { ...f.identity, [k]: v } })
  const setAsset = (k: keyof DeviceConfigAsset, v: string) =>
    setForm(f => f && { ...f, asset: { ...f.asset, [k]: v } })
  const setThreshold = (k: string, v: string) =>
    setForm(f => f && { ...f, thresholds: { ...f.thresholds, [k]: v } })
  const setRedfish = (k: keyof DeviceConfigRedfish, v: string) =>
    setForm(f => f && { ...f, redfish: { ...f.redfish, [k]: v } })

  const onSave = () => {
    if (dirty) update.mutate(payload)
  }
  const onReset = () => setForm(base)

  return (
    <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-lg font-semibold text-white flex items-center gap-2">
          <Settings2 className="w-5 h-5 text-blue-400" />
          Device Configuration
        </h3>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onReset} disabled={!dirty || update.isPending}
            className="text-slate-400 hover:text-white gap-1.5">
            <RotateCcw className="w-3.5 h-3.5" /> Reset
          </Button>
          <Button size="sm" onClick={onSave} disabled={!dirty || update.isPending}
            className="bg-blue-600 hover:bg-blue-700 gap-1.5">
            <Save className="w-3.5 h-3.5" /> {update.isPending ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </div>
      <p className="text-xs text-slate-500 mb-4">
        Edits are written back to the device over SNMP SET / Redfish on the next DCS poll.
        {cfg.pending_since && (
          <span className="ml-1 text-amber-400/80">Last edit queued {new Date(cfg.pending_since).toLocaleString()}.</span>
        )}
      </p>

      {update.isError && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded bg-red-500/10 border border-red-500/30 text-red-300 text-xs">
          <AlertTriangle className="w-4 h-4" /> {(update.error as Error)?.message ?? 'Save failed'}
        </div>
      )}
      {showSaved && !dirty && (
        <div className="mb-4 px-3 py-2 rounded bg-green-500/10 border border-green-500/30 text-green-300 text-xs">
          Configuration saved — changes queued for the device.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Identity */}
        <SectionCard title="Identity" icon={<ServerCog className="w-4 h-4 text-blue-400" />} note="SNMP SET · sysX">
          <div className="space-y-3">
            {IDENTITY_FIELDS.map(f => (
              <Field key={f.key} label={f.label} oid={f.oid}>
                <Input className={inputCls} value={form.identity[f.key]}
                  onChange={e => setIdentity(f.key, e.target.value)} />
              </Field>
            ))}
          </div>
        </SectionCard>

        {/* Asset / location */}
        <SectionCard title="Asset / Location" icon={<ServerCog className="w-4 h-4 text-cyan-400" />} note="SNMP SET · 99999.4.x">
          <div className="grid grid-cols-2 gap-3">
            {ASSET_FIELDS.map(f => (
              <Field key={f.key} label={f.label} oid={f.oid}>
                <Input className={`${inputCls}${f.readonly ? ' opacity-60 cursor-not-allowed' : ''}`}
                  type={f.numeric ? 'number' : 'text'}
                  value={form.asset[f.key]}
                  readOnly={f.readonly} disabled={f.readonly}
                  onChange={e => setAsset(f.key, e.target.value)} />
              </Field>
            ))}
          </div>
        </SectionCard>

        {/* Alert thresholds */}
        <SectionCard title="Alert Thresholds" icon={<AlertTriangle className="w-4 h-4 text-amber-400" />} note="SNMP SET · 99999.3.x">
          <div className="grid grid-cols-2 gap-3">
            {THRESHOLD_FIELDS.map(f => (
              <Field key={f.key} label={`${f.label} (${f.unit})`} oid={f.oid}>
                <Input className={inputCls} type="number" step="any"
                  value={form.thresholds[f.key]} onChange={e => setThreshold(f.key, e.target.value)} />
              </Field>
            ))}
          </div>
        </SectionCard>

        {/* Redfish — servers only */}
        {cfg.redfish_capable ? (
          <SectionCard title="Redfish" icon={<Power className="w-4 h-4 text-emerald-400" />} note="HTTPS :443 · servers only">
            <div className="space-y-3">
              <Field label="Power action" oid="ComputerSystem.Reset">
                <select
                  className={`flex h-9 w-full rounded-md border px-3 text-sm ${inputCls}`}
                  value={form.redfish.power_action}
                  onChange={e => setRedfish('power_action', e.target.value)}
                >
                  {POWER_ACTIONS.map(a => (
                    <option key={a || 'none'} value={a} className="bg-slate-900">
                      {a || '— no action —'}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Identify LED" oid="IndicatorLED">
                <div className="flex items-center gap-2">
                  <Lightbulb className="w-4 h-4 text-amber-300 shrink-0" />
                  <select
                    className={`flex h-9 w-full rounded-md border px-3 text-sm ${inputCls}`}
                    value={form.redfish.indicator_led}
                    onChange={e => setRedfish('indicator_led', e.target.value)}
                  >
                    {INDICATOR_STATES.map(s => (
                      <option key={s} value={s} className="bg-slate-900">{s}</option>
                    ))}
                  </select>
                </div>
              </Field>
              <p className="text-[11px] text-slate-500">
                Power transitions fire a serverPowerOff/On trap and update the device power state.
              </p>
            </div>
          </SectionCard>
        ) : (
          <SectionCard title="Redfish" icon={<Power className="w-4 h-4 text-slate-500" />} note="servers only">
            <p className="text-xs text-slate-500">
              Redfish power &amp; identify-LED controls are available on server devices only.
            </p>
          </SectionCard>
        )}
      </div>
    </div>
  )
}
