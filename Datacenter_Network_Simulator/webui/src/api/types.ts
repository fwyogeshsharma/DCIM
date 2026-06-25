export interface IfaceStats {
  index: number
  name: string
  oper_status: number
  speed: number
  in_octets: number
  out_octets: number
  in_errors: number
  out_errors: number
  in_discards: number
  out_discards: number
  in_unicast_pkts: number
  out_unicast_pkts: number
}

export interface DeviceInfo {
  id: string
  name: string
  device_type: string
  vendor: string
  ip_address: string
  mgmt_ip?: string
  snmp_port: number
  gnmi_port: number
  interface_count: number
  cpu_usage: number
  memory_used: number
  memory_total: number
  disk_used: number
  disk_total: number
  sys_location: string
  sys_contact: string
  uptime: number
  model_name?: string
  os_name?: string
  os_version?: string
  country?: string
  datacenter_city?: string
  datacenter?: string
  room?: string
  floor?: string
  rack_row?: number
  rack_num?: number
  rack_unit?: number
  metrics_enabled?: boolean
  cpu_temp?: number
  inlet_temp?: number
  power_watts?: number
  liquid_cooled?: boolean
  fan_rpm?: number
  power_state?: string
  mid_temp?: number
  outlet_temp?: number
  humidity?: number
  dewpoint?: number
  airflow?: number
  ups_status?: string
  ups_output_load?: number
  ups_battery_status?: string
  ups_input_voltage?: number
  ups_input_frequency?: number
  ups_fan_status?: string
  ups_charger_status?: string
  ups_rectifier_status?: string
  ups_phase_status?: string
  ups_operating_mode?: string
  ups_battery_health?: number
  ups_energy_kwh?: number
  ups_battery_voltage?: number
  ups_output_voltage?: number
  ups_output_current?: number
  ups_output_power?: number
  ups_input_current?: number
  ups_input_power?: number
  pdu_load?: number
  pdu_voltage?: number
  pdu_power_factor?: number
  pdu_phase_imbalance?: number
  pdu_outlet_status?: string
  pdu_breaker_status?: string
  pdu_outlet_failure?: string
  pdu_smoke?: string
  pdu_outlet_current?: number
  pdu_ground_fault?: string
  pdu_real_power?: number
  pdu_apparent_power?: number
  pdu_energy_kwh?: number
  pdu_frequency?: number
  pdu_temperature?: number
  pdu_humidity?: number
  pdu_outlet_power?: number
  bgp_sessions_up?: number
  bgp_sessions_total?: number
  total_rx_bytes?: number
  total_tx_bytes?: number
  total_errors?: number
  total_discards?: number
  flapping_count?: number
  interfaces_up?: number
  interfaces_total?: number
  iface_stats?: IfaceStats[]
}

export interface GraphDevice {
  id: string
  name: string
  device_type: string
  vendor: string
  ip_address: string
  mgmt_ip?: string
  x: number
  y: number
  interface_count: number
  cpu_usage: number
  memory_used: number
  os_name?: string
  os_version?: string
  snmp_port?: number
  gnmi_port?: number
  model_name?: string
  power_state?: string
}

export interface GraphLink {
  id: string
  src_id: string
  dst_id: string
  layer: string
  broken: boolean
  src_iface?: number
  dst_iface?: number
}

export interface SnmpStatus {
  running: boolean
  ready: boolean
  pid?: number
  active_endpoints: string[]
  datasets_generated: boolean
  dataset_count: number
  trap_receiver_ip: string
  trap_receiver_port: number
  rule_engine_enabled: boolean
  autonomous_faults: boolean
  active_job_id?: string
}

export interface GnmiClient {
  peer: string
  mode: string
  target: string
  paths: string[]
  push_count: number
  connected_at: number
}

export interface GnmiStatus {
  running: boolean
  ready: boolean
  proxy_running: boolean
  port?: number
  active_targets: string[]
  datasets_generated: boolean
  dataset_count: number
  active_job_id?: string
  clients: GnmiClient[]
}

export interface SFlowStatus {
  running: boolean
  collector_ip: string
  collector_port: number
  interval: number
  sample_rate: number
  active_devices: number
}

