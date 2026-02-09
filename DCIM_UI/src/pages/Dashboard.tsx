import { useAgents } from '@/hooks/useAgents'
import { useAlerts } from '@/hooks/useAlerts'
import { Server, Activity, AlertTriangle, ThermometerSun } from 'lucide-react'

export default function Dashboard() {
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: alerts, isLoading: alertsLoading } = useAlerts()

  const totalAgents = agents?.length || 0
  const onlineAgents = agents?.filter((a) => a.status === 'online').length || 0
  const criticalAlerts = alerts?.filter((a) => a.severity === 'CRITICAL' && !a.resolved).length || 0
  const totalAlerts = alerts?.filter((a) => !a.resolved).length || 0

  const stats = [
    {
      name: 'Total Agents',
      value: totalAgents,
      icon: Server,
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10',
    },
    {
      name: 'Online Agents',
      value: onlineAgents,
      icon: Activity,
      color: 'text-green-500',
      bgColor: 'bg-green-500/10',
    },
    {
      name: 'Critical Alerts',
      value: criticalAlerts,
      icon: AlertTriangle,
      color: 'text-red-500',
      bgColor: 'bg-red-500/10',
    },
    {
      name: 'Total Alerts',
      value: totalAlerts,
      icon: ThermometerSun,
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-500/10',
    },
  ]

  if (agentsLoading || alertsLoading) {
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
            <div
              key={stat.name}
              className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300 group cursor-pointer"
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">
                    {stat.name}
                  </p>
                  <p className="text-3xl font-bold mt-2 text-white">{stat.value}</p>
                </div>
                <div className={`${stat.bgColor} p-3 rounded-lg group-hover:scale-110 transition-transform duration-300`}>
                  <Icon className={`h-6 w-6 ${stat.color}`} />
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Charts and Tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <h3 className="text-xl font-semibold mb-4 text-white">Agent Status</h3>
          <div className="text-slate-400 text-sm">
            Agent status visualization will appear here
          </div>
        </div>

        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <h3 className="text-xl font-semibold mb-4 text-white">Recent Alerts</h3>
          <div className="space-y-3">
            {alerts?.slice(0, 5).map((alert) => (
              <div
                key={alert.id}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-white/5 transition-colors cursor-pointer"
              >
                <div>
                  <p className="text-sm font-medium text-white">{alert.message}</p>
                  <p className="text-xs text-slate-400 mt-1">{alert.agent_id}</p>
                </div>
                <span
                  className={`text-xs px-3 py-1.5 rounded-full font-medium ${
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
            {(!alerts || alerts.length === 0) && (
              <div className="text-center py-8 text-slate-400">
                No recent alerts
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
        <h3 className="text-xl font-semibold mb-4 text-white">System Metrics</h3>
        <div className="text-slate-400 text-sm">
          Real-time metrics charts will appear here
        </div>
      </div>
    </div>
  )
}
