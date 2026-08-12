import { useState, useMemo, useEffect } from 'react'
import { api, errorMessage } from '../api/client'
import { useStore } from '../store/useStore'
import { DEVICE_TYPES, VENDORS, MODELS, PASSIVE_DEVICE_TYPES } from '../data/deviceConstants'
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
  'HPE ProLiant DL380a Gen11 DLC': ['2 x 100GbE','2 x 25GbE','Total: 4 ports'],
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
  'Dell PowerEdge R760 DLC': ['4 x 25GbE','Total: 4 ports'],
  'Lenovo ThinkSystem SR630 V2': ['2 x 10GbE','Total: 2 ports'],
  'Lenovo ThinkSystem SR650 V2': ['2 x 25GbE','Total: 2 ports'],
  'Lenovo ThinkSystem SR860 V2': ['4 x 25GbE','Total: 4 ports'],
  'Supermicro SYS-120U-TNR': ['2 x 10GbE','Total: 2 ports'],
  'Supermicro SYS-220U-TNR': ['4 x 25GbE','Total: 4 ports'],
  'Supermicro AS-4124GS-TNR': ['4 x 25GbE','Total: 4 ports'],
  'Supermicro SYS-121H-TNR LCC': ['2 x 25GbE','Total: 2 ports'],
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

// The dedicated lights-out port each server vendor ships, on top of the data NICs in
// MODEL_PORTS. Mirrors BMC_PORT_NAME in core/device_manager.py — the server is the
// authority and actually builds the port; this only labels it, so the Port Config
// display matches the device that gets created instead of under-counting by one.
const BMC_PORT: Record<string, string> = {
  'Dell Technologies':          'iDRAC',
  'Hewlett Packard Enterprise': 'iLO',
  'Lenovo':                     'XCC',
  'Supermicro':                 'IPMI',
  'IBM':                        'IMM',
  'Cisco Systems':              'CIMC',
}

// Facility / electrical / mechanical gear: no data plane at all, so every port is a
// monitoring NIC (SNMP/Modbus/BACnet) and the TYPE decides how many — 2 where a
// redundant/cascade NMC ships (managed PDU, dual-NMC UPS, ATS, switchgear), else 1.
// Deliberately NOT rpp: that panel is passive and has no card at all (see
// PASSIVE_DEVICE_TYPES).
// Mirrors FACILITY_MGMT_TYPES / FACILITY_REDUNDANT_MGMT_TYPES in core/device_manager.py.
const FACILITY_MGMT_TYPES = new Set([
  'pdu', 'floor_pdu', 'ups', 'ats', 'mcc', 'mpp', 'switchgear',
  'utility_feed', 'generator', 'energy_monitor', 'sensor', 'crah', 'chiller',
  'cooling_tower', 'pump', 'valve', 'cdu', 'modbus_gateway',
])
const FACILITY_REDUNDANT_MGMT_TYPES = new Set(['pdu', 'floor_pdu', 'ups', 'ats', 'switchgear'])

// The dedicated OOB management port a device ships, or '' for none. Mirrors
// mgmt_port_name() in core/device_manager.py. OOB switches and facility gear get
// none on purpose — an OOB switch's management links ARE its data plane, and a
// PDU/sensor's NICs are already its management NICs.
function mgmtPortName(deviceType: string, vendor: string): string {
  if (deviceType === 'server')        return BMC_PORT[vendor] || ''
  if (deviceType === 'firewall')      return 'management'
  if (deviceType === 'load_balancer') return 'mgmt'
  if (deviceType === 'switch' || deviceType === 'router') {
    if (vendor === 'Arista Networks')  return 'Management1'
    if (vendor === 'Juniper Networks') return 'fxp0'
    return 'mgmt0'
  }
  return ''
}

// Types the LINKS section covers, for the "pick a rack first" hint shown BEFORE the
// candidates call can run. The server is the authority (_LINK_REQUIREMENTS in
// api/routers/devices.py) and its supported=false answer is what actually hides the
// section — this only decides whether to promise it before we have asked.
const _LINKS_TYPES = new Set(['server'])

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

