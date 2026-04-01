import { useAlerts } from '@/hooks/useAlerts'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Server, Activity, AlertTriangle, ServerCog, CheckCircle, Clock } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { Link } from 'react-router-dom'

export default function Dashboard() {
  const { data: alerts } = useAlerts()

  const { data: dashboardStats } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: () => api.getDashboardStats(),
    staleTime: 5000,
    refetchInterval: 5000,
  })

  const { data: alertCounts } = useQuery({
    queryKey: ['alert-counts'],
    queryFn: () => api.getAlertCounts(),
    staleTime: 5000,
    refetchInterval: 5000,
  })

  const { data: serverHealth, isLoading: serverHealthLoading } = useQuery({
    queryKey: ['server-health-summary'],
    queryFn: () => api.getServerHealthSummary(5),
    refetchInterval: 30000,
  })

  const { data: agentsByServerSummary, isLoading: agentsSummaryLoading } = useQuery({
    queryKey: ['agents-by-server-summary'],
    queryFn: () => api.getAgentsByServerSummary(5),
    refetchInterval: 30000,
  })

  const { data: recentAgents } = useQuery({
    queryKey: ['recent-agents'],
    queryFn: () => api.getRecentAgents(6),
    refetchInterval: 30000,
  })

  // Derive stat card values from aggregated endpoints
  const totalServers = serverHealth?.total ?? dashboardStats?.servers ?? 0
  const healthyServers = serverHealth?.healthy ?? 0
  const totalAgents = agentsByServerSummary?.totals.total ?? dashboardStats?.agents.total ?? 0
  const onlineAgents = agentsByServerSummary?.totals.online ?? dashboardStats?.agents.online ?? 0
  const criticalAlerts = alertCounts?.reduce((sum, c) => sum + Number(c.critical), 0) || 0
  const totalAlerts = dashboardStats?.activeAlerts || 0

  const stats = [
    {
      name: 'Servers',
      value: `${healthyServers}/${totalServers}`,
      subtitle: 'healthy',
      icon: ServerCog,
      color: 'text-cyan-500',
      bgColor: 'bg-cyan-500/10',
      link: '/app/servers',
    },
    {
      name: 'Total Agents',
      value: totalAgents.toLocaleString(),
      subtitle: `${onlineAgents.toLocaleString()} online`,
      icon: Server,
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10',
      link: '/app/agents',
    },
    {
      name: 'Online Agents',
      value: onlineAgents.toLocaleString(),
      subtitle: `${(totalAgents - onlineAgents).toLocaleString()} offline`,
      icon: Activity,
      color: 'text-green-500',
      bgColor: 'bg-green-500/10',
      link: '/app/agents',
    },
    {
      name: 'Active Alerts',
      value: totalAlerts,
      subtitle: `${criticalAlerts} critical`,
      icon: AlertTriangle,
      color: criticalAlerts > 0 ? 'text-red-500' : 'text-yellow-500',
      bgColor: criticalAlerts > 0 ? 'bg-red-500/10' : 'bg-yellow-500/10',
      link: '/app/alerts',
    },
  ]

  if (serverHealthLoading || agentsSummaryLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading dashboard...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white">Dashboard</h1>
        <p className="text-slate-400 mt-2 text-lg">
          Real-time overview of your data center infrastructure
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {stats.map((stat) => {
          const Icon = stat.icon
          return (
            <Link
              key={stat.name}
              to={stat.link}
              className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300 group cursor-pointer"
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">
                    {stat.name}
                  </p>
                  <p className="text-3xl font-bold mt-2 text-white">{stat.value}</p>
                  <p className="text-xs text-slate-500 mt-1">{stat.subtitle}</p>
                </div>
                <div className={`${stat.bgColor} p-3 rounded-lg group-hover:scale-110 transition-transform duration-300`}>
                  <Icon className={`h-6 w-6 ${stat.color}`} />
                </div>
              </div>
            </Link>
          )
        })}
      </div>

      {/* Server Health + Agent Distribution */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Server Health Summary */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Server Health</h3>
            <Link to="/app/servers" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          {serverHealth && serverHealth.total > 0 ? (
            <div className="space-y-4">
              {/* Stacked bar */}
              <div className="h-3 bg-slate-700 rounded-full overflow-hidden flex">
                {serverHealth.healthy > 0 && (
                  <div
                    className="h-full bg-green-500 transition-all duration-500"
                    style={{ width: `${(serverHealth.healthy / serverHealth.total) * 100}%` }}
                  />
                )}
                {serverHealth.offline > 0 && (
                  <div
                    className="h-full bg-red-500 transition-all duration-500"
                    style={{ width: `${(serverHealth.offline / serverHealth.total) * 100}%` }}
                  />
                )}
                {serverHealth.tls_error > 0 && (
                  <div
                    className="h-full bg-orange-500 transition-all duration-500"
                    style={{ width: `${(serverHealth.tls_error / serverHealth.total) * 100}%` }}
                  />
                )}
                {serverHealth.unknown > 0 && (
                  <div
                    className="h-full bg-slate-500 transition-all duration-500"
                    style={{ width: `${(serverHealth.unknown / serverHealth.total) * 100}%` }}
                  />
                )}
              </div>

              {/* Legend */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
                  <span className="text-slate-300">{serverHealth.healthy.toLocaleString()} Healthy</span>
                </span>
                {serverHealth.offline > 0 && (
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
                    <span className="text-red-400">{serverHealth.offline} Offline</span>
                  </span>
                )}
                {serverHealth.tls_error > 0 && (
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-orange-500" />
                    <span className="text-orange-400">{serverHealth.tls_error} TLS Error</span>
                  </span>
                )}
                {serverHealth.unknown > 0 && (
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-slate-500" />
                    <span className="text-slate-400">{serverHealth.unknown} Unknown</span>
                  </span>
                )}
              </div>

              {/* Needs attention list or all-healthy message */}
              {serverHealth.needs_attention.length > 0 ? (
                <div className="space-y-2">
                  <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Needs Attention</p>
                  {serverHealth.needs_attention.map((server) => (
                    <div key={server.id} className="flex items-center justify-between p-3 rounded-lg bg-slate-900/50 border border-white/5">
                      <div className="flex items-center gap-3">
                        <div
                          className="w-2 h-8 rounded-full"
                          style={{ backgroundColor: server.color }}
                        />
                        <div>
                          <p className="text-sm font-medium text-white">{server.name}</p>
                          <p className="text-xs text-slate-500 font-mono truncate max-w-[200px]">{server.url}</p>
                        </div>
                      </div>
                      <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                        server.status === 'offline'
                          ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                          : server.status === 'tls_error'
                          ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30'
                          : 'bg-slate-500/20 text-slate-400 border border-slate-500/30'
                      }`}>
                        {server.status === 'tls_error' ? 'TLS Error' : server.status === 'offline' ? 'Offline' : 'Unknown'}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex items-center gap-2 p-3 rounded-lg bg-green-500/10 border border-green-500/20">
                  <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
                  <span className="text-sm text-green-400">
                    All {serverHealth.total.toLocaleString()} servers healthy
                  </span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8">
              <ServerCog className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No servers configured</p>
              <Link to="/app/servers" className="text-sm text-blue-400 hover:text-blue-300 mt-2 inline-block">
                Add a server
              </Link>
            </div>
          )}
        </div>

        {/* Agents by Server Summary */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Agents by Server</h3>
            <Link to="/app/agents" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          {agentsByServerSummary && agentsByServerSummary.totals.total > 0 ? (
            <div className="space-y-4">
              {/* Overall stacked bar */}
              <div className="h-3 bg-slate-700 rounded-full overflow-hidden flex">
                {agentsByServerSummary.totals.online > 0 && (
                  <div
                    className="h-full bg-green-500 transition-all duration-500"
                    style={{ width: `${(agentsByServerSummary.totals.online / agentsByServerSummary.totals.total) * 100}%` }}
                  />
                )}
                {agentsByServerSummary.totals.offline > 0 && (
                  <div
                    className="h-full bg-red-500 transition-all duration-500"
                    style={{ width: `${(agentsByServerSummary.totals.offline / agentsByServerSummary.totals.total) * 100}%` }}
                  />
                )}
              </div>

              {/* Legend */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span className="text-green-400">{agentsByServerSummary.totals.online.toLocaleString()} online</span>
                <span className="text-red-400">{agentsByServerSummary.totals.offline.toLocaleString()} offline</span>
                <span className="text-slate-400">{agentsByServerSummary.totals.servers.toLocaleString()} servers</span>
              </div>

              {/* Per-server breakdown (top N by most offline) or all-online message */}
              {agentsByServerSummary.totals.offline > 0 ? (
                <div className="space-y-3">
                  <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Most Offline Agents</p>
                  {agentsByServerSummary.servers
                    .filter((s) => s.offline > 0)
                    .map((group) => {
                      const onlinePct = group.total > 0 ? (group.online / group.total) * 100 : 0
                      return (
                        <div key={group.server_id} className="space-y-2">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: group.color || '#3b82f6' }} />
                              <span className="text-sm font-medium text-white">{group.server_name}</span>
                            </div>
                            <div className="flex items-center gap-3 text-xs">
                              <span className="text-green-400">{group.online} online</span>
                              <span className="text-red-400">{group.offline} offline</span>
                            </div>
                          </div>
                          <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                            <div
                              className="h-full rounded-full transition-all duration-500"
                              style={{
                                width: `${onlinePct}%`,
                                backgroundColor: group.color || '#3b82f6',
                                opacity: 0.8,
                              }}
                            />
                          </div>
                        </div>
                      )
                    })}
                </div>
              ) : (
                <div className="flex items-center gap-2 p-3 rounded-lg bg-green-500/10 border border-green-500/20">
                  <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
                  <span className="text-sm text-green-400">All agents online</span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8">
              <Server className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No agents synced yet</p>
              <p className="text-xs text-slate-500 mt-1">Agents appear after servers start syncing</p>
            </div>
          )}
        </div>
      </div>

      {/* Recent Alerts + Recent Agents */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Alerts */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Recent Alerts</h3>
            <Link to="/app/alerts" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          <div className="space-y-3">
            {alerts?.filter(a => !a.resolved).slice(0, 5).map((alert) => (
              <div
                key={alert.id}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-white/5 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-white truncate">{alert.message}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <p className="text-xs text-slate-500 font-mono">{alert.agent_id}</p>
                    {alert.server_name && (
                      <span className="text-xs text-slate-500">
                        &middot; {alert.server_name}
                      </span>
                    )}
                  </div>
                </div>
                <span
                  className={`text-xs px-3 py-1.5 rounded-full font-medium ml-3 whitespace-nowrap ${
                    alert.severity === 'CRITICAL'
                      ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                      : alert.severity === 'WARNING'
                      ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30'
                      : 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                  }`}
                >
                  {alert.severity}
                </span>
              </div>
            ))}
            {(!alerts || alerts.filter(a => !a.resolved).length === 0) && (
              <div className="text-center py-8 text-slate-400">
                <AlertTriangle className="w-10 h-10 mx-auto mb-3 text-slate-600" />
                No active alerts
              </div>
            )}
          </div>
        </div>

        {/* Recently Seen Agents (from lightweight endpoint) */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Recently Active Agents</h3>
            <Link to="/app/agents" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          <div className="space-y-3">
            {recentAgents?.map((agent) => (
              <Link
                key={agent.agent_id}
                to={`/app/agents/${agent.agent_id}`}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-white/5 transition-colors cursor-pointer"
              >
                <div className="flex items-center gap-3">
                  <div className={`w-2 h-2 rounded-full ${agent.status === 'online' ? 'bg-green-500' : 'bg-red-500'}`} />
                  <div>
                    <p className="text-sm font-medium text-white">{agent.hostname}</p>
                    <div className="flex items-center gap-2">
                      <p className="text-xs text-slate-500 font-mono">{agent.ip_address}</p>
                      {agent.server_name && (
                        <span className="text-xs text-slate-500">&middot; {agent.server_name}</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Clock className="w-3 h-3" />
                  {agent.last_seen
                    ? formatDistanceToNow(new Date(agent.last_seen), { addSuffix: true })
                    : 'Never'}
                </div>
              </Link>
            ))}
            {(!recentAgents || recentAgents.length === 0) && (
              <div className="text-center py-8 text-slate-400">
                <Server className="w-10 h-10 mx-auto mb-3 text-slate-600" />
                No agents found
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
