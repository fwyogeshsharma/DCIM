// Shared device type / vendor / model data used by Add & Edit dialogs

export const DEVICE_TYPES = [
  { value: 'router',        label: 'Router' },
  { value: 'switch',        label: 'Switch' },
  { value: 'server',        label: 'Server' },
  { value: 'firewall',      label: 'Firewall' },
  { value: 'load_balancer', label: 'Load Balancer' },
  { value: 'ups',           label: 'UPS' },
  { value: 'pdu',           label: 'PDU' },
  { value: 'floor_pdu',     label: 'Floor PDU' },
  { value: 'rpp',           label: 'Remote Power Panel' },
  { value: 'generator',     label: 'Generator' },
  { value: 'utility_feed',  label: 'Utility Feed' },
  { value: 'switchgear',    label: 'Switchgear' },
  { value: 'ats',           label: 'Automatic Transfer Switch' },
  { value: 'mcc',           label: 'Motor Control Center' },
  { value: 'mpp',           label: 'Mechanical Power Panel' },
  { value: 'oob_switch',    label: 'OOB Switch' },
  { value: 'sensor',        label: 'Sensor' },
]

// Passive electrical gear: no monitoring card, so no network port, no IP, no SNMP.
// A bare RPP is a breaker panel — the only view of its load is the EV2 sub-meter
// clamped to its output conductors, which is a device of its own. Panels that ship
// branch-circuit monitoring are modelled as 'mpp' and keep their metering NIC.
// Mirrors FACILITY_PASSIVE_TYPES in core/device_manager.py.
export const PASSIVE_DEVICE_TYPES = new Set(['rpp'])

export const VENDORS = [
  'Cisco Systems',
  'Juniper Networks',
  'Arista Networks',
  'Hewlett Packard Enterprise',
  'Extreme Networks',
  'Huawei Technologies',
  'Dell Technologies',
  'Lenovo',
  'Supermicro',
  'IBM',
  'Palo Alto Networks',
  'F5 Networks',
  'APC by Schneider Electric',
  'Eaton',
  'Vertiv (Liebert)',
  'Raritan',
  'Server Technology',
  // Generator OEMs — models were keyed under these but the vendors were missing,
  // so the generator vendor/model cascade fell back to the network-vendor list.
  'Cummins',
  'Caterpillar',
  'Kohler Power',
  // Electrical-upstream OEMs (utility feed / switchgear / ATS / MCC / panelboard).
  'Schneider Electric',
  'ASCO Power Technologies',
]

