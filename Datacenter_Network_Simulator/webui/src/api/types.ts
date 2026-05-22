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
  cpu_temp?: number
  inlet_temp?: number
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

export interface LogEntry {
  id: number
  tab: 'snmp' | 'gnmi' | 'sflow'
  msg: string
  level: string
  ts: number
}
