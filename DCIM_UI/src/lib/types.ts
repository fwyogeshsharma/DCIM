// TypeScript types mirroring Go models

export interface ServerConfig {
  id?: string
  name: string
  url: string
  enabled?: boolean
  auth_type?: string
  auth_credentials?: any
  metadata?: {
    location?: string
    environment?: string
    color?: string
    [key: string]: any
  }
  hasCerts?: boolean
  health?: {
    status: 'healthy' | 'offline'
    responseTime?: number
    error?: string
    timestamp?: string
  }
  created_at?: string
  updated_at?: string
}

export interface Agent {
  id: number
  agent_id: string
  server_id?: string
  server_name?: string
  server_url?: string
  network_id?: string
  datacenter_id?: string
  certificate_cn: string
  hostname: string
  ip_address: string
  protocol?: string
  device_type?: string | null
  device_role?: string | null
  status: 'online' | 'offline' | 'pending'
  group: string
  last_seen: string
  first_seen?: string
  registered_at?: string
  approved_at?: string
  approved: boolean
  total_metrics: number
  total_alerts: number
  metadata?: Record<string, any>
  created_at: string
  updated_at: string
}

export interface Metric {
  id: number
  agent_id: string
  server_id?: string
  server_name?: string
  timestamp: string
  metric_type: string
  value: number
  unit: string
  metadata?: Record<string, any>
  created_at: string
}

export interface Alert {
  id: number | string
  agent_id: string
  server_id?: string
  server_name?: string
  timestamp: string
  severity: 'INFO' | 'WARNING' | 'CRITICAL'
  metric_type: string
  value: number
  threshold: number
  message: string
  retry_count: number
  resolved: boolean
  resolved_at?: string
  created_at: string
}

export interface DeduplicatedAlert {
  id: number | string
  agent_id: string
  server_id?: string
  server_name?: string
  timestamp: string
  severity: 'INFO' | 'WARNING' | 'CRITICAL'
  metric_type: string
  value: number
  threshold: number
  message: string
  created_at: string
  occurrence_count: number
  first_seen: string
}

export interface SNMPDevice {
  device_name: string
  device_ip: string
  agent_id: string
  server_id: string
  server_name: string
  network_id?: string
  datacenter_id?: string
  device_type?: string | null
  device_role?: string | null
  last_seen: string
}

// ── Energy metrics (BACnet/IP power meters, e.g. Verdigris EV2) ──────────────
// From the dedicated energy_metrics hypertable. One reading per
// (device, metric_name, tag); circuit/phase are first-class breakdowns derived
// from the tag (CktNN → circuit, PhA/PhB/PhC → phase, '' → panel scalar).

export interface EnergyDevice {
  device_id: string
  hostname: string
  mgmt_ip: string | null
  network_id: string
  status: 'online' | 'offline'
  last_reading: string | null
  scope: string | null
  circuit_count: number
}

export interface EnergyReading {
  device_id: string
  hostname: string
  mgmt_ip: string | null
  status: 'online' | 'offline'
  metric_name: string
  tag: string
  circuit: string
  phase: string
  value: number
  ts: string
  attributes: Record<string, unknown> | null
  // Meter coverage role from attributes.scope: 'facility' | 'it' | 'cooling' | …
  scope: string | null
}

export interface EnergyTimeseriesPoint {
  bucket: string
  avg_value: number
  max_value: number
  min_value: number
}

// Aggregated active power per meter scope per time bucket (PUE Power Trend).
export interface EnergyTrendRow {
  bucket: string
  scope: string
  total_kw: number
}

// One directed edge between two discovered SNMP devices, from the
// topology_links table populated by the walker's LLDP/CDP/ARP correlation
// and by discovery's deep-scan.
export interface TopologyLink {
  id?: number
  server_id: string
  server_name?: string
  source_ip: string
  source_name: string
  source_depth?: number
  source_port?: number
  target_ip: string
  target_name: string
  target_depth?: number
  target_port?: string
  last_seen: string
}

export interface SNMPMetric {
  id: number
  agent_id: string
  timestamp: string
  device_name: string
  device_host: string
  oid: string
  metric_name: string
  value: number
  value_type: 'gauge' | 'counter' | 'string'
  metadata?: Record<string, any>
  created_at: string
}

export interface AgentStatus {
  id: number
  agent_id: string
  status: 'online' | 'offline'
  timestamp: string
  reason?: string
  created_at: string
}

export interface License {
  id: number
  license_key: string
  company_name: string
  email: string
  max_agents: number
  max_snmp_devices: number
  features: string[]
  issued_at: string
  expires_at: string
  active: boolean
  created_at: string
  updated_at: string
}

