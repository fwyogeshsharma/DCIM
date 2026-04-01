import { useState, useEffect, useRef, useCallback } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { format, formatDistanceToNow } from 'date-fns'
import { Filter, AlertTriangle, ChevronDown, Loader2, Clock, Repeat } from 'lucide-react'
import type { DeduplicatedAlert } from '@/lib/types'

const PAGE_SIZE = 50

export default function Alerts() {
  const { data: agents } = useAgents()
  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })

  // Alert counts per agent (lightweight, loads immediately)
  const { data: alertCounts, isLoading: countsLoading } = useQuery({
    queryKey: ['alert-counts'],
    queryFn: () => api.getAlertCounts(),
    refetchInterval: 5000,
  })

  const [agentFilter, setAgentFilter] = useState<string>('all')
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [expandedAlertId, setExpandedAlertId] = useState<number | null>(null)

  // Paginated deduplicated alerts state
  const [alerts, setAlerts] = useState<DeduplicatedAlert[]>([])
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
    return filter
  }, [agentFilter, severityFilter])

  // Load alerts when filters change
  useEffect(() => {
    let cancelled = false
    const loadAlerts = async () => {
      setLoading(true)
      try {
        const filter = buildFilter()
        const result = await api.getLatestAlerts(filter)
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
        const result = await api.getLatestAlerts(filter)
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
      const result = await api.getLatestAlerts(filter)
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

  const formatDuration = (firstSeen: string) => {
    try {
      return formatDistanceToNow(new Date(firstSeen), { addSuffix: false })
    } catch {
      return '—'
    }
  }

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
            Active alerts deduplicated by agent, metric, and severity
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
        {(agentFilter !== 'all' || severityFilter !== 'all') && (
          <button
            onClick={() => { setAgentFilter('all'); setSeverityFilter('all') }}
            className="text-xs text-slate-400 hover:text-white"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-slate-500 ml-auto">
          {alerts.length} of {totalAlerts} unique alerts
        </span>
      </div>

      {/* Alerts Table */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg overflow-x-auto">
        <table className="w-full min-w-[900px]">
          <thead className="bg-slate-900/50">
            <tr>
              <th className="text-left p-4 font-medium text-slate-300">Severity</th>
              <th className="text-left p-4 font-medium text-slate-300">Server</th>
              <th className="text-left p-4 font-medium text-slate-300">Agent</th>
              <th className="text-left p-4 font-medium text-slate-300">Metric</th>
              <th className="text-left p-4 font-medium text-slate-300">Message</th>
              <th className="text-left p-4 font-medium text-slate-300">Value</th>
              <th className="text-left p-4 font-medium text-slate-300">Last Seen</th>
              <th className="text-left p-4 font-medium text-slate-300">Occurrences</th>
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
                const isExpanded = expandedAlertId === alert.id
                return (
                  <tr key={alert.id} className="group">
                    <td colSpan={8} className="p-0">
                      {/* Main row */}
                      <div className="flex items-center hover:bg-white/5 transition-colors">
                        <div className="p-4 w-[110px] shrink-0">
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
                        </div>
                        <div className="p-4 flex-1 min-w-[100px]">
                          <div className="flex items-center gap-2">
                            <div className="w-2 h-5 rounded-full" style={{ backgroundColor: serverColor }} />
                            <span className="text-sm text-slate-300">{alert.server_name || '—'}</span>
                          </div>
                        </div>
                        <div className="p-4 flex-1 min-w-[120px]">
                          <span className="font-mono text-sm text-slate-300">{alert.agent_id}</span>
                        </div>
                        <div className="p-4 flex-1 min-w-[100px]">
                          <span className="text-sm text-slate-300">{alert.metric_type}</span>
                        </div>
                        <div className="p-4 flex-[2] min-w-[150px]">
                          <span className="text-white truncate block max-w-xs">{alert.message}</span>
                        </div>
                        <div className="p-4 flex-1 min-w-[100px]">
                          <span className="font-mono text-sm text-slate-300">
                            {typeof alert.value === 'number' ? alert.value.toFixed(2) : alert.value}
                            {' / '}
                            {typeof alert.threshold === 'number' ? alert.threshold.toFixed(2) : alert.threshold}
                          </span>
                        </div>
                        <div className="p-4 flex-1 min-w-[110px]">
                          <div className="text-sm text-slate-400">{format(new Date(alert.timestamp), 'MMM dd, yyyy')}</div>
                          <div className="text-xs text-slate-500">{format(new Date(alert.timestamp), 'hh:mm:ss a')}</div>
                        </div>
                        <div className="p-4 flex-1 min-w-[120px]">
                          {alert.occurrence_count > 1 ? (
                            <button
                              onClick={() => setExpandedAlertId(isExpanded ? null : alert.id)}
                              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                                isExpanded
                                  ? 'bg-orange-500/30 text-orange-300 border border-orange-500/50'
                                  : 'bg-orange-500/20 text-orange-400 border border-orange-500/30 hover:bg-orange-500/30'
                              }`}
                            >
                              <Repeat className="w-3.5 h-3.5" />
                              {alert.occurrence_count}x
                              <ChevronDown className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                            </button>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-700/50 text-slate-400 border border-white/5">
                              1x
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Expanded duration info */}
                      {isExpanded && alert.occurrence_count > 1 && (
                        <div className="px-6 pb-4 bg-slate-900/30 border-t border-white/5">
                          <div className="flex items-center gap-6 py-3">
                            <div className="flex items-center gap-2 text-sm">
                              <Clock className="w-4 h-4 text-orange-400" />
                              <span className="text-slate-400">Recurring for:</span>
                              <span className="text-orange-300 font-medium">{formatDuration(alert.first_seen)}</span>
                            </div>
                            <div className="text-sm">
                              <span className="text-slate-400">First seen:</span>{' '}
                              <span className="text-slate-300">
                                {format(new Date(alert.first_seen), 'MMM dd, yyyy hh:mm:ss a')}
                              </span>
                            </div>
                            <div className="text-sm">
                              <span className="text-slate-400">Latest:</span>{' '}
                              <span className="text-slate-300">
                                {format(new Date(alert.timestamp), 'MMM dd, yyyy hh:mm:ss a')}
                              </span>
                            </div>
                            <div className="text-sm">
                              <span className="text-slate-400">Total occurrences:</span>{' '}
                              <span className="text-orange-300 font-semibold">{alert.occurrence_count}</span>
                            </div>
                          </div>
                        </div>
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
            {agentFilter !== 'all' ? 'No active alerts for this agent' : 'No active alerts'}
          </div>
        )}
      </div>
    </div>
  )
}
