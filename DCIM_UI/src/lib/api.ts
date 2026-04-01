import type {
  Agent,
  Metric,
  Alert,
  DeduplicatedAlert,
  SNMPMetric,
  SNMPDevice,
  License,
  AggregatedMetric,
  AgentFilter,
  MetricFilter,
  AlertFilter,
  ServerConfig,
} from './types'

// Use aggregator service URL if available, otherwise fall back to direct server URL
const API_BASE_URL = import.meta.env.VITE_AGGREGATOR_URL || import.meta.env.VITE_API_URL || '/api/v1'

class APIClient {
  private baseURL: string

  constructor(baseURL: string) {
    this.baseURL = baseURL
  }

  private async request<T>(
    endpoint: string,
    options?: RequestInit
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`

    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...options?.headers,
        },
      })

      if (!response.ok) {
        const error = await response.json().catch(() => ({ message: response.statusText }))
        throw new Error(error.message || `API request failed: ${response.status}`)
      }

      const json = await response.json()

      // Handle server response format: { success: boolean, data: T, message: string }
      if (json && typeof json === 'object' && 'data' in json) {
        return json.data as T
      }

      return json as T
    } catch (error) {
      console.error('API request error:', error)
      throw error
    }
  }

  // Agent endpoints
  async getAgents(filter?: AgentFilter): Promise<Agent[]> {
    const params = new URLSearchParams()
    if (filter?.status) params.append('status', filter.status)
    if (filter?.group) params.append('group', filter.group)
    if (filter?.search) params.append('search', filter.search)
    if (filter?.approved !== undefined) params.append('approved', String(filter.approved))

    const queryString = params.toString()
    return this.request<Agent[]>(`/agents${queryString ? `?${queryString}` : ''}`)
  }

  async getAgent(agentId: string): Promise<Agent> {
    return this.request<Agent>(`/agents/${agentId}`)
  }

  async approveAgent(agentId: string): Promise<Agent> {
    return this.request<Agent>(`/agents/${agentId}/approve`, {
      method: 'POST',
    })
  }

  async deleteAgent(agentId: string): Promise<void> {
    await this.request<void>(`/agents/${agentId}`, {
      method: 'DELETE',
    })
  }

  async updateAgentGroup(agentId: string, group: string): Promise<Agent> {
    return this.request<Agent>(`/agents/${agentId}/group`, {
      method: 'PUT',
      body: JSON.stringify({ group }),
    })
  }

  // Metric endpoints
  async getMetrics(filter: MetricFilter): Promise<Metric[]> {
    const params = new URLSearchParams()
    if (filter.agent_id) params.append('agent_id', filter.agent_id)
    if (filter.metric_type) params.append('metric_type', filter.metric_type)
    if (filter.start_time) params.append('start_time', filter.start_time)
    if (filter.end_time) params.append('end_time', filter.end_time)
    if (filter.time_range) params.append('time_range', filter.time_range)
    if (filter.limit) params.append('limit', filter.limit.toString())

    return this.request<Metric[]>(`/metrics?${params.toString()}`)
  }

  async getAggregatedMetrics(filter: MetricFilter & { interval?: string }): Promise<AggregatedMetric[]> {
    const params = new URLSearchParams()
    if (filter.agent_id) params.append('agent_id', filter.agent_id)
    if (filter.metric_type) params.append('metric_type', filter.metric_type)
    if (filter.start_time) params.append('start_time', filter.start_time)
    if (filter.end_time) params.append('end_time', filter.end_time)
    if (filter.interval) params.append('interval', filter.interval)

    return this.request<AggregatedMetric[]>(`/metrics/aggregated?${params.toString()}`)
  }

  async getLatestMetrics(agentId: string): Promise<Record<string, Metric>> {
    return this.request<Record<string, Metric>>(`/agents/${agentId}/metrics/latest`)
  }

  // Alert endpoints
  async getAlerts(filter?: AlertFilter & { limit?: number; offset?: number }): Promise<Alert[]> {
    const params = new URLSearchParams()
    if (filter?.agent_id) params.append('agent_id', filter.agent_id)
    if (filter?.severity) params.append('severity', filter.severity)
    if (filter?.resolved !== undefined) params.append('resolved', String(filter.resolved))
    if (filter?.metric_type) params.append('metric_type', filter.metric_type)
    if (filter?.time_range) params.append('time_range', filter.time_range)
    if (filter?.limit !== undefined) params.append('limit', String(filter.limit))
    if (filter?.offset !== undefined) params.append('offset', String(filter.offset))

    const queryString = params.toString()
    return this.request<Alert[]>(`/alerts${queryString ? `?${queryString}` : ''}`)
  }

  async getAlertsPaginated(filter?: AlertFilter & { limit?: number; offset?: number }): Promise<{
    data: Alert[]
    total: number
    hasMore: boolean
  }> {
    const params = new URLSearchParams()
    if (filter?.agent_id) params.append('agent_id', filter.agent_id)
    if (filter?.severity) params.append('severity', filter.severity)
    if (filter?.resolved !== undefined) params.append('resolved', String(filter.resolved))
    if (filter?.limit !== undefined) params.append('limit', String(filter.limit))
    if (filter?.offset !== undefined) params.append('offset', String(filter.offset))

    const queryString = params.toString()
    const res = await fetch(`${this.baseURL}/alerts${queryString ? `?${queryString}` : ''}`)
    const json = await res.json()
    return { data: json.data || [], total: json.total || 0, hasMore: json.hasMore || false }
  }

  async getAlertCounts(): Promise<{
    agent_id: string
    hostname: string
    server_name: string
    total: number
    active: number
    critical: number
    warning: number
    info: number
  }[]> {
    return this.request(`/alerts/counts`)
  }

  async getLatestAlerts(filter?: { agent_id?: string; severity?: string; limit?: number; offset?: number }): Promise<{
    data: DeduplicatedAlert[]
    total: number
    hasMore: boolean
  }> {
    const params = new URLSearchParams()
    if (filter?.agent_id) params.append('agent_id', filter.agent_id)
    if (filter?.severity) params.append('severity', filter.severity)
    if (filter?.limit !== undefined) params.append('limit', String(filter.limit))
    if (filter?.offset !== undefined) params.append('offset', String(filter.offset))

    const queryString = params.toString()
    const res = await fetch(`${this.baseURL}/alerts/latest${queryString ? `?${queryString}` : ''}`)
    const json = await res.json()
    return { data: json.data || [], total: json.total || 0, hasMore: json.hasMore || false }
  }

  async getAlert(alertId: number): Promise<Alert> {
    return this.request<Alert>(`/alerts/${alertId}`)
  }

  async resolveAlert(alertId: number): Promise<Alert> {
    return this.request<Alert>(`/alerts/${alertId}/resolve`, {
      method: 'POST',
    })
  }

  async bulkResolveAlerts(alertIds: number[]): Promise<void> {
    await this.request<void>('/alerts/bulk/resolve', {
      method: 'POST',
      body: JSON.stringify({ alert_ids: alertIds }),
    })
  }

  // SNMP endpoints
  async getSNMPMetrics(agentId?: string): Promise<SNMPMetric[]> {
    const queryString = agentId ? `?agent_id=${agentId}` : ''
    return this.request<SNMPMetric[]>(`/snmp/metrics${queryString}`)
  }

  async getSNMPDevices(agentId?: string): Promise<SNMPDevice[]> {
    const queryString = agentId ? `?agent_id=${agentId}` : ''
    return this.request<SNMPDevice[]>(`/snmp/devices${queryString}`)
  }

  // License endpoints
  async getLicense(): Promise<License> {
    return this.request<License>('/license')
  }

  // Statistics endpoints
  async getStatistics(): Promise<any> {
    return this.request<any>('/statistics')
  }

  // Dashboard endpoints
  async getDashboardData(): Promise<{
    total_agents: number
    online_agents: number
    total_alerts: number
    critical_alerts: number
    recent_metrics: Metric[]
    recent_alerts: Alert[]
  }> {
    return this.request('/dashboard')
  }

  // Anomaly endpoints
  async getAnomalies(agentId?: string): Promise<any[]> {
    const queryString = agentId ? `?agent_id=${agentId}` : ''
    return this.request<any[]>(`/anomalies${queryString}`)
  }

  // RCA endpoints
  async getRootCauseAnalysis(anomalyId: string): Promise<any> {
    return this.request<any>(`/rca/${anomalyId}`)
  }

  // Search endpoint for natural language queries
  async searchMetrics(query: string): Promise<any> {
    return this.request<any>('/search', {
      method: 'POST',
      body: JSON.stringify({ query }),
    })
  }

  // Server Management endpoints (for aggregator)
  async getServers(): Promise<ServerConfig[]> {
    return this.request<ServerConfig[]>('/servers')
  }

  async getServer(id: string): Promise<ServerConfig> {
    return this.request<ServerConfig>(`/servers/${id}`)
  }

  async addServer(
    server: Omit<ServerConfig, 'id' | 'created_at' | 'updated_at'>,
    certFiles?: { caCert?: File; clientCert?: File; clientKey?: File }
  ): Promise<ServerConfig> {
    if (certFiles && (certFiles.caCert || certFiles.clientCert || certFiles.clientKey)) {
      return this.sendWithCerts('/servers', 'POST', server, certFiles)
    }
    return this.request<ServerConfig>('/servers', {
      method: 'POST',
      body: JSON.stringify(server),
    })
  }

  async updateServer(
    id: string,
    updates: Partial<ServerConfig>,
    certFiles?: { caCert?: File; clientCert?: File; clientKey?: File }
  ): Promise<ServerConfig> {
    if (certFiles && (certFiles.caCert || certFiles.clientCert || certFiles.clientKey)) {
      return this.sendWithCerts(`/servers/${id}`, 'PUT', updates, certFiles)
    }
    return this.request<ServerConfig>(`/servers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(updates),
    })
  }

  private async sendWithCerts<T>(
    endpoint: string,
    method: string,
    data: any,
    certFiles: { caCert?: File; clientCert?: File; clientKey?: File }
  ): Promise<T> {
    const formData = new FormData()
    formData.append('data', JSON.stringify(data))
    if (certFiles.caCert) formData.append('caCert', certFiles.caCert)
    if (certFiles.clientCert) formData.append('clientCert', certFiles.clientCert)
    if (certFiles.clientKey) formData.append('clientKey', certFiles.clientKey)

    const url = `${this.baseURL}${endpoint}`
    const response = await fetch(url, { method, body: formData })

    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: response.statusText }))
      throw new Error(error.message || `API request failed: ${response.status}`)
    }

    const json = await response.json()
    if (json && typeof json === 'object' && 'data' in json) {
      return json.data as T
    }
    return json as T
  }

  async deleteServer(id: string): Promise<void> {
    await this.request<void>(`/servers/${id}`, {
      method: 'DELETE',
    })
  }

  async testServerConnection(id: string): Promise<{ status: string; responseTime?: number; error?: string }> {
    return this.request(`/servers/${id}/health`)
  }

  async toggleServerStatus(id: string, enabled: boolean): Promise<void> {
    await this.request<void>(`/servers/${id}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    })
  }

  // Dashboard stats (aggregated)
  async getDashboardStats(): Promise<{
    servers: number
    agents: { total: number; online: number; offline: number }
    activeAlerts: number
  }> {
    return this.request('/dashboard/stats')
  }

  // Aggregated server health summary
  async getServerHealthSummary(limit = 5): Promise<{
    total: number
    healthy: number
    offline: number
    tls_error: number
    unknown: number
    needs_attention: Array<{
      id: string
      name: string
      url: string
      color: string
      status: string
      error: string | null
      responseTime: number | null
    }>
  }> {
    return this.request(`/servers/health/summary?limit=${limit}`)
  }

  // Aggregated agent stats grouped by server
  async getAgentsByServerSummary(limit = 5): Promise<{
    totals: { total: number; online: number; offline: number; servers: number }
    servers: Array<{
      server_id: string
      server_name: string
      color: string | null
      total: number
      online: number
      offline: number
    }>
  }> {
    return this.request(`/agents/stats/by-server?limit=${limit}`)
  }

  // Recently active agents (lightweight)
  async getRecentAgents(limit = 6): Promise<Array<{
    agent_id: string
    hostname: string
    ip_address: string
    status: string
    last_seen: string
    group: string
    server_name: string
  }>> {
    return this.request(`/agents/recent?limit=${limit}`)
  }
}

export const api = new APIClient(API_BASE_URL)
