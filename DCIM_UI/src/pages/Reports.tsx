import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { Zap, Thermometer, Activity, BarChart3, Download } from 'lucide-react'

const SERVER_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
  '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16',
]

function getServerColor(index: number, serverColor?: string): string {
  return serverColor || SERVER_COLORS[index % SERVER_COLORS.length]
}

function tempColor(temp: number | undefined): string {
  if (temp === undefined) return 'text-slate-400'
  if (temp < 45) return 'text-green-400'
  if (temp < 60) return 'text-yellow-400'
  return 'text-red-400'
}

export default function Reports() {
  const [timeRange, setTimeRange] = useState('24h')

  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
  })

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.getAgents(),
  })

  const { data: powerMetrics, isLoading: powerLoading } = useQuery({
    queryKey: ['report-power', timeRange],
    queryFn: () => api.getMetrics({ metric_type: 'power_consumption', time_range: timeRange, limit: 10000 }),
  })

  const { data: tempMetrics } = useQuery({
    queryKey: ['report-temp', timeRange],
    queryFn: () => api.getMetrics({ metric_type: 'temperature', time_range: timeRange, limit: 10000 }),
  })

  const { data: powerAggregated } = useQuery({
    queryKey: ['report-power-agg', timeRange],
    queryFn: () => api.getAggregatedMetrics({ metric_type: 'power_consumption', time_range: timeRange, interval: '1h' }),
  })

  const enabledServers = useMemo(
    () => (servers ?? []).filter((s) => s.enabled !== false),
    [servers]
  )

  const serverReports = useMemo(() => {
    return enabledServers.map((server, idx) => {
      const agentsForServer = (agents ?? []).filter((a) => a.server_id === server.id)
      const agentIds = new Set(agentsForServer.map((a) => a.agent_id))

      const serverPowerMetrics = (powerMetrics ?? []).filter((m) => agentIds.has(m.agent_id))
      const serverTempMetrics = (tempMetrics ?? []).filter((m) => agentIds.has(m.agent_id))

      const avgPower =
        serverPowerMetrics.length > 0
          ? Math.round((serverPowerMetrics.reduce((s, m) => s + m.value, 0) / serverPowerMetrics.length) * 10) / 10
          : 0
      const maxPower =
        serverPowerMetrics.length > 0 ? Math.max(...serverPowerMetrics.map((m) => m.value)) : 0

      const totalAgents = agentsForServer.length
      const onlineAgents = agentsForServer.filter((a) => a.status === 'online').length

      const avgTemp =
        serverTempMetrics.length > 0
          ? serverTempMetrics.reduce((s, m) => s + m.value, 0) / serverTempMetrics.length
          : undefined
      const maxTemp =
        serverTempMetrics.length > 0 ? Math.max(...serverTempMetrics.map((m) => m.value)) : undefined

      const estimatedPUE = 1.2
      const efficiencyScore = totalAgents > 0 ? (onlineAgents / totalAgents) * 100 : 0

      const color = getServerColor(idx, server.metadata?.color)

      // Mini power chart data (last 12 data points from powerMetrics for this server)
      const miniData = serverPowerMetrics
        .slice(-12)
        .map((m) => ({ v: m.value }))

      return {
        serverId: server.id ?? '',
        serverName: server.name,
        color,
        isOnline: server.health?.status === 'healthy',
        agentsForServer,
        totalAgents,
        onlineAgents,
        avgPower,
        maxPower,
        avgTemp,
        maxTemp,
        estimatedPUE,
        efficiencyScore,
        miniData,
      }
    })
  }, [enabledServers, agents, powerMetrics, tempMetrics])

  const chartData = useMemo(() => {
    if (!powerAggregated || !enabledServers.length) return []

    // Build a map: time_bucket -> { time: 'HH:mm', [serverName]: value }
    const agentToServer: Record<string, string> = {}
    for (const server of enabledServers) {
      for (const agent of (agents ?? []).filter((a) => a.server_id === server.id)) {
        agentToServer[agent.agent_id] = server.name
      }
    }

    const bucketMap: Record<string, Record<string, number[]>> = {}
    for (const m of powerAggregated) {
      const serverName = agentToServer[m.agent_id]
      if (!serverName) continue
      if (!bucketMap[m.time_bucket]) bucketMap[m.time_bucket] = {}
      if (!bucketMap[m.time_bucket][serverName]) bucketMap[m.time_bucket][serverName] = []
      bucketMap[m.time_bucket][serverName].push(m.avg_value)
    }

    return Object.entries(bucketMap)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([bucket, serverData]) => {
        const date = new Date(bucket)
        const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
        const entry: Record<string, string | number> = { time }
        for (const [sName, vals] of Object.entries(serverData)) {
          entry[sName] = Math.round(vals.reduce((s, v) => s + v, 0) / vals.length)
        }
        return entry
      })
  }, [powerAggregated, enabledServers, agents])

  const summaryStats = useMemo(() => {
    const totalPower = serverReports.reduce((s, r) => s + r.avgPower, 0)
    const tempsWithData = serverReports.filter((r) => r.avgTemp !== undefined)
    const avgTemp =
      tempsWithData.length > 0
        ? tempsWithData.reduce((s, r) => s + (r.avgTemp ?? 0), 0) / tempsWithData.length
        : undefined
    const totalAgents = serverReports.reduce((s, r) => s + r.totalAgents, 0)
    const onlineAgents = serverReports.reduce((s, r) => s + r.onlineAgents, 0)
    const fleetEfficiency = totalAgents > 0 ? (onlineAgents / totalAgents) * 100 : 0
    return { totalPower, avgTemp, fleetEfficiency }
  }, [serverReports])

  const exportCSV = () => {
    const rows = [
      ['Server', 'Agents', 'Online', 'Avg Power (W)', 'Max Power (W)', 'Avg Temp (°C)', 'Max Temp (°C)', 'Efficiency (%)', 'Est. PUE'],
    ]
    serverReports.forEach((r) =>
      rows.push([
        r.serverName,
        String(r.totalAgents),
        String(r.onlineAgents),
        String(r.avgPower),
        String(r.maxPower),
        r.avgTemp?.toFixed(1) ?? 'N/A',
        r.maxTemp?.toFixed(1) ?? 'N/A',
        r.efficiencyScore.toFixed(1),
        String(r.estimatedPUE),
      ])
    )
    const csv = rows.map((r) => r.join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `dcim-report-${timeRange}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (powerLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading reports...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-white">Power & Efficiency Reports</h1>
          <p className="text-slate-400 mt-2 text-lg">Per-server power consumption and efficiency metrics</p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <div className="flex items-center bg-slate-800 border border-white/10 rounded-lg p-1 gap-1">
            {(['24h', '7d', '30d'] as const).map((r) => (
              <button
                key={r}
                onClick={() => setTimeRange(r)}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-200 ${
                  timeRange === r
                    ? 'bg-blue-600 text-white shadow'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {r}
              </button>
            ))}
          </div>
          <button
            onClick={exportCSV}
            className="flex items-center gap-2 px-4 py-2 bg-slate-800 border border-white/10 rounded-lg text-sm text-slate-300 hover:text-white hover:border-white/20 transition-all duration-200"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-400">Total Power Draw</p>
              <p className="text-3xl font-bold mt-2 text-white">
                {summaryStats.totalPower.toFixed(1)}<span className="text-lg font-normal text-slate-400 ml-1">W</span>
              </p>
              <p className="text-xs text-slate-500 mt-1">across all servers</p>
            </div>
            <div className="bg-yellow-500/10 p-3 rounded-lg">
              <Zap className="h-6 w-6 text-yellow-500" />
            </div>
          </div>
        </div>

        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-400">Avg Temperature</p>
              <p className="text-3xl font-bold mt-2 text-white">
                {summaryStats.avgTemp !== undefined ? summaryStats.avgTemp.toFixed(1) : '—'}
                <span className="text-lg font-normal text-slate-400 ml-1">°C</span>
              </p>
              <p className="text-xs text-slate-500 mt-1">fleet average</p>
            </div>
            <div className="bg-orange-500/10 p-3 rounded-lg">
              <Thermometer className="h-6 w-6 text-orange-500" />
            </div>
          </div>
        </div>

        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-400">Fleet Efficiency</p>
              <p className="text-3xl font-bold mt-2 text-white">
                {summaryStats.fleetEfficiency.toFixed(1)}<span className="text-lg font-normal text-slate-400 ml-1">%</span>
              </p>
              <p className="text-xs text-slate-500 mt-1">agents online</p>
            </div>
            <div className="bg-green-500/10 p-3 rounded-lg">
              <Activity className="h-6 w-6 text-green-500" />
            </div>
          </div>
        </div>

        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-400">Estimated PUE</p>
              <p className="text-3xl font-bold mt-2 text-white">1.20</p>
              <p className="text-xs text-slate-500 mt-1">facility data unavailable</p>
            </div>
            <div className="bg-blue-500/10 p-3 rounded-lg">
              <BarChart3 className="h-6 w-6 text-blue-500" />
            </div>
          </div>
        </div>
      </div>

      {/* Per-server cards */}
      {serverReports.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {serverReports.map((report) => {
            const effColor =
              report.efficiencyScore >= 80
                ? 'bg-green-500'
                : report.efficiencyScore >= 50
                ? 'bg-yellow-500'
                : 'bg-red-500'

            return (
              <div
                key={report.serverId}
                className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden hover:border-white/20 transition-all duration-300"
                style={{ borderLeftColor: report.color, borderLeftWidth: 3 }}
              >
                <div className="p-5">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-white">{report.serverName}</h3>
                    <span
                      className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                        report.isOnline
                          ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                          : 'bg-red-500/20 text-red-400 border border-red-500/30'
                      }`}
                    >
                      {report.isOnline ? 'Online' : 'Offline'}
                    </span>
                  </div>

                  {/* Metrics row */}
                  <div className="grid grid-cols-4 gap-3 mb-4">
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Avg Power</p>
                      <p className="text-sm font-bold text-yellow-400">{report.avgPower}W</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Max Power</p>
                      <p className="text-sm font-bold text-orange-400">{report.maxPower.toFixed(1)}W</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Avg Temp</p>
                      <p className={`text-sm font-bold ${tempColor(report.avgTemp)}`}>
                        {report.avgTemp !== undefined ? `${report.avgTemp.toFixed(1)}°C` : '—'}
                      </p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Max Temp</p>
                      <p className={`text-sm font-bold ${tempColor(report.maxTemp)}`}>
                        {report.maxTemp !== undefined ? `${report.maxTemp.toFixed(1)}°C` : '—'}
                      </p>
                    </div>
                  </div>

                  {/* Efficiency progress bar */}
                  <div className="mb-4">
                    <div className="flex items-center justify-between text-xs mb-1.5">
                      <span className="text-slate-400">Efficiency</span>
                      <span className="text-slate-300 font-medium">
                        {report.onlineAgents}/{report.totalAgents} agents online ({report.efficiencyScore.toFixed(0)}%)
                      </span>
                    </div>
                    <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${effColor}`}
                        style={{ width: `${report.efficiencyScore}%` }}
                      />
                    </div>
                  </div>

                  {/* Mini power chart */}
                  {report.miniData.length > 1 && (
                    <div className="h-14">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={report.miniData}>
                          <Line
                            type="monotone"
                            dataKey="v"
                            stroke={report.color}
                            strokeWidth={1.5}
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                  {report.miniData.length <= 1 && (
                    <div className="h-14 flex items-center justify-center">
                      <p className="text-xs text-slate-600">No trend data</p>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-12 text-center">
          <BarChart3 className="w-12 h-12 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400">No enabled servers found</p>
          <p className="text-xs text-slate-500 mt-1">Add and enable servers to see per-server reports</p>
        </div>
      )}

      {/* Full-width power trend chart */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
        <h3 className="text-xl font-semibold text-white mb-1">Power Trend</h3>
        <p className="text-sm text-slate-400 mb-4">Avg power consumption per server over time</p>
        {chartData.length > 0 ? (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="time"
                  tick={{ fill: '#94a3b8', fontSize: 11 }}
                  axisLine={{ stroke: '#334155' }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: '#94a3b8', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={40}
                  tickFormatter={(v) => `${v}W`}
                />
                <Tooltip
                  contentStyle={{
                    background: '#1e293b',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 8,
                    fontSize: 12,
                    color: '#e2e8f0',
                  }}
                  formatter={(value: number) => [`${value}W`]}
                />
                {enabledServers.map((server, idx) => (
                  <Area
                    key={server.id}
                    type="monotone"
                    dataKey={server.name}
                    stroke={getServerColor(idx, server.metadata?.color)}
                    fill={getServerColor(idx, server.metadata?.color)}
                    fillOpacity={0.1}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="h-56 flex items-center justify-center">
            <p className="text-slate-500">No aggregated power data for this time range</p>
          </div>
        )}
      </div>

      {/* Temperature table */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden hover:border-white/20 transition-all duration-300">
        <div className="p-6 border-b border-white/10">
          <h3 className="text-xl font-semibold text-white">Temperature Summary</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/5">
                <th className="text-left px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Server</th>
                <th className="text-right px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Agent Count</th>
                <th className="text-right px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Avg Temp</th>
                <th className="text-right px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Max Temp</th>
                <th className="text-right px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {serverReports.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-6 py-8 text-center text-slate-500">No data available</td>
                </tr>
              )}
              {serverReports.map((report) => (
                <tr key={report.serverId} className="hover:bg-white/5 transition-colors">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: report.color }} />
                      <span className="font-medium text-white">{report.serverName}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right text-slate-300">{report.totalAgents}</td>
                  <td className={`px-6 py-4 text-right font-medium ${tempColor(report.avgTemp)}`}>
                    {report.avgTemp !== undefined ? `${report.avgTemp.toFixed(1)}°C` : 'N/A'}
                  </td>
                  <td className={`px-6 py-4 text-right font-medium ${tempColor(report.maxTemp)}`}>
                    {report.maxTemp !== undefined ? `${report.maxTemp.toFixed(1)}°C` : 'N/A'}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <span
                      className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                        report.isOnline
                          ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                          : 'bg-red-500/20 text-red-400 border border-red-500/30'
                      }`}
                    >
                      {report.isOnline ? 'Online' : 'Offline'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
