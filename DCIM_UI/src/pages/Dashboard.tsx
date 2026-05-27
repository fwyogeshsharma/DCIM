import { useAlerts } from '@/hooks/useAlerts'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  Network,
  Server,
  Activity,
  AlertTriangle,
  CheckCircle,
  Clock,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { Link } from 'react-router-dom'

function formatNetworkLabel(id: string): string {
  return id
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// Consistent color per network position
const NETWORK_COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#06b6d4']

export default function Dashboard() {
  const { data: alerts } = useAlerts()

  const { data: networks, isLoading: networksLoading } = useQuery({
    queryKey: ['networks-summary'],
    queryFn: () => api.getNetworksSummary(),
    staleTime: 10000,
    refetchInterval: 15000,
  })

  const { data: recentAgents } = useQuery({
    queryKey: ['recent-agents'],
    queryFn: () => api.getRecentAgents(6),
    refetchInterval: 30000,
  })

  // Totals derived from networks
  const totalNetworks = networks?.length ?? 0
  const totalDevices = networks?.reduce((s, n) => s + n.total_devices, 0) ?? 0
  const onlineDevices = networks?.reduce((s, n) => s + n.online, 0) ?? 0
  const offlineDevices = networks?.reduce((s, n) => s + n.offline, 0) ?? 0
  const totalActiveAlerts = networks?.reduce((s, n) => s + n.active_alerts, 0) ?? 0
  const totalCriticalAlerts = networks?.reduce((s, n) => s + n.critical_alerts, 0) ?? 0

  const stats = [
    {
      name: 'Networks',
      value: totalNetworks.toLocaleString(),
      subtitle: 'monitored',
      icon: Network,
      color: 'text-cyan-500',
      bgColor: 'bg-cyan-500/10',
      link: '/app/agents',
    },
    {
      name: 'Total Devices',
      value: totalDevices.toLocaleString(),
      subtitle: `across ${totalNetworks} network${totalNetworks !== 1 ? 's' : ''}`,
      icon: Server,
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10',
      link: '/app/agents',
    },
    {
      name: 'Online Devices',
      value: onlineDevices.toLocaleString(),
      subtitle: `${offlineDevices.toLocaleString()} offline`,
      icon: Activity,
      color: 'text-green-500',
      bgColor: 'bg-green-500/10',
      link: '/app/agents',
    },
    {
      name: 'Active Alerts',
      value: totalActiveAlerts.toLocaleString(),
      subtitle: `${totalCriticalAlerts} critical`,
      icon: AlertTriangle,
      color: totalCriticalAlerts > 0 ? 'text-red-500' : 'text-yellow-500',
      bgColor: totalCriticalAlerts > 0 ? 'bg-red-500/10' : 'bg-yellow-500/10',
      link: '/app/alerts',
    },
  ]

  if (networksLoading) {
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
                  <p className="text-sm font-medium text-slate-400">{stat.name}</p>
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

      {/* Network Health + Recent Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Network Health */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Network Health</h3>
            <Link to="/app/agents" className="text-sm text-blue-400 hover:text-blue-300">View Devices</Link>
          </div>

          {networks && networks.length > 0 ? (
            <div className="space-y-4">
              {networks.map((net, idx) => {
                const color = NETWORK_COLORS[idx % NETWORK_COLORS.length]
                const onlinePct = net.total_devices > 0 ? (net.online / net.total_devices) * 100 : 0
                const hasAlerts = net.active_alerts > 0

                return (
                  <div key={net.network_id} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
                        <span className="text-sm font-medium text-white truncate">
                          {formatNetworkLabel(net.network_id)}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 text-xs flex-shrink-0 ml-2">
                        {hasAlerts && (
                          <span className={`px-2 py-0.5 rounded-full font-medium ${
                            net.critical_alerts > 0
                              ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                              : 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30'
                          }`}>
                            {net.critical_alerts > 0 ? `${net.critical_alerts} critical` : `${net.active_alerts} alerts`}
                          </span>
                        )}
                        <span className="text-green-400 flex items-center gap-1">
                          <Wifi className="w-3 h-3" />
                          {net.online}
                        </span>
                        <span className="text-red-400 flex items-center gap-1">
                          <WifiOff className="w-3 h-3" />
                          {net.offline}
                        </span>
                        <span className="text-slate-400">{net.total_devices} total</span>
                      </div>
                    </div>

                    {/* Online/Offline bar */}
                    <div className="h-2 bg-slate-700 rounded-full overflow-hidden flex">
                      {net.online > 0 && (
                        <div
                          className="h-full rounded-l-full transition-all duration-500"
                          style={{ width: `${onlinePct}%`, backgroundColor: color }}
                        />
                      )}
                      {net.offline > 0 && (
                        <div
                          className="h-full bg-red-500/60 transition-all duration-500"
                          style={{ width: `${100 - onlinePct}%` }}
                        />
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="text-center py-8">
              <Network className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No networks found</p>
              <p className="text-xs text-slate-500 mt-1">Devices appear after simulators start syncing</p>
            </div>
          )}
        </div>

        {/* Recent Alerts */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Recent Alerts</h3>
            <Link to="/app/alerts" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          <div className="space-y-3">
            {alerts?.filter((a) => !a.resolved).slice(0, 5).map((alert) => (
              <div
                key={alert.id}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-white/5 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-white truncate">{alert.message}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <p className="text-xs text-slate-500 font-mono">{alert.agent_id}</p>
                    {alert.server_name && (
                      <span className="text-xs text-slate-500">&middot; {alert.server_name}</span>
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
            {(!alerts || alerts.filter((a) => !a.resolved).length === 0) && (
              <div className="text-center py-8 text-slate-400">
                <AlertTriangle className="w-10 h-10 mx-auto mb-3 text-slate-600" />
                No active alerts
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Device Distribution + Recently Active Devices */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Device distribution across networks — overall stacked bar */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Device Distribution</h3>
            <Link to="/app/agents" className="text-sm text-blue-400 hover:text-blue-300">View All</Link>
          </div>

          {networks && networks.length > 0 ? (
            <div className="space-y-4">
              {/* Overall online vs offline bar */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-slate-400">
                  <span>Overall reachability</span>
                  <span>
                    {totalDevices > 0
                      ? `${Math.round((onlineDevices / totalDevices) * 100)}% online`
                      : '—'}
                  </span>
                </div>
                <div className="h-3 bg-slate-700 rounded-full overflow-hidden flex">
                  {onlineDevices > 0 && (
                    <div
                      className="h-full bg-green-500 transition-all duration-500"
                      style={{ width: `${(onlineDevices / totalDevices) * 100}%` }}
                    />
                  )}
                  {offlineDevices > 0 && (
                    <div
                      className="h-full bg-red-500 transition-all duration-500"
                      style={{ width: `${(offlineDevices / totalDevices) * 100}%` }}
                    />
                  )}
                </div>
                <div className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
                    <span className="text-green-400">{onlineDevices.toLocaleString()} online</span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
                    <span className="text-red-400">{offlineDevices.toLocaleString()} offline</span>
                  </span>
                </div>
              </div>

              {/* Per-network device counts */}
              <div className="space-y-2 pt-2 border-t border-white/5">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Per Network</p>
                {networks.map((net, idx) => {
                  const color = NETWORK_COLORS[idx % NETWORK_COLORS.length]
                  const pct = totalDevices > 0 ? (net.total_devices / totalDevices) * 100 : 0
                  return (
                    <div key={net.network_id} className="space-y-1">
                      <div className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                          <span className="text-slate-300">{formatNetworkLabel(net.network_id)}</span>
                        </div>
                        <span className="text-slate-400">{net.total_devices} devices</span>
                      </div>
                      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${pct}%`, backgroundColor: color }}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* All online indicator */}
              {offlineDevices === 0 && totalDevices > 0 && (
                <div className="flex items-center gap-2 p-3 rounded-lg bg-green-500/10 border border-green-500/20">
                  <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
                  <span className="text-sm text-green-400">
                    All {totalDevices.toLocaleString()} devices online
                  </span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8">
              <Server className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No devices synced yet</p>
              <p className="text-xs text-slate-500 mt-1">Devices appear after networks start reporting</p>
            </div>
          )}
        </div>

        {/* Recently Active Devices */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-semibold text-white">Recently Active Devices</h3>
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
                        <span className="text-xs text-slate-500">
                          &middot; {formatNetworkLabel(agent.server_name)}
                        </span>
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
                No devices found
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
