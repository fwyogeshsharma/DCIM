import { useState } from 'react'
import { useAlerts, useResolveAlert, useBulkResolveAlerts } from '@/hooks/useAlerts'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatDistanceToNow } from 'date-fns'
import { Filter, CheckCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Alerts() {
  const { data: alerts, isLoading } = useAlerts()
  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })
  const resolveAlert = useResolveAlert()
  const bulkResolve = useBulkResolveAlerts()

  const [serverFilter, setServerFilter] = useState<string>('all')
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('active')

  const filteredAlerts = alerts?.filter((alert) => {
    if (serverFilter !== 'all' && alert.server_name !== serverFilter) return false
    if (severityFilter !== 'all' && alert.severity !== severityFilter) return false
    if (statusFilter === 'active' && alert.resolved) return false
    if (statusFilter === 'resolved' && !alert.resolved) return false
    return true
  })

  const serverNames = [...new Set(alerts?.map((a) => a.server_name).filter(Boolean) as string[])]

  const activeCount = alerts?.filter((a) => !a.resolved).length || 0
  const criticalCount = alerts?.filter((a) => a.severity === 'CRITICAL' && !a.resolved).length || 0

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
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-4xl font-bold text-white">Alerts</h1>
          <p className="text-slate-400 mt-2 text-lg">
            Monitor and manage system alerts across all servers
          </p>
        </div>
        <div className="flex items-center gap-3">
          {criticalCount > 0 && (
            <span className="px-3 py-1.5 rounded-full text-xs font-medium bg-red-500/20 text-red-400 border border-red-500/30">
              {criticalCount} Critical
            </span>
          )}
          <span className="px-3 py-1.5 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
            {activeCount} Active
          </span>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400" />
          <span className="text-sm text-slate-400">Filter:</span>
        </div>
        <select
          value={serverFilter}
          onChange={(e) => setServerFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Servers</option>
          {serverNames.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Severity</option>
          <option value="CRITICAL">Critical</option>
          <option value="WARNING">Warning</option>
          <option value="INFO">Info</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Status</option>
          <option value="active">Active</option>
          <option value="resolved">Resolved</option>
        </select>
        {(serverFilter !== 'all' || severityFilter !== 'all' || statusFilter !== 'active') && (
          <button
            onClick={() => { setServerFilter('all'); setSeverityFilter('all'); setStatusFilter('active') }}
            className="text-xs text-slate-400 hover:text-white"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-slate-500 ml-auto">
          {filteredAlerts?.length || 0} alerts
        </span>
      </div>

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-slate-900/50">
            <tr>
              <th className="text-left p-4 font-medium text-slate-300">Severity</th>
              <th className="text-left p-4 font-medium text-slate-300">Server</th>
              <th className="text-left p-4 font-medium text-slate-300">Agent</th>
              <th className="text-left p-4 font-medium text-slate-300">Metric</th>
              <th className="text-left p-4 font-medium text-slate-300">Message</th>
              <th className="text-left p-4 font-medium text-slate-300">Value</th>
              <th className="text-left p-4 font-medium text-slate-300">Time</th>
              <th className="text-left p-4 font-medium text-slate-300">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {filteredAlerts?.map((alert) => {
              const serverColor = servers?.find((s) => s.name === alert.server_name)?.metadata?.color || '#3b82f6'
              return (
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
                  <td className="p-4">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-5 rounded-full" style={{ backgroundColor: serverColor }} />
                      <span className="text-sm text-slate-300">{alert.server_name || '—'}</span>
                    </div>
                  </td>
                  <td className="p-4 font-mono text-sm text-slate-300">{alert.agent_id}</td>
                  <td className="p-4 text-sm text-slate-300">{alert.metric_type}</td>
                  <td className="p-4 text-white max-w-xs truncate">{alert.message}</td>
                  <td className="p-4 font-mono text-sm text-slate-300">
                    {typeof alert.value === 'number' ? alert.value.toFixed(2) : alert.value}
                    {' / '}
                    {typeof alert.threshold === 'number' ? alert.threshold.toFixed(2) : alert.threshold}
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
                      <button
                        onClick={() => resolveAlert.mutate(alert.id)}
                        disabled={resolveAlert.isPending}
                        className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 hover:bg-yellow-500/30 transition-colors cursor-pointer"
                      >
                        <CheckCircle className="w-3 h-3" />
                        Resolve
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {(!filteredAlerts || filteredAlerts.length === 0) && (
          <div className="text-center py-12 text-slate-400">
            {alerts && alerts.length > 0 ? 'No alerts match the current filters' : 'No alerts found'}
          </div>
        )}
      </div>
    </div>
  )
}
