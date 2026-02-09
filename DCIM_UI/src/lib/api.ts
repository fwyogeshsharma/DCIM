import type {
  Agent,
  Metric,
  Alert,
  SNMPMetric,
  License,
  AggregatedMetric,
  AgentFilter,
  MetricFilter,
  AlertFilter,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1'

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

      return response.json()
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
  async getAlerts(filter?: AlertFilter): Promise<Alert[]> {
    const params = new URLSearchParams()
    if (filter?.agent_id) params.append('agent_id', filter.agent_id)
    if (filter?.severity) params.append('severity', filter.severity)
    if (filter?.resolved !== undefined) params.append('resolved', String(filter.resolved))
    if (filter?.metric_type) params.append('metric_type', filter.metric_type)
    if (filter?.time_range) params.append('time_range', filter.time_range)

    const queryString = params.toString()
    return this.request<Alert[]>(`/alerts${queryString ? `?${queryString}` : ''}`)
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

  async getSNMPDevices(agentId?: string): Promise<any[]> {
    const queryString = agentId ? `?agent_id=${agentId}` : ''
    return this.request<any[]>(`/snmp/devices${queryString}`)
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
}

export const api = new APIClient(API_BASE_URL)
