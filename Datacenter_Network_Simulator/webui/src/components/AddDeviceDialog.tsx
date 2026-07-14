import { useState, useMemo } from 'react'
import { api, errorMessage } from '../api/client'
import { useStore } from '../store/useStore'
import { DEVICE_TYPES, VENDORS, MODELS } from '../data/deviceConstants'
import NumberInput from './NumberInput'

// port config lines per model name
const MODEL_PORTS: Record<string, string[]> = {
  'Cisco ISR 4321': ['2 x 1GbE','Total: 2 ports'],
  'Cisco ISR 4431': ['4 x 1GbE','Total: 4 ports'],
  'Cisco ASR 1001-X': ['6 x 1GbE','2 x 10GbE','Total: 8 ports'],
  'Cisco ASR 9001': ['4 x 10GbE','2 x 100GbE','Total: 6 ports'],
  'Cisco ASR 9904': ['8 x 100GbE','Total: 8 ports'],
  'Cisco Catalyst 2960-X-24TS': ['24 x 1GbE','4 x 10GbE','Total: 28 ports'],
  'Cisco Catalyst 3850-48': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'Cisco Catalyst 9300-48P': ['48 x 1GbE','4 x 25GbE','Total: 52 ports'],
  'Cisco Nexus 9372PX': ['48 x 10GbE','6 x 40GbE','Total: 54 ports'],
  'Cisco Nexus 93180YC-FX': ['48 x 25GbE','6 x 100GbE','Total: 54 ports'],
  'Cisco Nexus 9336C-FX2': ['36 x 100GbE','Total: 36 ports'],
  'Cisco Nexus 9364C': ['64 x 100GbE','Total: 64 ports'],
  'Cisco UCS C220 M6': ['2 x 10GbE','Total: 2 ports'],
  'Cisco UCS C240 M6': ['4 x 25GbE','Total: 4 ports'],
  'Cisco UCS B200 M6': ['2 x 25GbE','Total: 2 ports'],
  'Juniper SRX345': ['16 x 1GbE','Total: 16 ports'],
  'Juniper SRX4600': ['8 x 10GbE','4 x 100GbE','Total: 12 ports'],
  'Juniper MX204': ['8 x 10GbE','4 x 100GbE','Total: 12 ports'],
  'Juniper MX480': ['20 x 10GbE','8 x 100GbE','Total: 28 ports'],
  'Juniper EX2300-24T': ['24 x 1GbE','4 x 10GbE','Total: 28 ports'],
  'Juniper EX4300-48T': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'Juniper EX9253': ['48 x 10GbE','6 x 100GbE','Total: 54 ports'],
  'Juniper QFX5120-48Y': ['48 x 25GbE','8 x 100GbE','Total: 56 ports'],
  'Juniper QFX10002-36Q': ['36 x 40GbE','Total: 36 ports'],
  'Arista 7280R3-48YC8': ['48 x 25GbE','8 x 100GbE','Total: 56 ports'],
  'Arista 7500R3-24D': ['24 x 100GbE','Total: 24 ports'],
  'Arista 7010T-48': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'Arista 7050CX3-32S': ['32 x 100GbE','2 x 10GbE','Total: 34 ports'],
  'Arista 7060CX2-32S': ['32 x 100GbE','Total: 32 ports'],
  'Arista 7280CR3-96': ['96 x 100GbE','Total: 96 ports'],
  'HPE FlexNetwork 7510': ['8 x 1GbE','2 x 10GbE','Total: 10 ports'],
  'HPE Aruba 2930F-48G': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'HPE Aruba 6300M-48G': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'HPE FlexFabric 5940-48SFP+': ['48 x 10GbE','6 x 40GbE','Total: 54 ports'],
  'HPE Aruba 8325-48Y8C': ['48 x 25GbE','8 x 100GbE','Total: 56 ports'],
  'HPE ProLiant DL360 Gen10': ['2 x 10GbE','Total: 2 ports'],
  'HPE ProLiant DL380 Gen10': ['4 x 10GbE','Total: 4 ports'],
  'HPE ProLiant DL380 Gen11': ['4 x 25GbE','Total: 4 ports'],
  'HPE ProLiant DL560 Gen10': ['4 x 25GbE','Total: 4 ports'],
  'Extreme SLX 9640': ['24 x 10GbE','4 x 100GbE','Total: 28 ports'],
  'Extreme X460-G2-48t': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'Extreme X695-48Y': ['48 x 25GbE','8 x 100GbE','Total: 56 ports'],
  'Extreme X870-96x': ['96 x 25GbE','8 x 100GbE','Total: 104 ports'],
  'Huawei NE40E-X3': ['16 x 1GbE','4 x 10GbE','Total: 20 ports'],
  'Huawei NE8000-F8': ['8 x 100GbE','Total: 8 ports'],
  'Huawei S6730-H48Y6C': ['48 x 25GbE','6 x 100GbE','Total: 54 ports'],
  'Huawei CE6870-48S6CQ': ['48 x 25GbE','6 x 100GbE','Total: 54 ports'],
  'Huawei CE8850-64CQ': ['64 x 100GbE','Total: 64 ports'],
  'Dell EMC PowerSwitch Z9332F-ON': ['32 x 100GbE','2 x 10GbE','Total: 34 ports'],
  'Dell S5248F-ON': ['48 x 25GbE','2 x 100GbE','2 x 10GbE','Total: 52 ports'],
  'Dell S5296F-ON': ['96 x 25GbE','4 x 100GbE','Total: 100 ports'],
  'Dell Z9264F-ON': ['64 x 100GbE','2 x 10GbE','Total: 66 ports'],
  'Dell PowerEdge R640': ['2 x 10GbE','Total: 2 ports'],
  'Dell PowerEdge R740': ['2 x 10GbE','Total: 2 ports'],
  'Dell PowerEdge R750': ['2 x 25GbE','Total: 2 ports'],
  'Dell PowerEdge R940': ['4 x 25GbE','Total: 4 ports'],
  'Dell PowerEdge R7525': ['4 x 25GbE','Total: 4 ports'],
  'Lenovo ThinkSystem SR630 V2': ['2 x 10GbE','Total: 2 ports'],
  'Lenovo ThinkSystem SR650 V2': ['2 x 25GbE','Total: 2 ports'],
  'Lenovo ThinkSystem SR860 V2': ['4 x 25GbE','Total: 4 ports'],
  'Supermicro SYS-120U-TNR': ['2 x 10GbE','Total: 2 ports'],
  'Supermicro SYS-220U-TNR': ['4 x 25GbE','Total: 4 ports'],
  'Supermicro AS-4124GS-TNR': ['4 x 25GbE','Total: 4 ports'],
  'PA-820': ['4 x 1GbE','4 x 10GbE','Total: 8 ports'],
  'PA-3220': ['4 x 1GbE','8 x 10GbE','Total: 12 ports'],
  'PA-5220': ['8 x 10GbE','4 x 40GbE','Total: 12 ports'],
  'PA-5260': ['8 x 10GbE','4 x 100GbE','Total: 12 ports'],
  'BIG-IP i2800': ['4 x 1GbE','4 x 10GbE','Total: 8 ports'],
  'BIG-IP i4800': ['8 x 10GbE','Total: 8 ports'],
  'BIG-IP i5800': ['8 x 10GbE','2 x 40GbE','Total: 10 ports'],
  'BIG-IP i10800': ['8 x 10GbE','4 x 100GbE','Total: 12 ports'],
  'IBM Power System S922': ['4 x 10GbE','Total: 4 ports'],
  'IBM System x3850 X6': ['4 x 10GbE','Total: 4 ports'],
  'IBM FlexSystem x240 M5': ['2 x 25GbE','Total: 2 ports'],
  'APC Smart-UPS 1500': ['1 x 1GbE','Total: 1 port'],
  'APC Smart-UPS 3000': ['1 x 1GbE','Total: 1 port'],
  'APC Smart-UPS SRT 5000': ['1 x 1GbE','Total: 1 port'],
  'APC Symmetra PX 100': ['1 x 1GbE','Total: 1 port'],
  'Eaton 5PX 2200': ['1 x 1GbE','Total: 1 port'],
  'Eaton 9PX 5000': ['1 x 1GbE','Total: 1 port'],
  'Eaton 9E 20kVA': ['1 x 1GbE','Total: 1 port'],
  'Eaton 93E 40kVA': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Liebert GXT5 2000VA': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Liebert EXL S1 20kVA': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Liebert APS 20kVA': ['1 x 1GbE','Total: 1 port'],
  'APC AP8941': ['1 x 1GbE','Total: 1 port'],
  'APC AP8886': ['1 x 1GbE','Total: 1 port'],
  'APC AP8959': ['1 x 1GbE','Total: 1 port'],
  'APC AP8681': ['1 x 1GbE','Total: 1 port'],
  'Raritan PX3-5190R': ['1 x 1GbE','Total: 1 port'],
  'Raritan PX3-5161R': ['1 x 1GbE','Total: 1 port'],
  'Raritan PX2-5170CR': ['1 x 1GbE','Total: 1 port'],
  'Eaton ePDU G3 MA 1U 16A': ['1 x 1GbE','Total: 1 port'],
  'Eaton ePDU G3 MA 1U 32A': ['1 x 1GbE','Total: 1 port'],
  'Eaton ePDU G3 MI 1U 32A': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Geist rPDU2 15A': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Geist rPDU2 30A': ['1 x 1GbE','Total: 1 port'],
  'Sentry PT40': ['1 x 1GbE','Total: 1 port'],
  'Sentry 4805-XLS': ['1 x 1GbE','Total: 1 port'],
  'APC FlexPDU 40kVA': ['1 x 1GbE','Total: 1 port'],
  'APC Galaxy RPP 80A': ['1 x 1GbE','Total: 1 port'],
  'Eaton PDU 80kVA': ['1 x 1GbE','Total: 1 port'],
  'Eaton PDU 160kVA': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Liebert MPX 60kVA': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Liebert MPH2 24kVA': ['1 x 1GbE','Total: 1 port'],
  'Raritan PX3-5000 Floor 30A': ['1 x 1GbE','Total: 1 port'],
  'Cisco Catalyst 1000-48T': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'Cisco Catalyst 1000-24T': ['24 x 1GbE','4 x 10GbE','Total: 28 ports'],
  'HPE Aruba 2530-48G': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'HPE Aruba 2530-24G': ['24 x 1GbE','4 x 10GbE','Total: 28 ports'],
  'Dell N1148T-ON': ['48 x 1GbE','4 x 10GbE','Total: 52 ports'],
  'Dell N1124T-ON': ['24 x 1GbE','4 x 10GbE','Total: 28 ports'],
  'Raritan DPX2-T1H1': ['1 x 1GbE','Total: 1 port'],
  'Raritan DPX2-T3H1': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Geist GTHD': ['1 x 1GbE','Total: 1 port'],
  'Vertiv Geist IMD-3': ['1 x 1GbE','Total: 1 port'],
  'APC NetBotz 250': ['1 x 1GbE','Total: 1 port'],
  'APC NetBotz 355': ['1 x 1GbE','Total: 1 port'],
}

