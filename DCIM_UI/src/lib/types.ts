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
  certificate_cn: string
  hostname: string
  ip_address: string
  status: 'online' | 'offline' | 'pending'
  group: string
  last_seen: string
  first_seen: string
  registered_at: string
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
  timestamp: string
  metric_type: string
  value: number
  unit: string
  metadata?: Record<string, any>
  created_at: string
}

export interface Alert {
  id: number
  agent_id: string
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
