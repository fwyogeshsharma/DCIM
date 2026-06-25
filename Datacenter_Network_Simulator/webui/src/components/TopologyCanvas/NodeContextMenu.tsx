import { useState, useEffect, useRef, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { api, errorMessage } from '../../api/client'
import { useStore } from '../../store/useStore'
import type { DeviceInfo } from '../../api/types'
import { VENDORS, MODELS, deviceTypeLabel } from '../../data/deviceConstants'

// ── Applicable traps per device_type (mirrors Python APPLICABLE_TRAPS, LINK_DOWN/LINK_UP excluded) ──

type TrapEntry = { type: string; label: string }

const APPLICABLE_TRAPS: Record<string, TrapEntry[]> = {
  router: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'WARM_START',        label: 'Warm Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'CPU_HIGH',          label: 'CPU High Usage' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
    { type: 'BGP_DOWN',          label: 'BGP Session Down' },
  ],
  switch: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'WARM_START',        label: 'Warm Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'CPU_HIGH',          label: 'CPU High Usage' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
    { type: 'LINK_FLAP',         label: 'Link Flap' },
  ],
  server: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'CPU_HIGH',          label: 'CPU High Usage' },
    { type: 'MEMORY_HIGH',       label: 'Memory High Usage' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
    { type: 'UPS_ON_BATTERY',    label: 'UPS On Battery' },
    { type: 'UPS_LOW_BATTERY',   label: 'UPS Low Battery' },
  ],
  firewall: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'WARM_START',        label: 'Warm Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'CPU_HIGH',          label: 'CPU High Usage' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
    { type: 'BGP_DOWN',          label: 'BGP Session Down' },
  ],
  load_balancer: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'CPU_HIGH',          label: 'CPU High Usage' },
    { type: 'MEMORY_HIGH',       label: 'Memory High Usage' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
  ],
  oob_switch: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'WARM_START',        label: 'Warm Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'CPU_HIGH',          label: 'CPU High Usage' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
  ],
  sensor: [
    { type: 'COLD_START',        label: 'Cold Start' },
    { type: 'AUTH_FAILURE',      label: 'Authentication Failure' },
    { type: 'TEMPERATURE_ALERT', label: 'Temperature Alert' },
    { type: 'HUMIDITY_ALERT',    label: 'Humidity Alert' },
    { type: 'DEWPOINT_ALERT',    label: 'Dew Point Alert' },
    { type: 'AIRFLOW_ALERT',             label: 'Airflow Alert' },
    { type: 'SENSOR_MID_TEMP_HIGH',      label: 'Mid-Rack Temp High' },
    { type: 'SENSOR_MID_TEMP_NORMAL',    label: 'Mid-Rack Temp Normal' },
    { type: 'SENSOR_OUTLET_TEMP_HIGH',   label: 'Exhaust Temp High' },
    { type: 'SENSOR_OUTLET_TEMP_NORMAL', label: 'Exhaust Temp Normal' },
  ],
  ups: [
    { type: 'COLD_START',               label: 'Cold Start' },
    { type: 'AUTH_FAILURE',             label: 'Authentication Failure' },
    { type: 'UPS_ON_BATTERY',           label: 'UPS On Battery' },
    { type: 'UPS_LOW_BATTERY',          label: 'UPS Low Battery' },
    { type: 'UPS_BATTERY_NORMAL',       label: 'Battery Normal' },
    { type: 'UPS_UTILITY_RESTORED',     label: 'Utility Power Restored' },
    { type: 'UPS_OUTPUT_OVERLOAD',      label: 'Output Overload' },
    { type: 'UPS_OUTPUT_NORMAL',        label: 'Output Normal' },
    { type: 'UPS_FAN_FAILURE',          label: 'Fan Failure' },
    { type: 'UPS_BATTERY_FAILURE',      label: 'Battery Failure' },
    { type: 'UPS_BATTERY_DISCONNECTED', label: 'Battery Disconnected' },
    { type: 'UPS_CHARGER_FAILURE',      label: 'Charger Failure' },
    { type: 'UPS_INPUT_VOLTAGE_HIGH',   label: 'Input Voltage High' },
    { type: 'UPS_INPUT_VOLTAGE_LOW',    label: 'Input Voltage Low' },
    { type: 'UPS_FREQUENCY_OUT_RANGE',  label: 'Frequency Out of Range' },
    { type: 'UPS_RECTIFIER_FAILURE',    label: 'Rectifier Failure' },
    { type: 'UPS_PHASE_FAILURE',        label: 'Phase Failure' },
    { type: 'TEMPERATURE_ALERT',          label: 'Temperature Alert' },
    { type: 'UPS_BATTERY_LOW_HEALTH',     label: 'Battery Low Health' },
    { type: 'UPS_BATTERY_HEALTH_RESTORED',label: 'Battery Health Restored' },
    { type: 'UPS_BYPASS_ACTIVE',          label: 'Bypass Active' },
    { type: 'UPS_BYPASS_CLEARED',         label: 'Bypass Cleared' },
  ],
  pdu: [
    { type: 'COLD_START',              label: 'Cold Start' },
    { type: 'AUTH_FAILURE',            label: 'Authentication Failure' },
    { type: 'PDU_OUTLET_ON',           label: 'Outlet On' },
    { type: 'PDU_OUTLET_OFF',          label: 'Outlet Off' },
    { type: 'PDU_BREAKER_TRIPPED',     label: 'Breaker Tripped' },
    { type: 'PDU_LOAD_HIGH',           label: 'Load High' },
    { type: 'PDU_LOAD_CRITICAL',       label: 'Load Critical' },
    { type: 'PDU_VOLTAGE_HIGH',        label: 'Voltage High' },
    { type: 'PDU_VOLTAGE_LOW',         label: 'Voltage Low' },
    { type: 'PDU_PHASE_IMBALANCE',     label: 'Phase Imbalance' },
    { type: 'PDU_POWER_FACTOR_LOW',    label: 'Power Factor Low' },
    { type: 'PDU_OUTLET_FAILURE',      label: 'Outlet Failure' },
    { type: 'PDU_SMOKE_DETECTED',      label: 'Smoke Detected' },
    { type: 'PDU_OUTLET_CURRENT_HIGH', label: 'Outlet Current High' },
    { type: 'PDU_GROUND_FAULT',        label: 'Ground Fault' },
    { type: 'PDU_FREQUENCY_FAULT',     label: 'Frequency Fault' },
    { type: 'PDU_FREQUENCY_NORMAL',    label: 'Frequency Normal' },
    { type: 'PDU_TEMP_HIGH',           label: 'PDU Temp High' },
    { type: 'PDU_TEMP_NORMAL',         label: 'PDU Temp Normal' },
    { type: 'PDU_HUMIDITY_HIGH',       label: 'PDU Humidity High' },
    { type: 'PDU_HUMIDITY_NORMAL',     label: 'PDU Humidity Normal' },
  ],
  floor_pdu: [
    { type: 'COLD_START',           label: 'Cold Start' },
    { type: 'AUTH_FAILURE',         label: 'Authentication Failure' },
    { type: 'PDU_BREAKER_TRIPPED',  label: 'Breaker Tripped' },
    { type: 'PDU_LOAD_HIGH',        label: 'Load High' },
    { type: 'PDU_LOAD_CRITICAL',    label: 'Load Critical' },
    { type: 'PDU_VOLTAGE_HIGH',     label: 'Voltage High' },
    { type: 'PDU_VOLTAGE_LOW',      label: 'Voltage Low' },
    { type: 'PDU_PHASE_IMBALANCE',  label: 'Phase Imbalance' },
    { type: 'PDU_POWER_FACTOR_LOW', label: 'Power Factor Low' },
    { type: 'PDU_SMOKE_DETECTED',   label: 'Smoke Detected' },
    { type: 'PDU_GROUND_FAULT',     label: 'Ground Fault' },
    { type: 'PDU_FREQUENCY_FAULT',  label: 'Frequency Fault' },
    { type: 'PDU_FREQUENCY_NORMAL', label: 'Frequency Normal' },
    { type: 'PDU_TEMP_HIGH',        label: 'PDU Temp High' },
    { type: 'PDU_TEMP_NORMAL',      label: 'PDU Temp Normal' },
    { type: 'PDU_HUMIDITY_HIGH',    label: 'PDU Humidity High' },
    { type: 'PDU_HUMIDITY_NORMAL',  label: 'PDU Humidity Normal' },
  ],
  rpp: [],
  energy_monitor: [],
  generator: [
    { type: 'COLD_START',          label: 'Cold Start' },
    { type: 'AUTH_FAILURE',        label: 'Auth Failure' },
    { type: 'TEMPERATURE_ALERT',   label: 'Temperature Alert' },
    { type: 'GEN_RUNNING',         label: 'Generator Running' },
    { type: 'GEN_STOPPED',         label: 'Generator Stopped' },
    { type: 'GEN_LOW_FUEL',        label: 'Low Fuel' },
    { type: 'GEN_LOW_COOLANT',     label: 'Low Coolant' },
    { type: 'GEN_BATTERY_FAILURE', label: 'Battery Failure' },
    { type: 'GEN_TRANSFER_SWITCH', label: 'Transfer Switch Fault' },
    { type: 'GEN_OVERCRANK',       label: 'Overcrank' },
  ],
}

