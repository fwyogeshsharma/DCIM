import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { mlApi } from '@/lib/ml-api'
import type { MLRecommendation, MLCapacitySummary, MLForecastPoint, MLModelRun } from '@/lib/ml-api'
import {
  AreaChart, Area, LineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import {
  BrainCircuit, Server, HardDrive, Zap, Cpu, AlertTriangle,
  Info, CheckCircle2, RefreshCw, Calendar, TrendingUp, Activity,
  Database,
} from 'lucide-react'
import toast from 'react-hot-toast'

// ── Severity badge ─────────────────────────────────────────────────────────────

function SeverityBadge({ severity }: { severity: string }) {
  const meta: Record<string, { label: string; cls: string }> = {
    critical: { label: 'Critical', cls: 'bg-red-500/20 text-red-400 border-red-500/30' },
    warning:  { label: 'Warning',  cls: 'bg-amber-500/20 text-amber-400 border-amber-500/30' },
    info:     { label: 'Info',     cls: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
  }
  const m = meta[severity] ?? meta.info
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${m.cls}`}>
      {m.label}
    </span>
  )
}

// ── Category icon ──────────────────────────────────────────────────────────────

function CategoryIcon({ category }: { category: string }) {
  const icons: Record<string, React.ReactNode> = {
    rack:    <Server className="w-4 h-4 text-blue-400" />,
    storage: <HardDrive className="w-4 h-4 text-cyan-400" />,
    power:   <Zap className="w-4 h-4 text-yellow-400" />,
    cpu:     <Cpu className="w-4 h-4 text-purple-400" />,
    server:  <Activity className="w-4 h-4 text-green-400" />,
  }
  return <>{icons[category] ?? <AlertTriangle className="w-4 h-4 text-slate-400" />}</>
}

// ── KPI card ───────────────────────────────────────────────────────────────────

function KpiCard({
  label, value, sub, icon, color = 'text-white', bg = 'bg-slate-800/50',
}: {
  label: string
  value: React.ReactNode
  sub?: string
  icon: React.ReactNode
  color?: string
  bg?: string
}) {
  return (
    <div className={`${bg} border border-white/10 rounded-xl p-5 flex items-start justify-between gap-3`}>
      <div className="min-w-0">
        <p className="text-xs text-slate-400 mb-1">{label}</p>
        <p className={`text-2xl font-bold truncate ${color}`}>{value}</p>
        {sub && <p className="text-xs text-slate-500 mt-1 truncate">{sub}</p>}
      </div>
      <div className="flex-shrink-0 mt-0.5">{icon}</div>
    </div>
  )
}

// ── Recommendation card ────────────────────────────────────────────────────────

function RecCard({ rec }: { rec: MLRecommendation }) {
  const border: Record<string, string> = {
    critical: 'border-red-500/30',
    warning:  'border-amber-500/30',
    info:     'border-blue-500/20',
  }
  return (
    <div className={`bg-slate-800/40 border ${border[rec.severity] ?? 'border-white/10'} rounded-xl p-5`}>
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 mt-0.5">
          <CategoryIcon category={rec.category} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1.5">
            <SeverityBadge severity={rec.severity} />
            <span className="text-xs text-slate-500 capitalize">{rec.category}</span>
            <span className="text-xs text-slate-600">· {Math.round(rec.confidence * 100)}% confidence</span>
          </div>
          <p className="text-sm text-slate-200 leading-relaxed">{rec.message}</p>
          {rec.predicted_date && (
            <p className="text-xs text-slate-500 mt-2 flex items-center gap-1">
              <Calendar className="w-3 h-3" />
              Predicted by {new Date(rec.predicted_date).toLocaleDateString(undefined, { dateStyle: 'medium' })}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Forecast chart ─────────────────────────────────────────────────────────────

function ForecastChart({
  points, target, unit, color,
}: {
  points: MLForecastPoint[]
  target: string
  unit: string
  color: string
}) {
  if (points.length === 0) return null

  const data = points.map(p => ({
    date: new Date(p.forecast_for).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    value: Math.round(p.predicted_value * 10) / 10,
    lower: p.lower_bound != null ? Math.round(p.lower_bound * 10) / 10 : undefined,
    upper: p.upper_bound != null ? Math.round(p.upper_bound * 10) / 10 : undefined,
  }))

  const step = Math.ceil(data.length / 8)

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
        <XAxis
          dataKey="date"
          tick={{ fill: '#94a3b8', fontSize: 11 }}
          axisLine={{ stroke: '#334155' }}
          tickLine={false}
          interval={step - 1}
        />
        <YAxis
          tick={{ fill: '#94a3b8', fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={45}
          tickFormatter={v => `${v}${unit}`}
        />
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#e2e8f0' }}
          formatter={(v: number | undefined, name: string) => [
            `${v ?? '—'} ${unit}`,
            name === 'upper' ? 'Upper bound' : name === 'lower' ? 'Lower bound' : target,
          ]}
        />
        {data[0]?.upper != null && (
          <Area type="monotone" dataKey="upper" stroke="transparent" fill={color} fillOpacity={0.12} dot={false} />
        )}
        {data[0]?.lower != null && (
          <Area type="monotone" dataKey="lower" stroke="transparent" fill="#0f172a" fillOpacity={1} dot={false} />
        )}
        <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

// ── Model summary row ──────────────────────────────────────────────────────────

const MODEL_COLORS: Record<string, string> = {
  xgboost:      'text-orange-400',
  holt_winters: 'text-cyan-400',
  trend:        'text-slate-400',
  derived:      'text-slate-500',
}

function ModelBadge({ name }: { name: string }) {
  return (
    <span className={`text-xs font-mono font-medium ${MODEL_COLORS[name] ?? 'text-slate-300'}`}>
      {name}
    </span>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function InventoryAIInsights() {
  const [training, setTraining] = useState(false)

  const { data: health, isError: healthErr } = useQuery({
    queryKey: ['ml', 'health'],
    queryFn: () => mlApi.health(),
    retry: 1,
    staleTime: 30_000,
  })

  const { data: capacity = [] } = useQuery({
    queryKey: ['ml', 'capacity'],
    queryFn: () => mlApi.capacitySummary({ scope: 'global', scope_id: 'all' }),
    retry: 1,
    staleTime: 60_000,
    enabled: health?.status === 'healthy',
  })

  const { data: recs = [] } = useQuery({
    queryKey: ['ml', 'recommendations'],
    queryFn: () => mlApi.recommendations({ scope: 'global', scope_id: 'all' }),
    retry: 1,
    staleTime: 60_000,
    enabled: health?.status === 'healthy',
  })

  const { data: serverForecasts = [] } = useQuery({
    queryKey: ['ml', 'forecasts', 'server_count'],
    queryFn: () => mlApi.forecasts({ scope: 'global', scope_id: 'all', target: 'server_count', horizon: 90 }),
    retry: 1,
    staleTime: 60_000,
    enabled: health?.status === 'healthy',
  })

  const { data: rackForecasts = [] } = useQuery({
    queryKey: ['ml', 'forecasts', 'rack_units'],
    queryFn: () => mlApi.forecasts({ scope: 'global', scope_id: 'all', target: 'rack_units', horizon: 90 }),
    retry: 1,
    staleTime: 60_000,
    enabled: health?.status === 'healthy',
  })

  const { data: modelRuns = [] } = useQuery({
    queryKey: ['ml', 'models'],
    queryFn: () => mlApi.modelRuns({ scope: 'global', scope_id: 'all' }),
    retry: 1,
    staleTime: 120_000,
    enabled: health?.status === 'healthy',
  })

  async function handleTrain() {
    setTraining(true)
    try {
      const result = await mlApi.triggerTrain()
      toast.success(`Training complete — ${result.forecasts} forecasts, ${result.recommendations} recommendations`)
    } catch (e: any) {
      toast.error(e.message || 'Training failed')
    } finally {
      setTraining(false)
    }
  }

  const cap: MLCapacitySummary | undefined = capacity[0]

  // Separate recommendations by severity
  const critRecs = recs.filter(r => r.severity === 'critical')
  const warnRecs = recs.filter(r => r.severity === 'warning')
  const infoRecs = recs.filter(r => r.severity === 'info')

  const lastRun = health?.last_run_at
    ? new Date(health.last_run_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : null

  // ── ML unavailable state ─────────────────────────────────────────────────────
  if (healthErr || (health && health.status !== 'healthy')) {
    return (
      <div className="flex-1 flex items-center justify-center p-12">
        <div className="text-center max-w-md">
          <BrainCircuit className="w-16 h-16 text-slate-700 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-white mb-2">ML Service Unavailable</h3>
          <p className="text-sm text-slate-400 mb-4">
            The DCIM_ML service (port 5002) is not reachable. Start it with Docker Compose to view AI insights.
          </p>
          <code className="block bg-slate-800 rounded-lg px-4 py-2 text-xs text-slate-300 font-mono">
            docker compose up dcim-ml
          </code>
        </div>
      </div>
    )
  }

  if (!health) {
    return (
      <div className="flex-1 flex items-center justify-center p-12">
        <div className="w-8 h-8 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
      </div>
    )
  }

  // ── Headroom color ───────────────────────────────────────────────────────────
  function headroomColor(pct: number | null) {
    if (pct == null) return 'text-slate-400'
    if (pct > 30) return 'text-green-400'
    if (pct > 10) return 'text-amber-400'
    return 'text-red-400'
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <BrainCircuit className="w-5 h-5 text-purple-400" />
            AI Insights
          </h2>
          <p className="text-sm text-slate-400 mt-0.5">
            ML-powered capacity planning and physical change recommendations
            {lastRun && <span className="text-slate-500"> · Last trained {lastRun}</span>}
          </p>
        </div>
        <button
          onClick={handleTrain}
          disabled={training}
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-60 rounded-lg text-sm text-white font-medium transition-all"
        >
          <RefreshCw className={`w-4 h-4 ${training ? 'animate-spin' : ''}`} />
          {training ? 'Training…' : 'Retrain Models'}
        </button>
      </div>

      {/* DB connectivity warning */}
      {!health.db_reachable && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0" />
          <p className="text-sm text-red-300">ML service cannot reach the database — forecasts may be stale.</p>
        </div>
      )}

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Rack Full In"
          value={cap?.days_until_rack_full != null ? `${cap.days_until_rack_full}d` : '—'}
          sub={cap?.rack_exhaustion_date ? `by ${new Date(cap.rack_exhaustion_date).toLocaleDateString(undefined, { dateStyle: 'medium' })}` : 'no exhaustion predicted'}
          icon={<Server className="w-5 h-5 text-blue-400" />}
          color={
            cap?.days_until_rack_full != null && cap.days_until_rack_full < 30
              ? 'text-red-400'
              : cap?.days_until_rack_full != null && cap.days_until_rack_full < 90
              ? 'text-amber-400'
              : 'text-green-400'
          }
        />
        <KpiCard
          label="Storage Full In"
          value={cap?.days_until_storage_full != null ? `${cap.days_until_storage_full}d` : '—'}
          sub={cap?.storage_exhaustion_date ? `by ${new Date(cap.storage_exhaustion_date).toLocaleDateString(undefined, { dateStyle: 'medium' })}` : 'no exhaustion predicted'}
          icon={<HardDrive className="w-5 h-5 text-cyan-400" />}
          color={
            cap?.days_until_storage_full != null && cap.days_until_storage_full < 30
              ? 'text-red-400'
              : cap?.days_until_storage_full != null && cap.days_until_storage_full < 90
              ? 'text-amber-400'
              : 'text-green-400'
          }
        />
        <KpiCard
          label="Server Growth (30d)"
          value={cap?.expected_server_growth_30d != null ? `+${cap.expected_server_growth_30d}` : '—'}
          sub={cap?.expected_server_growth_90d != null ? `+${cap.expected_server_growth_90d} in 90d` : undefined}
          icon={<TrendingUp className="w-5 h-5 text-green-400" />}
          color="text-green-400"
        />
        <KpiCard
          label="Power Headroom"
          value={cap?.power_headroom_pct != null ? `${Math.round(cap.power_headroom_pct)}%` : '—'}
          sub={
            cap?.power_headroom_pct != null
              ? cap.power_headroom_pct > 30
                ? 'comfortable margin'
                : cap.power_headroom_pct > 10
                ? 'getting tight'
                : 'critically low'
              : undefined
          }
          icon={<Zap className="w-5 h-5 text-yellow-400" />}
          color={headroomColor(cap?.power_headroom_pct ?? null)}
        />
      </div>

      {/* New racks needed */}
      {(cap?.predicted_new_racks_30d != null || cap?.predicted_new_racks_60d != null || cap?.predicted_new_racks_90d != null) && (
        <div className="bg-slate-800/50 border border-white/10 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">Predicted New Racks Needed</h3>
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: '30 days', val: cap.predicted_new_racks_30d },
              { label: '60 days', val: cap.predicted_new_racks_60d },
              { label: '90 days', val: cap.predicted_new_racks_90d },
            ].map(({ label, val }) => (
              <div key={label} className="text-center">
                <p className="text-xl font-bold text-blue-400">{val ?? '—'}</p>
                <p className="text-xs text-slate-500 mt-0.5">{label}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recommendations */}
      {recs.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Info className="w-4 h-4 text-blue-400" />
            Recommendations
            <span className="text-xs px-2 py-0.5 bg-slate-700 rounded-full text-slate-300">{recs.length}</span>
          </h3>
          {[...critRecs, ...warnRecs, ...infoRecs].map(rec => (
            <RecCard key={rec.id} rec={rec} />
          ))}
        </div>
      )}

      {recs.length === 0 && (
        <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-5 flex items-center gap-3">
          <CheckCircle2 className="w-5 h-5 text-green-400 flex-shrink-0" />
          <p className="text-sm text-green-300">No open recommendations — all capacity metrics look healthy.</p>
        </div>
      )}

      {/* Server growth forecast chart */}
      {serverForecasts.length > 0 && (
        <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
          <h3 className="text-base font-semibold text-white mb-1">Server Count Forecast (90 days)</h3>
          <p className="text-xs text-slate-500 mb-4">Predicted cumulative server growth with confidence band</p>
          <div className="h-56">
            <ForecastChart points={serverForecasts} target="servers" unit=" srv" color="#10b981" />
          </div>
        </div>
      )}

      {/* Rack units forecast chart */}
      {rackForecasts.length > 0 && (
        <div className="bg-slate-800/50 border border-white/10 rounded-xl p-6">
          <h3 className="text-base font-semibold text-white mb-1">Rack Units Utilization Forecast (90 days)</h3>
          <p className="text-xs text-slate-500 mb-4">Derived from server growth × avg rack-unit height per device</p>
          <div className="h-56">
            <ForecastChart points={rackForecasts} target="rack-U" unit="U" color="#3b82f6" />
          </div>
        </div>
      )}

      {/* Model summary */}
      {modelRuns.length > 0 && (
        <div className="bg-slate-800/50 border border-white/10 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-white/10">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Database className="w-4 h-4 text-slate-400" />
              Active Models
            </h3>
          </div>
          <div className="divide-y divide-white/5">
            {modelRuns.map((m, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3">
                <span className="text-xs text-slate-400 w-28 truncate capitalize">{m.target.replace('_', ' ')}</span>
                <ModelBadge name={m.model_name} />
                <span className="flex-1" />
                {m.n_points != null && <span className="text-xs text-slate-500">{m.n_points} pts</span>}
                {m.mae != null && <span className="text-xs text-slate-400">MAE {m.mae.toFixed(2)}</span>}
                {m.mape != null && <span className="text-xs text-slate-500">MAPE {m.mape.toFixed(1)}%</span>}
                <span className="text-xs text-slate-600">
                  {new Date(m.trained_at).toLocaleDateString(undefined, { dateStyle: 'short' })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