const PROD_IP_TYPES = new Set(['server', 'switch', 'router', 'firewall', 'load_balancer'])

// ── Helpers ─────────────────────────────────────────────────────────────────

function defaultVendorFor(type: string): string {
  const typeVendorMap: Record<string, string> = {
    router:        'Cisco Systems',
    switch:        'Cisco Systems',
    server:        'Dell Technologies',
    firewall:      'Palo Alto Networks',
    load_balancer: 'F5 Networks',
    ups:           'APC by Schneider Electric',
    pdu:           'APC by Schneider Electric',
    floor_pdu:     'APC by Schneider Electric',
    rpp:           'APC by Schneider Electric',
    generator:     'Cummins',
    oob_switch:    'Cisco Systems',
    sensor:        'Raritan',
    utility_feed:  'Schneider Electric',
    switchgear:    'Eaton',
    ats:           'ASCO Power Technologies',
    mcc:           'Eaton',
    mpp:           'Eaton',
  }
  return typeVendorMap[type] || VENDORS[0]
}

function vendorsFor(type: string): string[] {
  return VENDORS.filter(v => MODELS[`${type}:${v}`]?.length > 0)
}

function modelsFor(type: string, vendor: string): string[] {
  return MODELS[`${type}:${vendor}`] || []
}

function buildSysLocation(f: Form): string {
  if (!f.datacenter) return ''
  const parts: string[] = []
  if (f.country)         parts.push(f.country)
  if (f.datacenter_city) parts.push(f.datacenter_city)
  parts.push(f.datacenter)
  if (f.floor)     parts.push(`Floor ${f.floor}`)
  if (f.room)      parts.push(`Room ${f.room}`)
  if (f.rack_row)  parts.push(`Row ${f.rack_row}`)
  if (f.rack_num)  parts.push(`Rack ${f.rack_num}`)
  if (f.rack_unit) parts.push(`U${f.rack_unit}`)
  return parts.join(', ')
}