// ── Shared menu styles ────────────────────────────────────────────────────────

const MENU_S: React.CSSProperties = {
  background: '#111827',
  border: '1px solid var(--border)',
  borderRadius: 4,
  boxShadow: '0 10px 28px rgba(0,0,0,0.7), 0 2px 6px rgba(0,0,0,0.4)',
  padding: '4px 0',
  minWidth: 200,
  fontSize: 12,
  fontFamily: 'system-ui, -apple-system, sans-serif',
  userSelect: 'none',
}

function MenuDivider() {
  return <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />
}

function MenuItem({
  label, onClick, disabled, danger,
}: {
  label: string; onClick?: () => void; disabled?: boolean; danger?: boolean
}) {
  const [hov, setHov] = useState(false)
  return (
    <div
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      onMouseDown={!disabled ? onClick : undefined}
      style={{
        padding: '6px 14px',
        cursor: disabled ? 'not-allowed' : 'pointer',
        color: disabled ? 'var(--text-dim)' : danger ? '#f87171' : 'var(--text)',
        background: hov && !disabled ? 'rgba(255,255,255,0.06)' : 'transparent',
      }}
    >
      {label}
    </div>
  )
}

// ── EditDeviceDialog ──────────────────────────────────────────────────────────

const editOverlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000,
}
const editDialog: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 6, width: 420, maxHeight: '90vh',
  display: 'flex', flexDirection: 'column',
  boxShadow: '0 16px 48px rgba(0,0,0,0.8)',
}
const SEC: React.CSSProperties = {
  fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
  textTransform: 'uppercase' as const, letterSpacing: '0.8px',
  borderBottom: '1px solid var(--border)',
  paddingBottom: 4, marginBottom: 6, marginTop: 10,
}
const spinW: React.CSSProperties = { width: 72 }

