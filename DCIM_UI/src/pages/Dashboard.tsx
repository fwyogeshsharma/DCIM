import { useAgents } from '@/hooks/useAgents'
import { useAlerts } from '@/hooks/useAlerts'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Server, Activity, AlertTriangle, ThermometerSun, ServerCog, CheckCircle, XCircle, Clock } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { Link } from 'react-router-dom'

export default function Dashboard() {
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: alerts, isLoading: alertsLoading } = useAlerts()
  const { data: servers, isLoading: serversLoading } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 30000,
    refetchInterval: 30000,
  })

  // Use the stats API for accurate total counts (alerts list is paginated to 20)
  const { data: dashboardStats } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: () => api.getDashboardStats(),
    staleTime: 5000,
    refetchInterval: 5000,
  })

  // Use alert counts API for critical count
  const { data: alertCounts } = useQuery({
    queryKey: ['alert-counts'],
    queryFn: () => api.getAlertCounts(),
    staleTime: 5000,
    refetchInterval: 5000,
  })

  const totalAgents = agents?.length || 0
  const onlineAgents = agents?.filter((a) => a.status === 'online').length || 0
  const criticalAlerts = alertCounts?.reduce((sum, c) => sum + Number(c.critical), 0) || 0
  const totalAlerts = dashboardStats?.activeAlerts || 0
  const totalServers = servers?.length || 0
  const healthyServers = servers?.filter((s) => s.health?.status === 'healthy').length || 0

  // Group agents by server
  const agentsByServer: Record<string, { name: string; online: number; offline: number; total: number; color: string }> = {}
  agents?.forEach((agent) => {
    const key = agent.server_name || 'Unknown'
    if (!agentsByServer[key]) {
      agentsByServer[key] = { name: key, online: 0, offline: 0, total: 0, color: '#3b82f6' }
    }
    agentsByServer[key].total++
    if (agent.status === 'online') agentsByServer[key].online++
    else agentsByServer[key].offline++
  })

  // Try to assign server colors from server metadata
  servers?.forEach((s) => {
    if (agentsByServer[s.name] && s.metadata?.color) {
      agentsByServer[s.name].color = s.metadata.color
    }
  })

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
      value: totalAgents,
      subtitle: `${onlineAgents} online`,
      icon: Server,
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10',
      link: '/app/agents',
    },
    {
      name: 'Online Agents',
      value: onlineAgents,
      subtitle: `${totalAgents - onlineAgents} offline`,
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

  if (agentsLoading || serversLoading) {
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
        {/* Server Health */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Server Health</h3>
            <Link to="/app/servers" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          {servers && servers.length > 0 ? (
            <div className="space-y-3">
              {servers.map((server) => (
                <div key={server.id} className="flex items-center justify-between p-3 rounded-lg bg-slate-900/50 border border-white/5">
                  <div className="flex items-center gap-3">
                    <div
                      className="w-2 h-8 rounded-full"
                      style={{ backgroundColor: server.metadata?.color || '#3b82f6' }}
                    />
                    <div>
                      <p className="text-sm font-medium text-white">{server.name}</p>
                      <p className="text-xs text-slate-500 font-mono">{server.url}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {server.health?.status === 'healthy' ? (
                      <>
                        <CheckCircle className="w-4 h-4 text-green-500" />
                        <span className="text-xs text-green-400">{server.health.responseTime}ms</span>
                      </>
                    ) : (
                      <>
                        <XCircle className="w-4 h-4 text-red-500" />
                        <span className="text-xs text-red-400">Offline</span>
                      </>
                    )}
                  </div>
                </div>
              ))}
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

        {/* Agents by Server */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Agents by Server</h3>
            <Link to="/app/agents" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          {Object.keys(agentsByServer).length > 0 ? (
            <div className="space-y-4">
              {Object.values(agentsByServer).map((group) => {
                const onlinePct = group.total > 0 ? (group.online / group.total) * 100 : 0
                return (
                  <div key={group.name} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div className="w-3 h-3 rounded-full" style={{ backgroundColor: group.color }} />
                        <span className="text-sm font-medium text-white">{group.name}</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs">
                        <span className="text-green-400">{group.online} online</span>
                        {group.offline > 0 && <span className="text-red-400">{group.offline} offline</span>}
                        <span className="text-slate-500">{group.total} total</span>
                      </div>
                    </div>
                    <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${onlinePct}%`,
                          backgroundColor: group.color,
                          opacity: 0.8,
                        }}
                      />
                    </div>
                  </div>
                )
              })}
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

        {/* Recently Seen Agents */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Recently Active Agents</h3>
            <Link to="/app/agents" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          <div className="space-y-3">
            {agents
              ?.slice()
              .sort((a, b) => new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime())
              .slice(0, 6)
              .map((agent) => (
                <Link
                  key={agent.id}
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
                    {formatDistanceToNow(new Date(agent.last_seen), { addSuffix: true })}
                  </div>
                </Link>
              ))}
            {(!agents || agents.length === 0) && (
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
