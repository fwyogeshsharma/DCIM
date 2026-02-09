import { useParams } from 'react-router-dom'
import { useAgent } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { LineChart, Line, AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell, RadialBarChart, RadialBar } from 'recharts'
import { Activity, Cpu, HardDrive, Wifi, TrendingUp, Clock, AlertTriangle, CheckCircle, XCircle, Gauge } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { Link } from 'react-router-dom'

export default function AgentAnalytics() {
  const { agentId } = useParams<{ agentId: string }>()
  const { data: agent, isLoading: agentLoading } = useAgent(agentId!)

  // Fetch metrics for different time ranges
  const { data: metrics1h } = useQuery({
    queryKey: ['metrics', agentId, '1h'],
    queryFn: () => api.getMetrics({ agent_id: agentId, time_range: '1h', limit: 100 }),
    enabled: !!agentId,
    refetchInterval: 30000,
  })

  const { data: metrics24h } = useQuery({
    queryKey: ['metrics', agentId, '24h'],
    queryFn: () => api.getMetrics({ agent_id: agentId, time_range: '24h', limit: 100 }),
    enabled: !!agentId,
  })

  if (agentLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading analytics...</div>
        </div>
      </div>
    )
  }

  if (!agent) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <XCircle className="w-16 h-16 text-red-400 mx-auto mb-4" />
          <div className="text-xl text-white">Agent not found</div>
        </div>
      </div>
    )
  }

  // Group metrics by type with better categorization
  const groupMetricsByType = (metrics: any[]) => {
    const grouped: Record<string, any[]> = {}

    metrics?.forEach(metric => {
      let type = metric.metric_type || metric.unit || 'other'

      // Enhanced categorization for disk metrics
      if (metric.unit === 'percent' || metric.unit === 'percentage') {
        // Check if it's disk related (look for drive letters or disk keywords)
        const metricStr = JSON.stringify(metric).toLowerCase()

        if (metricStr.includes('disk') || metricStr.includes('drive')) {
          // Try to extract drive letter
          const driveMatch = metricStr.match(/[a-z]:/gi)
          if (driveMatch && driveMatch[0]) {
            type = `disk_${driveMatch[0].toUpperCase()}_usage`
          } else {
            type = 'disk_usage'
          }
        } else if (metricStr.includes('cpu')) {
          type = 'cpu_percent'
        } else if (metricStr.includes('memory') || metricStr.includes('ram')) {
          type = 'memory_percent'
        } else {
          type = `${type}_percent`
        }
      }

      // Group CPU core metrics together
      if (type.includes('cpu') && !type.includes('percent')) {
        type = 'cpu_cores'
      }

      if (!grouped[type]) {
        grouped[type] = []
      }

      grouped[type].push({
        timestamp: new Date(metric.timestamp).getTime(),
        value: metric.value,
        unit: metric.unit,
        label: metric.metric_type || type,
      })
    })

    // Sort each group by timestamp
    Object.keys(grouped).forEach(key => {
      grouped[key].sort((a, b) => a.timestamp - b.timestamp)
    })

    return grouped
  }

  const groupedMetrics1h = groupMetricsByType(metrics1h || [])
  const groupedMetrics24h = groupMetricsByType(metrics24h || [])

  // Calculate aggregated stats
  const calculateStats = (metrics: any[]) => {
    if (!metrics || metrics.length === 0) return null

    const values = metrics.map(m => m.value)
    return {
      current: values[values.length - 1],
      avg: values.reduce((a, b) => a + b, 0) / values.length,
      min: Math.min(...values),
      max: Math.max(...values),
      count: values.length,
    }
  }

  // Chart colors
  const COLORS = {
    cpu: '#3b82f6',
    memory: '#10b981',
    disk: '#8b5cf6',
    network: '#f59e0b',
    temperature: '#ef4444',
    other: '#6366f1',
  }

  // Format timestamp for charts
  const formatTimestamp = (timestamp: number) => {
    const date = new Date(timestamp)
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  // Custom tooltip for charts
  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-slate-800 border border-white/20 rounded-lg p-3 shadow-lg">
          <p className="text-slate-300 text-sm">
            {new Date(payload[0].payload.timestamp).toLocaleString()}
          </p>
          {payload.map((entry: any, index: number) => (
            <p key={index} className="text-white font-semibold" style={{ color: entry.color }}>
              {entry.value.toFixed(2)} {entry.payload.unit || ''}
            </p>
          ))}
        </div>
      )
    }
    return null
  }

  // Render gauge for percentage metrics
  const renderGauge = (value: number, label: string, type: string) => {
    const gaugeData = [{
      name: label,
      value: value,
      fill: value > 90 ? '#ef4444' : value > 75 ? '#f59e0b' : '#10b981'
    }]

    return (
      <div className="flex flex-col items-center">
        <ResponsiveContainer width="100%" height={150}>
          <RadialBarChart
            cx="50%"
            cy="50%"
            innerRadius="60%"
            outerRadius="100%"
            barSize={15}
            data={gaugeData}
            startAngle={180}
            endAngle={0}
          >
            <RadialBar
              background
              dataKey="value"
              cornerRadius={10}
            />
            <text
              x="50%"
              y="50%"
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-white text-3xl font-bold"
            >
              {value.toFixed(1)}%
            </text>
          </RadialBarChart>
        </ResponsiveContainer>
        <p className="text-sm text-slate-400 mt-2 capitalize">{label.replace(/_/g, ' ')}</p>
      </div>
    )
  }

  // Check if metric is percentage-based
  const isPercentageMetric = (type: string, data: any[]) => {
    return data[0]?.unit === 'percent' || data[0]?.unit === 'percentage' || type.includes('percent') || type.includes('usage')
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <Link
              to="/app/agents"
              className="text-slate-400 hover:text-white transition-colors"
            >
              Agents
            </Link>
            <span className="text-slate-600">/</span>
            <Link
              to={`/app/agents/${agent.agent_id}`}
              className="text-slate-400 hover:text-white transition-colors"
            >
              {agent.hostname}
            </Link>
            <span className="text-slate-600">/</span>
            <span className="text-white">Analytics</span>
          </div>
          <h1 className="text-4xl font-bold text-white">Metrics Analytics</h1>
          <p className="text-slate-400 mt-2 text-lg">Real-time performance monitoring and historical analysis</p>
        </div>
        <div>
          <span className={`inline-flex items-center px-4 py-2 rounded-full text-sm font-medium border ${
            agent.status === 'online'
              ? 'text-green-400 bg-green-500/20 border-green-500/30'
              : 'text-red-400 bg-red-500/20 border-red-500/30'
          }`}>
            <Activity className="w-4 h-4 mr-2" />
            {agent.status.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-5 h-5 text-blue-400" />
            <p className="text-sm text-slate-400">Total Metrics</p>
          </div>
          <p className="text-3xl font-bold text-white">{agent.total_metrics.toLocaleString()}</p>
        </div>
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-5 h-5 text-yellow-400" />
            <p className="text-sm text-slate-400">Active Alerts</p>
          </div>
          <p className="text-3xl font-bold text-yellow-400">{agent.total_alerts}</p>
        </div>
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Clock className="w-5 h-5 text-green-400" />
            <p className="text-sm text-slate-400">Last Seen</p>
          </div>
          <p className="text-lg font-semibold text-white">
            {formatDistanceToNow(new Date(agent.last_seen), { addSuffix: true })}
          </p>
        </div>
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle className="w-5 h-5 text-purple-400" />
            <p className="text-sm text-slate-400">Metric Types</p>
          </div>
          <p className="text-3xl font-bold text-white">{Object.keys(groupedMetrics1h).length}</p>
        </div>
      </div>

      {/* System Health Gauges */}
      {Object.entries(groupedMetrics1h).some(([type, data]) => isPercentageMetric(type, data)) && (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
          <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
            <Gauge className="w-6 h-6 text-purple-400" />
            System Health Overview
          </h2>

          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
            {Object.entries(groupedMetrics1h)
              .filter(([type, data]) => isPercentageMetric(type, data))
              .map(([type, data]) => {
                const stats = calculateStats(data)
                if (!stats) return null

                return (
                  <div key={type} className="bg-slate-900/50 border border-white/5 rounded-lg p-4">
                    {renderGauge(stats.current, type, type)}
                    <div className="text-center mt-3 space-y-1">
                      <div className="text-xs text-slate-400">
                        Avg: {stats.avg.toFixed(1)}%
                      </div>
                      <div className="text-xs text-slate-500">
                        Range: {stats.min.toFixed(0)}-{stats.max.toFixed(0)}%
                      </div>
                    </div>
                  </div>
                )
              })}
          </div>
        </div>
      )}

      {/* Time Series Charts - Last 1 Hour */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
        <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
          <Activity className="w-6 h-6 text-blue-400" />
          Performance Metrics - Last Hour
        </h2>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {Object.entries(groupedMetrics1h)
            .filter(([type, data]) => type.startsWith('disk_') && isPercentageMetric(type, data))
            .map(([type, data]) => {
              const stats = calculateStats(data)
              return (
                <div key={type} className="bg-slate-900/50 border border-white/5 rounded-lg p-4">
                  {stats && renderGauge(stats.current, type, type)}
                  <div className="text-center mt-3 text-xs text-slate-400">
                    Avg: {stats?.avg.toFixed(1)}% | Max: {stats?.max.toFixed(1)}%
                  </div>
                </div>
              )
            })}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {Object.entries(groupedMetrics1h)
            .filter(([type, data]) => !type.startsWith('disk_') || !isPercentageMetric(type, data))
            .slice(0, 6)
            .map(([type, data]) => {
              const stats = calculateStats(data)
              const isPercent = isPercentageMetric(type, data)

              return (
                <div key={type} className="bg-slate-900/50 border border-white/5 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-white capitalize">{type.replace(/_/g, ' ')}</h3>
                    {stats && (
                      <div className="text-right">
                        <div className="text-2xl font-bold text-white">
                          {stats.current.toFixed(2)}{isPercent ? '%' : ` ${data[0]?.unit || ''}`}
                        </div>
                        <div className="text-xs text-slate-400">
                          Avg: {stats.avg.toFixed(2)} | Min: {stats.min.toFixed(2)} | Max: {stats.max.toFixed(2)}
                        </div>
                      </div>
                    )}
                  </div>
                  <ResponsiveContainer width="100%" height={200}>
                    <AreaChart data={data}>
                      <defs>
                        <linearGradient id={`gradient-${type}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={COLORS[type as keyof typeof COLORS] || COLORS.other} stopOpacity={0.3}/>
                          <stop offset="95%" stopColor={COLORS[type as keyof typeof COLORS] || COLORS.other} stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                      <XAxis
                        dataKey="timestamp"
                        tickFormatter={formatTimestamp}
                        stroke="#64748b"
                        style={{ fontSize: '12px' }}
                      />
                      <YAxis
                        stroke="#64748b"
                        style={{ fontSize: '12px' }}
                        domain={isPercent ? [0, 100] : ['auto', 'auto']}
                      />
                      <Tooltip content={<CustomTooltip />} />
                      <Area
                        type="monotone"
                        dataKey="value"
                        stroke={COLORS[type as keyof typeof COLORS] || COLORS.other}
                        fill={`url(#gradient-${type})`}
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )
            })}
        </div>

        {Object.keys(groupedMetrics1h).length === 0 && (
          <div className="text-center py-12 text-slate-400">
            <Activity className="w-16 h-16 mx-auto mb-4 text-slate-600" />
            <p className="text-lg">No metrics data available for the last hour</p>
            <p className="text-sm mt-2">Metrics will appear here once the agent starts reporting</p>
          </div>
        )}
      </div>

      {/* 24 Hour Trends */}
      {Object.keys(groupedMetrics24h).length > 0 && (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
          <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
            <TrendingUp className="w-6 h-6 text-green-400" />
            24 Hour Trends
          </h2>

          <div className="grid grid-cols-1 gap-6">
            {Object.entries(groupedMetrics24h).slice(0, 4).map(([type, data]) => (
              <div key={type} className="bg-slate-900/50 border border-white/5 rounded-lg p-4">
                <h3 className="text-lg font-semibold text-white capitalize mb-4">{type}</h3>
                <ResponsiveContainer width="100%" height={250}>
                  <LineChart data={data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(ts) => new Date(ts).toLocaleTimeString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit'
                      })}
                      stroke="#64748b"
                      style={{ fontSize: '12px' }}
                    />
                    <YAxis
                      stroke="#64748b"
                      style={{ fontSize: '12px' }}
                    />
                    <Tooltip content={<CustomTooltip />} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="value"
                      stroke={COLORS[type as keyof typeof COLORS] || COLORS.other}
                      strokeWidth={2}
                      dot={false}
                      name={`${type} (${data[0]?.unit || ''})`}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* All Metrics Table */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
        <h2 className="text-2xl font-bold text-white mb-6">All Metric Types</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Object.entries(groupedMetrics1h).map(([type, data]) => {
            const stats = calculateStats(data)
            return (
              <div key={type} className="bg-slate-900/50 border border-white/5 rounded-lg p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-medium text-slate-400 capitalize">{type}</h3>
                  <span className="text-xs text-slate-500">{data.length} samples</span>
                </div>
                {stats && (
                  <div className="space-y-1">
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">Current:</span>
                      <span className="text-sm font-mono text-white">{stats.current.toFixed(2)} {data[0]?.unit}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">Average:</span>
                      <span className="text-sm font-mono text-slate-300">{stats.avg.toFixed(2)} {data[0]?.unit}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">Range:</span>
                      <span className="text-sm font-mono text-slate-300">
                        {stats.min.toFixed(2)} - {stats.max.toFixed(2)}
                      </span>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