function buildLocPreview(f: {
  country: string; datacenter_city: string; datacenter: string;
  floor: string; room: string; rack_row: number; rack_num: number; rack_unit: number
}): string {
  if (!f.datacenter) return ''
  const p: string[] = []
  if (f.country)         p.push(f.country)
  if (f.datacenter_city) p.push(f.datacenter_city)
  p.push(f.datacenter)
  if (f.floor)    p.push(`Floor ${f.floor}`)
  if (f.room)     p.push(`Room ${f.room}`)
  if (f.rack_row) p.push(`Row ${f.rack_row}`)
  if (f.rack_num) p.push(`Rack ${f.rack_num}`)
  if (f.rack_unit) p.push(`U${f.rack_unit}`)
  return p.join(', ')
}

export function EditDeviceDialog({ deviceId, onClose }: { deviceId: string; onClose: () => void }) {
  const { fetchGraph, fetchDevices } = useStore()
  const [form, setFormState] = useState({
    device_type: '', name: '', vendor: '', model_name: '',
    snmp_port: 161, gnmi_port: 57400,
    sys_contact: '', metrics_enabled: true,
    country: '', datacenter_city: '', datacenter: '',
    room: '', floor: '', rack_row: 0, rack_num: 0, rack_unit: 0,
  })
  const [busy, setBusy] = useState(false)
  const [err,  setErr]  = useState('')

  useEffect(() => {
    api.device(deviceId).then((d: unknown) => {
      const dev = d as DeviceInfo
      setFormState({
        device_type: dev.device_type,
        name: dev.name, vendor: dev.vendor,
        model_name: dev.model_name || '',
        snmp_port: dev.snmp_port, gnmi_port: dev.gnmi_port,
        sys_contact: dev.sys_contact || '',
        metrics_enabled: dev.metrics_enabled ?? true,
        country: dev.country || '',
        datacenter_city: dev.datacenter_city || '',
        datacenter: dev.datacenter || '',
        room: dev.room || '',
        floor: dev.floor || '',
        rack_row: dev.rack_row ?? 0,
        rack_num: dev.rack_num ?? 0,
        rack_unit: dev.rack_unit ?? 0,
      })
    }).catch(() => {})
  }, [deviceId])

  const set = <K extends keyof typeof form>(k: K, v: typeof form[K]) =>
    setFormState(f => ({ ...f, [k]: v }))

  // Vendor options filtered to those with known models for this device type
  const vendorOptions = useMemo(() => {
    const filtered = VENDORS.filter(v => MODELS[`${form.device_type}:${v}`]?.length > 0)
    // Always include current vendor so existing data isn't lost
    if (form.vendor && !filtered.includes(form.vendor)) return [form.vendor, ...filtered]
    return filtered.length > 0 ? filtered : VENDORS
  }, [form.device_type, form.vendor])

  // Model options filtered by device_type + vendor
  const modelOptions = useMemo(() => {
    return MODELS[`${form.device_type}:${form.vendor}`] || []
  }, [form.device_type, form.vendor])

  const locPreview = buildLocPreview(form)

  async function submit() {
    if (!form.name.trim()) { setErr('Name required'); return }
    setBusy(true); setErr('')
    try {
      await api.editDevice(deviceId, form)
      fetchGraph(); fetchDevices()
      onClose()
    } catch (e) { setErr(errorMessage(e)) }
    finally { setBusy(false) }
  }

  return createPortal(
    <div style={editOverlay} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div style={editDialog}>
        <div className="panel-header" style={{ borderRadius: '6px 6px 0 0', flexShrink: 0 }}>
          <span className="title">Edit Device</span>
          <button onClick={onClose} style={{ border: 'none', background: 'none', color: 'var(--text-muted)', fontSize: 16, cursor: 'pointer', padding: '0 4px' }}>✕</button>
        </div>

        <div style={{ padding: '10px 16px 14px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 0 }}>

          {/* Device Identity */}
          <div style={SEC}>Device Identity</div>
          <ERow label="Type">
            <span style={{ color: 'var(--text)', fontSize: 11 }}>{deviceTypeLabel(form.device_type)}</span>
          </ERow>
          <ERow label="Name">
            <input style={{ flex: 1 }} value={form.name} onChange={e => set('name', e.target.value)} placeholder="e.g. Router1" />
          </ERow>
          <ERow label="Vendor">
            <select style={{ flex: 1 }} value={form.vendor}
              onChange={e => {
                const v = e.target.value
                const models = MODELS[`${form.device_type}:${v}`] || []
                if (models.length > 0 && !models.includes(form.model_name)) {
                  setFormState(f => ({ ...f, vendor: v, model_name: models[0] }))
                } else {
                  set('vendor', v)
                }
              }}>
              {vendorOptions.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </ERow>
          <ERow label="Model">
            {modelOptions.length > 0 ? (
              <select style={{ flex: 1 }} value={form.model_name}
                onChange={e => set('model_name', e.target.value)}
                disabled={modelOptions.length === 0}>
                {modelOptions.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input style={{ flex: 1 }} value={form.model_name}
                onChange={e => set('model_name', e.target.value)}
                placeholder="e.g. Custom Model" />
            )}
          </ERow>

          {/* Network Settings */}
          <div style={SEC}>Network Settings</div>
          <ERow label="SNMP Port">
            <input type="number" style={spinW} value={form.snmp_port}
              onChange={e => set('snmp_port', parseInt(e.target.value) || 161)} />
          </ERow>
          {['router', 'switch'].includes(form.device_type) && (
          <ERow label="gNMI Port">
            <input type="number" style={spinW} value={form.gnmi_port}
              onChange={e => set('gnmi_port', parseInt(e.target.value) || 57400)} />
          </ERow>
          )}
          <ERow label="Contact">
            <input style={{ flex: 1 }} value={form.sys_contact}
              onChange={e => set('sys_contact', e.target.value)}
              placeholder="e.g. admin@dc1.example.com" />
          </ERow>
          <ERow label="">
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)' }}>
              <input type="checkbox" checked={form.metrics_enabled}
                onChange={e => set('metrics_enabled', e.target.checked)}
                style={{ accentColor: 'var(--accent)', width: 13, height: 13 }} />
              Enable metric simulation
            </label>
          </ERow>

          {/* Physical Location */}
          <div style={SEC}>Physical Location</div>
          <ERow label="Country">
            <input style={{ flex: 1 }} value={form.country}
              onChange={e => set('country', e.target.value)} placeholder="e.g. USA" />
          </ERow>
          <ERow label="City">
            <input style={{ flex: 1 }} value={form.datacenter_city}
              onChange={e => set('datacenter_city', e.target.value)} placeholder="e.g. Dallas" />
          </ERow>
          <ERow label="Datacenter">
            <input style={{ flex: 1 }} value={form.datacenter}
              onChange={e => set('datacenter', e.target.value)} placeholder="e.g. DC1" />
          </ERow>
          <ERow label="Floor">
            <input style={{ flex: 1 }} value={form.floor}
              onChange={e => set('floor', e.target.value)} placeholder="e.g. 1" />
          </ERow>
          <ERow label="Room">
            <input style={{ flex: 1 }} value={form.room}
              onChange={e => set('room', e.target.value)} placeholder="e.g. A" />
          </ERow>
          <ERow label="Row">
            <input type="number" style={spinW} value={form.rack_row || ''}
              onChange={e => set('rack_row', parseInt(e.target.value) || 0)} placeholder="—" min={0} />
          </ERow>
          <ERow label="Rack">
            <input type="number" style={spinW} value={form.rack_num || ''}
              onChange={e => set('rack_num', parseInt(e.target.value) || 0)} placeholder="—" min={0} />
          </ERow>
          <ERow label="Unit (U)">
            <input type="number" style={spinW} value={form.rack_unit || ''}
              onChange={e => set('rack_unit', parseInt(e.target.value) || 0)} placeholder="—" min={0} />
          </ERow>
          {locPreview && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', fontStyle: 'italic', marginTop: 4, paddingLeft: 100 }}>
              sysLocation: {locPreview}
            </div>
          )}

          {err && <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 6 }}>{err}</div>}
        </div>

        <div style={{ display: 'flex', gap: 8, padding: '8px 16px 12px', flexShrink: 0, borderTop: '1px solid var(--border)' }}>
          <button className="primary" style={{ flex: 1 }} onClick={submit} disabled={busy}>
            {busy ? 'Saving…' : 'OK'}
          </button>
          <button style={{ flex: 1 }} onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>,
    document.body
  )
}