export interface BacnetDevice {
  ip: string
  instance: number
  name: string
  circuits: number
  kind?: string
  status: string
}

export interface BacnetStatus {
  running: boolean
  base_instance: number
  frequency_hz: number
  port: number
  active_devices: number
  devices: BacnetDevice[]
}

export interface RedfishDevice {
  name:     string
  ip:       string
  port:     number
  vendor:   string
  model:    string
  url:      string
  sessions: number
  status:   string
  power_state?:   string
  indicator_led?: string
  sel_count?:     number
  subscriptions?: number
}

export interface RedfishSubscription {
  ip:          string
  device:      string
  id:          string
  destination: string
  context:     string
  event_types: string[] | string
}

export interface RedfishLogEntry {
  Id:       number
  Name?:    string
  Severity: string
  Message:  string
  Created:  string
}

export interface RedfishStatus {
  running: boolean
  port: number
  active_devices: number
  sessions: number
  devices?: RedfishDevice[]
}

export interface BindingStatus {
  selected_adapter: string
  subnet_mask: string
  bound_count: number
  bound_ips: string[]
  gnmi_bound_count: number
  gnmi_bound_ips: string[]
  active_job_id?: string
}

export interface AdaptersResponse {
  adapters: string[]
  adapter_labels: Record<string, string>
}

export interface Rule {
  name: string
  enabled: boolean
  description: string
  total_fired: number
  last_fired: string
  conditions: Record<string, unknown>[]
  actions: string[]
  severity?: string
}

export interface RulesTable {
  rule_engine_enabled: boolean
  total_fired_grand: number
  rules: Rule[]
}

export interface TrapRecord {
  timestamp: string
  device_id?: string
  device_name?: string
  device_ip?: string
  trap_type?: string
  display_name?: string
  severity?: string
  details: string
  rule_name: string
  iface_index?: number
}

export interface JobStatus {
  job_id: string
  operation: string
  status: string
  progress_done: number
  progress_total: number
  message: string
  error: string
  result?: unknown
  started_at: string
  finished_at: string
}

export interface HealthStatus {
  status: string
  core_initialized: boolean
  topology_loaded: boolean
  snmp_running: boolean
  gnmi_running: boolean
  rule_engine_enabled: boolean
  bound_ips: number
  gnmi_bound_ips: number
}

// ── Verdigris EV2 BACnet metrics ─────────────────────────────────────────────

export interface EV2CircuitMetrics {
  circuit:      number
  label:        string
  device_name?: string
  current?:     number
  kw?:          number
  kwh?:         number
  pf?:          number
  thd?:         number
}

export interface EV2PanelMetrics {
  total_kw?:    number
  total_kwh?:   number
  voltage_pha?: number
  voltage_phb?: number
  voltage_phc?: number
  current_pha?: number
  current_phb?: number
  current_phc?: number
  frequency?:   number
  power_factor?: number
  voltage_thd?: number
  current_thd?: number
  harmonic_3?:  number
  harmonic_5?:  number
  harmonic_7?:  number
  harmonic_9?:  number
  alarm_overcurrent:       boolean
  alarm_voltage_imbalance: boolean
  alarm_high_thd:          boolean
  alarm_phase_loss:        boolean
  alarm_sensor_fault:      boolean
}

export interface EV2DeviceSnapshot {
  ip:                  string
  instance:            number
  name:                string
  circuits:            number
  monitored_pdu_name?: string
  panel:               EV2PanelMetrics
  circuit_list:        EV2CircuitMetrics[]
}

// Chiller-plant BACnet device (chiller / pump / cooling_tower / valve / crah / cdu).
// `values` maps point name (e.g. "CHW_Supply_Temp") → present-value.
export interface PlantDeviceSnapshot {
  ip:          string
  instance:    number
  name:        string
  device_type: string
  values:      Record<string, number>
}

export interface LogEntry {
  id: number
  tab: 'snmp' | 'gnmi' | 'sflow' | 'bacnet' | 'redfish'
  msg: string
  level: string
  ts: number
}
