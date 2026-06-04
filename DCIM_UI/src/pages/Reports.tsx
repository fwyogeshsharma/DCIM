import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { usePrediction } from '@/hooks/usePredictions'
import { getDeviceTypeMeta } from '@/lib/deviceType'
import { classifyReading, energyMetricMeta, formatEnergyValue } from '@/lib/energyMetrics'
import type { EnergyReading } from '@/lib/types'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine,
} from 'recharts'
import {
  Zap, Thermometer, Activity, BarChart3, Download,
  BatteryCharging, Battery, DollarSign, Calendar, TrendingUp,
  Plus, CheckCircle, Clock, AlertTriangle, Gauge, Trash2, X,
} from 'lucide-react'

// ── Types ──────────────────────────────────────────────────────────────────────

type Tab = 'overview' | 'ups' | 'pdu' | 'generator' | 'energy' | 'forecast'

interface GeneratorTest {
  id: string
  scheduledDate: string
  durationMin: number
  notes: string
  status: 'scheduled' | 'completed' | 'failed'
  result?: string
}

// ── Helpers ────────────────────────────────────────────────────────────────────

const SERVER_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
  '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16',
]

function serverColor(index: number, override?: string) {
  return override || SERVER_COLORS[index % SERVER_COLORS.length]
}

function tempColor(t: number | undefined) {
  if (t === undefined) return 'text-slate-400'
  if (t < 45) return 'text-green-400'
  if (t < 60) return 'text-yellow-400'
  return 'text-red-400'
}

function batteryColor(pct: number) {
  if (pct >= 70) return 'text-green-400'
  if (pct >= 30) return 'text-yellow-400'
  return 'text-red-400'
}

function loadColor(pct: number) {
  if (pct < 60) return 'bg-green-500'
  if (pct < 80) return 'bg-yellow-500'
  return 'bg-red-500'
}

function genTests(): GeneratorTest[] {
  try {
    return JSON.parse(localStorage.getItem('dcim_gen_tests') || '[]')
  } catch {
    return []
  }
}

function saveTests(tests: GeneratorTest[]) {
  localStorage.setItem('dcim_gen_tests', JSON.stringify(tests))
}

function kwhFromWatts(avgWatts: number, hours: number) {
  return (avgWatts * hours) / 1000
}

// ── Tab button ─────────────────────────────────────────────────────────────────

interface TabBtnProps {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
}
function TabBtn({ active, onClick, icon, label }: TabBtnProps) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
        active ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white hover:bg-white/5'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

// ── Energy monitor card ──────────────────────────────────────────────────────

interface EnergyCardData {
  deviceId: string
  hostname: string
  mgmtIp: string | null
  status: 'online' | 'offline'
  lastTs: string
  activePower?: number
  panel: { label: string; unit: string; value: number }[]
  phases: { phase: string; voltage?: number; current?: number }[]
  circuits: { circuit: string; current?: number; power?: number; energy?: number }[]
  harmonics: { tag: string; value: number }[]
  imbalancePct: number
}

