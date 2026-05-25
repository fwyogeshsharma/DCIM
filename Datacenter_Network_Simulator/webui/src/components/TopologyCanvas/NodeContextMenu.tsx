import { useState, useEffect, useRef, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../../api/client'
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
    { type: 'AIRFLOW_ALERT',     label: 'Airflow Alert' },
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
    { type: 'TEMPERATURE_ALERT',        label: 'Temperature Alert' },
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
    } catch (e) { setErr(String(e)) }
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
          <ERow label="gNMI Port">
            <input type="number" style={spinW} value={form.gnmi_port}
              onChange={e => set('gnmi_port', parseInt(e.target.value) || 57400)} />
          </ERow>
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
  const { graphDevices, graphLinks } = useStore()
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
    if (info.os_name)      rows.push(['OS',      info.os_name])
    if (info.os_version)   rows.push(['Version', info.os_version])
    if (info.ip_address)   rows.push(['Prod IP', info.ip_address])
    if (info.mgmt_ip)      rows.push(['Mgmt IP', info.mgmt_ip])
    rows.push(['SNMP Port', String(info.snmp_port)])
    rows.push(['gNMI Port', String(info.gnmi_port)])
    if (info.sys_contact)  rows.push(['Contact',  info.sys_contact])
    if (info.sys_location) rows.push(['Location', info.sys_location])
    rows.push(['Interfaces', String(info.interface_count)])
    if (info.cpu_usage > 0)   rows.push(['CPU',    `${info.cpu_usage.toFixed(1)} %`])
    if (info.memory_used > 0) rows.push(['Memory', `${info.memory_used.toFixed(0)} MB`])
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

// ── NodeContextMenu ───────────────────────────────────────────────────────────

interface Props {
  nodeId: string
  deviceType: string
  deviceName: string
  x: number
  y: number
  onClose: () => void
  onLocate: () => void
  onEditDevice: () => void
  onShowInfo: () => void
}

export default function NodeContextMenu({ nodeId, deviceType, deviceName, x, y, onClose, onLocate, onEditDevice, onShowInfo }: Props) {
  const { snmp, fetchGraph, fetchDevices } = useStore()
  const menuRef  = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [pos,     setPos]    = useState({ x, y })
  const [subOpen, setSubOpen] = useState(false)
  const snmpRunning = snmp?.running ?? false

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

  const traps = APPLICABLE_TRAPS[deviceType] || [
    { type: 'COLD_START', label: 'Cold Start' },
    { type: 'CPU_HIGH',   label: 'CPU High Usage' },
  ]

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

      {snmpRunning && (
        <>
          <MenuDivider />
          <div style={{ position: 'relative' }}
            onMouseEnter={openSub}
            onMouseLeave={closeSub}
          >
            {/* "Send Trap" row */}
            <div style={{
              padding: '6px 14px',
              cursor: 'pointer',
              color: 'var(--text)',
              background: subOpen ? 'rgba(255,255,255,0.06)' : 'transparent',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span>Send Trap</span>
              <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>▶</span>
            </div>

            {/* Submenu */}
            {subOpen && (
              <div
                style={{ ...MENU_S, position: 'absolute', top: 0, left: '100%', zIndex: 9001, maxHeight: 340, overflowY: 'auto' }}
                onMouseEnter={openSub}
                onMouseLeave={closeSub}
              >
                {traps.map(t => (
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
