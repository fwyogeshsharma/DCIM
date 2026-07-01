import type {
  Agent,
  Metric,
  Alert,
  DeduplicatedAlert,
  SNMPMetric,
  SNMPDevice,
  FacilityDeviceRow,
  TopologyLink,
  License,
  AggregatedMetric,
  AgentFilter,
  MetricFilter,
  AlertFilter,
  ServerConfig,
  Ticket,
  TicketActivity,
  EnergyDevice,
  EnergyReading,
  EnergyTimeseriesPoint,
  EnergyTrendRow,
} from './types'

// Use aggregator service URL if available, otherwise fall back to direct server URL
const API_BASE_URL = import.meta.env.VITE_AGGREGATOR_URL || import.meta.env.VITE_API_URL || '/api/v1'

class APIClient {
  private baseURL: string
  private authToken: string | null = null

  constructor(baseURL: string) {
    this.baseURL = baseURL
  }

  // The bearer token is set by the auth store on login / rehydrate and sent on
  // every request so the aggregator can enforce RBAC.
  setAuthToken(token: string | null) {
    this.authToken = token
  }

  private async request<T>(
    endpoint: string,
    options?: RequestInit
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`

    // POST/PUT/PATCH always carry a Content-Type: application/json header (set
    // below), and the aggregator's express.json() rejects such a request with a
    // 400 Bad Request when the body is empty. So body-less mutations (e.g.
    // /inventory/sync, /agents/:id/approve) must still send an empty "{}".
    const method = (options?.method ?? 'GET').toUpperCase()
    const mutating = method === 'POST' || method === 'PUT' || method === 'PATCH'
    const body = options?.body ?? (mutating ? '{}' : undefined)

    try {
      const response = await fetch(url, {
        ...options,
        body,
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          ...(this.authToken ? { Authorization: `Bearer ${this.authToken}` } : {}),
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

  // Auth endpoints — credentials are stored/verified against the database
  // (users table) so an account works across browsers, devices and deployments.
  async register(
    username: string,
    email: string,
    password: string,
    requestedRole?: string,
  ): Promise<AuthSession> {
    return this.request<AuthSession>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, email, password, requested_role: requestedRole }),
    })
  }

  async login(username: string, password: string): Promise<AuthSession> {
    return this.request<AuthSession>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  }

  async me(): Promise<AuthUser> {
    return this.request<AuthUser>('/auth/me')
  }

  async logout(): Promise<void> {
    await this.request('/auth/logout', { method: 'POST' })
  }

  // Roles a new user may request at sign-up (excludes root/admin).
  async getRequestableRoles(): Promise<RoleOption[]> {
    return this.request<RoleOption[]>('/auth/roles')
  }

  // Approver-only: users awaiting approval.
  async getPendingUsers(): Promise<PendingUser[]> {
    return this.request<PendingUser[]>('/auth/pending')
  }

  // Approver-only: full role catalog for assigning roles.
  async getRoleCatalog(): Promise<RoleCatalogEntry[]> {
    return this.request<RoleCatalogEntry[]>('/auth/role-catalog')
  }

  // Approver-only: approve/reject a user (optionally overriding their roles).
  async reviewUser(
    userId: number,
    action: 'approve' | 'reject',
    roles?: string[],
  ): Promise<AuthUser> {
    return this.request<AuthUser>(`/auth/users/${userId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ action, roles }),
    })
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

  async createAgent(data: {
    server_id: string
    hostname: string
    ip_address?: string
    agent_group?: string
    certificate_cn?: string
    protocol?: string
    metadata?: Record<string, any>
  }): Promise<any> {
    return this.request<any>('/agents', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async updateAgent(agentId: string, data: {
    hostname?: string
    ip_address?: string
    agent_group?: string
    certificate_cn?: string
    protocol?: string
    metadata?: Record<string, any>
  }): Promise<any> {
    return this.request<any>(`/agents/${agentId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
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

  // Energy metrics (power meters) — dedicated energy_metrics hypertable.
  async getEnergyDevices(): Promise<EnergyDevice[]> {
    return this.request<EnergyDevice[]>(`/energy/devices`)
  }

  // Latest reading per (device, metric_name, tag). Omit deviceId for all meters.
  async getEnergySnapshot(deviceId?: string): Promise<EnergyReading[]> {
    const qs = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : ''
    return this.request<EnergyReading[]>(`/energy/snapshot${qs}`)
  }

  async getEnergyTimeseries(filter: {
    device_id: string
    metric_name: string
    tag?: string
    time_range?: string
    interval?: string
  }): Promise<EnergyTimeseriesPoint[]> {
    const params = new URLSearchParams()
    params.append('device_id', filter.device_id)
    params.append('metric_name', filter.metric_name)
    if (filter.tag) params.append('tag', filter.tag)
    if (filter.time_range) params.append('time_range', filter.time_range)
    if (filter.interval) params.append('interval', filter.interval)
    return this.request<EnergyTimeseriesPoint[]>(`/energy/timeseries?${params.toString()}`)
  }

  // Active power summed per meter scope per time bucket (PUE Power Trend).
  async getEnergyTrend(filter?: { time_range?: string; interval?: string }): Promise<EnergyTrendRow[]> {
    const params = new URLSearchParams()
    if (filter?.time_range) params.append('time_range', filter.time_range)
    if (filter?.interval) params.append('interval', filter.interval)
    const qs = params.toString()
    return this.request<EnergyTrendRow[]>(`/energy/trend${qs ? `?${qs}` : ''}`)
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
    const res = await fetch(`${this.baseURL}/alerts${queryString ? `?${queryString}` : ''}`, { cache: 'no-store' })
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
    last_ts?: string | null
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
    const res = await fetch(`${this.baseURL}/alerts/latest${queryString ? `?${queryString}` : ''}`, { cache: 'no-store' })
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

  // Physical facility layout — one row per device with its building (network_id)
  // and rack placement. Drives the Fire & Safety 3D Facility view.
  async getFacilityLayout(networkId?: string): Promise<FacilityDeviceRow[]> {
    const qs = networkId ? `?network_id=${encodeURIComponent(networkId)}` : ''
    return this.request<FacilityDeviceRow[]>(`/facility/layout${qs}`)
  }

  // SNMP trap endpoints
  async getTraps(filter?: { source_ip?: string; server_id?: string; severity?: string; limit?: number }): Promise<any[]> {
    const params = new URLSearchParams()
    if (filter?.source_ip) params.append('source_ip', filter.source_ip)
    if (filter?.server_id) params.append('server_id', filter.server_id)
    if (filter?.severity) params.append('severity', filter.severity)
    if (filter?.limit) params.append('limit', String(filter.limit))
    return this.request<any[]>(`/traps?${params.toString()}`)
  }

  async getTrapTimeline(filter?: { source_ip?: string; server_id?: string; hours?: number }): Promise<{
    bucket: string; total: number; critical: number; warning: number; info: number
  }[]> {
    const params = new URLSearchParams()
    if (filter?.source_ip) params.append('source_ip', filter.source_ip)
    if (filter?.server_id) params.append('server_id', filter.server_id)
    if (filter?.hours) params.append('hours', String(filter.hours))
    return this.request(`/traps/timeline?${params.toString()}`)
  }

  // Alert counts keyed by device mgmt_ip — used by topology to badge ingest-API nodes.
  async getDeviceAlertSummary(): Promise<Array<{
    device_ip: string
    hostname: string
    total: number
    active: number
    critical: number
    warning: number
    info: number
  }>> {
    return this.request('/alerts/by-device-ip')
  }

  // Topology links — device↔device edges discovered via LLDP/CDP/ARP by the
  // SNMP walker and CIDR-sweep deep-scan. Used by the Topology page to render
  // the actual network wiring between discovered devices.
  async getTopologyLinks(serverId?: string): Promise<TopologyLink[]> {
    const queryString = serverId ? `?server_id=${serverId}` : ''
    return this.request<TopologyLink[]>(`/topology/links${queryString}`)
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

  // Topology tree — device hierarchy + all physical links
  async getTopologyTree(networkId?: string): Promise<{
    nodes: Array<{
      device_id: string
      hostname: string
      device_type: string
      device_role: string | null
      role_confidence: number | null
      mgmt_ip: string | null
      network_id: string
      group_id: string
      datacenter_id: string
      parent_device_id: string | null
      parent_hostname: string | null
      depth: number
      is_root: boolean
      status: 'online' | 'offline'
      active_alerts: number
      critical_alerts: number
      at_risk: boolean
      risk_reason: string | null
    }>
    links: Array<{
      id: string
      src_device_id: string
      dst_device_id: string
      is_active: boolean
      relation: string
      link_speed_mbps: number | null
      src_hostname: string
      dst_hostname: string
      src_status: 'online' | 'offline'
      dst_status: 'online' | 'offline'
    }>
  }> {
    const qs = networkId ? `?network_id=${encodeURIComponent(networkId)}` : ''
    return this.request(`/topology/tree${qs}`)
  }

  // Networks summary — per-network device counts, online/offline, alerts
  async getNetworksSummary(): Promise<Array<{
    network_id: string
    total_devices: number
    online: number
    offline: number
    device_types: number
    last_seen: string | null
    datacenter: string | null
    datacenter_city: string | null
    country: string | null
    active_alerts: number
    critical_alerts: number
  }>> {
    return this.request('/networks/summary')
  }

  // ── Critical tickets ───────────────────────────────────────────────────────
  async getTickets(filter?: {
    status?: string; priority?: string; assigned_to?: string; category?: string
    q?: string; limit?: number; offset?: number
  }): Promise<{ data: Ticket[]; total: number; hasMore: boolean }> {
    const params = new URLSearchParams()
    if (filter?.status)      params.append('status', filter.status)
    if (filter?.priority)    params.append('priority', filter.priority)
    if (filter?.assigned_to) params.append('assigned_to', filter.assigned_to)
    if (filter?.category)    params.append('category', filter.category)
    if (filter?.q)           params.append('q', filter.q)
    if (filter?.limit !== undefined)  params.append('limit', String(filter.limit))
    if (filter?.offset !== undefined) params.append('offset', String(filter.offset))
    const qs = params.toString()
    const res = await fetch(`${this.baseURL}/tickets${qs ? `?${qs}` : ''}`, { cache: 'no-store' })
    const json = await res.json()
    return { data: json.data || [], total: json.total || 0, hasMore: json.hasMore || false }
  }

  async getTicketStats(): Promise<{
    total: number; open: number; unacked: number; in_progress: number
    resolved: number; closed: number; unassigned: number; p1_open: number
  }> {
    return this.request('/tickets/stats')
  }

  async getTicketAssignees(): Promise<Array<{ name: string; open_tickets: number }>> {
    return this.request('/tickets/assignees')
  }

  async getTicket(id: string): Promise<Ticket & { activity: TicketActivity[] }> {
    return this.request(`/tickets/${id}`)
  }

  // Generate a ticket from a critical alert/event. Returns the existing open
  // ticket (deduped) if one already exists for that event.
  async createTicketFromAlert(data: {
    event_id: string; priority?: string; category?: string
    assigned_to?: string; actor?: string; title?: string; description?: string
  }): Promise<Ticket> {
    return this.request('/tickets/from-alert', { method: 'POST', body: JSON.stringify(data) })
  }

  async createTicket(data: Partial<Ticket> & { title: string; actor?: string }): Promise<Ticket> {
    return this.request('/tickets', { method: 'POST', body: JSON.stringify(data) })
  }

  async updateTicket(id: string, data: Partial<Ticket> & { actor?: string }): Promise<Ticket> {
    return this.request(`/tickets/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
  }

  async assignTicket(id: string, assigned_to: string | null, actor?: string): Promise<Ticket> {
    return this.request(`/tickets/${id}/assign`, {
      method: 'POST', body: JSON.stringify({ assigned_to, actor }),
    })
  }

  async addTicketComment(id: string, message: string, actor?: string): Promise<TicketActivity[]> {
    return this.request(`/tickets/${id}/comment`, {
      method: 'POST', body: JSON.stringify({ message, actor }),
    })
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

  // ── Inventory ──────────────────────────────────────────────────────────────

  async getInventorySummary(): Promise<{
    devices: {
      total: number; idle: number; in_use: number; maintenance: number; decommissioned: number
      total_cpu_cores: number; total_ram_gb: number; total_storage_gb: number
      available_cpu_cores: number; available_ram_gb: number; available_storage_gb: number
      storage_remaining_gb: number
    }
    links: { total_links: number; active_links: number; disconnected_links: number }
  }> {
    return this.request('/inventory/summary')
  }

  async getInventoryDevices(filter?: {
    status?: string; device_type?: string; search?: string; datacenter_id?: string
  }): Promise<InventoryDevice[]> {
    const params = new URLSearchParams()
    if (filter?.status)        params.append('status', filter.status)
    if (filter?.device_type)   params.append('device_type', filter.device_type)
    if (filter?.search)        params.append('search', filter.search)
    if (filter?.datacenter_id) params.append('datacenter_id', filter.datacenter_id)
    const qs = params.toString()
    return this.request(`/inventory/devices${qs ? `?${qs}` : ''}`)
  }

  async createInventoryDevice(data: Partial<InventoryDevice>): Promise<InventoryDevice> {
    return this.request('/inventory/devices', { method: 'POST', body: JSON.stringify(data) })
  }

  async updateInventoryDevice(id: string, data: Partial<InventoryDevice>): Promise<InventoryDevice> {
    return this.request(`/inventory/devices/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  }

  async deleteInventoryDevice(id: string): Promise<void> {
    return this.request(`/inventory/devices/${id}`, { method: 'DELETE' })
  }

  async getInventoryDeviceAnalytics(id: string, hours = 24): Promise<{
    linked: boolean
    monitoring_device_id?: string
    metrics: Array<{ bucket: string; metric_name: string; avg_value: number; max_value: number; min_value: number }>
    latest: Array<{ metric_name: string; value: number; timestamp: string; tag: string }>
  }> {
    return this.request(`/inventory/devices/${id}/analytics?hours=${hours}`)
  }

  async getInventoryLinks(filter?: {
    device_id?: string; status?: string; datacenter_id?: string
  }): Promise<InventoryLink[]> {
    const params = new URLSearchParams()
    if (filter?.device_id)     params.append('device_id', filter.device_id)
    if (filter?.status)        params.append('status', filter.status)
    if (filter?.datacenter_id) params.append('datacenter_id', filter.datacenter_id)
    const qs = params.toString()
    return this.request(`/inventory/links${qs ? `?${qs}` : ''}`)
  }

  async createInventoryLink(data: Partial<InventoryLink>): Promise<InventoryLink> {
    return this.request('/inventory/links', { method: 'POST', body: JSON.stringify(data) })
  }

  async updateInventoryLink(id: string, data: Partial<InventoryLink>): Promise<InventoryLink> {
    return this.request(`/inventory/links/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  }

  async deleteInventoryLink(id: string): Promise<void> {
    return this.request(`/inventory/links/${id}`, { method: 'DELETE' })
  }

  async syncInventory(): Promise<{ devices_synced: number; links_synced: number }> {
    return this.request('/inventory/sync', { method: 'POST' })
  }

  // ── Device configuration (editable SNMP SET + Redfish surface) ───────────────
  async getDeviceConfig(agentId: string): Promise<DeviceConfig> {
    return this.request<DeviceConfig>(`/agents/${agentId}/config`)
  }

  async updateDeviceConfig(agentId: string, data: DeviceConfigUpdate): Promise<{
    agent_id: string; changed: string[]
  }> {
    return this.request(`/agents/${agentId}/config`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  }
}

export const api = new APIClient(API_BASE_URL)

// ── Device configuration types ───────────────────────────────────────────────

export interface DeviceConfigIdentity {
  sysContact: string
  sysName: string
  sysLocation: string
}

export interface DeviceConfigAsset {
  country: string
  datacenter_city: string
  datacenter: string
  floor: string
  room: string
  rack_row: number | null
  rack_num: number | null
  rack_unit: number | null
  model: string
  power_draw_w: number | null
}

export interface DeviceConfigRedfish {
  power_action: string
  indicator_led: string
}

export interface DeviceConfig {
  agent_id: string
  device_type: string
  redfish_capable: boolean
  identity: DeviceConfigIdentity
  asset: DeviceConfigAsset
  thresholds: Record<string, number>
  redfish: DeviceConfigRedfish | null
  pending_since: string | null
}

export interface DeviceConfigUpdate {
  identity?: Partial<DeviceConfigIdentity>
  asset?: Partial<DeviceConfigAsset>
  thresholds?: Record<string, number>
  redfish?: Partial<DeviceConfigRedfish>
}

// ── Auth types ─────────────────────────────────────────────────────────────

export type AccessLevel = 'none' | 'read' | 'write' | 'manage'

// Feature keys mirror the `features:` block in DCIM_Aggregator/src/config/rbac.yml.
export type PermissionMap = Record<string, AccessLevel>

export interface AuthUser {
  id: number
  username: string
  email: string
  status: 'pending' | 'approved' | 'rejected'
  requested_role?: string | null
  roles: string[]
  permissions: PermissionMap
}

export interface AuthSession {
  token: string
  user: AuthUser
}

export interface RoleOption {
  key: string
  label: string
  tier: number
}

export interface RoleCatalogEntry extends RoleOption {
  approves: boolean
  assignable_by: string[]
}

export interface PendingUser {
  id: number
  username: string
  email: string
  status: string
  requested_role?: string | null
  created_at: string
  roles: string[]
}

// ── Inventory types ────────────────────────────────────────────────────────

export interface InventoryDevice {
  id: string
  org_id: string
  datacenter_id: string
  monitoring_device_id?: string
  hostname: string
  device_type: string
  vendor?: string
  model?: string
  serial_number?: string
  datacenter_name?: string
  room?: string
  rack?: string
  rack_unit?: number
  rack_unit_height?: number
  status: 'idle' | 'in_use' | 'maintenance' | 'decommissioned'
  specs: Record<string, string>
  cpu_cores?: number
  ram_gb?: number
  storage_gb?: number
  power_capacity_w?: number
  port_count?: number
  cpu_used_pct?: number
  ram_used_pct?: number
  storage_used_gb?: number
  storage_available_gb?: number
  storage_used_pct?: number
  power_draw_w?: number
  notes?: string
  monitoring_status?: 'online' | 'offline' | null
  at_risk?: boolean
  risk_reason?: string | null
  created_at: string
  updated_at: string
}

export interface InventoryLink {
  id: string
  org_id: string
  datacenter_id: string
  src_device_id: string
  dst_device_id: string
  src_port?: string
  dst_port?: string
  link_type: string
  speed_mbps?: number
  status: 'active' | 'disconnected'
  notes?: string
  src_hostname?: string
  src_device_type?: string
  dst_hostname?: string
  dst_device_type?: string
  created_at: string
  updated_at: string
}