// key: "device_type:Vendor Name" → model names
export const MODELS: Record<string, string[]> = {
  'router:Cisco Systems':        ['Cisco ISR 4321','Cisco ISR 4431','Cisco ASR 1001-X','Cisco ASR 9001','Cisco ASR 9904'],
  'router:Juniper Networks':     ['Juniper SRX345','Juniper SRX4600','Juniper MX204','Juniper MX480'],
  'router:Arista Networks':      ['Arista 7280R3-48YC8','Arista 7500R3-24D'],
  'router:Hewlett Packard Enterprise': ['HPE FlexNetwork 7510'],
  'router:Extreme Networks':     ['Extreme SLX 9640'],
  'router:Huawei Technologies':  ['Huawei NE40E-X3','Huawei NE8000-F8'],
  'router:Dell Technologies':    ['Dell EMC PowerSwitch Z9332F-ON'],
  'switch:Cisco Systems':        ['Cisco Catalyst 2960-X-24TS','Cisco Catalyst 3850-48','Cisco Catalyst 9300-48P','Cisco Nexus 9372PX','Cisco Nexus 93180YC-FX','Cisco Nexus 9336C-FX2','Cisco Nexus 9364C'],
  'switch:Juniper Networks':     ['Juniper EX2300-24T','Juniper EX4300-48T','Juniper EX9253','Juniper QFX5120-48Y','Juniper QFX10002-36Q'],
  'switch:Arista Networks':      ['Arista 7010T-48','Arista 7050CX3-32S','Arista 7060CX2-32S','Arista 7280CR3-96'],
  'switch:Hewlett Packard Enterprise': ['HPE Aruba 2930F-48G','HPE Aruba 6300M-48G','HPE FlexFabric 5940-48SFP+','HPE Aruba 8325-48Y8C'],
  'switch:Extreme Networks':     ['Extreme X460-G2-48t','Extreme X695-48Y','Extreme X870-96x'],
  'switch:Huawei Technologies':  ['Huawei S6730-H48Y6C','Huawei CE6870-48S6CQ','Huawei CE8850-64CQ'],
  'switch:Dell Technologies':    ['Dell S5248F-ON','Dell S5296F-ON','Dell Z9264F-ON'],
  'server:Cisco Systems':        ['Cisco UCS C220 M6','Cisco UCS C240 M6','Cisco UCS B200 M6'],
  'server:Hewlett Packard Enterprise': ['HPE ProLiant DL360 Gen10','HPE ProLiant DL380 Gen10','HPE ProLiant DL380 Gen11','HPE ProLiant DL380a Gen11 DLC','HPE ProLiant DL560 Gen10'],
  'server:Dell Technologies':    ['Dell PowerEdge R640','Dell PowerEdge R740','Dell PowerEdge R750','Dell PowerEdge R940','Dell PowerEdge R7525','Dell PowerEdge R760 DLC'],
  'server:Lenovo':               ['Lenovo ThinkSystem SR630 V2','Lenovo ThinkSystem SR650 V2','Lenovo ThinkSystem SR860 V2'],
  'server:Supermicro':           ['Supermicro SYS-120U-TNR','Supermicro SYS-220U-TNR','Supermicro AS-4124GS-TNR','Supermicro SYS-121H-TNR LCC'],
  'server:IBM':                  ['IBM Power System S922','IBM System x3850 X6','IBM FlexSystem x240 M5'],
  'firewall:Palo Alto Networks': ['PA-820','PA-3220','PA-5220','PA-5260'],
  'load_balancer:F5 Networks':   ['BIG-IP i2800','BIG-IP i4800','BIG-IP i5800','BIG-IP i10800'],
  'ups:APC by Schneider Electric': ['APC Smart-UPS 1500','APC Smart-UPS 3000','APC Smart-UPS SRT 5000','APC Symmetra PX 100'],
  'ups:Eaton':                   ['Eaton 5PX 2200','Eaton 9PX 5000','Eaton 9E 20kVA','Eaton 93E 40kVA'],
  'ups:Vertiv (Liebert)':        ['Vertiv Liebert GXT5 2000VA','Vertiv Liebert EXL S1 20kVA','Vertiv Liebert APS 20kVA'],
  'pdu:APC by Schneider Electric': ['APC AP8941','APC AP8886','APC AP8959','APC AP8681'],
  'pdu:Raritan':                 ['Raritan PX3-5190R','Raritan PX3-5161R','Raritan PX2-5170CR'],
  'pdu:Eaton':                   ['Eaton ePDU G3 MA 1U 16A','Eaton ePDU G3 MA 1U 32A','Eaton ePDU G3 MI 1U 32A'],
  'pdu:Vertiv (Liebert)':        ['Vertiv Geist rPDU2 15A','Vertiv Geist rPDU2 30A'],
  'pdu:Server Technology':       ['Sentry PT40','Sentry 4805-XLS'],
  'floor_pdu:APC by Schneider Electric': ['APC FlexPDU 40kVA','APC Galaxy RPP 80A'],
  'floor_pdu:Eaton':             ['Eaton PDU 80kVA','Eaton PDU 160kVA'],
  'floor_pdu:Vertiv (Liebert)':  ['Vertiv Liebert MPX 60kVA','Vertiv Liebert MPH2 24kVA'],
  'floor_pdu:Raritan':           ['Raritan PX3-5000 Floor 30A'],
  'rpp:APC by Schneider Electric': ['APC Galaxy RPP 80A','APC Galaxy RPP 160A'],
  'rpp:Schneider Electric':      ['Schneider PanelBoard 400A'],
  'rpp:Eaton':                   ['Eaton RPP 250A'],
  'generator:Cummins':           ['Cummins C250D5','Cummins C500D5','Cummins C1000D5'],
  'generator:Caterpillar':       ['Caterpillar XQ230','Caterpillar XQ600'],
  'generator:Kohler Power':      ['Kohler 250REOZJB','Kohler 600REOZJB'],
  // Electrical upstream — SKUs mirror the deployed topology (add_electrical_upstream.py).
  'utility_feed:Schneider Electric':      ['Schneider PowerLogic ION9000'],
  'switchgear:Eaton':                     ['Eaton Magnum DS 4000A'],
  'switchgear:ASCO Power Technologies':   ['ASCO 7000 Paralleling Switchgear'],
  'ats:ASCO Power Technologies':          ['ASCO 7000 Series 4000A'],
  'ats:Eaton':                            ['Eaton ATC-900'],
  'mcc:Eaton':                            ['Eaton Freedom 2100 MCC 1600A'],
  'mpp:Eaton':                            ['Eaton Pow-R-Line 3a 150A'],
  'oob_switch:Cisco Systems':    ['Cisco Catalyst 1000-48T','Cisco Catalyst 1000-24T'],
  'oob_switch:Hewlett Packard Enterprise': ['HPE Aruba 2530-48G','HPE Aruba 2530-24G'],
  'oob_switch:Dell Technologies': ['Dell N1148T-ON','Dell N1124T-ON'],
  'sensor:Raritan':              ['Raritan DPX2-T1H1','Raritan DPX2-T3H1'],
  'sensor:Vertiv (Liebert)':     ['Vertiv Geist GTHD','Vertiv Geist IMD-3'],
  'sensor:APC by Schneider Electric': ['APC NetBotz 250','APC NetBotz 355'],
}

export function vendorsForType(type: string): string[] {
  return VENDORS.filter(v => MODELS[`${type}:${v}`]?.length > 0)
}

export function modelsForTypeVendor(type: string, vendor: string): string[] {
  return MODELS[`${type}:${vendor}`] || []
}

export function deviceTypeLabel(value: string): string {
  return DEVICE_TYPES.find(t => t.value === value)?.label ?? value.replace(/_/g, ' ')
}