// MODEL_PORTS lists a SKU's data NICs and ends with a "Total:" line. A server, switch,
// router, firewall or LB also gets a dedicated management port built for it, so splice
// that in and re-total — otherwise the dialog promises 48 ports and the device arrives
// with 49.
function portConfigLines(f: Form): string[] {
  const lines = MODEL_PORTS[f.model_name] || []
  // A passive panel has no card to network — say so rather than showing "Total: 0",
  // which reads as a SKU whose ports the dialog failed to look up.
  if (PASSIVE_DEVICE_TYPES.has(f.device_type)) {
    return ['Passive panel — no monitoring card',
            'Total: no network ports (load is metered by its EV2)']
  }
  // Facility gear ignores the SKU's port line entirely — it has no data plane, and
  // MODEL_PORTS under-counts the redundant-NMC units at 1 port when they ship 2.
  if (FACILITY_MGMT_TYPES.has(f.device_type)) {
    const n = FACILITY_REDUNDANT_MGMT_TYPES.has(f.device_type) ? 2 : 1
    return [`${n} x 1GbE monitoring NIC${n > 1 ? 's (redundant)' : ''}`,
            `Total: ${n} port${n > 1 ? 's' : ''}`]
  }
  const mgmt = mgmtPortName(f.device_type, f.vendor)
  if (!mgmt) return lines
  const data = lines.filter(l => !l.startsWith('Total:'))
  const total = data.reduce((n, l) => n + (parseInt(l) || 0), 0) + 1
  const kind = f.device_type === 'server' ? 'BMC' : 'OOB mgmt'
  return [...data, `1 x 1GbE ${mgmt} (${kind})`, `Total: ${total} ports`]
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

// ── Link candidates (LINKS section) ──────────────────────────────────────────
// Shape of GET /devices/link-candidates. Each group is a layer, each slot is one
// cable to create, each candidate a far-end device with the ports/outlets that are
// actually free on it. The near end (this device's NIC / BMC port / PSU) is never
// picked here — the device does not exist yet, so the server chooses it, the same
// way fleet churn does.
// Every port/outlet in the picker's window, taken ones included: the dropdown shows
// the whole port map (so the operator sees what the switch carries and why the
// obvious port is unavailable) but only lets a free one be picked.
type LinkPort  = { value: number; label: string; used: boolean; peer: string | null }
// `same_rack` drives the optgroup split below: the far end sitting in the very rack
// being filled is the one an operator almost always wants (a cord or a coolant hose
// that never leaves the cabinet), so it is called out under its own heading rather
// than left to be spotted in a suffix.
type LinkCand  = { id: string; name: string; detail: string; same_rack?: boolean
                   ports: LinkPort[] }
// `optional` slots may be left unset. Nothing declares itself optional today — the
// coolant loop did until it was made required, because a cold-plated CPU has no air
// heatsink and so cannot run unplumbed — but the mechanism stays for a slot that is
// genuinely a choice. A slot whose
// candidates carry no ports is a PIPE — a cooling link has no ifIndex, so there is
// no port picker and the pick is complete with the far-end device alone.
type LinkSlot  = { key: string; label: string; port_label: string
                   near_end: string; optional?: boolean; candidates: LinkCand[] }
type LinkGroup = { key: string; layer: string; label: string; help: string
                   slots: LinkSlot[] }
type LinkCands = { device_type: string; supported: boolean; groups: LinkGroup[] }

// slot key -> chosen far end. Both halves must be set for the slot to count.
type LinkPick  = { dst_id: string; port: number | null }

// A slot whose candidates offer no ports at all terminates on nothing pickable — a
// cooling pipe. Judged from the candidates rather than the layer so the dialog stays
// generic: any future portless layer behaves the same without another special case.
const isPortless = (s: LinkSlot) =>
  s.candidates.length > 0 && s.candidates.every(c => c.ports.length === 0)

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

// `units` is the rack's whole U1–U40 face, each slot flagged with its occupant —
// shown in full (a rack face has no gaps) with the taken U's disabled. `free_units`
// remains the pickable set and drives the room/floor/row/rack cascade.
// `reserved` marks U41/U42 — the ToR pair positions. They are part of the 42U face
// and are shown, but no server may start there, so they render disabled with the
// reason rather than being cropped out of the elevation.
type RackUnit = { unit: number; used: boolean; occupant: string | null
                  reserved?: boolean }
// liquid_ready is always true for an air-cooled SKU — every rack takes one. For a
// direct-to-chip SKU it means the rack has a CDU with a free UQD pair on its
// manifold, and the cdu_* fields name that unit so the picker can show which cabinet
// the server is joining. The server decides this (see _rack_cdus) rather than the
// dialog, so the rack list and the LINKS section cannot disagree.
type RackOcc = { room: string; floor: string; rack_row: number; rack_num: number
                 used: number; total: number; free_units: number[]
                 // Everything in the cabinet, including 0U side-rail PDUs and gear at
                 // U41/42. `used` counts only SERVER-U spans, so a network rack of
                 // spines and PDUs reads 0/40 while holding four devices — showing
                 // just that made a populated rack look empty.
                 device_count?: number
                 // What the cabinet is for, from its occupants: compute (holds
                 // servers), network (spine/OOB/MDA gear), facility (RPP panel, CRAH,
                 // mechanical). A power-panel position reports 40 free U and would
                 // otherwise look like the emptiest rack in the hall.
                 role?: 'compute' | 'spine' | 'oob' | 'facility'
                 // Occupancy over the WHOLE 42U face. `used`/`total` cover only the
                 // server area U1-40 and are what placement is judged against; this
                 // is what the cabinet actually holds. A spine pair at U41/42 is
                 // invisible to the former and the reason a 2-switch rack read 0/40.
                 face_used?: number; face_total?: number 
                 units: RackUnit[]
                 liquid_ready?: boolean; cdu_name?: string | null
                 cdu_used?: number | null; cdu_ports?: number | null
                 // Air-side thermal co-limit: what this cabinet already rejects into
                 // the room, its budget, and what the chosen SKU would add. air_ok is
                 // the server's verdict — a liquid SKU adds only its residual
                 // fraction, so it can fit where an air box of the same draw cannot.
                 air_used_w?: number; air_budget_w?: number
                 air_add_w?: number; air_ok?: boolean
                 next_free: number | null; full: boolean }

export default function AddDeviceDialog({ onClose }: Props) {
  // Per-slice selectors, NOT a bare useStore() destructure: the bare form
  // subscribes to the WHOLE store, so the 4s status poll (snmp/gnmi/bacnet/… each
  // set()) re-rendered this dialog every tick, rebuilding every <option> child. A
  // re-render while a native <select> popup is open makes Chrome repaint the popup —
  // the blank-then-fill flash. Actions are stable refs and `devices` only changes on
  // an SSE 'devices' event, so the dialog now re-renders only when its data actually
  // changes.
  const devices          = useStore(s => s.devices)
  const fetchGraph       = useStore(s => s.fetchGraph)
  const fetchDevices     = useStore(s => s.fetchDevices)
  const setActiveView    = useStore(s => s.setActiveView)
  const setProvisionOpen = useStore(s => s.setProvisionOpen)

  // Deep-link to the Floor-Plan page's Provision dialog when a DC is out of rack
  // space (Add Device is placement-only — provisioning lives there).
  const goProvision = () => { setProvisionOpen(true); setActiveView('floorplan'); onClose() }

  // Per-DC rack occupancy (fetched when a DC is picked); drives the capacity-aware
  // Room→Floor→Row→Rack→Unit cascade — deeper levels appear only where there's space.
  const [racks,   setRacks]   = useState<RackOcc[]>([])
  const [locBusy, setLocBusy] = useState(false)
  // Height the server computed free_units for — lets the picker name the SPAN a pick
  // occupies rather than only its anchor U.
  const [uHeight, setUHeight] = useState(1)
  // Set by the occupancy fetch when the chosen SKU is direct-to-chip: the cascade
  // then only offers racks that can actually take a coolant hose.
  const [liquidOnly,    setLiquidOnly]    = useState(false)
  const [noLiquidRacks, setNoLiquidRacks] = useState(false)
  // room -> how many rack POSITIONS the hall's floor width fits. Positions with no
  // rack in them are shown greyed, so a gap in the numbering reads as free floor
  // space rather than as missing data. Racks are not modelled as objects — only the
  // gear in them is — so an empty position is a place to provision, not a thing.
  const [rowPositions, setRowPositions] = useState<Record<string, number>>({})
  // rack role -> device types it will take. Served by the API so the rule lives in
  // one place; the picker only renders the verdict.
  const [roleAccepts, setRoleAccepts] = useState<Record<string, string[]>>({})

  // Upper location levels come straight from the live inventory (cascading distinct).
  const uniq = (xs: (string | undefined)[]) =>
    [...new Set(xs.filter((x): x is string => !!x))].sort()

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

  // ── Links ────────────────────────────────────────────────────────────────
  // Fetched once the rack is known: which leaf/OOB/PDU a device of this type in
  // THIS rack can be cabled to, and what is free on each.
  const [cands,    setCands]    = useState<LinkCands | null>(null)
  const [linkBusy, setLinkBusy] = useState(false)
  const [picks,    setPicks]    = useState<Record<string, LinkPick>>({})

  const set = <K extends keyof Form>(k: K, v: Form[K]) =>
    setForm(f => ({ ...f, [k]: v }))

  const availableVendors = useMemo(() => vendorsFor(form.device_type), [form.device_type])
  const availableModels  = useMemo(() => modelsFor(form.device_type, form.vendor), [form.device_type, form.vendor])
  const sysLocation      = useMemo(() => buildSysLocation(form), [form])
  const showProdIp       = PROD_IP_TYPES.has(form.device_type)
  const isPassive        = PASSIVE_DEVICE_TYPES.has(form.device_type)

  // ── Cascading, capacity-aware location picker ───────────────────────────────
  // Country → City → Datacenter come from the inventory (distinct, cascading).
  // Room → Floor → Row → Rack → Unit come from the DC's rack occupancy and only
  // list levels that still have a free server U somewhere below them.
  const countries = useMemo(() => uniq(devices.map(d => d.country)), [devices])
  const cities = useMemo(() =>
    uniq(devices.filter(d => d.country === form.country).map(d => d.datacenter_city)),
    [devices, form.country])
  const dcOptions = useMemo(() =>
    uniq(devices.filter(d => d.country === form.country
      && d.datacenter_city === form.datacenter_city).map(d => d.datacenter)),
    [devices, form.country, form.datacenter_city])

  // Fetch this DC's rack occupancy whenever the DC changes. Re-fetches on type AND
  // model change: height is per-SKU, so the pickable U's differ between a 1U DL360 and
  // a 2U R750 in the very same rack.
  useEffect(() => {
    if (!form.datacenter) { setRacks([]); return }
    let live = true
    setLocBusy(true)
    api.rackOccupancy(form.datacenter, form.device_type, form.model_name)
      .then((r: unknown) => {
        if (!live) return
        const d = r as { racks?: RackOcc[]; device_u_height?: number
                         liquid_only?: boolean; no_liquid_racks?: boolean
                         row_positions?: Record<string, number>
                         role_accepts?: Record<string, string[]> }
        setRacks(d.racks || [])
        setUHeight(d.device_u_height || 1)
        setLiquidOnly(!!d.liquid_only)
        setNoLiquidRacks(!!d.no_liquid_racks)
        setRowPositions(d.row_positions || {})
        setRoleAccepts(d.role_accepts || {})
      })
      .catch(() => { if (live) setRacks([]) })
      .finally(() => { if (live) setLocBusy(false) })
    return () => { live = false }
  }, [form.datacenter, form.device_type, form.model_name])

  // "Usable" is free U AND — for a DLC SKU — a manifold to plug into. Upper levels
  // (room/floor/row) hide when nothing under them qualifies, exactly as they already
  // do for a rack with no space; the RACK level still lists the others, disabled, so
  // a cabinet never silently disappears without saying why.
  // Which cabinets may take THIS device. A server belongs in a compute rack — never
  // in the MDA rack beside the spines, and certainly not on an RPP panel position.
  // Network gear (router/firewall/LB/switch) legitimately lives in either a compute
  // rack (a ToR) or the network row (a border router in the MDA), so it accepts both.
  // Nothing IT goes in a facility position.
  const rackFits = (r: RackOcc) =>
    r.role === undefined ? true
    : form.device_type === 'server' ? r.role === 'compute'
    : r.role !== 'facility'

  const withSpace = (r: RackOcc) =>
    rackFits(r)
    && r.free_units.length > 0
    && (!liquidOnly || r.liquid_ready !== false)
    && r.air_ok !== false

  // An empty cascade has three different causes for a DLC SKU, each with a different
  // fix, so they must not share one message:
  //   no liquid-ready rack at all   → add a CDU, or pick an air-cooled model
  //   liquid-ready racks, none free → free a U in one of them; provisioning a NEW
  //                                   rack does not help, it would have no CDU
  //   neither                       → ordinary out-of-space, Provision is the answer
  // The middle case is easy to hit: a CDU rack can hold seven free U's and still
  // have nowhere to put a 2U server if they are all isolated singles.
  const anyLiquidReady = useMemo(
    () => racks.some(r => r.liquid_ready !== false), [racks])
  const liquidRacksFull = liquidOnly && anyLiquidReady
  // Out of COOLING rather than out of space: racks have free U (and a manifold, if
  // this is a DLC SKU) but the room cannot carry any more heat from them. Different
  // fix again — provisioning another rack helps, freeing a U does not.
  const airBlocked = useMemo(
    () => racks.length > 0
      && racks.some(r => r.free_units.length > 0 && (!liquidOnly || r.liquid_ready !== false))
      && !racks.some(withSpace),
    [racks, liquidOnly])
  const roomsWithSpace = useMemo(() =>
    uniq(racks.filter(withSpace).map(r => r.room)), [racks])
  const floorsWithSpace = useMemo(() =>
    uniq(racks.filter(r => withSpace(r) && r.room === form.room).map(r => r.floor)),
    [racks, form.room])
  const rowsWithSpace = useMemo(() =>
    [...new Set(racks.filter(r => withSpace(r) && r.room === form.room
      && r.floor === form.floor).map(r => r.rack_row))].sort((a, b) => a - b),
    [racks, form.room, form.floor])
  const racksInRow = useMemo(() =>
    racks.filter(r => r.room === form.room && r.floor === form.floor
      && r.rack_row === form.rack_row).sort((a, b) => a.rack_num - b.rack_num),
    [racks, form.room, form.floor, form.rack_row])
  const chosenRack = useMemo(() =>
    racks.find(r => r.room === form.room && r.floor === form.floor
      && r.rack_row === form.rack_row && r.rack_num === form.rack_num),
    [racks, form.room, form.floor, form.rack_row, form.rack_num])

  // Switching an already-located device to a DLC SKU can strand it in a rack with no
  // manifold. The location fields are filled in ABOVE the model field's influence, so
  // nothing else would notice — the form would look complete and submit a server into
  // a cabinet it cannot be plumbed in. Clear back to the rack level and let the
  // operator re-pick from the filtered list.
  useEffect(() => {
    if (!form.rack_num || !chosenRack) return
    if (liquidOnly && chosenRack.liquid_ready === false) {
      setForm(f => ({ ...f, rack_row: 0, rack_num: 0, rack_unit: 0 }))
      setErr('That rack has no CDU — pick a liquid-ready rack for a direct-to-chip server')
    } else if (chosenRack.air_ok === false) {
      // Switching to a hotter SKU can exhaust a rack's air budget under a selection
      // that was legal a moment ago — the same stale-location trap as the CDU case.
      const kw = (w?: number) => ((w || 0) / 1000).toFixed(1)
      setForm(f => ({ ...f, rack_row: 0, rack_num: 0, rack_unit: 0 }))
      setErr(`That rack is out of air cooling (${kw(chosenRack.air_used_w)} of `
             + `${kw(chosenRack.air_budget_w)} kW used) — pick another rack`)
    }
  }, [liquidOnly, chosenRack, form.rack_num])

  // Cascade resets: picking an upper level clears everything below it.
  const setCountry = (v: string) => setForm(f => ({ ...f, country: v, datacenter_city: '', datacenter: '', room: '', floor: '', rack_row: 0, rack_num: 0, rack_unit: 0 }))
  const setCity    = (v: string) => setForm(f => ({ ...f, datacenter_city: v, datacenter: '', room: '', floor: '', rack_row: 0, rack_num: 0, rack_unit: 0 }))
  const setDc      = (v: string) => setForm(f => ({ ...f, datacenter: v, room: '', floor: '', rack_row: 0, rack_num: 0, rack_unit: 0 }))
  const setRoom    = (v: string) => setForm(f => ({ ...f, room: v, floor: '', rack_row: 0, rack_num: 0, rack_unit: 0 }))
  const setFloor   = (v: string) => setForm(f => ({ ...f, floor: v, rack_row: 0, rack_num: 0, rack_unit: 0 }))
  const setRow     = (v: number) => setForm(f => ({ ...f, rack_row: v, rack_num: 0, rack_unit: 0 }))
  const setRack    = (num: number) => setForm(f => {
    const rk = racks.find(r => r.room === f.room && r.floor === f.floor
      && r.rack_row === f.rack_row && r.rack_num === num)
    return { ...f, rack_num: num, rack_unit: rk?.next_free ?? 0 }   // auto-fill next free U
  })

  // Fetch the cabling candidates once the rack is fully known — a leaf/PDU choice
  // is rack-specific (a cord never leaves the cabinet), so there is nothing to ask
  // for until row+rack are picked. Re-fetches on type/model change: what a device
  // must be cabled to, and which outlet its PSU inlet fits, both follow the model.
  const rackReady = !!(form.datacenter && form.room && form.rack_row > 0 && form.rack_num > 0)
  useEffect(() => {
    if (!rackReady) { setCands(null); setPicks({}); return }
    let live = true
    setLinkBusy(true)
    api.linkCandidates({
      device_type: form.device_type, vendor: form.vendor, model_name: form.model_name,
      datacenter: form.datacenter, room: form.room, floor: form.floor,
      rack_row: form.rack_row, rack_num: form.rack_num,
    })
      .then((r: unknown) => { if (live) { setCands(r as LinkCands); setPicks({}) } })
      .catch(() => { if (live) { setCands(null); setPicks({}) } })
      .finally(() => { if (live) setLinkBusy(false) })
    return () => { live = false }
  }, [rackReady, form.device_type, form.vendor, form.model_name,
      form.datacenter, form.room, form.floor, form.rack_row, form.rack_num])

  const linkSlots = useMemo(
    () => (cands?.supported ? cands.groups.flatMap(g => g.slots) : []),
    [cands])
  // A slot counts only with BOTH halves chosen: a device with no port names no
  // actual termination. Two exceptions:
  //   optional slots — may be left unset entirely (none are, currently)
  //   portless slots — a pipe has no ifIndex, so the far-end device IS the whole pick
  const missingLinks = useMemo(
    () => linkSlots.filter(s => {
      const p = picks[s.key]
      if (s.optional) return false
      if (!p || !p.dst_id) return true
      return !isPortless(s) && p.port === null
    }),
    [linkSlots, picks])
  // A pick can point at a port that reads used — the dropdown lists taken ports (the
  // UI disables them, but a stale view or a keyboard pick could still land on one).
  // The backend refuses it; catching it here names the port instead of failing at OK.
  const takenPicks = useMemo(
    () => linkSlots.filter(s => {
      const p = picks[s.key]
      if (!p || p.port === null) return false
      const cand = s.candidates.find(c => c.id === p.dst_id)
      return !!cand?.ports.find(pt => pt.value === p.port)?.used
    }),
    [linkSlots, picks])

  // Both cords on one PDU is not redundancy — it is a single point of failure
  // wearing an A/B label. The backend refuses the duplicate power edge anyway;
  // catching it here says why instead of failing at submit.
  const powerGroup = cands?.groups.find(g => g.layer === 'power')
  const feedClash = useMemo(() => {
    const ids = (powerGroup?.slots || [])
      .map(s => picks[s.key]?.dst_id).filter(Boolean)
    return ids.length > 1 && new Set(ids).size !== ids.length
  }, [powerGroup, picks])
  // Hard block: a device racked without its cabling is a dead node — it answers
  // SNMP but carries no traffic and draws no metered power.
  const linksReady = cands
    ? (!cands.supported
       || (missingLinks.length === 0 && takenPicks.length === 0 && !feedClash))
    : true

  const setPickDev = (slot: LinkSlot, dst_id: string) =>
    setPicks(p => {
      // Clearing an optional slot drops the pick entirely — an empty dst_id would
      // otherwise ride along to submit and POST a link to nowhere.
      if (!dst_id) { const { [slot.key]: _drop, ...rest } = p; return rest }
      const cand = slot.candidates.find(c => c.id === dst_id)
      // Auto-fill the first FREE port/outlet — the same "next free" the fleet takes,
      // and what an operator patching a fresh rack does anyway. Still overridable.
      // Must skip used ports: they are in the list now, and ports[0] is often taken.
      const first = cand?.ports.find(pt => !pt.used)
      return { ...p, [slot.key]: { dst_id, port: first?.value ?? null } }
    })
  const setPickPort = (slot: LinkSlot, port: number) =>
    setPicks(p => ({ ...p, [slot.key]: { dst_id: p[slot.key]?.dst_id || '', port } }))

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
    // Rack-location validation. A U-slot belongs to a specific rack in a room, so a
    // unit only makes sense with the full location set, and must be a real rack U.
    const { rack_row: rr, rack_num: rn, rack_unit: ru } = form
    if (rr < 0 || rn < 0 || ru < 0)                 { setErr('Row / Rack / Unit cannot be negative'); return }
    if (ru > 52)                                    { setErr('Rack Unit must be 1–52'); return }
    if (ru > 0 && (!form.datacenter.trim() || !form.room.trim() || rr <= 0 || rn <= 0)) {
      setErr('A Rack Unit needs Datacenter, Room, Row and Rack set'); return
    }
    if ((rr > 0 || rn > 0) && (rr <= 0 || rn <= 0)) { setErr('Set both Row and Rack together'); return }
    // The U list disables slots this device can't take; a stale view could still hold
    // one. Names the occupant rather than failing with a bare collision at OK.
    if (ru > 0 && chosenRack && !chosenRack.free_units.includes(ru)) {
      const at = chosenRack.units?.find(x => x.unit === ru)
      setErr(at?.used
        ? `U${ru} is occupied by ${at.occupant || 'another device'} — pick a free unit`
        : `U${ru} has no room for a ${form.device_type} — pick another unit`)
      return
    }
    if (feedClash) { setErr('Feed A and Feed B must be different PDUs'); return }
    if (takenPicks.length) {
      setErr(`Already in use: ${takenPicks.map(s => s.label).join(', ')} — pick a free port`)
      return
    }
    if (!linksReady) {
      setErr(`Cabling incomplete: ${missingLinks.map(s => s.label).join(', ')}`); return
    }
    // Layer comes from the slot's group, and outlet vs port from that layer: a power
    // cord lands on an outlet, an Ethernet link on a port. Sent with the device so
    // the whole thing lands or none of it does.
    // A cooling link carries NEITHER: it is a pipe, and the API rejects an iface or
    // an outlet on that layer outright. Sent as a bare far end, which is also why an
    // unset optional slot simply contributes nothing.
    const links = (cands?.supported ? cands.groups : []).flatMap(g =>
      g.slots.map(s => {
        const p = picks[s.key]
        if (!p || !p.dst_id) return null
        if (g.layer === 'cooling') return { layer: g.layer, dst_id: p.dst_id }
        if (p.port === null) return null
        return g.layer === 'power'
          ? { layer: g.layer, dst_id: p.dst_id, outlet: p.port }
          : { layer: g.layer, dst_id: p.dst_id, dst_iface: p.port }
      }).filter(Boolean))
    setBusy(true); setErr('')
    try {
      await api.addDevice({
        ...form,
        metrics_enabled: form.metrics_enabled,
        links,
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
          {/* A passive panel has no card to address, so the whole section is a dead
              control: the server clears mgmt_ip/snmp_port on create (see
              FACILITY_PASSIVE_TYPES in core/device_manager.py). Offering the inputs
              invited an operator to assign an address to gear that can never answer
              it, and then silently discarded what they typed. */}
          {!isPassive && (<>
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
          </>)}

          {/* ── Physical Location (cascading, capacity-aware) ── */}
          <div style={sectionHeader}>Physical Location</div>
          <FormRow label="Country">
            <select style={{ flex: 1 }} value={form.country} onChange={e => setCountry(e.target.value)}>
              <option value="">— select —</option>
              {countries.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </FormRow>
          {form.country && (
            <FormRow label="City">
              <select style={{ flex: 1 }} value={form.datacenter_city} onChange={e => setCity(e.target.value)}>
                <option value="">— select —</option>
                {cities.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </FormRow>
          )}
          {form.datacenter_city && (
            <FormRow label="Datacenter">
              <select style={{ flex: 1 }} value={form.datacenter} onChange={e => setDc(e.target.value)}>
                <option value="">— select —</option>
                {dcOptions.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </FormRow>
          )}
          {/* Room and below appear only where a free server U exists. */}
          {form.datacenter && (
            locBusy ? (
              <div style={{ fontSize: 10, color: 'var(--text-dim)', paddingLeft: 100, marginTop: 4 }}>loading rack capacity…</div>
            ) : roomsWithSpace.length === 0 ? (
              <div style={{ paddingLeft: 100, marginTop: 4 }}>
                <div style={{ fontSize: 10, color: 'var(--warn)' }}>
                  {/* Distinguish the two reasons a DC can come back empty. "No free
                      space" sends the operator to Provision; "no liquid-ready rack"
                      is a different problem with a different fix (add a CDU, or pick
                      an air-cooled SKU), and conflating them would send them to the
                      wrong dialog. */}
                  {airBlocked
                    ? `No rack in ${form.datacenter} has cooling headroom for ${form.model_name}
                       (${((racks[0]?.air_budget_w || 0) / 1000).toFixed(0)} kW of air per rack).
                       They have space, but the room cannot carry more heat from them —
                       provision another rack to spread the load, or pick a direct-to-chip SKU,
                       which puts only ~30% of its heat in the air.`
                    : noLiquidRacks
                    ? `No liquid-ready racks in ${form.datacenter} — ${form.model_name} is a
                       direct-to-chip SKU and needs a rack with a CDU. Add a CDU, or choose an
                       air-cooled model.`
                    : liquidRacksFull
                    ? `${form.datacenter}'s liquid-ready racks have no room for a ${uHeight}U
                       server. Free ${uHeight === 1 ? 'a U' : `${uHeight} contiguous U`} in a rack
                       with a CDU${uHeight > 1 ? ', or pick a 1U DLC model' : ''} — a newly
                       provisioned rack would have no CDU to plumb into.`
                    : `No free rack space in ${form.datacenter} — provision a rack/hall (or free a U) first.`}
                </div>
                {/* Provisioning adds racks and halls, which does not put a CDU in
                    one — offering it here would send the operator down a path that
                    cannot solve their problem. */}
                {(airBlocked || (!noLiquidRacks && !liquidRacksFull)) && (
                  <button type="button" onClick={goProvision} style={{
                    marginTop: 5, background: 'var(--accent)', border: '1px solid var(--accent)',
                    color: 'var(--on-accent)', borderRadius: 4, padding: '3px 9px', fontSize: 10,
                    fontWeight: 700, cursor: 'pointer',
                  }}>Provision capacity →</button>
                )}
              </div>
            ) : (
              <FormRow label="Room">
                <select style={{ flex: 1 }} value={form.room} onChange={e => setRoom(e.target.value)}>
                  <option value="">— select —</option>
                  {roomsWithSpace.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </FormRow>
            )
          )}
          {form.room && floorsWithSpace.length > 0 && (
            <FormRow label="Floor">
              <select style={{ flex: 1 }} value={form.floor} onChange={e => setFloor(e.target.value)}>
                <option value="">— select —</option>
                {floorsWithSpace.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </FormRow>
          )}
          {form.floor && rowsWithSpace.length > 0 && (
            <FormRow label="Row">
              <select style={{ flex: 1 }} value={form.rack_row || ''} onChange={e => setRow(parseInt(e.target.value) || 0)}>
                <option value="">— select —</option>
                {rowsWithSpace.map(r => <option key={r} value={r}>Row {r}</option>)}
              </select>
            </FormRow>
          )}
          {form.rack_row > 0 && (
            <FormRow label="Rack">
              <select style={{ flex: 1 }} value={form.rack_num || ''} onChange={e => setRack(parseInt(e.target.value) || 0)}>
                <option value="">— select —</option>
                {(() => {
                  // One list in rack-number order, real cabinets and empty floor
                  // positions interleaved — R1-04 must not appear after R1-13 just
                  // because one is populated and the other is not.
                  // Empty positions only in a row this device could actually use —
                  // a bare position in the CRAH row or on the power wall is not a
                  // server slot waiting to be provisioned.
                  // A row qualifies for empty-position hints unless it is nothing but
                  // facility gear (the CRAH perimeter, the power wall). The NETWORK
                  // row does qualify: its leftover positions are compute floor, and
                  // both the fleet and manual provisioning now build there.
                  const rowFits = racksInRow.length === 0
                    || racksInRow.some(r => r.role !== 'facility')
                  const byNum = new Map(racksInRow.map(r => [r.rack_num, r]))
                  const total = Math.max(rowPositions[form.room] || 0,
                                         ...racksInRow.map(r => r.rack_num), 0)
                  return Array.from({ length: total }, (_, i) => i + 1)
                    .map(num => byNum.get(num) ?? (rowFits ? num : null))
                    .filter(x => x !== null) as (RackOcc | number)[]
                })().map(entry => {
                  // A bare number is a position with no rack in it. Never selectable:
                  // there is no PDU or ToR there, so anything racked would be dead.
                  if (typeof entry === 'number') {
                    return (
                      <option key={`empty-${entry}`} value={entry} disabled>
                        R{form.rack_row}-{String(entry).padStart(2, '0')} · empty floor position — provision a rack first
                      </option>
                    )
                  }
                  const r = entry
                  if (!rackFits(r)) return null
                  const noSpace = r.free_units.length === 0
                  const noCdu   = liquidOnly && r.liquid_ready === false
                  const noAir   = r.air_ok === false
                  // A rack with none of the three problems is pickable. The others
                  // stay VISIBLE but disabled and labelled — an operator looking for
                  // R2-04 should find it and read why it cannot take this server,
                  // rather than wonder whether the cabinet exists at all.
                  if (noSpace && !liquidOnly && !noAir) return null
                  const kw = (w?: number) => ((w || 0) / 1000).toFixed(1)
                  // One reason, most fundamental first: no room beats no manifold
                  // beats no cooling. Showing all three would be noise — the operator
                  // needs the blocker, not an audit.
                  let tail: string
                  if (noSpace)      tail = 'full'
                  else if (noCdu)   tail = 'no CDU — not liquid-ready'
                  else if (noAir)   tail = `no cooling headroom · air ${kw(r.air_used_w)}/${kw(r.air_budget_w)} kW`
                  else {
                    // Occupancy over the whole face, so a rack reads as full as it
                    // actually is. Counts every body — the 4U CDU, the ToR at U42,
                    // a spine pair at U41/42 — because what an operator asks here is
                    // "how much of this cabinet is left", not "how much of the server
                    // window". Placement still uses the server area; see free_units.
                    const bits = [`${r.face_used ?? r.used}/${r.face_total ?? r.total} U`]
                    if (r.cdu_name) bits.push(`CDU ${r.cdu_used}/${r.cdu_ports}`)
                    if (r.air_budget_w) bits.push(`air ${kw(r.air_used_w)}/${kw(r.air_budget_w)} kW`)
                    tail = bits.join(' · ')
                  }
                  return (
                    <option key={r.rack_num} value={r.rack_num}
                            disabled={noSpace || noCdu || noAir}>
                      R{r.rack_row}-{String(r.rack_num).padStart(2, '0')} · {tail}
                    </option>
                  )
                })}
              </select>
            </FormRow>
          )}
          {form.rack_num > 0 && chosenRack && (
            <FormRow label={uHeight > 1 ? `Unit (${uHeight}U, bottom)` : 'Unit (U)'}>
              {/* The whole rack face — the elevation an operator reads before racking.
                  A rack_unit is the device's BOTTOM, so a selectable slot is labelled
                  with the SPAN it will occupy (U39–U40 for a 2U box), not just its
                  anchor. Selectable = free_units (where the whole height FITS): a lone
                  free U between two 2U servers, or U40 with only the ToR reserve above
                  it, is free yet cannot start a 2U box. */}
              <select style={{ flex: 1 }} value={form.rack_unit || ''} onChange={e => set('rack_unit', parseInt(e.target.value) || 0)}>
                {(chosenRack.units || []).map(u => {
                  const fits = chosenRack.free_units.includes(u.unit)
                  const top  = u.unit + uHeight - 1
                  const span = uHeight > 1 && fits ? `U${u.unit}–U${top}` : `U${u.unit}`
                  // Name the actual obstacle: the U above is taken, or the body would
                  // overrun the rack. "No room" alone reads as "this U is dead".
                  const blocker = chosenRack.units.find(
                    x => x.unit > u.unit && x.unit <= top && x.used)
                  const why = u.used ? ` — ${u.occupant || 'occupied'}`
                    : fits ? ''
                    : blocker ? ` — blocked by ${blocker.occupant} at U${blocker.unit}`
                    : ` — needs ${uHeight}U, would overrun U${chosenRack.total}`
                  return (
                    <option key={u.unit} value={u.unit} disabled={!fits}>
                      {span}{why}
                    </option>
                  )
                })}
              </select>
            </FormRow>
          )}
          {sysLocation && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', fontStyle: 'italic', marginTop: 4, paddingLeft: 100 }}>
              sysLocation: {sysLocation}
            </div>
          )}

          {/* ── Links (cabling — created atomically with the device) ── */}
          {(linkBusy || cands?.supported) && (
            <>
              <div style={sectionHeader}>Links</div>
              {linkBusy && (
                <div style={{ fontSize: 10, color: 'var(--text-dim)', paddingLeft: 100 }}>
                  loading cabling options…
                </div>
              )}
              {!linkBusy && cands?.supported && cands.groups.length === 0 && (
                <div style={{ fontSize: 10, color: 'var(--warn)', paddingLeft: 100 }}>
                  Nothing to cable to in this rack — no leaf with a free downlink, no
                  hall OOB and no rack PDU. Pick another rack.
                </div>
              )}
              {!linkBusy && cands?.supported && cands.groups.map(g => (
                <div key={g.key} style={{ marginBottom: 6 }}>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', paddingLeft: 100,
                                marginBottom: 3 }}>
                    {g.label} — <span style={{ color: 'var(--text-dim)' }}>{g.help}</span>
                  </div>
                  {g.slots.map(s => {
                    const pick = picks[s.key]
                    const cand = s.candidates.find(c => c.id === pick?.dst_id)
                    return (
                      <div key={s.key}>
                        <FormRow label={s.label}>
                          <select style={{ flex: 1 }} value={pick?.dst_id || ''}
                            onChange={e => setPickDev(s, e.target.value)}>
                            <option value="">
                              {s.optional ? '— none (air-cooled) —' : '— select —'}
                            </option>
                            {(() => {
                              // Split under headings only when there is a real choice
                              // to make. A list that is entirely one or the other gets
                              // no heading — a lone "This rack" group above a single
                              // entry is noise, not information.
                              const here = s.candidates.filter(c => c.same_rack)
                              const away = s.candidates.filter(c => !c.same_rack)
                              const opt = (c: LinkCand) => (
                                <option key={c.id} value={c.id}>{c.name} · {c.detail}</option>
                              )
                              if (!here.length || !away.length) return s.candidates.map(opt)
                              return (
                                <>
                                  <optgroup label="This rack">{here.map(opt)}</optgroup>
                                  <optgroup label="Elsewhere in this hall">{away.map(opt)}</optgroup>
                                </>
                              )
                            })()}
                          </select>
                        </FormRow>
                        {s.candidates.length === 0 && (
                          <div style={{ fontSize: 10, color: 'var(--warn)', paddingLeft: 104,
                                        marginTop: -2, marginBottom: 4 }}>
                            none available with a free port/outlet
                          </div>
                        )}
                        {cand && !isPortless(s) && (
                          <FormRow label={s.port_label}>
                            {/* Whole port map, taken ones disabled with their peer —
                                shows WHY a port is unavailable instead of hiding it. */}
                            <select style={{ flex: 1 }} value={pick?.port ?? ''}
                              onChange={e => setPickPort(s, parseInt(e.target.value))}>
                              {cand.ports.map(p => (
                                <option key={p.value} value={p.value} disabled={p.used}>
                                  {p.label}{p.used ? ` — in use${p.peer ? ` by ${p.peer}` : ''}` : ''}
                                </option>
                              ))}
                            </select>
                          </FormRow>
                        )}
                        {cand && (
                          <div style={{ fontSize: 10, color: 'var(--text-dim)',
                                        fontStyle: 'italic', paddingLeft: 104,
                                        marginTop: -2, marginBottom: 4 }}>
                            {s.near_end} → {cand.name}
                            {isPortless(s) ? ' (supply + return)' : ''}
                            {!isPortless(s) && pick?.port !== null
                             && cand.ports.find(p => p.value === pick?.port)
                              ? ` ${cand.ports.find(p => p.value === pick?.port)!.label}` : ''}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
              {feedClash && (
                <div style={{ fontSize: 10, color: 'var(--red)', paddingLeft: 100 }}>
                  Feed A and Feed B are the same PDU — that is not redundancy.
                </div>
              )}
            </>
          )}
          {/* Links are rack-specific, so there is nothing to offer until a rack is set. */}
          {!rackReady && _LINKS_TYPES.has(form.device_type) && (
            <>
              <div style={sectionHeader}>Links</div>
              <div style={{ fontSize: 10, color: 'var(--text-dim)', paddingLeft: 100 }}>
                Pick a rack above to choose the leaf, OOB switch and PDU feeds.
              </div>
            </>
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
                ? portConfigLines(form).map((line, i, arr) => (
                    <div key={i} style={{ color: i === arr.length - 1 ? 'var(--text-muted)' : 'var(--text)' }}>
                      {line}
                    </div>
                  ))
                : <span style={{ color: 'var(--text-dim)' }}>—</span>
              }
            </div>
          </FormRow>
          {/* Nothing polls a passive panel, so there are no metrics to simulate — the
              server forces metrics_enabled false on create. The checkbox rendered
              ticked and did nothing. */}
          {!isPassive && (
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
          )}

          {err && <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 6 }}>{err}</div>}
        </div>

        {/* Footer */}
        <div style={{ display: 'flex', gap: 8, padding: '8px 16px 12px', flexShrink: 0, borderTop: '1px solid var(--border)' }}>
          <button className="primary" style={{ flex: 1 }} onClick={submit}
            disabled={busy || !linksReady}
            title={linksReady ? '' :
              `Cabling incomplete: ${missingLinks.map(s => s.label).join(', ')}`}>
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