// ── Types ────────────────────────────────────────────────────────────────────

interface Form {
  name: string
  device_type: string
  vendor: string
  model_name: string
  ip_address: string
  mgmt_ip: string
  snmp_port: number
  gnmi_port: number
  sys_contact: string
  country: string
  datacenter_city: string
  datacenter: string
  room: string
  floor: string
  rack_row: number
  rack_num: number
  rack_unit: number
  metrics_enabled: boolean
}

interface Props { onClose: () => void }

// ── Styles ───────────────────────────────────────────────────────────────────

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
}
const dialog: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 6, width: 420, maxHeight: '90vh',
  display: 'flex', flexDirection: 'column',
  boxShadow: '0 16px 48px rgba(0,0,0,0.8)',
}
const sectionHeader: React.CSSProperties = {
  fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
  textTransform: 'uppercase', letterSpacing: '0.8px',
  borderBottom: '1px solid var(--border)',
  paddingBottom: 4, marginBottom: 6, marginTop: 10,
}
const spinnerStyle: React.CSSProperties = { width: 72 }

// ── Component ────────────────────────────────────────────────────────────────

export default function AddDeviceDialog({ onClose }: Props) {
  const { fetchGraph, fetchDevices } = useStore()

  const initType = 'router'
  const initVendor = defaultVendorFor(initType)
  const initModels = modelsFor(initType, initVendor)

  const [form, setForm] = useState<Form>({
    name: '', device_type: initType, vendor: initVendor,
    model_name: initModels[0] || '',
    ip_address: '', mgmt_ip: '', snmp_port: 161, gnmi_port: 57400,
    sys_contact: '',
    country: '', datacenter_city: '', datacenter: '',
    room: '', floor: '', rack_row: 0, rack_num: 0, rack_unit: 0,
    metrics_enabled: true,
  })
  const [busy, setBusy] = useState(false)
  const [err,  setErr]  = useState('')

  const set = <K extends keyof Form>(k: K, v: Form[K]) =>
    setForm(f => ({ ...f, [k]: v }))

  const availableVendors = useMemo(() => vendorsFor(form.device_type), [form.device_type])
  const availableModels  = useMemo(() => modelsFor(form.device_type, form.vendor), [form.device_type, form.vendor])
  const sysLocation      = useMemo(() => buildSysLocation(form), [form])
  const showProdIp       = PROD_IP_TYPES.has(form.device_type)

  function onTypeChange(newType: string) {
    const vendors = vendorsFor(newType)
    const vendor  = vendors.includes(form.vendor) ? form.vendor : (vendors[0] || form.vendor)
    const models  = modelsFor(newType, vendor)
    setForm(f => ({ ...f, device_type: newType, vendor, model_name: models[0] || '' }))
  }

  function onVendorChange(newVendor: string) {
    const models = modelsFor(form.device_type, newVendor)
    setForm(f => ({ ...f, vendor: newVendor, model_name: models[0] || '' }))
  }

  async function submit() {
    if (!form.name.trim())                          { setErr('Name required');       return }
    if (showProdIp && !form.ip_address.trim())      { setErr('IP address required'); return }
    setBusy(true); setErr('')
    try {
      await api.addDevice({
        ...form,
        metrics_enabled: form.metrics_enabled,
      } as Parameters<typeof api.addDevice>[0])
      fetchGraph(); fetchDevices()
      onClose()
    } catch (e: unknown) {
      setErr(errorMessage(e))
    } finally { setBusy(false) }
  }

  return (
    <div style={overlay} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div style={dialog}>
        {/* Header */}
        <div className="panel-header" style={{ borderRadius: '6px 6px 0 0', flexShrink: 0 }}>
          <span className="title">Add Device</span>
          <button onClick={onClose} style={{ border:'none', background:'none', color:'var(--text-muted)', fontSize:16, cursor:'pointer', padding:'0 4px' }}>✕</button>
        </div>

        {/* Scrollable body */}
        <div style={{ padding: '10px 16px 14px', display: 'flex', flexDirection: 'column', gap: 0, overflowY: 'auto' }}>

          {/* ── Device Identity ── */}
          <div style={sectionHeader}>Device Identity</div>
          <FormRow label="Name *">
            <input style={{ flex: 1 }} value={form.name}
              onChange={e => set('name', e.target.value)} placeholder="e.g. Router1" />
          </FormRow>
          <FormRow label="Type">
            <select style={{ flex: 1 }} value={form.device_type}
              onChange={e => onTypeChange(e.target.value)}>
              {DEVICE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </FormRow>
          <FormRow label="Vendor">
            <select style={{ flex: 1 }} value={form.vendor}
              onChange={e => onVendorChange(e.target.value)}>
              {availableVendors.length > 0
                ? availableVendors.map(v => <option key={v} value={v}>{v}</option>)
                : VENDORS.map(v => <option key={v} value={v}>{v}</option>)
              }
            </select>
          </FormRow>
          <FormRow label="Model">
            <select style={{ flex: 1 }} value={form.model_name}
              onChange={e => set('model_name', e.target.value)}
              disabled={availableModels.length === 0}>
              {availableModels.length > 0
                ? availableModels.map(m => <option key={m} value={m}>{m}</option>)
                : <option value="">— no models —</option>
              }
            </select>
          </FormRow>

          {/* ── Network Settings ── */}
          <div style={sectionHeader}>Network Settings</div>
          {showProdIp && (
            <FormRow label="Production IP *">
              <input style={{ flex: 1 }} value={form.ip_address}
                onChange={e => set('ip_address', e.target.value)} placeholder="auto if blank" />
            </FormRow>
          )}
          <FormRow label="Mgmt IP (OOB)">
            <input style={{ flex: 1 }} value={form.mgmt_ip}
              onChange={e => set('mgmt_ip', e.target.value)}
              placeholder="assigned by OOB management network" />
          </FormRow>
          <FormRow label="SNMP Port">
            <NumberInput style={spinnerStyle} value={form.snmp_port}
              onChange={n => set('snmp_port', n)} fallback={161} int />
          </FormRow>
          {['router', 'switch'].includes(form.device_type) && (
          <FormRow label="gNMI Port">
            <NumberInput style={spinnerStyle} value={form.gnmi_port}
              onChange={n => set('gnmi_port', n)} fallback={57400} int />
          </FormRow>
          )}
          <FormRow label="Community">
            <input style={{ flex: 1, color: 'var(--text-muted)', fontStyle: 'italic' }}
              value="" readOnly placeholder="auto (mirrors IP address)" />
          </FormRow>

          {/* ── Physical Location ── */}
          <div style={sectionHeader}>Physical Location</div>
          <FormRow label="Country">
            <input style={{ flex: 1 }} value={form.country}
              onChange={e => set('country', e.target.value)} placeholder="e.g. USA" />
          </FormRow>
          <FormRow label="City">
            <input style={{ flex: 1 }} value={form.datacenter_city}
              onChange={e => set('datacenter_city', e.target.value)} placeholder="e.g. Dallas" />
          </FormRow>
          <FormRow label="Datacenter">
            <input style={{ flex: 1 }} value={form.datacenter}
              onChange={e => set('datacenter', e.target.value)} placeholder="e.g. DC1" />
          </FormRow>
          <FormRow label="Room">
            <input style={{ flex: 1 }} value={form.room}
              onChange={e => set('room', e.target.value)} placeholder="e.g. A" />
          </FormRow>
          <FormRow label="Floor">
            <input style={{ flex: 1 }} value={form.floor}
              onChange={e => set('floor', e.target.value)} placeholder="e.g. 1" />
          </FormRow>
          <FormRow label="Row">
            <input type="number" style={spinnerStyle} value={form.rack_row || ''}
              onChange={e => set('rack_row', parseInt(e.target.value) || 0)} placeholder="—" min={0} />
          </FormRow>
          <FormRow label="Rack">
            <input type="number" style={spinnerStyle} value={form.rack_num || ''}
              onChange={e => set('rack_num', parseInt(e.target.value) || 0)} placeholder="—" min={0} />
          </FormRow>
          <FormRow label="Unit (U)">
            <input type="number" style={spinnerStyle} value={form.rack_unit || ''}
              onChange={e => set('rack_unit', parseInt(e.target.value) || 0)} placeholder="—" min={0} />
          </FormRow>
          {sysLocation && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', fontStyle: 'italic', marginTop: 4, paddingLeft: 100 }}>
              sysLocation: {sysLocation}
            </div>
          )}

          {/* ── Interfaces ── */}
          <div style={sectionHeader}>Interfaces</div>
          <FormRow label="Port Config">
            <div style={{
              flex: 1, fontFamily: 'monospace', fontSize: 10,
              color: form.model_name ? 'var(--text)' : 'var(--text-dim)',
              lineHeight: 1.6,
            }}>
              {form.model_name && MODEL_PORTS[form.model_name]
                ? MODEL_PORTS[form.model_name].map((line, i, arr) => (
                    <div key={i} style={{ color: i === arr.length - 1 ? 'var(--text-muted)' : 'var(--text)' }}>
                      {line}
                    </div>
                  ))
                : <span style={{ color: 'var(--text-dim)' }}>—</span>
              }
            </div>
          </FormRow>
          <FormRow label="">
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)' }}>
              <input
                type="checkbox"
                checked={form.metrics_enabled}
                onChange={e => set('metrics_enabled', e.target.checked)}
                style={{ accentColor: 'var(--accent)', width: 13, height: 13 }}
              />
              Enable metric simulation
            </label>
          </FormRow>

          {err && <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 6 }}>{err}</div>}
        </div>

        {/* Footer */}
        <div style={{ display: 'flex', gap: 8, padding: '8px 16px 12px', flexShrink: 0, borderTop: '1px solid var(--border)' }}>
          <button className="primary" style={{ flex: 1 }} onClick={submit} disabled={busy}>
            {busy ? 'Adding…' : 'OK'}
          </button>
          <button style={{ flex: 1 }} onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 11, width: 96, textAlign: 'right', flexShrink: 0 }}>{label}</span>
      {children}
    </div>
  )
}