function ERow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 11, width: 96, textAlign: 'right', flexShrink: 0 }}>
        {label ? `${label}` : ''}
      </span>
      {children}
    </div>
  )
}

// ── DeviceInfoModal ───────────────────────────────────────────────────────────

export function DeviceInfoModal({ deviceId, onClose }: { deviceId: string; onClose: () => void }) {
  const { graphDevices, graphLinks, bacnet } = useStore()
  const [info, setInfo] = useState<DeviceInfo | null>(null)

  useEffect(() => {
    api.device(deviceId).then(d => setInfo(d as DeviceInfo)).catch(() => {})
  }, [deviceId])

  const device   = graphDevices.find(d => d.id === deviceId)
  const neighbors = graphLinks
    .filter(l => l.src_id === deviceId || l.dst_id === deviceId)
    .map(l => {
      const peerId     = l.src_id === deviceId ? l.dst_id  : l.src_id
      const peer       = graphDevices.find(d => d.id === peerId)
      const localIface = l.src_id === deviceId ? l.src_iface : l.dst_iface
      const remoteIface= l.src_id === deviceId ? l.dst_iface : l.src_iface
      return { name: peer?.name || peerId, layer: l.layer, localIface, remoteIface }
    })

  const rows: [string, string][] = []
  if (info) {
    rows.push(['Name',    info.name])
    rows.push(['Type',    info.device_type.replace(/_/g, ' ')])
    rows.push(['Vendor',  info.vendor])
    if (info.model_name)   rows.push(['Model',   info.model_name])
    if (info.os_name    && info.os_name    !== 'Unknown') rows.push(['OS',      info.os_name])
    if (info.os_version && info.os_version !== 'Unknown') rows.push(['Version', info.os_version])
    if (info.ip_address)   rows.push(['Prod IP', info.ip_address])
    if (info.mgmt_ip)      rows.push(['Mgmt IP', info.mgmt_ip])
    const isPassive = info.device_type === 'rpp'
    if (!isPassive) rows.push(['SNMP Port', String(info.snmp_port)])
    if (['router', 'switch'].includes(info.device_type))
      rows.push(['gNMI Port', String(info.gnmi_port)])
    const bacnetDev = (bacnet?.devices ?? []).find(
      b => b.ip === info.ip_address || b.ip === info.mgmt_ip)
    if (bacnetDev)
      rows.push(['BACnet Instance', String(bacnetDev.instance)])
    if (info.sys_contact)  rows.push(['Contact',  info.sys_contact])
    if (info.sys_location) rows.push(['Location', info.sys_location])
    if (!isPassive) rows.push(['Interfaces', String(info.interface_count)])
    if (!isPassive && info.cpu_usage > 0)   rows.push(['CPU',    `${info.cpu_usage.toFixed(1)} %`])
    if (!isPassive && info.memory_used > 0) rows.push(['Memory', `${info.memory_used.toFixed(0)} MB`])
  }

  const LAYER_COLOR: Record<string, string> = {
    production: '#4a9eff', management: '#3fb950', power: '#f59e0b',
  }

  return createPortal(
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000, backdropFilter: 'blur(2px)' }}
      onMouseDown={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, width: 430, maxHeight: '80vh', display: 'flex', flexDirection: 'column', boxShadow: '0 16px 48px rgba(0,0,0,0.8)' }}>
        <div className="panel-header" style={{ borderRadius: '6px 6px 0 0', flexShrink: 0 }}>
          <span className="title">{device?.name || 'Device Info'}</span>
          <button onClick={onClose} style={{ border: 'none', background: 'none', color: 'var(--text-muted)', fontSize: 16, cursor: 'pointer', padding: '0 4px' }}>✕</button>
        </div>

        <div style={{ padding: '14px 20px', overflowY: 'auto' }}>
          {!info && <div style={{ color: 'var(--text-dim)', fontSize: 11, textAlign: 'center', padding: 20 }}>Loading…</div>}
          {info && (
            <>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
                {rows.map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 10, lineHeight: 1.7 }}>
                    <span style={{ color: 'var(--text-muted)', width: 88, textAlign: 'right', flexShrink: 0 }}>{k}:</span>
                    <span style={{
                      color: 'var(--text)',
                      fontFamily: (k.includes('IP') || k.includes('Port')) ? 'Consolas, monospace' : undefined,
                    }}>{v}</span>
                  </div>
                ))}
              </div>

              {neighbors.length > 0 && (
                <>
                  <div style={{
                    fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.8px',
                    borderBottom: '1px solid var(--border)',
                    paddingBottom: 4, marginTop: 14, marginBottom: 8,
                  }}>
                    Neighbors · {neighbors.length}
                  </div>
                  {neighbors.map((n, i) => {
                    const col = LAYER_COLOR[n.layer] || '#4a9eff'
                    return (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, padding: '3px 0' }}>
                        <span style={{ color: 'var(--text)', fontWeight: 600, flex: 1 }}>{n.name}</span>
                        <span style={{ color: 'var(--text-dim)', fontFamily: 'Consolas, monospace', fontSize: 10 }}>
                          {n.localIface != null ? `if${n.localIface}` : '?'} ↔ {n.remoteIface != null ? `if${n.remoteIface}` : '?'}
                        </span>
                        <span style={{
                          fontSize: 9, padding: '1px 5px', borderRadius: 3,
                          background: `${col}22`, color: col,
                          border: `1px solid ${col}55`,
                        }}>{n.layer}</span>
                      </div>
                    )
                  })}
                </>
              )}
            </>
          )}
        </div>

        <div style={{ padding: '8px 20px 14px', borderTop: '1px solid var(--border)', flexShrink: 0 }}>
          <button className="primary" style={{ width: '100%' }} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ── Plant events (per-unit fault injection — BACnet plant only) ───────────────
