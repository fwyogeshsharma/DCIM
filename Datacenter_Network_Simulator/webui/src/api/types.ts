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
  disk_used: number
  sys_location: string
  sys_contact: string
  uptime: number
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
