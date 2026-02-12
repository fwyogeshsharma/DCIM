import { useState, useEffect, useRef, useCallback } from 'react'
import { useResolveAlert } from '@/hooks/useAlerts'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { format } from 'date-fns'
import { Filter, CheckCircle, AlertTriangle, ChevronDown, Loader2 } from 'lucide-react'
import type { Alert } from '@/lib/types'

const PAGE_SIZE = 20

export default function Alerts() {
  const { data: agents } = useAgents()
  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })
  const resolveAlert = useResolveAlert()

  // Alert counts per agent (lightweight, loads immediately)
  const { data: alertCounts, isLoading: countsLoading } = useQuery({
    queryKey: ['alert-counts'],
    queryFn: () => api.getAlertCounts(),
    refetchInterval: 5000,
  })

  const [agentFilter, setAgentFilter] = useState<string>('all')
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('active')

  // Paginated alerts state
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [totalAlerts, setTotalAlerts] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const tableEndRef = useRef<HTMLDivElement>(null)

  // Build query filter
  const buildFilter = useCallback(() => {
    const filter: any = { limit: PAGE_SIZE, offset: 0 }
    if (agentFilter !== 'all') filter.agent_id = agentFilter
    if (severityFilter !== 'all') filter.severity = severityFilter.toLowerCase()
    if (statusFilter === 'active') filter.resolved = false
    else if (statusFilter === 'resolved') filter.resolved = true
    return filter
  }, [agentFilter, severityFilter, statusFilter])

  // Load alerts when filters change
  useEffect(() => {
    let cancelled = false
    const loadAlerts = async () => {
      setLoading(true)
      try {
        const filter = buildFilter()
        const result = await api.getAlertsPaginated(filter)
        if (!cancelled) {
          setAlerts(result.data)
          setTotalAlerts(result.total)
          setHasMore(result.hasMore)
        }
      } catch (error) {
        console.error('Failed to load alerts:', error)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    loadAlerts()
    return () => { cancelled = true }
  }, [buildFilter])

  // Auto-refresh every 5s
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const filter = buildFilter()
        filter.limit = alerts.length || PAGE_SIZE
        const result = await api.getAlertsPaginated(filter)
        setAlerts(result.data)
        setTotalAlerts(result.total)
        setHasMore(result.hasMore)
      } catch {}
    }, 5000)
    return () => clearInterval(interval)
  }, [buildFilter, alerts.length])

  // Load more
  const loadMore = async () => {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    try {
      const filter = buildFilter()
      filter.offset = alerts.length
      const result = await api.getAlertsPaginated(filter)
      setAlerts(prev => [...prev, ...result.data])
      setTotalAlerts(result.total)
      setHasMore(result.hasMore)
    } catch (error) {
      console.error('Failed to load more alerts:', error)
    } finally {
      setLoadingMore(false)
    }
  }

  // Infinite scroll observer
  useEffect(() => {
    if (!tableEndRef.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingMore && !loading) {
          loadMore()
        }
      },
      { threshold: 0.1 }
    )
    observer.observe(tableEndRef.current)
    return () => observer.disconnect()
  }, [hasMore, loadingMore, loading, alerts.length])

  const agentNames = [...new Set(agents?.map((a) => a.agent_id).filter(Boolean) as string[])].sort()

  // Summary counts from the counts API
  const totalActive = alertCounts?.reduce((sum, c) => sum + Number(c.active), 0) || 0
  const totalCritical = alertCounts?.reduce((sum, c) => sum + Number(c.critical), 0) || 0

  if (countsLoading) {
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
          {totalCritical > 0 && (
            <span className="px-3 py-1.5 rounded-full text-xs font-medium bg-red-500/20 text-red-400 border border-red-500/30">
              {totalCritical} Critical
            </span>
          )}
          <span className="px-3 py-1.5 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
            {totalActive} Active
          </span>
        </div>
      </div>

      {/* Alert Counts per Agent */}
      {alertCounts && alertCounts.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          {alertCounts.map((ac) => {
            const serverColor = servers?.find(s => s.name === ac.server_name)?.metadata?.color || '#3b82f6'
            const isSelected = agentFilter === ac.agent_id
            return (
              <button
                key={ac.agent_id}
                onClick={() => setAgentFilter(isSelected ? 'all' : ac.agent_id)}
                className={`text-left p-4 rounded-xl border transition-all ${
                  isSelected
                    ? 'bg-blue-500/20 border-blue-500/50 ring-1 ring-blue-500/30'
                    : 'bg-slate-800/50 border-white/10 hover:border-white/20 hover:bg-slate-800/80'
                }`}
              >
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-2 h-4 rounded-full" style={{ backgroundColor: serverColor }} />
                  <span className="text-xs text-slate-400 truncate">{ac.server_name}</span>
                </div>
                <p className="text-sm font-semibold text-white truncate" title={ac.hostname || ac.agent_id}>
                  {ac.hostname || ac.agent_id}
                </p>
                <div className="flex items-center gap-2 mt-2">
                  {Number(ac.critical) > 0 && (
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-400">
                      {ac.critical} crit
                    </span>
                  )}
                  {Number(ac.warning) > 0 && (
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-500/20 text-yellow-400">
                      {ac.warning} warn
                    </span>
                  )}
                  {Number(ac.active) === 0 && (
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-500/20 text-green-400">
                      Clear
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-500 mt-1">{ac.total} total</p>
              </button>
            )
          })}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400" />
          <span className="text-sm text-slate-400">Filter:</span>
        </div>
        <select
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Agents</option>
          {agentNames.map((name) => (
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
        {(agentFilter !== 'all' || severityFilter !== 'all' || statusFilter !== 'active') && (
          <button
            onClick={() => { setAgentFilter('all'); setSeverityFilter('all'); setStatusFilter('active') }}
            className="text-xs text-slate-400 hover:text-white"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-slate-500 ml-auto">
          {alerts.length} of {totalAlerts} alerts
        </span>
      </div>

      {/* Alerts Table */}
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
            {loading ? (
              <tr>
                <td colSpan={8} className="p-8 text-center">
                  <div className="flex items-center justify-center gap-3 text-slate-400">
                    <Loader2 className="w-5 h-5 animate-spin" />
                    Loading alerts...
                  </div>
                </td>
              </tr>
            ) : (
              alerts.map((alert) => {
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
                      <div>{format(new Date(alert.timestamp), 'MMM dd, yyyy')}</div>
                      <div className="text-xs text-slate-500">{format(new Date(alert.timestamp), 'hh:mm:ss a')}</div>
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
              })
            )}
          </tbody>
        </table>

        {/* Load more / infinite scroll trigger */}
        <div ref={tableEndRef}>
          {loadingMore && (
            <div className="flex items-center justify-center gap-3 p-4 text-slate-400">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading more...
            </div>
          )}
          {hasMore && !loadingMore && !loading && (
            <button
              onClick={loadMore}
              className="w-full flex items-center justify-center gap-2 p-3 text-sm text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
            >
              <ChevronDown className="w-4 h-4" />
              Load more ({alerts.length} of {totalAlerts})
            </button>
          )}
        </div>

        {!loading && alerts.length === 0 && (
          <div className="text-center py-12 text-slate-400">
            {agentFilter !== 'all' ? 'No alerts found for this agent' : 'No alerts found'}
          </div>
        )}
      </div>
    </div>
  )
}