function EnergyMonitorCard({ card, timeRange }: { card: EnergyCardData; timeRange: string }) {
  // Active-power trend for this meter (panel total, tag='').
  const { data: trend } = useQuery({
    queryKey: ['energy-trend', card.deviceId, timeRange],
    queryFn: () => api.getEnergyTimeseries({
      device_id: card.deviceId,
      metric_name: 'energy.active_power_kw',
      time_range: timeRange,
      interval: '15 minutes',
    }),
    enabled: card.activePower !== undefined,
  })
  const trendData = (trend ?? []).map((p) => ({ v: Number(p.avg_value) }))

  return (
    <div className="bg-slate-800/50 border border-cyan-500/20 rounded-xl p-6 hover:border-cyan-500/30 transition-all">
      <div className="flex items-start justify-between mb-5">
        <div className="flex items-center gap-2">
          <Gauge className="w-4 h-4 text-cyan-400" />
          <div>
            <h3 className="text-base font-semibold text-white">{card.hostname}</h3>
            <p className="text-xs text-slate-500 mt-0.5">{card.mgmtIp || card.deviceId}</p>
          </div>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${card.status === 'online' ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30'}`}>
          {card.status === 'online' ? 'Online' : 'Offline'}
        </span>
      </div>

      {/* Panel scalars (active power headline + other panel-level readings) */}
      {(card.activePower !== undefined || card.panel.length > 0) && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          {card.activePower !== undefined && (
            <div className="bg-slate-900/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-500 mb-1">Active Power</p>
              <p className="text-sm font-bold text-amber-400">{formatEnergyValue(card.activePower, 'kW')}</p>
            </div>
          )}
          {card.panel.map((p) => (
            <div key={p.label} className="bg-slate-900/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-500 mb-1">{p.label}</p>
              <p className="text-sm font-bold text-cyan-400">{formatEnergyValue(p.value, p.unit)}</p>
            </div>
          ))}
        </div>
      )}

      {/* Per-phase voltage & current */}
      {card.phases.length > 0 && (
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium text-slate-400">Phases</p>
            {card.imbalancePct >= 10 && (
              <span className="flex items-center gap-1 text-xs text-yellow-400">
                <AlertTriangle className="w-3 h-3" /> {card.imbalancePct.toFixed(0)}% current imbalance
              </span>
            )}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {card.phases.map((ph) => (
              <div key={ph.phase} className="bg-slate-900/50 rounded-lg p-2 text-center">
                <p className="text-xs text-slate-500 mb-0.5">{ph.phase}</p>
                <p className="text-sm font-bold text-cyan-400">{ph.voltage != null ? formatEnergyValue(ph.voltage, 'V') : '—'}</p>
                <p className="text-xs text-purple-400">{ph.current != null ? formatEnergyValue(ph.current, 'A') : '—'}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Per-circuit (breaker) mapping */}
      {card.circuits.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-slate-400 mb-2">Circuits ({card.circuits.length})</p>
          <div className="max-h-56 overflow-y-auto rounded-lg border border-white/5">
            <table className="w-full text-xs">
              <thead className="bg-slate-900/60 text-slate-500 sticky top-0">
                <tr>
                  <th className="text-left px-3 py-1.5 font-medium">Circuit</th>
                  <th className="text-right px-3 py-1.5 font-medium">Current</th>
                  <th className="text-right px-3 py-1.5 font-medium">Power</th>
                  <th className="text-right px-3 py-1.5 font-medium">Energy</th>
                </tr>
              </thead>
              <tbody>
                {card.circuits.map((c) => (
                  <tr key={c.circuit} className="border-t border-white/5">
                    <td className="px-3 py-1.5 text-slate-300">{c.circuit}</td>
                    <td className="px-3 py-1.5 text-right text-purple-400">{c.current != null ? formatEnergyValue(c.current, 'A') : '—'}</td>
                    <td className="px-3 py-1.5 text-right text-amber-400">{c.power != null ? formatEnergyValue(c.power, 'kW') : '—'}</td>
                    <td className="px-3 py-1.5 text-right text-green-400">{c.energy != null ? formatEnergyValue(c.energy, 'kWh') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Current harmonics */}
      {card.harmonics.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-slate-400 mb-2">Current Harmonics</p>
          <div className="flex flex-wrap gap-2">
            {card.harmonics.map((h) => (
              <div key={h.tag} className="bg-slate-900/50 rounded px-2 py-1 text-xs">
                <span className="text-slate-500">{h.tag}</span>{' '}
                <span className="text-cyan-400 font-medium">{h.value.toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active-power trend */}
      {trendData.length > 1 && (
        <div className="h-16">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trendData} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
              <Area type="monotone" dataKey="v" stroke="#06b6d4" fill="#06b6d4" fillOpacity={0.1} strokeWidth={1.5} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────────

export default function Reports() {
  const [tab, setTab] = useState<Tab>('overview')
  const [timeRange, setTimeRange] = useState('24h')
  const [facilityPower, setFacilityPower] = useState<string>('')
  const [costPerKwh, setCostPerKwh] = useState<string>('0.12')
  const [tests, setTests] = useState<GeneratorTest[]>(genTests)
  const [showAddTest, setShowAddTest] = useState(false)
  const [newTest, setNewTest] = useState<Omit<GeneratorTest, 'id'>>({
    scheduledDate: '',
    durationMin: 30,
    notes: '',
    status: 'scheduled',
  })

  // ── Queries ─────────────────────────────────────────────────────────────────

  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
  })

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.getAgents(),
  })

  // Energy meter readings (energy_metrics table) — the per-panel / per-phase /
  // per-circuit / harmonic telemetry from energy_monitor devices, surfaced here
  // in circuit mapping. These devices are hidden from the network topology views.
  const { data: energyReadings } = useQuery({
    queryKey: ['energy-snapshot'],
    queryFn: () => api.getEnergySnapshot(),
    refetchInterval: 30000,
  })

  const { data: powerMetrics, isLoading: powerLoading } = useQuery({
    queryKey: ['report-power', timeRange],
    queryFn: () => api.getMetrics({ metric_type: 'power_consumption', time_range: timeRange, limit: 10000 }),
  })

  const { data: tempMetrics } = useQuery({
    queryKey: ['report-temp', timeRange],
    queryFn: () => api.getMetrics({ metric_type: 'temperature', time_range: timeRange, limit: 10000 }),
  })

  const { data: voltageMetrics } = useQuery({
    queryKey: ['report-voltage', timeRange],
    queryFn: () => api.getMetrics({ metric_type: 'power_voltage', time_range: timeRange, limit: 5000 }),
  })

  const { data: currentMetrics } = useQuery({
    queryKey: ['report-current', timeRange],
    queryFn: () => api.getMetrics({ metric_type: 'power_current', time_range: timeRange, limit: 5000 }),
  })

  const { data: powerAggregated } = useQuery({
    queryKey: ['report-power-agg', timeRange],
    queryFn: () => api.getAggregatedMetrics({ metric_type: 'power_consumption', time_range: timeRange, interval: '1h' }),
  })

  // ── Derived state ─────────────────────────────────────────────────────────────

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
      const maxPower = serverPowerMetrics.length > 0 ? Math.max(...serverPowerMetrics.map((m) => m.value)) : 0
      const totalAgents = agentsForServer.length
      const onlineAgents = agentsForServer.filter((a) => a.status === 'online').length
      const avgTemp =
        serverTempMetrics.length > 0
          ? serverTempMetrics.reduce((s, m) => s + m.value, 0) / serverTempMetrics.length
          : undefined
      const maxTemp = serverTempMetrics.length > 0 ? Math.max(...serverTempMetrics.map((m) => m.value)) : undefined
      const efficiencyScore = totalAgents > 0 ? (onlineAgents / totalAgents) * 100 : 0
      const color = serverColor(idx, server.metadata?.color)
      const miniData = serverPowerMetrics.slice(-12).map((m) => ({ v: m.value }))

      return {
        serverId: server.id ?? '',
        serverName: server.name,
        color,
        isOnline: server.health?.status === 'healthy',
        totalAgents, onlineAgents, avgPower, maxPower, avgTemp, maxTemp, efficiencyScore, miniData,
      }
    })
  }, [enabledServers, agents, powerMetrics, tempMetrics])

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

    const facilityW = parseFloat(facilityPower)
    const pue = facilityW > 0 && totalPower > 0 ? facilityW / totalPower : null
    const dcie = pue ? (1 / pue) * 100 : null

    return { totalPower, avgTemp, fleetEfficiency, pue, dcie }
  }, [serverReports, facilityPower])

  const chartData = useMemo(() => {
    if (!powerAggregated || !enabledServers.length) return []
    const agentToServer: Record<string, string> = {}
    for (const server of enabledServers) {
      for (const agent of (agents ?? []).filter((a) => a.server_id === server.id)) {
        agentToServer[agent.agent_id] = server.name
      }
    }
    const bucketMap: Record<string, Record<string, number[]>> = {}
    for (const m of powerAggregated) {
      const sn = agentToServer[m.agent_id]
      if (!sn) continue
      if (!bucketMap[m.time_bucket]) bucketMap[m.time_bucket] = {}
      if (!bucketMap[m.time_bucket][sn]) bucketMap[m.time_bucket][sn] = []
      bucketMap[m.time_bucket][sn].push(m.avg_value)
    }
    return Object.entries(bucketMap)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([bucket, serverData]) => {
        const d = new Date(bucket)
        const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
        const entry: Record<string, string | number> = { time }
        for (const [sn, vals] of Object.entries(serverData)) {
          entry[sn] = Math.round(vals.reduce((s, v) => s + v, 0) / vals.length)
        }
        return entry
      })
  }, [powerAggregated, enabledServers, agents])

  // UPS agents
  const upsAgents = useMemo(() => {
    return (agents ?? []).filter((a) => getDeviceTypeMeta(a.agent_id).category === 'UPS')
  }, [agents])

  // PDU agents
  const pduAgents = useMemo(() => {
    return (agents ?? []).filter((a) => getDeviceTypeMeta(a.agent_id).category === 'PDU')
  }, [agents])

  // Per-UPS metrics
  const upsCards = useMemo(() => {
    return upsAgents.map((ups) => {
      const powerVals = (powerMetrics ?? []).filter((m) => m.agent_id === ups.agent_id)
      const voltVals = (voltageMetrics ?? []).filter((m) => m.agent_id === ups.agent_id)
      const currVals = (currentMetrics ?? []).filter((m) => m.agent_id === ups.agent_id)

      const avgLoad = powerVals.length > 0
        ? powerVals.reduce((s, m) => s + m.value, 0) / powerVals.length
        : 0
      const maxLoad = powerVals.length > 0 ? Math.max(...powerVals.map((m) => m.value)) : 0
      const avgVolt = voltVals.length > 0
        ? voltVals.reduce((s, m) => s + m.value, 0) / voltVals.length
        : 0
      const avgCurr = currVals.length > 0
        ? currVals.reduce((s, m) => s + m.value, 0) / currVals.length
        : 0

      // Simulate battery % from load (in real system this comes from SNMP OID)
      const batteryPct = ups.status === 'online' ? Math.max(20, 100 - (avgLoad / 20)) : 0
      const runtimeMin = Math.round(batteryPct * 2.5) // rough estimate

      const trend = powerVals.slice(-12).map((m) => ({ v: m.value }))

      return { ups, avgLoad, maxLoad, avgVolt, avgCurr, batteryPct, runtimeMin, trend }
    })
  }, [upsAgents, powerMetrics, voltageMetrics, currentMetrics])

  // Per-PDU metrics
  const pduCards = useMemo(() => {
    return pduAgents.map((pdu) => {
      const powerVals = (powerMetrics ?? []).filter((m) => m.agent_id === pdu.agent_id)
      const voltVals = (voltageMetrics ?? []).filter((m) => m.agent_id === pdu.agent_id)
      const currVals = (currentMetrics ?? []).filter((m) => m.agent_id === pdu.agent_id)

      const avgPower = powerVals.length > 0
        ? powerVals.reduce((s, m) => s + m.value, 0) / powerVals.length : 0
      const maxPower = powerVals.length > 0 ? Math.max(...powerVals.map((m) => m.value)) : 0
      const avgVolt = voltVals.length > 0
        ? voltVals.reduce((s, m) => s + m.value, 0) / voltVals.length : 0
      const avgCurr = currVals.length > 0
        ? currVals.reduce((s, m) => s + m.value, 0) / currVals.length : 0

      // Rated capacity in watts (typical PDU 20A @ 240V = 4800W)
      const ratedW = 4800
      const loadPct = ratedW > 0 ? Math.min(100, (avgPower / ratedW) * 100) : 0

      const trend = powerVals.slice(-12).map((m) => ({ v: m.value }))

      return { pdu, avgPower, maxPower, avgVolt, avgCurr, loadPct, ratedW, trend }
    })
  }, [pduAgents, powerMetrics, voltageMetrics, currentMetrics])

  // Energy monitor cards — group the latest readings by device, then carve them
  // into panel scalars, per-phase, per-circuit and harmonic breakdowns.
  const energyCards = useMemo(() => {
    const byDevice = new Map<string, EnergyReading[]>()
    for (const r of energyReadings ?? []) {
      const arr = byDevice.get(r.device_id) ?? []
      arr.push(r)
      byDevice.set(r.device_id, arr)
    }

    return [...byDevice.entries()].map(([deviceId, readings]) => {
      const first = readings[0]
      const lastTs = readings.reduce((m, r) => (r.ts > m ? r.ts : m), readings[0]?.ts ?? '')

      // Panel scalars (tag=''), excluding the active-power headline shown above.
      const panel = readings
        .filter((r) => classifyReading(r) === 'panel' && r.metric_name !== 'energy.active_power_kw')
        .map((r) => ({ ...energyMetricMeta(r.metric_name), value: r.value }))
        .sort((a, b) => a.label.localeCompare(b.label))

      // Per-phase voltage & current.
      const phaseMap = new Map<string, Record<string, number>>()
      readings.filter((r) => classifyReading(r) === 'phase').forEach((r) => {
        const row = phaseMap.get(r.phase) ?? {}
        row[r.metric_name] = r.value
        phaseMap.set(r.phase, row)
      })
      const phases = [...phaseMap.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([phase, vals]) => ({
          phase,
          voltage: vals['energy.voltage_v'],
          current: vals['energy.current_a'],
        }))

      // Per-circuit (breaker) current / power / energy.
      const circuitMap = new Map<string, Record<string, number>>()
      readings.filter((r) => classifyReading(r) === 'circuit').forEach((r) => {
        const row = circuitMap.get(r.circuit) ?? {}
        row[r.metric_name] = r.value
        circuitMap.set(r.circuit, row)
      })
      const circuits = [...circuitMap.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([circuit, vals]) => ({
          circuit,
          current: vals['energy.circuit_current_a'],
          power: vals['energy.circuit_power_kw'],
          energy: vals['energy.circuit_energy_kwh'],
        }))

      // Harmonics (H3, H5, …) sorted by order.
      const harmonics = readings
        .filter((r) => classifyReading(r) === 'harmonic')
        .map((r) => ({ tag: r.tag, value: r.value }))
        .sort((a, b) => (parseInt(a.tag.slice(1)) || 0) - (parseInt(b.tag.slice(1)) || 0))

      const activePower = readings.find(
        (r) => r.metric_name === 'energy.active_power_kw' && !r.circuit && !r.phase
      )?.value

      // Phase current imbalance (max−min)/max — flags an unbalanced load.
      const phaseCurrents = phases.map((p) => p.current).filter((v): v is number => v != null)
      const imbalancePct = phaseCurrents.length > 1 && Math.max(...phaseCurrents) > 0
        ? ((Math.max(...phaseCurrents) - Math.min(...phaseCurrents)) / Math.max(...phaseCurrents)) * 100
        : 0

      return {
        deviceId,
        hostname: first?.hostname ?? deviceId,
        mgmtIp: first?.mgmt_ip ?? null,
        status: first?.status ?? 'offline',
        lastTs,
        activePower,
        panel,
        phases,
        circuits,
        harmonics,
        imbalancePct,
      }
    }).sort((a, b) => a.hostname.localeCompare(b.hostname))
  }, [energyReadings])

  // Energy cost
  const energyStats = useMemo(() => {
    const rate = parseFloat(costPerKwh) || 0.12
    const hours = timeRange === '24h' ? 24 : timeRange === '7d' ? 168 : 720
    const totalAvgW = summaryStats.totalPower
    const kwh = kwhFromWatts(totalAvgW, hours)
    const cost = kwh * rate
    const monthlyKwh = kwhFromWatts(totalAvgW, 720)
    const monthlyCost = monthlyKwh * rate
    const annualCost = monthlyCost * 12

    const costTrend = chartData.map((d) => {
      const totalW = enabledServers.reduce((s, srv) => s + ((d[srv.name] as number) || 0), 0)
      return { time: d.time as string, cost: parseFloat((kwhFromWatts(totalW, 1) * rate).toFixed(4)) }
    })

    return { kwh, cost, monthlyKwh, monthlyCost, annualCost, costTrend, rate }
  }, [summaryStats.totalPower, costPerKwh, timeRange, chartData, enabledServers])

  // AI forecast: use first server's power data as representative
  const forecastHistory = useMemo(() => {
    return (powerAggregated ?? []).slice(-30).map((m) => ({
      timestamp: m.time_bucket,
      value: m.avg_value,
      agent_id: m.agent_id,
    }))
  }, [powerAggregated])

  const firstAgentId = forecastHistory[0]?.agent_id ?? ''
  const { data: forecast, isLoading: forecastLoading } = usePrediction(
    firstAgentId,
    'power_consumption',
    forecastHistory as any,
    forecastHistory.length >= 7
  )

  const forecastChartData = useMemo(() => {
    if (!forecast) return []
    return forecast.predictions.slice(0, 14).map((p) => ({
      time: new Date(p.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      predicted: Math.round(p.value),
      lower: Math.round(p.lower_bound ?? p.value * 0.9),
      upper: Math.round(p.upper_bound ?? p.value * 1.1),
    }))
  }, [forecast])

  // ── Generator test handlers ────────────────────────────────────────────────

  function addTest() {
    if (!newTest.scheduledDate) return
    const updated = [...tests, { ...newTest, id: crypto.randomUUID() }]
    setTests(updated)
    saveTests(updated)
    setShowAddTest(false)
    setNewTest({ scheduledDate: '', durationMin: 30, notes: '', status: 'scheduled' })
  }

  function updateTestStatus(id: string, status: GeneratorTest['status'], result?: string) {
    const updated = tests.map((t) => (t.id === id ? { ...t, status, result } : t))
    setTests(updated)
    saveTests(updated)
  }

  function deleteTest(id: string) {
    const updated = tests.filter((t) => t.id !== id)
    setTests(updated)
    saveTests(updated)
  }

  // ── CSV export ────────────────────────────────────────────────────────────

  function exportCSV() {
    const rows = [['Server', 'Avg Power (W)', 'Max Power (W)', 'Avg Temp (°C)', 'Efficiency (%)']]
    serverReports.forEach((r) =>
      rows.push([r.serverName, String(r.avgPower), String(r.maxPower.toFixed(1)), r.avgTemp?.toFixed(1) ?? 'N/A', r.efficiencyScore.toFixed(1)])
    )
    const csv = rows.map((r) => r.join(',')).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    a.download = `power-mgmt-${timeRange}.csv`
    a.click()
  }

  // ── Loading ────────────────────────────────────────────────────────────────

  if (powerLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading power data...</div>
        </div>
      </div>
    )
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-white">Power Management</h1>
          <p className="text-slate-400 mt-1 text-lg">Monitor, model, and forecast your data centre power</p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <div className="flex items-center bg-slate-800 border border-white/10 rounded-lg p-1 gap-1">
            {(['24h', '7d', '30d'] as const).map((r) => (
              <button
                key={r}
                onClick={() => setTimeRange(r)}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-200 ${
                  timeRange === r ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'
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
            Export
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 bg-slate-800/30 border border-white/10 rounded-xl p-2">
        <TabBtn active={tab === 'overview'} onClick={() => setTab('overview')} icon={<Gauge className="w-4 h-4" />} label="PUE / DCiE" />
        <TabBtn active={tab === 'ups'} onClick={() => setTab('ups')} icon={<BatteryCharging className="w-4 h-4" />} label="UPS Health" />
        <TabBtn active={tab === 'pdu'} onClick={() => setTab('pdu')} icon={<Zap className="w-4 h-4" />} label="PDU Circuits" />
        <TabBtn active={tab === 'generator'} onClick={() => setTab('generator')} icon={<Calendar className="w-4 h-4" />} label="Generator Tests" />
        <TabBtn active={tab === 'energy'} onClick={() => setTab('energy')} icon={<DollarSign className="w-4 h-4" />} label="Energy Cost" />
        <TabBtn active={tab === 'forecast'} onClick={() => setTab('forecast')} icon={<TrendingUp className="w-4 h-4" />} label="AI Forecast" />
      </div>

      {/* ── TAB: Overview (PUE / DCiE) ─────────────────────────────────────── */}
      {tab === 'overview' && (
        <div className="space-y-6">
          {/* PUE input */}
          <div className="bg-slate-800/50 border border-white/10 rounded-xl p-5">
            <p className="text-sm font-medium text-slate-300 mb-3">
              Facility Total Power Input <span className="text-slate-500 font-normal">(required for live PUE/DCiE)</span>
            </p>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={0}
                placeholder="e.g. 12000"
                value={facilityPower}
                onChange={(e) => setFacilityPower(e.target.value)}
                className="w-48 bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
              />
              <span className="text-slate-400 text-sm">Watts (W)</span>
              {facilityPower && <span className="text-xs text-slate-500">IT load: {summaryStats.totalPower.toFixed(1)} W</span>}
            </div>
          </div>

          {/* Summary KPI cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">Total IT Power</p>
                  <p className="text-3xl font-bold mt-2 text-white">
                    {summaryStats.totalPower.toFixed(1)}<span className="text-lg font-normal text-slate-400 ml-1">W</span>
                  </p>
                  <p className="text-xs text-slate-500 mt-1">across all servers</p>
                </div>
                <div className="bg-yellow-500/10 p-3 rounded-lg"><Zap className="h-6 w-6 text-yellow-500" /></div>
              </div>
            </div>

            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">PUE</p>
                  <p className="text-3xl font-bold mt-2 text-white">
                    {summaryStats.pue !== null ? summaryStats.pue.toFixed(2) : '—'}
                  </p>
                  <p className="text-xs text-slate-500 mt-1">
                    {summaryStats.pue === null ? 'enter facility power above' : summaryStats.pue <= 1.2 ? 'excellent' : summaryStats.pue <= 1.5 ? 'average' : 'needs improvement'}
                  </p>
                </div>
                <div className="bg-blue-500/10 p-3 rounded-lg"><Gauge className="h-6 w-6 text-blue-500" /></div>
              </div>
            </div>

            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">DCiE</p>
                  <p className="text-3xl font-bold mt-2 text-white">
                    {summaryStats.dcie !== null ? summaryStats.dcie.toFixed(1) : '—'}
                    {summaryStats.dcie !== null && <span className="text-lg font-normal text-slate-400 ml-1">%</span>}
                  </p>
                  <p className="text-xs text-slate-500 mt-1">data centre infrastructure efficiency</p>
                </div>
                <div className="bg-green-500/10 p-3 rounded-lg"><Activity className="h-6 w-6 text-green-500" /></div>
              </div>
            </div>

            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">Avg Temperature</p>
                  <p className="text-3xl font-bold mt-2 text-white">
                    {summaryStats.avgTemp !== undefined ? summaryStats.avgTemp.toFixed(1) : '—'}
                    <span className="text-lg font-normal text-slate-400 ml-1">°C</span>
                  </p>
                  <p className="text-xs text-slate-500 mt-1">fleet average</p>
                </div>
                <div className="bg-orange-500/10 p-3 rounded-lg"><Thermometer className="h-6 w-6 text-orange-500" /></div>
              </div>
            </div>
          </div>

          {/* PUE rating bar */}
          {summaryStats.pue !== null && (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-base font-semibold text-white">PUE Rating</h3>
                <span className={`text-sm font-medium ${summaryStats.pue <= 1.2 ? 'text-green-400' : summaryStats.pue <= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                  {summaryStats.pue <= 1.2 ? 'Excellent' : summaryStats.pue <= 1.5 ? 'Average' : 'Poor'}
                </span>
              </div>
              <div className="relative h-4 bg-gradient-to-r from-green-500 via-yellow-500 to-red-500 rounded-full opacity-30" />
              <div className="relative -mt-4 h-4 flex items-center">
                <div
                  className="absolute w-4 h-4 rounded-full bg-white border-2 border-slate-900 shadow-lg"
                  style={{ left: `${Math.min(100, Math.max(0, ((summaryStats.pue - 1) / 1.5) * 100))}%` }}
                />
              </div>
              <div className="flex justify-between text-xs text-slate-500 mt-2">
                <span>1.0 (Perfect)</span><span>1.5</span><span>2.0+</span>
              </div>
            </div>
          )}

          {/* Per-server cards */}
          {serverReports.length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {serverReports.map((r) => {
                const effColor = r.efficiencyScore >= 80 ? 'bg-green-500' : r.efficiencyScore >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                return (
                  <div key={r.serverId} className="bg-slate-800/50 border border-white/10 rounded-xl overflow-hidden hover:border-white/20 transition-all duration-300" style={{ borderLeftColor: r.color, borderLeftWidth: 3 }}>
                    <div className="p-5">
                      <div className="flex items-center justify-between mb-4">
                        <h3 className="text-lg font-semibold text-white">{r.serverName}</h3>
                        <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${r.isOnline ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30'}`}>
                          {r.isOnline ? 'Online' : 'Offline'}
                        </span>
                      </div>
                      <div className="grid grid-cols-4 gap-3 mb-4">
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-1">Avg Power</p>
                          <p className="text-sm font-bold text-yellow-400">{r.avgPower}W</p>
                        </div>
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-1">Max Power</p>
                          <p className="text-sm font-bold text-orange-400">{r.maxPower.toFixed(1)}W</p>
                        </div>
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-1">Avg Temp</p>
                          <p className={`text-sm font-bold ${tempColor(r.avgTemp)}`}>{r.avgTemp !== undefined ? `${r.avgTemp.toFixed(1)}°C` : '—'}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-1">Max Temp</p>
                          <p className={`text-sm font-bold ${tempColor(r.maxTemp)}`}>{r.maxTemp !== undefined ? `${r.maxTemp.toFixed(1)}°C` : '—'}</p>
                        </div>
                      </div>
                      <div className="mb-3">
                        <div className="flex items-center justify-between text-xs mb-1.5">
                          <span className="text-slate-400">Efficiency</span>
                          <span className="text-slate-300">{r.onlineAgents}/{r.totalAgents} agents ({r.efficiencyScore.toFixed(0)}%)</span>
                        </div>
                        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                          <div className={`h-full rounded-full transition-all duration-500 ${effColor}`} style={{ width: `${r.efficiencyScore}%` }} />
                        </div>
                      </div>
                      {r.miniData.length > 1 ? (
                        <div className="h-12">
                          <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={r.miniData}>
                              <Line type="monotone" dataKey="v" stroke={r.color} strokeWidth={1.5} dot={false} />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      ) : (
                        <div className="h-12 flex items-center justify-center"><p className="text-xs text-slate-600">No trend data</p></div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Power trend chart */}
          <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
            <h3 className="text-xl font-semibold text-white mb-1">Power Trend</h3>
            <p className="text-sm text-slate-400 mb-4">Avg power consumption per server over time</p>
            {chartData.length > 0 ? (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <XAxis dataKey="time" tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={{ stroke: '#334155' }} tickLine={false} />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} width={40} tickFormatter={(v) => `${v}W`} />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#e2e8f0' }} formatter={(v: number) => [`${v}W`]} />
                    {enabledServers.map((s, idx) => (
                      <Area key={s.id} type="monotone" dataKey={s.name} stroke={serverColor(idx, s.metadata?.color)} fill={serverColor(idx, s.metadata?.color)} fillOpacity={0.1} strokeWidth={2} dot={false} connectNulls />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="h-56 flex items-center justify-center"><p className="text-slate-500">No aggregated data for this time range</p></div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: UPS Health ────────────────────────────────────────────────── */}
      {tab === 'ups' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">UPS Health Monitor</h2>
              <p className="text-sm text-slate-400 mt-0.5">{upsCards.length} UPS unit{upsCards.length !== 1 ? 's' : ''} detected</p>
            </div>
          </div>

          {upsCards.length === 0 ? (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-12 text-center">
              <BatteryCharging className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No UPS devices detected</p>
              <p className="text-xs text-slate-500 mt-1">UPS agents use agent IDs containing "UPS" (e.g. dc1-rack1-UPS01)</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {upsCards.map(({ ups, avgLoad, maxLoad, avgVolt, avgCurr, batteryPct, runtimeMin, trend }) => (
                <div key={ups.agent_id} className="bg-slate-800/50 border border-lime-500/20 rounded-xl p-6 hover:border-lime-500/30 transition-all">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h3 className="text-base font-semibold text-white">{ups.hostname || ups.agent_id}</h3>
                      <p className="text-xs text-slate-500 mt-0.5">{ups.agent_id}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${ups.status === 'online' ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30'}`}>
                        {ups.status === 'online' ? 'Online' : 'Offline'}
                      </span>
                    </div>
                  </div>

                  {/* Battery meter */}
                  <div className="mb-4">
                    <div className="flex items-center justify-between text-sm mb-2">
                      <span className="text-slate-400 flex items-center gap-1.5"><Battery className="w-4 h-4" />Battery</span>
                      <span className={`font-bold ${batteryColor(batteryPct)}`}>{batteryPct.toFixed(0)}%</span>
                    </div>
                    <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${batteryPct >= 70 ? 'bg-green-500' : batteryPct >= 30 ? 'bg-yellow-500' : 'bg-red-500'}`}
                        style={{ width: `${batteryPct}%` }}
                      />
                    </div>
                    <p className="text-xs text-slate-500 mt-1.5">Est. runtime: {runtimeMin} min</p>
                  </div>

                  {/* Metrics */}
                  <div className="grid grid-cols-4 gap-3 mb-4">
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Avg Load</p>
                      <p className="text-sm font-bold text-yellow-400">{avgLoad.toFixed(1)}W</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Peak Load</p>
                      <p className="text-sm font-bold text-orange-400">{maxLoad.toFixed(1)}W</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Voltage</p>
                      <p className="text-sm font-bold text-cyan-400">{avgVolt > 0 ? `${avgVolt.toFixed(0)}V` : '—'}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-slate-500 mb-1">Current</p>
                      <p className="text-sm font-bold text-purple-400">{avgCurr > 0 ? `${avgCurr.toFixed(1)}A` : '—'}</p>
                    </div>
                  </div>

                  {/* Load trend */}
                  {trend.length > 1 ? (
                    <div className="h-14">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={trend}>
                          <Line type="monotone" dataKey="v" stroke="#84cc16" strokeWidth={1.5} dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <div className="h-14 flex items-center justify-center"><p className="text-xs text-slate-600">No load trend</p></div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── TAB: PDU Circuits ─────────────────────────────────────────────── */}
      {tab === 'pdu' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-semibold text-white">PDU Circuit Mapping</h2>
            <p className="text-sm text-slate-400 mt-0.5">{pduCards.length} PDU{pduCards.length !== 1 ? 's' : ''} detected</p>
          </div>

          {pduCards.length === 0 ? (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-12 text-center">
              <Zap className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No PDU devices detected</p>
              <p className="text-xs text-slate-500 mt-1">PDU agents use agent IDs containing "PDU" or "FPDU"</p>
            </div>
          ) : (
            <div className="space-y-4">
              {pduCards.map(({ pdu, avgPower, maxPower, avgVolt, avgCurr, loadPct, ratedW, trend }) => (
                <div key={pdu.agent_id} className="bg-slate-800/50 border border-amber-500/20 rounded-xl p-6 hover:border-amber-500/30 transition-all">
                  <div className="flex items-start justify-between mb-5">
                    <div>
                      <h3 className="text-base font-semibold text-white">{pdu.hostname || pdu.agent_id}</h3>
                      <p className="text-xs text-slate-500 mt-0.5">{pdu.agent_id}</p>
                    </div>
                    <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${pdu.status === 'online' ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30'}`}>
                      {pdu.status === 'online' ? 'Online' : 'Offline'}
                    </span>
                  </div>

                  {/* Circuit load bar */}
                  <div className="mb-5">
                    <div className="flex items-center justify-between text-sm mb-2">
                      <span className="text-slate-400">Circuit Load</span>
                      <div className="flex items-center gap-3">
                        <span className="text-slate-300 font-medium">{avgPower.toFixed(1)} W</span>
                        <span className={`font-bold ${loadPct >= 80 ? 'text-red-400' : loadPct >= 60 ? 'text-yellow-400' : 'text-green-400'}`}>{loadPct.toFixed(1)}%</span>
                      </div>
                    </div>
                    <div className="h-4 bg-slate-700 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all duration-500 ${loadColor(loadPct)}`} style={{ width: `${loadPct}%` }} />
                    </div>
                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                      <span>0 W</span>
                      <span>Rated: {ratedW.toLocaleString()} W</span>
                    </div>
                  </div>

                  {/* Alert if overloaded */}
                  {loadPct >= 80 && (
                    <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-4 text-sm text-red-400">
                      <AlertTriangle className="w-4 h-4 flex-shrink-0" />
                      Circuit exceeds 80% rated capacity — risk of tripping
                    </div>
                  )}

                  {/* Electrical readings */}
                  <div className="grid grid-cols-4 gap-3 mb-4">
                    <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                      <p className="text-xs text-slate-500 mb-1">Avg Power</p>
                      <p className="text-sm font-bold text-amber-400">{avgPower.toFixed(1)} W</p>
                    </div>
                    <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                      <p className="text-xs text-slate-500 mb-1">Peak Power</p>
                      <p className="text-sm font-bold text-orange-400">{maxPower.toFixed(1)} W</p>
                    </div>
                    <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                      <p className="text-xs text-slate-500 mb-1">Voltage</p>
                      <p className="text-sm font-bold text-cyan-400">{avgVolt > 0 ? `${avgVolt.toFixed(0)} V` : '—'}</p>
                    </div>
                    <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                      <p className="text-xs text-slate-500 mb-1">Current</p>
                      <p className="text-sm font-bold text-purple-400">{avgCurr > 0 ? `${avgCurr.toFixed(2)} A` : '—'}</p>
                    </div>
                  </div>

                  {/* Power trend */}
                  {trend.length > 1 ? (
                    <div className="h-16">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={trend} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
                          <Area type="monotone" dataKey="v" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.1} strokeWidth={1.5} dot={false} />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <div className="h-16 flex items-center justify-center"><p className="text-xs text-slate-600">No trend data</p></div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Energy Monitors — devices with device_role "energy_monitor", hidden
              from the network topology and surfaced here for circuit mapping.
              Driven by the energy_metrics table: panel / phase / circuit / harmonics. */}
          {energyCards.length > 0 && (
            <div className="space-y-4 pt-4">
              <div>
                <h2 className="text-xl font-semibold text-white">Energy Monitors</h2>
                <p className="text-sm text-slate-400 mt-0.5">{energyCards.length} energy monitor{energyCards.length !== 1 ? 's' : ''} detected</p>
              </div>

              {energyCards.map((card) => (
                <EnergyMonitorCard key={card.deviceId} card={card} timeRange={timeRange} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── TAB: Generator Tests ──────────────────────────────────────────── */}
      {tab === 'generator' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Generator Test Scheduling</h2>
              <p className="text-sm text-slate-400 mt-0.5">Plan and track periodic generator load tests</p>
            </div>
            <button
              onClick={() => setShowAddTest(true)}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all"
            >
              <Plus className="w-4 h-4" />
              Schedule Test
            </button>
          </div>

          {/* Add test modal */}
          {showAddTest && (
            <div className="bg-slate-800/80 border border-blue-500/30 rounded-xl p-6 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-white">New Generator Test</h3>
                <button onClick={() => setShowAddTest(false)} className="text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Scheduled Date & Time</label>
                  <input
                    type="datetime-local"
                    value={newTest.scheduledDate}
                    onChange={(e) => setNewTest((p) => ({ ...p, scheduledDate: e.target.value }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Duration (minutes)</label>
                  <input
                    type="number"
                    min={5}
                    value={newTest.durationMin}
                    onChange={(e) => setNewTest((p) => ({ ...p, durationMin: parseInt(e.target.value) || 30 }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Notes</label>
                <textarea
                  value={newTest.notes}
                  onChange={(e) => setNewTest((p) => ({ ...p, notes: e.target.value }))}
                  placeholder="Transfer switch test, load bank connected..."
                  rows={2}
                  className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 resize-none"
                />
              </div>
              <div className="flex gap-3">
                <button onClick={addTest} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">
                  Schedule
                </button>
                <button onClick={() => setShowAddTest(false)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm text-slate-300 transition-all">
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Test stats */}
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: 'Scheduled', count: tests.filter((t) => t.status === 'scheduled').length, color: 'text-blue-400', bg: 'bg-blue-500/10' },
              { label: 'Completed', count: tests.filter((t) => t.status === 'completed').length, color: 'text-green-400', bg: 'bg-green-500/10' },
              { label: 'Failed', count: tests.filter((t) => t.status === 'failed').length, color: 'text-red-400', bg: 'bg-red-500/10' },
            ].map(({ label, count, color, bg }) => (
              <div key={label} className={`${bg} border border-white/10 rounded-xl p-4 text-center`}>
                <p className={`text-2xl font-bold ${color}`}>{count}</p>
                <p className="text-xs text-slate-400 mt-1">{label}</p>
              </div>
            ))}
          </div>

          {/* Test list */}
          {tests.length === 0 ? (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-12 text-center">
              <Calendar className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No tests scheduled yet</p>
            </div>
          ) : (
            <div className="space-y-3">
              {[...tests].sort((a, b) => a.scheduledDate.localeCompare(b.scheduledDate)).map((t) => (
                <div key={t.id} className="bg-slate-800/50 border border-white/10 rounded-xl p-5 hover:border-white/20 transition-all">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 mb-1">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${
                          t.status === 'scheduled' ? 'bg-blue-500/20 text-blue-400 border-blue-500/30' :
                          t.status === 'completed' ? 'bg-green-500/20 text-green-400 border-green-500/30' :
                          'bg-red-500/20 text-red-400 border-red-500/30'
                        }`}>{t.status}</span>
                        <span className="text-sm font-medium text-white">
                          {new Date(t.scheduledDate).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                        </span>
                        <span className="text-xs text-slate-500 flex items-center gap-1"><Clock className="w-3 h-3" />{t.durationMin} min</span>
                      </div>
                      {t.notes && <p className="text-sm text-slate-400 truncate">{t.notes}</p>}
                      {t.result && <p className="text-xs text-slate-500 mt-1 italic">{t.result}</p>}
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {t.status === 'scheduled' && (
                        <>
                          <button onClick={() => updateTestStatus(t.id, 'completed', 'Passed — transfer switch operated normally')} className="p-1.5 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-400 transition-all" title="Mark completed">
                            <CheckCircle className="w-4 h-4" />
                          </button>
                          <button onClick={() => updateTestStatus(t.id, 'failed', 'Failed — check log for details')} className="p-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-all" title="Mark failed">
                            <AlertTriangle className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      <button onClick={() => deleteTest(t.id)} className="p-1.5 rounded-lg bg-slate-700/50 hover:bg-slate-700 text-slate-400 hover:text-red-400 transition-all" title="Delete">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── TAB: Energy Cost ──────────────────────────────────────────────── */}
      {tab === 'energy' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-semibold text-white">Energy Cost Modelling</h2>
            <p className="text-sm text-slate-400 mt-0.5">Estimate costs from measured power consumption</p>
          </div>

          {/* Rate input */}
          <div className="bg-slate-800/50 border border-white/10 rounded-xl p-5">
            <p className="text-sm font-medium text-slate-300 mb-3">Electricity Tariff</p>
            <div className="flex items-center gap-3">
              <span className="text-slate-400 text-sm">$</span>
              <input
                type="number"
                step="0.01"
                min={0}
                value={costPerKwh}
                onChange={(e) => setCostPerKwh(e.target.value)}
                className="w-28 bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
              />
              <span className="text-slate-400 text-sm">per kWh</span>
            </div>
          </div>

          {/* Cost KPI cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {[
              { label: `Cost (${timeRange})`, value: `$${energyStats.cost.toFixed(2)}`, sub: `${energyStats.kwh.toFixed(1)} kWh`, color: 'text-green-400', bg: 'bg-green-500/10', icon: <DollarSign className="h-6 w-6 text-green-500" /> },
              { label: 'Monthly Cost', value: `$${energyStats.monthlyCost.toFixed(0)}`, sub: `${energyStats.monthlyKwh.toFixed(0)} kWh`, color: 'text-yellow-400', bg: 'bg-yellow-500/10', icon: <BarChart3 className="h-6 w-6 text-yellow-500" /> },
              { label: 'Annual Cost', value: `$${energyStats.annualCost.toFixed(0)}`, sub: `${(energyStats.monthlyKwh * 12).toFixed(0)} kWh/yr`, color: 'text-orange-400', bg: 'bg-orange-500/10', icon: <TrendingUp className="h-6 w-6 text-orange-500" /> },
              { label: 'Cost Rate', value: `$${energyStats.rate.toFixed(3)}`, sub: 'per kWh', color: 'text-blue-400', bg: 'bg-blue-500/10', icon: <Zap className="h-6 w-6 text-blue-500" /> },
            ].map(({ label, value, sub, color, bg, icon }) => (
              <div key={label} className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-400">{label}</p>
                    <p className={`text-3xl font-bold mt-2 ${color}`}>{value}</p>
                    <p className="text-xs text-slate-500 mt-1">{sub}</p>
                  </div>
                  <div className={`${bg} p-3 rounded-lg`}>{icon}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Cost trend chart */}
          {energyStats.costTrend.length > 0 && (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <h3 className="text-base font-semibold text-white mb-4">Hourly Energy Cost Trend</h3>
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={energyStats.costTrend} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="time" tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={{ stroke: '#334155' }} tickLine={false} />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} width={52} tickFormatter={(v) => `$${v}`} />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#e2e8f0' }} formatter={(v: number) => [`$${v.toFixed(4)}`, 'Cost/hr']} />
                    <Area type="monotone" dataKey="cost" stroke="#10b981" fill="#10b981" fillOpacity={0.15} strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Per-server cost breakdown */}
          {serverReports.length > 0 && (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl overflow-hidden">
              <div className="p-5 border-b border-white/10">
                <h3 className="text-base font-semibold text-white">Per-Server Cost Breakdown</h3>
              </div>
              <div className="divide-y divide-white/5">
                {serverReports.map((r) => {
                  const hours = timeRange === '24h' ? 24 : timeRange === '7d' ? 168 : 720
                  const serverKwh = kwhFromWatts(r.avgPower, hours)
                  const serverCost = serverKwh * energyStats.rate
                  const pct = summaryStats.totalPower > 0 ? (r.avgPower / summaryStats.totalPower) * 100 : 0
                  return (
                    <div key={r.serverId} className="flex items-center gap-4 px-5 py-4 hover:bg-white/5 transition-colors">
                      <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: r.color }} />
                      <span className="flex-1 text-sm font-medium text-white">{r.serverName}</span>
                      <div className="w-32 h-2 bg-slate-700 rounded-full overflow-hidden">
                        <div className="h-full rounded-full bg-blue-500" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-xs text-slate-400 w-10 text-right">{pct.toFixed(0)}%</span>
                      <span className="text-sm font-medium text-green-400 w-16 text-right">${serverCost.toFixed(2)}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── TAB: AI Forecast ──────────────────────────────────────────────── */}
      {tab === 'forecast' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-semibold text-white">AI Load Forecasting</h2>
            <p className="text-sm text-slate-400 mt-0.5">Prophet-based power consumption predictions with confidence bands</p>
          </div>

          {/* Model info */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-5">
              <p className="text-xs text-slate-400 mb-1">Model</p>
              <p className="text-base font-semibold text-white">Prophet (Facebook)</p>
              <p className="text-xs text-slate-500 mt-1">Time-series decomposition</p>
            </div>
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-5">
              <p className="text-xs text-slate-400 mb-1">Confidence</p>
              <p className="text-base font-semibold text-white">{forecast ? '85%' : '—'}</p>
              <p className="text-xs text-slate-500 mt-1">prediction interval</p>
            </div>
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-5">
              <p className="text-xs text-slate-400 mb-1">Training Data</p>
              <p className="text-base font-semibold text-white">{forecastHistory.length} points</p>
              <p className="text-xs text-slate-500 mt-1">from aggregated metrics</p>
            </div>
          </div>

          {forecastLoading ? (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-12 flex items-center justify-center gap-4">
              <div className="w-8 h-8 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
              <p className="text-slate-400">Generating forecast...</p>
            </div>
          ) : forecastChartData.length > 0 ? (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <h3 className="text-base font-semibold text-white mb-4">14-Day Power Forecast</h3>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={forecastChartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="time" tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={{ stroke: '#334155' }} tickLine={false} />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} width={50} tickFormatter={(v) => `${v}W`} />
                    <Tooltip
                      contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#e2e8f0' }}
                      formatter={(v: number, name: string) => [`${v}W`, name === 'predicted' ? 'Forecast' : name === 'upper' ? 'Upper bound' : 'Lower bound']}
                    />
                    <Area type="monotone" dataKey="upper" stroke="transparent" fill="#3b82f6" fillOpacity={0.1} dot={false} />
                    <Area type="monotone" dataKey="lower" stroke="transparent" fill="#1e293b" fillOpacity={1} dot={false} />
                    <Line type="monotone" dataKey="predicted" stroke="#3b82f6" strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <p className="text-xs text-slate-500 mt-3">Shaded region shows 85% prediction interval. Requires AI prediction service (VITE_AI_API_URL).</p>
            </div>
          ) : forecastHistory.length < 7 ? (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-12 text-center">
              <TrendingUp className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">Insufficient historical data</p>
              <p className="text-xs text-slate-500 mt-1">Need at least 7 aggregated data points — try a longer time range</p>
            </div>
          ) : (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-12 text-center">
              <TrendingUp className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">Forecast unavailable</p>
              <p className="text-xs text-slate-500 mt-1">AI prediction service not reachable (VITE_AI_API_URL)</p>
            </div>
          )}

          {/* Per-server forecast summary from current trends */}
          {serverReports.length > 0 && (
            <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
              <h3 className="text-base font-semibold text-white mb-4">Projected Monthly Load by Server</h3>
              <div className="space-y-3">
                {serverReports.map((r) => {
                  const monthlyKwh = kwhFromWatts(r.avgPower, 720)
                  const trend = r.avgPower > 0 ? ((r.maxPower - r.avgPower) / r.avgPower) * 100 : 0
                  return (
                    <div key={r.serverId} className="flex items-center gap-4">
                      <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: r.color }} />
                      <span className="text-sm text-slate-300 w-40 truncate">{r.serverName}</span>
                      <div className="flex-1 h-2 bg-slate-700 rounded-full overflow-hidden">
                        <div className="h-full rounded-full bg-blue-500/70" style={{ width: `${Math.min(100, (r.avgPower / (summaryStats.totalPower || 1)) * 100)}%` }} />
                      </div>
                      <span className="text-sm font-medium text-white w-24 text-right">{monthlyKwh.toFixed(0)} kWh/mo</span>
                      <span className={`text-xs w-16 text-right ${trend > 20 ? 'text-red-400' : trend > 5 ? 'text-yellow-400' : 'text-green-400'}`}>
                        {trend > 0 ? '+' : ''}{trend.toFixed(0)}% var
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