// Rendered as the "Trigger Event" submenu in NodeContextMenu (toggle to inject/clear).
// Each event forces ONE BACnet point on this single unit. Alarm_* forces also
// drive the engine's coupled fault physics (pressure/flow/temp cascades; the CDU
// leak heats downstream CPUs); a running-status force trips the unit. The forced
// present-value auto-dispatches a COV notification — no separate "send" step.
// SNMP devices are intentionally excluded: they already emit traps for events.
// Mechanism: plant_alarm_overrides[ip][point]=value (api/routers/bacnet.py).

type PlantEvent = { label: string; point: string; value: number; kind: 'alarm' | 'trip' }

const PLANT_EVENTS: Record<string, PlantEvent[]> = {
  crah: [
    { label: 'High Supply Temp', point: 'Alarm_HighTemp',    value: 1, kind: 'alarm' },
    { label: 'Airflow Loss',     point: 'Alarm_AirflowLoss', value: 1, kind: 'alarm' },
    { label: 'Filter Clogged',   point: 'Filter_Dirty',      value: 1, kind: 'alarm' },
    { label: 'Unit Trip',        point: 'Unit_Running',      value: 0, kind: 'trip'  },
  ],
  chiller: [
    { label: 'High Condenser Pressure', point: 'Alarm_HighPressure', value: 1, kind: 'alarm' },
    { label: 'Low Evap Temp',           point: 'Alarm_LowEvapTemp',  value: 1, kind: 'alarm' },
    { label: 'Flow Loss',               point: 'Alarm_FlowLoss',     value: 1, kind: 'alarm' },
    { label: 'Compressor Trip',         point: 'Chiller_Running',    value: 0, kind: 'trip'  },
  ],
  pump: [
    { label: 'Pump Fault', point: 'Alarm_Fault',   value: 1, kind: 'alarm' },
    { label: 'Low Flow',   point: 'Alarm_LowFlow',  value: 1, kind: 'alarm' },
    { label: 'Pump Stop',  point: 'Run_Status',     value: 0, kind: 'trip'  },
  ],
  cooling_tower: [
    { label: 'High Vibration',  point: 'Alarm_HighVibration', value: 1, kind: 'alarm' },
    { label: 'Low Basin Level', point: 'Alarm_LowBasin',      value: 1, kind: 'alarm' },
    { label: 'Fan Stop',        point: 'Fan_Status',          value: 0, kind: 'trip'  },
  ],
  valve: [
    { label: 'Actuator Fault',          point: 'Alarm_ActuatorFault', value: 1, kind: 'alarm' },
    { label: 'Stuck (stop modulating)', point: 'Status_Modulating',   value: 0, kind: 'trip'  },
  ],
  cdu: [
    { label: 'Coolant Leak',     point: 'Alarm_Leak',           value: 1, kind: 'alarm' },
    { label: 'High Supply Temp', point: 'Alarm_HighSupplyTemp', value: 1, kind: 'alarm' },
    { label: 'Pump Fault',       point: 'Alarm_PumpFault',      value: 1, kind: 'alarm' },
    { label: 'Low Flow',         point: 'Alarm_LowFlow',        value: 1, kind: 'alarm' },
    { label: 'Unit Trip',        point: 'Unit_Running',         value: 0, kind: 'trip'  },
  ],
  // Verdigris EV2 energy meter — BACnet/COV like plant (no SNMP traps), so it
  // belongs here. Alarm bits are bit-only forces (the meter engine has no coupled
  // physics hook), but the forced present-value still dispatches a COV notification.
  energy_monitor: [
    { label: 'Overcurrent',       point: 'Alarm_Overcurrent',      value: 1, kind: 'alarm' },
    { label: 'Voltage Imbalance', point: 'Alarm_VoltageImbalance', value: 1, kind: 'alarm' },
    { label: 'High THD',          point: 'Alarm_HighTHD',          value: 1, kind: 'alarm' },
    { label: 'Phase Loss',        point: 'Alarm_PhaseLoss',        value: 1, kind: 'alarm' },
    { label: 'Sensor Fault',      point: 'Alarm_SensorFault',      value: 1, kind: 'alarm' },
  ],
}