export interface AggregatedMetric {
  agent_id: string
  metric_type: string
  time_bucket: string
  avg_value: number
  min_value: number
  max_value: number
  count: number
}

// SSE Event types
export interface SSEEvent {
  event: 'agent_update' | 'metric' | 'alert' | 'status_change'
  data: Agent | Metric | Alert | AgentStatus
}

// Prediction types for AI features
export interface Prediction {
  timestamp: string
  value: number
  lower_bound: number
  upper_bound: number
}

export interface PredictionResult {
  metric_type: string
  agent_id: string
  predictions: Prediction[]
  confidence: number
  model: string
}

// AI Insight types
export interface AIInsight {
  id: string
  title: string
  description: string
  severity: 'info' | 'warning' | 'critical'
  affected_agents: string[]
  metric_type?: string
  action?: string
  timestamp: string
  confidence: number
}

// Anomaly types
export interface Anomaly {
  id: string
  agent_id: string
  metric_type: string
  timestamp: string
  value: number
  expected_value: number
  deviation: number
  severity: 'INFO' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  confidence: number
}

// RCA types
export interface RootCauseAnalysis {
  id: string
  anomaly_id: string
  agent_id: string
  root_cause: string
  correlated_metrics: string[]
  recommendations: string[]
  confidence: number
  timestamp: string
}

// Chart data types
export interface ChartDataPoint {
  timestamp: string
  value: number
  [key: string]: any
}

// Time range options
export type TimeRange = '5m' | '1h' | '6h' | '24h' | '7d' | '30d' | 'custom'

// Metric type categories
export const MetricCategories = {
  CPU: ['cpu.usage', 'cpu.load_avg_1', 'cpu.load_avg_5', 'cpu.load_avg_15'],
  Memory: ['memory.usage', 'memory.available', 'memory.used', 'memory.free'],
  Disk: ['disk.usage', 'disk.read_bytes', 'disk.write_bytes', 'disk.io_time'],
  Network: ['network.bytes_sent', 'network.bytes_recv', 'network.packets_sent', 'network.packets_recv'],
  Temperature: ['temperature.cpu', 'temperature.gpu', 'temperature.motherboard'],
  Power: ['power.consumption', 'power.voltage', 'power.current'],
  Cooling: ['fan.speed', 'fan.rpm'],
} as const

// API Response types
export interface APIResponse<T> {
  data: T
  message?: string
  error?: string
}

export interface PaginatedResponse<T> {
  data: T[]
  total: number
  page: number
  per_page: number
  total_pages: number
}

// Filter types
export interface AgentFilter {
  status?: Agent['status']
  group?: string
  search?: string
  approved?: boolean
}

export interface MetricFilter {
  agent_id?: string
  metric_type?: string
  time_range?: TimeRange
  start_time?: string
  end_time?: string
  limit?: number
}

export interface AlertFilter {
  agent_id?: string
  severity?: Alert['severity']
  resolved?: boolean
  metric_type?: string
  time_range?: TimeRange
}

// Natural Language Query types
export interface NLQueryRequest {
  query: string
  context?: Record<string, any>
}

export interface NLQueryResponse {
  filters: Record<string, any>
  visualization: 'line_chart' | 'bar_chart' | 'table' | 'gauge' | 'heatmap'
  explanation: string
  data: any[]
}

// ── Critical tickets ─────────────────────────────────────────────────────────
export type TicketStatus = 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'closed'
export type TicketPriority = 'P1' | 'P2' | 'P3' | 'P4'
export type TicketCategory = 'power' | 'cooling' | 'network' | 'compliance' | 'security' | 'other'

export interface Ticket {
  id: string
  ticket_number: string
  org_id: string
  datacenter_id: string
  event_id: string | null
  device_id: string | null
  source_hostname: string | null
  severity: string
  priority: TicketPriority
  status: TicketStatus
  category: TicketCategory
  title: string
  description: string | null
  assigned_to: string | null
  root_cause: string | null
  resolution: string | null
  sla_due_at: string | null
  acknowledged_at: string | null
  resolved_at: string | null
  closed_at: string | null
  attributes: Record<string, any> | null
  created_at: string
  updated_at: string
}

export interface TicketActivity {
  id: string
  ticket_id: string
  activity_type: 'created' | 'comment' | 'status_change' | 'assignment' | 'escalation'
  actor: string | null
  from_status: string | null
  to_status: string | null
  message: string | null
  created_at: string
}
