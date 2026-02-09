import { useAlerts } from '@/hooks/useAlerts'
import { formatDistanceToNow } from 'date-fns'

export default function Alerts() {
  const { data: alerts, isLoading } = useAlerts()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading alerts...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white">Alerts</h1>
        <p className="text-slate-400 mt-2 text-lg">
          Monitor and manage system alerts
        </p>
      </div>

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-slate-900/50">
            <tr>
              <th className="text-left p-4 font-medium text-slate-300">Severity</th>
              <th className="text-left p-4 font-medium text-slate-300">Agent</th>
              <th className="text-left p-4 font-medium text-slate-300">Metric</th>
              <th className="text-left p-4 font-medium text-slate-300">Message</th>
              <th className="text-left p-4 font-medium text-slate-300">Value</th>
              <th className="text-left p-4 font-medium text-slate-300">Time</th>
              <th className="text-left p-4 font-medium text-slate-300">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {alerts?.map((alert) => (
              <tr key={alert.id} className="hover:bg-white/5 transition-colors">
                <td className="p-4">
                  <span
                    className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${
                      alert.severity === 'CRITICAL'
                        ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                        : alert.severity === 'WARNING'
                        ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30'
                        : 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                    }`}
                  >
                    {alert.severity}
                  </span>
                </td>
                <td className="p-4 font-mono text-sm text-slate-300">{alert.agent_id}</td>
                <td className="p-4 text-sm text-slate-300">{alert.metric_type}</td>
                <td className="p-4 text-white">{alert.message}</td>
                <td className="p-4 font-mono text-sm text-slate-300">
                  {alert.value.toFixed(2)} / {alert.threshold.toFixed(2)}
                </td>
                <td className="p-4 text-sm text-slate-400">
                  {formatDistanceToNow(new Date(alert.timestamp), { addSuffix: true })}
                </td>
                <td className="p-4">
                  {alert.resolved ? (
                    <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-green-500/20 text-green-400 border border-green-500/30">
                      Resolved
                    </span>
                  ) : (
                    <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
                      Active
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(!alerts || alerts.length === 0) && (
          <div className="text-center py-12 text-slate-400">
            No alerts found
          </div>
        )}
      </div>
    </div>
  )
}