export const PLANT_EVENT_TYPES = Object.keys(PLANT_EVENTS)

// ── NodeContextMenu ───────────────────────────────────────────────────────────

interface Props {
  nodeId: string
  deviceType: string
  deviceName: string
  modelName: string
  x: number
  y: number
  onClose: () => void
  onLocate: () => void
  onEditDevice: () => void
  onShowInfo: () => void
}

export default function NodeContextMenu({ nodeId, deviceType, deviceName, modelName, x, y, onClose, onLocate, onEditDevice, onShowInfo }: Props) {
  const { snmp, fetchGraph, fetchDevices } = useStore()
  const menuRef  = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [pos,     setPos]    = useState({ x, y })
  const [subOpen, setSubOpen] = useState(false)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [evBusy, setEvBusy] = useState<string | null>(null)
  const [faultsAvail, setFaultsAvail] = useState<{ fault: string; label: string }[]>([])
  const [faultsActive, setFaultsActive] = useState<string[]>([])
  const [fltBusy, setFltBusy] = useState<string | null>(null)
  const snmpRunning = snmp?.running ?? false
  const plantEvents = PLANT_EVENTS[deviceType] ?? []

  // Load active fault overrides so the submenu can show ACTIVE state (plant only)
  async function loadOverrides() {
    try {
      const ov = await api.plantOverrides(nodeId) as { overrides: Record<string, number> }
      setOverrides(ov.overrides ?? {})
    } catch { /* ignore */ }
  }
  useEffect(() => {
    if (PLANT_EVENT_TYPES.includes(deviceType)) loadOverrides()
  }, [deviceType, nodeId])

  // Inject Fault — available faults for this device type + which ramps are active
  async function loadFaults() {
    try {
      const r = await api.deviceFaults(nodeId) as {
        available: { fault: string; label: string }[]; active: string[]
      }
      setFaultsAvail(r.available ?? [])
      setFaultsActive(r.active ?? [])
    } catch { /* ignore */ }
  }
  useEffect(() => { loadFaults() }, [nodeId])

  // Adjust to keep on-screen after first render
  useEffect(() => {
    const el = menuRef.current
    if (!el) return
    const r  = el.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight
    setPos({
      x: x + r.width  > vw ? Math.max(0, x - r.width)  : x,
      y: y + r.height > vh ? Math.max(0, y - r.height)  : y,
    })
  }, [x, y])

  // Close on outside click or Escape
  useEffect(() => {
    const down = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', down, true)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('mousedown', down, true)
      document.removeEventListener('keydown', key)
    }
  }, [onClose])

  function openSub() {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
    setSubOpen(true)
  }
  function closeSub() {
    timerRef.current = setTimeout(() => setSubOpen(false), 150)
  }

  // Toggle a plant fault override (force/clear a BACnet point + COV alarm).
  async function toggleEvent(ev: PlantEvent) {
    const active = ev.point in overrides
    setEvBusy(ev.point)
    try { await api.setPlantOverride(nodeId, ev.point, active ? null : ev.value); await loadOverrides() }
    catch (e) { alert(String(e)) }
    finally { setEvBusy(null) }
  }

  // Toggle an Inject Fault ramp. start = ramp the metric up across the SNMP
  // threshold (trap fires organically); clear = ramp back, firing recovery.
  async function toggleFault(fault: string) {
    const active = faultsActive.includes(fault)
    setFltBusy(fault)
    try { await api.setDeviceFault(nodeId, fault, active ? 'clear' : 'start'); await loadFaults() }
    catch (e) { alert(String(e)) }
    finally { setFltBusy(null) }
  }

  async function removeDevice() {
    if (!confirm(`Remove '${deviceName}' and all its connections?`)) return
    onClose()
    try { await api.delDevice(nodeId); fetchGraph(); fetchDevices() }
    catch (e) { alert(String(e)) }
  }

  async function sendTrap(trapType: string) {
    onClose()
    try { await api.sendTrap(nodeId, trapType) }
    catch (e) { alert(String(e)) }
  }

  function getSensorTraps(model: string): TrapEntry[] {
    const base: TrapEntry[] = [
      { type: 'COLD_START',            label: 'Cold Start' },
      { type: 'AUTH_FAILURE',          label: 'Auth Failure' },
      { type: 'TEMPERATURE_ALERT',     label: 'Temperature Alert' },
    ]
    const humidity: TrapEntry[] = [
      { type: 'HUMIDITY_ALERT',        label: 'Humidity Alert' },
      { type: 'SENSOR_HIGH_HUMIDITY',  label: 'High Humidity' },
      { type: 'SENSOR_LOW_HUMIDITY',   label: 'Low Humidity' },
    ]
    const airflow: TrapEntry[] = [
      { type: 'AIRFLOW_ALERT',         label: 'Airflow Alert' },
    ]
    const dewpoint: TrapEntry[] = [
      { type: 'DEWPOINT_ALERT',        label: 'Dew Point Alert' },
    ]
    const midExhaust: TrapEntry[] = [
      { type: 'SENSOR_MID_TEMP_HIGH',    label: 'Mid-Rack Temp High' },
      { type: 'SENSOR_OUTLET_TEMP_HIGH', label: 'Exhaust Temp High' },
    ]
    if (model === 'Raritan DPX2-T3H1') return [...base, ...humidity, ...midExhaust]
    if (model === 'Vertiv Geist GTHD')  return [...base, ...humidity, ...dewpoint]
    if (model === 'APC NetBotz 355' || model === 'APC NetBotz 250') return [...base, ...humidity, ...airflow]
    // DPX2-CC2 and unknown sensor models: inlet temp only
    return base
  }

  const traps = deviceType === 'sensor'
    ? getSensorTraps(modelName)
    : APPLICABLE_TRAPS[deviceType] || [
        { type: 'COLD_START', label: 'Cold Start' },
        { type: 'CPU_HIGH',   label: 'CPU High Usage' },
      ]

  // ── Unified "Simulate Fault" model ─────────────────────────────────────────
  // CONDITIONS = stateful toggles (ramp a metric on SNMP devices, force a BACnet
  // point on plant devices). EVENTS = one-shot traps (SNMP only). The two are
  // grouped under one submenu so the user sees a single, consistent entry point.
  const isPlant = PLANT_EVENT_TYPES.includes(deviceType)

  type SimCond = { key: string; label: string; on: boolean; busy: boolean; toggle: () => void }
  const conditions: SimCond[] = isPlant
    ? plantEvents.map(ev => ({
        key: ev.point, label: ev.label,
        on: ev.point in overrides, busy: evBusy === ev.point,
        toggle: () => { if (!evBusy) toggleEvent(ev) },
      }))
    : faultsAvail.map(f => ({
        key: f.fault, label: f.label,
        on: faultsActive.includes(f.fault), busy: fltBusy === f.fault,
        toggle: () => { if (!fltBusy) toggleFault(f.fault) },
      }))

  // One-shot events: drop trap types already represented as a Condition so the
  // two groups never list the same fault twice.
  const FAULT_TRAP: Record<string, string> = {
    cpu_high: 'CPU_HIGH', memory_high: 'MEMORY_HIGH', temp_high: 'TEMPERATURE_ALERT',
    ambient_high: 'TEMPERATURE_ALERT', humidity_high: 'HUMIDITY_ALERT',
    ups_overload: 'UPS_OUTPUT_OVERLOAD', pdu_load_high: 'PDU_LOAD_HIGH',
  }
  const covered = new Set(faultsAvail.map(f => FAULT_TRAP[f.fault]).filter(Boolean))
  const events = (!isPlant && snmpRunning) ? traps.filter(t => !covered.has(t.type)) : []

  const hasSim = conditions.length > 0 || events.length > 0
  const busyAny = evBusy !== null || fltBusy !== null

  const GROUP_HDR: React.CSSProperties = {
    fontSize: 9, fontWeight: 600, color: 'var(--text-muted)',
    textTransform: 'uppercase', letterSpacing: '0.6px',
    padding: '5px 14px 2px',
  }

  const menu = (
    <div
      ref={menuRef}
      style={{ ...MENU_S, position: 'fixed', left: pos.x, top: pos.y, zIndex: 9000 }}
    >
      <MenuItem label="Edit Device…"    onClick={() => { onClose(); onEditDevice() }} />
      <MenuItem label="Remove Device"   onClick={removeDevice} danger />
      <MenuDivider />
      <MenuItem label="Locate on Graph" onClick={() => { onLocate(); onClose() }} />
      <MenuItem label="Show Info"       onClick={() => { onClose(); onShowInfo() }} />

      {hasSim && (
        <>
          <MenuDivider />
          <div style={{ position: 'relative' }}
            onMouseEnter={openSub}
            onMouseLeave={closeSub}
          >
            {/* "Simulate Fault" row */}
            <div style={{
              padding: '6px 14px',
              cursor: 'pointer',
              color: 'var(--text)',
              background: subOpen ? 'rgba(255,255,255,0.06)' : 'transparent',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span>Simulate Fault</span>
              <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>▶</span>
            </div>

            {/* Submenu — Conditions (stateful toggles) + Events (one-shot) */}
            {subOpen && (
              <div
                style={{ ...MENU_S, position: 'absolute', top: 0, left: '100%', zIndex: 9001, maxHeight: 360, overflowY: 'auto' }}
                onMouseEnter={openSub}
                onMouseLeave={closeSub}
              >
                {conditions.length > 0 && <div style={GROUP_HDR}>Conditions</div>}
                {conditions.map(c => (
                  <div
                    key={c.key}
                    style={{
                      padding: '5px 14px', cursor: busyAny ? 'wait' : 'pointer',
                      color: c.on ? '#f87171' : 'var(--text)', whiteSpace: 'nowrap',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 18,
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.06)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    onMouseDown={e => { e.preventDefault(); c.toggle() }}
                  >
                    <span>{c.label}</span>
                    <span style={{ fontSize: 9, color: c.on ? '#f87171' : 'var(--text-dim)' }}>
                      {c.busy ? '…' : c.on ? 'ACTIVE' : ''}
                    </span>
                  </div>
                ))}

                {events.length > 0 && <div style={GROUP_HDR}>Events</div>}
                {events.map(t => (
                  <div
                    key={t.type}
                    style={{ padding: '5px 14px', cursor: 'pointer', color: 'var(--text)', whiteSpace: 'nowrap' }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.06)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    onMouseDown={e => { e.preventDefault(); sendTrap(t.type) }}
                  >
                    {t.label}
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )

  return createPortal(menu, document.body)
}
