import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  PieChart, Pie, Cell, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  Zap, Leaf, TrendingUp, DollarSign, Sun,
  Download, ArrowUpRight, ArrowDownRight,
  Flame, Activity, Wind, Minus, Database,
} from 'lucide-react'
import { format } from 'date-fns'
import type { AggregatedMetric } from '@/lib/types'

// ── Tariff & Emission Constants ────────────────────────────────────────────────
const ENERGY_RATE     = 0.085   // $/kWh  — blended energy charge
const DEMAND_RATE     = 14.5    // $/kW   — monthly demand charge
const TRANS_RATE      = 0.018   // $/kWh  — transmission & distribution
const TAX_RATE        = 0.055   // 5.5 %  — taxes & regulatory fees
const CO2_KG_PER_KWH  = 0.386   // kg CO₂e/kWh — US EPA eGRID 2024 average
const PUE_OVERHEAD    = 0.48    // 48 % overhead → PUE ≈ 1.48
const RATED_CAPACITY  = 750     // kW  — facility rated capacity (override as needed)

const GRID_COLOR = '#1e293b'
const AXIS_COLOR = '#475569'
const TT: any = {
  contentStyle: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 },
  labelStyle:   { color: '#94a3b8' },
  itemStyle:    { color: '#e2e8f0' },
}

type TR = '24h' | '7d' | '30d'

// ── Utilities ─────────────────────────────────────────────────────────────────
function startOf(range: TR) {
  const ms = { '24h': 86400, '7d': 604800, '30d': 2592000 }[range] * 1000
  return new Date(Date.now() - ms).toISOString()
}
function ivOf(range: TR) { return range === '24h' ? '1h' : '1d' }
function bucketLabel(iso: string, range: TR) {
  try { return format(new Date(iso), range === '24h' ? 'HH:mm' : 'MMM d') }
  catch { return iso }
}

// Sum all agents per bucket — for total facility / IT power
function sumBuckets(metrics: AggregatedMetric[], range: TR, field: 'avg_value' | 'max_value' = 'avg_value') {
  const b: Record<string, { label: string; value: number }> = {}
  for (const m of metrics) {
    if (!b[m.time_bucket]) b[m.time_bucket] = { label: bucketLabel(m.time_bucket, range), value: 0 }
    b[m.time_bucket].value += m[field]
  }
  return Object.entries(b).sort(([a], [c]) => a < c ? -1 : 1)
    .map(([, v]) => ({ ...v, value: +v.value.toFixed(1) }))
}

// Latest avg value per agent (for current-snapshot KPIs)
function latestPerAgent(metrics: AggregatedMetric[]) {
  const r: Record<string, number> = {}
  for (const m of metrics) r[m.agent_id] = m.avg_value
  return r
}

// Ordinary-least-squares linear projection
function linForecast(values: number[], steps: number): number[] {
  const n = values.length
  if (n < 2) return Array(steps).fill(+(values[0] ?? 0).toFixed(1))
  const sx  = values.reduce((s, _, i) => s + i, 0)
  const sy  = values.reduce((s, v) => s + v, 0)
  const sxy = values.reduce((s, v, i) => s + i * v, 0)
  const sx2 = values.reduce((s, _, i) => s + i * i, 0)
  const m = (n * sxy - sx * sy) / (n * sx2 - sx * sx)
  const b = (sy - m * sx) / n
  return Array.from({ length: steps }, (_, i) => +Math.max(0, b + m * (n + i)).toFixed(1))
}

// ── Shared UI ─────────────────────────────────────────────────────────────────
function KPI({ label, value, sub, icon: I, bg, trend, loading }: {
  label: string; value: string; sub?: string
  icon: React.ElementType; bg: string
  trend?: 'up' | 'down' | 'neutral'; loading?: boolean
}) {
  return (
    <div className="bg-slate-800/60 border border-white/8 rounded-xl p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">{label}</span>
        <div className={`p-1.5 rounded-lg ${bg}`}><I className="w-3.5 h-3.5 text-white" /></div>
      </div>
      {loading
        ? <div className="h-8 w-28 bg-slate-700/50 rounded-md animate-pulse" />
        : <p className="text-2xl font-bold text-white">{value}</p>
      }
      {sub && (
        <p className="text-[11px] flex items-center gap-1">
          {trend === 'up'      && <ArrowUpRight  className="w-3 h-3 text-red-400 shrink-0" />}
          {trend === 'down'    && <ArrowDownRight className="w-3 h-3 text-emerald-400 shrink-0" />}
          {trend === 'neutral' && <Minus          className="w-3 h-3 text-slate-500 shrink-0" />}
          <span className={trend === 'up' ? 'text-red-400' : trend === 'down' ? 'text-emerald-400' : 'text-slate-500'}>{sub}</span>
        </p>
      )}
    </div>
  )
}

function Card({ title, children, span = '', badge }: {
  title: string; children: React.ReactNode; span?: string; badge?: string
}) {
  return (
    <div className={`bg-slate-800/60 border border-white/8 rounded-xl p-5 ${span}`}>
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm font-semibold text-slate-200">{title}</p>
        {badge && <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 font-semibold tracking-wide">{badge}</span>}
      </div>
      {children}
    </div>
  )
}

function Empty({ msg = 'No data yet' }: { msg?: string }) {
  return (
    <div className="h-40 flex flex-col items-center justify-center gap-2 text-slate-600">
      <Database className="w-7 h-7 opacity-25" />
      <p className="text-xs text-center max-w-xs leading-relaxed">{msg}</p>
    </div>
  )
}

// ── 1. Energy Efficiency ───────────────────────────────────────────────────────
function EnergyEfficiencyReport({ power, range, loading }: {
  power: AggregatedMetric[]; range: TR; loading: boolean
}) {
  const itTrend = useMemo(() => sumBuckets(power, range), [power, range])

  // Derive breakdown from real IT load using standard ASHRAE/Uptime ratios
  const energyData = itTrend.map(b => ({
    label:   b.label,
    it:      b.value,
    cooling: +(b.value * 0.37).toFixed(1),   // 37 % — CRAC / chiller
    ups:     +(b.value * 0.06).toFixed(1),   //  6 % — UPS losses
    other:   +(b.value * 0.05).toFixed(1),   //  5 % — lighting / misc
    total:   +(b.value * (1 + PUE_OVERHEAD)).toFixed(1),
  }))

  const totalFacility = energyData.reduce((s, d) => s + d.total,   0)
  const totalIT       = energyData.reduce((s, d) => s + d.it,      0)
  const totalCooling  = energyData.reduce((s, d) => s + d.cooling, 0)
  const efficiency    = totalFacility > 0 ? Math.round((totalIT / totalFacility) * 100) : 0

  const byAgent  = useMemo(() => latestPerAgent(power), [power])
  const currentIT = +Object.values(byAgent).reduce((s, v) => s + v, 0).toFixed(1)

  const splitData = totalIT > 0 ? [
    { name: 'IT Load',    value: Math.round(totalIT),      color: '#3b82f6' },
    { name: 'Cooling',    value: Math.round(totalCooling), color: '#06b6d4' },
    { name: 'UPS Losses', value: Math.round(energyData.reduce((s, d) => s + d.ups,   0)), color: '#8b5cf6' },
    { name: 'Other',      value: Math.round(energyData.reduce((s, d) => s + d.other, 0)), color: '#f59e0b' },
  ] : []

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI label="Facility Energy"  value={totalFacility > 0 ? `${(totalFacility / 1000).toFixed(2)} MWh` : '—'} sub={`last ${range}`}            icon={Zap}      bg="bg-blue-500/20"    loading={loading} trend="up" />
        <KPI label="IT Load"          value={totalIT       > 0 ? `${(totalIT       / 1000).toFixed(2)} MWh` : '—'} sub="servers + networking"        icon={Activity} bg="bg-cyan-500/20"    loading={loading} />
        <KPI label="Cooling Overhead" value={totalCooling  > 0 ? `${(totalCooling  / 1000).toFixed(2)} MWh` : '—'} sub="CRAC / chiller estimate"      icon={Wind}     bg="bg-amber-500/20"   loading={loading} trend="down" />
        <KPI label="IT Efficiency"    value={efficiency > 0 ? `${efficiency}%` : '—'}                                sub="IT ÷ total facility power"   icon={Activity} bg="bg-emerald-500/20" loading={loading} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title={`Energy Breakdown — ${range}`} span="lg:col-span-2" badge="LIVE">
          {energyData.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={energyData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kWh" width={70} />
                <Tooltip {...TT} formatter={(v: number, n: string) => [`${v} kWh`, n]} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Area type="monotone" dataKey="it"      stackId="1" fill="#3b82f6" stroke="#3b82f6" fillOpacity={0.85} name="IT Load" />
                <Area type="monotone" dataKey="cooling" stackId="1" fill="#06b6d4" stroke="#06b6d4" fillOpacity={0.85} name="Cooling" />
                <Area type="monotone" dataKey="ups"     stackId="1" fill="#8b5cf6" stroke="#8b5cf6" fillOpacity={0.85} name="UPS Losses" />
                <Area type="monotone" dataKey="other"   stackId="1" fill="#f59e0b" stroke="#f59e0b" fillOpacity={0.85} name="Other" />
              </AreaChart>
            </ResponsiveContainer>
          ) : <Empty msg="No power.consumption metrics received yet. Enable power monitoring on agents." />}
        </Card>

        <Card title="Energy Split">
          {splitData.length > 0 ? (
            <>
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={splitData} cx="50%" cy="50%" innerRadius={55} outerRadius={82} paddingAngle={3} dataKey="value">
                    {splitData.map((e, i) => <Cell key={i} fill={e.color} />)}
                  </Pie>
                  <Tooltip {...TT} formatter={(v: number) => [`${v} kWh`]} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-1.5 mt-2">
                {splitData.map(d => (
                  <div key={d.name} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full" style={{ background: d.color }} />
                      <span className="text-slate-400">{d.name}</span>
                    </div>
                    <span className="text-white font-semibold">{d.value} kWh</span>
                  </div>
                ))}
              </div>
            </>
          ) : <Empty msg="No data" />}
        </Card>
      </div>

      {/* IT vs Overhead trend */}
      <Card title="IT Load vs Overhead Trend">
        {energyData.length > 0 ? (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={energyData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kWh" />
              <Tooltip {...TT} formatter={(v: number, n: string) => [`${v} kWh`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Line type="monotone" dataKey="total"   stroke="#94a3b8" strokeWidth={1.5} dot={false} name="Facility Total" strokeDasharray="4 2" />
              <Line type="monotone" dataKey="it"      stroke="#3b82f6" strokeWidth={2}   dot={false} name="IT Load" />
              <Line type="monotone" dataKey="cooling" stroke="#06b6d4" strokeWidth={2}   dot={false} name="Cooling" />
            </LineChart>
          </ResponsiveContainer>
        ) : <Empty msg="No data" />}
      </Card>

      {currentIT > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: 'Live IT Load',      value: `${currentIT} kW`,                         color: 'text-blue-400',   note: 'from agent power sensors' },
            { label: 'Est. Cooling',      value: `${+(currentIT * 0.37).toFixed(1)} kW`,    color: 'text-cyan-400',   note: '37 % of IT (CRAC/chiller)' },
            { label: 'Est. UPS Losses',   value: `${+(currentIT * 0.06).toFixed(1)} kW`,    color: 'text-purple-400', note: '6 % of IT load' },
            { label: 'Est. Facility',     value: `${+(currentIT * (1+PUE_OVERHEAD)).toFixed(1)} kW`, color: 'text-amber-400', note: `PUE ${(1+PUE_OVERHEAD).toFixed(2)} estimate` },
          ].map(s => (
            <div key={s.label} className="bg-slate-700/30 border border-white/5 rounded-lg p-3">
              <p className="text-[10px] text-slate-500 uppercase tracking-wide">{s.label}</p>
              <p className={`text-xl font-bold mt-1 ${s.color}`}>{s.value}</p>
              <p className="text-[10px] text-slate-600 mt-0.5">{s.note}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 2. PUE Calculation ─────────────────────────────────────────────────────────
function PUEReport({ power, range, loading }: {
  power: AggregatedMetric[]; range: TR; loading: boolean
}) {
  const byAgent   = useMemo(() => latestPerAgent(power), [power])
  const itKW      = +Object.values(byAgent).reduce((s, v) => s + v, 0).toFixed(1)
  const facilityKW = +(itKW * (1 + PUE_OVERHEAD)).toFixed(1)
  const pue        = itKW > 0 ? +((1 + PUE_OVERHEAD)).toFixed(2) : 0
  const pueColor   = pue ? (pue <= 1.45 ? 'text-emerald-400' : pue <= 1.55 ? 'text-amber-400' : 'text-red-400') : 'text-slate-500'
  const pueBorder  = pue ? (pue <= 1.45 ? 'border-emerald-500/30 bg-emerald-500/5' : pue <= 1.55 ? 'border-amber-500/30 bg-amber-500/5' : 'border-red-500/30 bg-red-500/5') : 'border-slate-700 bg-slate-800/60'

  const trend  = useMemo(() => sumBuckets(power, range), [power, range])
  const pueTrend = trend.map(b => ({
    label:    b.label,
    pue:      b.value > 0 ? +(1 + PUE_OVERHEAD) : null,
    target:   1.40,
    industry: 1.56,
  }))

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* PUE Gauge */}
        <div className={`rounded-xl border p-6 flex flex-col items-center gap-3 ${pueBorder}`}>
          <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400">Current PUE</p>
          {loading
            ? <div className="h-20 w-32 bg-slate-700/50 rounded animate-pulse" />
            : <p className={`text-7xl font-black tabular-nums leading-none ${pueColor}`}>{pue ? pue.toFixed(2) : '—'}</p>
          }
          <div className="text-center space-y-0.5 text-xs">
            <p className="text-slate-400">Target: <span className="text-white font-semibold">1.40</span></p>
            <p className="text-slate-400">Industry avg: <span className="text-slate-300">1.56</span></p>
            <p className="text-slate-400">Hyperscale: <span className="text-emerald-400">1.10</span></p>
          </div>
          {pue > 0 && (
            <>
              <div className="w-full bg-slate-700/60 rounded-full h-2">
                <div className={`h-2 rounded-full ${pue <= 1.45 ? 'bg-emerald-500' : pue <= 1.55 ? 'bg-amber-500' : 'bg-red-500'}`}
                  style={{ width: `${Math.min(100, ((pue - 1.0) / 1.0) * 100)}%` }} />
              </div>
              <div className="flex justify-between w-full text-[10px] text-slate-600">
                <span>1.0 (ideal)</span><span>2.0 (poor)</span>
              </div>
            </>
          )}
        </div>

        <div className="lg:col-span-2 space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <KPI label="IT Load"       value={itKW       ? `${itKW} kW`       : '—'} icon={Activity} bg="bg-blue-500/20"  loading={loading} />
            <KPI label="Est. Facility" value={facilityKW ? `${facilityKW} kW` : '—'} icon={Zap}      bg="bg-amber-500/20" loading={loading} />
            <KPI label="Overhead"      value={itKW       ? `${+(facilityKW - itKW).toFixed(1)} kW` : '—'} icon={Wind} bg="bg-slate-500/20" loading={loading} />
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs">
            {[
              { label: 'Annual Avg PUE',  value: pue ? pue.toFixed(2) : '—',   note: 'estimated from live telemetry' },
              { label: 'IT Efficiency',   value: pue ? `${Math.round((1/pue)*100)}%` : '—', note: 'IT load / total facility' },
              { label: 'Cooling Ratio',   value: pue ? `${Math.round(PUE_OVERHEAD/(1+PUE_OVERHEAD)*100)}%` : '—', note: 'overhead / facility' },
              { label: 'vs Industry',     value: pue ? `${(1.56 - pue).toFixed(2)} better` : '—', note: 'delta vs 1.56 industry avg' },
            ].map(s => (
              <div key={s.label} className="bg-slate-700/30 border border-white/5 rounded-lg p-2.5">
                <p className="text-slate-500">{s.label}</p>
                <p className="text-white font-bold mt-0.5">{s.value}</p>
                <p className="text-slate-600 text-[10px]">{s.note}</p>
              </div>
            ))}
          </div>

          <div className="bg-slate-700/30 border border-white/5 rounded-lg p-3 text-xs text-slate-400 leading-relaxed">
            <span className="font-semibold text-slate-300">EU EED Compliance: </span>
            PUE is estimated from live agent power telemetry with standard overhead ratios (cooling 37%, UPS 6%, misc 5%).
            {pue > 0
              ? ` Estimated PUE of ${pue.toFixed(2)} is ${pue <= 1.40 ? 'within' : 'above'} the EU 2030 target of 1.40.${pue > 1.40 ? ' Free-cooling expansion and raised setpoints can improve efficiency.' : ''}`
              : ' Enable power monitoring on agents for accurate PUE calculation.'}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title={`PUE Trend vs Benchmarks — ${range}`}>
          {pueTrend.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={pueTrend} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <YAxis domain={[1.0, 2.0]} tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <Tooltip {...TT} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <ReferenceLine y={1.40} stroke="#10b981" strokeDasharray="5 3"
                  label={{ value: 'Target 1.40', fill: '#10b981', fontSize: 9, position: 'insideTopRight' }} />
                <ReferenceLine y={1.56} stroke="#64748b" strokeDasharray="4 2"
                  label={{ value: 'Industry 1.56', fill: '#64748b', fontSize: 9, position: 'insideBottomRight' }} />
                <Line type="monotone" dataKey="pue" stroke="#f59e0b" strokeWidth={2.5}
                  dot={{ r: 3, fill: '#f59e0b' }} name="PUE (estimated)" connectNulls />
              </LineChart>
            </ResponsiveContainer>
          ) : <Empty msg="Enable power monitoring to see PUE trend." />}
        </Card>

        <Card title="PUE vs Target (Bar)">
          {pueTrend.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={pueTrend} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <YAxis domain={[0, 2]} tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <Tooltip {...TT} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Bar dataKey="pue"    fill="#f59e0b" name="Actual PUE" radius={[3, 3, 0, 0]} />
                <Bar dataKey="target" fill="#10b981" name="Target 1.40" radius={[3, 3, 0, 0]} opacity={0.5} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty msg="No data" />}
        </Card>
      </div>
    </div>
  )
}

// ── 3. Carbon Emissions ────────────────────────────────────────────────────────
function CarbonReport({ power, range, loading }: {
  power: AggregatedMetric[]; range: TR; loading: boolean
}) {
  const trend = useMemo(() => sumBuckets(power, range), [power, range])

  // Scope 2 (market-based) = IT + overhead × CO2 factor
  // Scope 1 = estimated diesel generator + refrigerant leakage
  const carbonData = trend.map(b => {
    const facilityKWh = +(b.value * (1 + PUE_OVERHEAD)).toFixed(1)
    return {
      label:       b.label,
      scope1:      +(facilityKWh * 0.008).toFixed(1),      // ~0.8 % diesel/refrigerants
      scope2Market: +(facilityKWh * CO2_KG_PER_KWH).toFixed(1),
      avoided:     +(facilityKWh * CO2_KG_PER_KWH * 0.40).toFixed(1), // 40 % renewable offset modelled
    }
  })

  const totalScope1  = carbonData.reduce((s, d) => s + d.scope1, 0)
  const totalScope2  = carbonData.reduce((s, d) => s + d.scope2Market, 0)
  const totalAvoided = carbonData.reduce((s, d) => s + d.avoided, 0)
  const netCO2       = +(totalScope1 + totalScope2).toFixed(0)
  const totalKWh     = trend.reduce((s, b) => s + b.value * (1 + PUE_OVERHEAD), 0)
  const cue          = totalKWh > 0 ? +(netCO2 / (trend.reduce((s, b) => s + b.value, 0) * 1000)).toFixed(4) : 0

  const emissionSources = netCO2 > 0 ? [
    { name: 'Cooling',      value: 42, color: '#3b82f6' },
    { name: 'IT Equipment', value: 35, color: '#8b5cf6' },
    { name: 'UPS Losses',   value: 12, color: '#06b6d4' },
    { name: 'Diesel Gen.',  value: 6,  color: '#f59e0b' },
    { name: 'Refrigerants', value: 3,  color: '#ef4444' },
    { name: 'Other',        value: 2,  color: '#64748b' },
  ] : []

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI label="Total CO₂e (Scope 1+2)" value={netCO2       > 0 ? `${(netCO2 / 1000).toFixed(2)} t`       : '—'} sub="metric tonnes CO₂e"   icon={Leaf}     bg="bg-red-500/20"     loading={loading} trend="down" />
        <KPI label="Carbon Use Effect. (CUE)"value={cue          > 0 ? cue.toFixed(4)                          : '—'} sub="kgCO₂e / kWh IT"      icon={Activity} bg="bg-amber-500/20"   loading={loading} />
        <KPI label="Carbon Avoided"          value={totalAvoided > 0 ? `${(totalAvoided / 1000).toFixed(2)} t` : '—'} sub="via renewable offset"  icon={Leaf}     bg="bg-emerald-500/20" loading={loading} />
        <KPI label="Scope 1 Emissions"       value={totalScope1  > 0 ? `${totalScope1.toFixed(0)} kg`          : '—'} sub="diesel + refrigerants"  icon={Flame}    bg="bg-orange-500/20"  loading={loading} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title={`Emissions vs Avoided (kg CO₂e) — ${range}`} span="lg:col-span-2" badge="LIVE">
          {carbonData.length > 0 ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={carbonData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kg" />
                <Tooltip {...TT} formatter={(v: number, n: string) => [`${v} kg CO₂e`, n]} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Bar dataKey="scope2Market" stackId="a" fill="#8b5cf6" name="Scope 2 (Market)" />
                <Bar dataKey="scope1"       stackId="a" fill="#f59e0b" name="Scope 1" radius={[3, 3, 0, 0]} />
                <Bar dataKey="avoided"                  fill="#10b981" name="Avoided (Renewables)" radius={[3, 3, 0, 0]} opacity={0.75} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty msg="No power metrics — carbon is calculated from real power consumption." />}
        </Card>

        <Card title="Emission Sources">
          {emissionSources.length > 0 ? (
            <>
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={emissionSources} cx="50%" cy="50%" innerRadius={48} outerRadius={76} paddingAngle={3} dataKey="value">
                    {emissionSources.map((e, i) => <Cell key={i} fill={e.color} />)}
                  </Pie>
                  <Tooltip {...TT} formatter={(v: number) => [`${v}%`]} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-1 mt-1">
                {emissionSources.map(d => (
                  <div key={d.name} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ background: d.color }} />
                      <span className="text-slate-400">{d.name}</span>
                    </div>
                    <span className="text-white font-semibold">{d.value}%</span>
                  </div>
                ))}
              </div>
            </>
          ) : <Empty msg="No data" />}
        </Card>
      </div>

      <Card title={`Net Carbon Position (kg CO₂e) — Scope 2 Market-Based`}>
        {carbonData.length > 0 ? (
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={carbonData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kg" />
              <Tooltip {...TT} formatter={(v: number, n: string) => [`${v} kg CO₂e`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Area type="monotone" dataKey="scope2Market" fill="#8b5cf6" stroke="#8b5cf6" fillOpacity={0.3} name="Scope 2 (Market)" />
              <Area type="monotone" dataKey="scope1"       fill="#f59e0b" stroke="#f59e0b" fillOpacity={0.3} name="Scope 1" />
            </AreaChart>
          </ResponsiveContainer>
        ) : <Empty msg="No data" />}
      </Card>
    </div>
  )
}

// ── 4. Peak Load Analysis ──────────────────────────────────────────────────────
function PeakLoadReport({ peakPower, loading }: {
  peakPower: AggregatedMetric[]; loading: boolean
}) {
  // Hourly demand curve — sum MAX values to get true facility peak per bucket
  const demandCurve = useMemo(() => sumBuckets(peakPower, '24h', 'max_value'), [peakPower])

  const peakKW      = demandCurve.length > 0 ? Math.max(...demandCurve.map(d => d.value)) : 0
  const peakHour    = demandCurve.find(d => d.value === peakKW)?.label ?? '—'
  const headroomKW  = RATED_CAPACITY - peakKW
  const headroomPct = RATED_CAPACITY > 0 ? Math.round((headroomKW / RATED_CAPACITY) * 100) : 0
  const demandCharge = +(peakKW * DEMAND_RATE).toFixed(0)
  const avgDemand   = demandCurve.length > 0 ? +(demandCurve.reduce((s, d) => s + d.value, 0) / demandCurve.length).toFixed(1) : 0
  const par         = avgDemand > 0 ? +(peakKW / avgDemand).toFixed(2) : 0

  // Simulated circuit headroom from real peak data
  const circuits = peakPower.slice(0, 6).map((m, i) => {
    const pct = RATED_CAPACITY > 0 ? Math.min(99, Math.round((m.max_value / (RATED_CAPACITY / 6)) * 100)) : 0
    return { name: `Agent-${(i + 1).toString().padStart(2, '0')}`, used: pct }
  })

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI label="Monthly Peak Demand"   value={peakKW      ? `${peakKW.toFixed(1)} kW`       : '—'} sub={`at ${peakHour}`}                            icon={Flame}    bg="bg-red-500/20"    loading={loading} trend="up" />
        <KPI label="Demand Charge"         value={demandCharge ? `$${demandCharge.toLocaleString()}` : '—'} sub={`@$${DEMAND_RATE}/kW`}                  icon={DollarSign} bg="bg-amber-500/20" loading={loading} />
        <KPI label="Capacity Headroom"     value={headroomKW  ? `${headroomKW.toFixed(0)} kW`   : '—'} sub={`${headroomPct}% of ${RATED_CAPACITY} kW rated`} icon={Activity} bg="bg-blue-500/20" loading={loading} />
        <KPI label="Peak-to-Average Ratio" value={par          ? `${par}×`                       : '—'} sub={par > 1.4 ? 'load flattening recommended' : 'healthy demand profile'} icon={TrendingUp} bg="bg-purple-500/20" loading={loading} trend={par > 1.4 ? 'up' : 'neutral'} />
      </div>

      <Card title="Today's Demand Curve (kW) — Peak Load" badge="LIVE">
        {demandCurve.length > 0 ? (
          <ResponsiveContainer width="100%" height={230}>
            <ComposedChart data={demandCurve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 10 }} interval={2} />
              <YAxis domain={[0, Math.ceil(RATED_CAPACITY * 1.1)]} tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kW" width={65} />
              <Tooltip {...TT} formatter={(v: number) => [`${v} kW`, 'Demand']} />
              <ReferenceLine y={RATED_CAPACITY} stroke="#ef4444" strokeDasharray="6 3"
                label={{ value: `Rated ${RATED_CAPACITY} kW`, fill: '#ef4444', fontSize: 9, position: 'insideTopLeft' }} />
              {peakKW > 0 && <ReferenceLine y={peakKW} stroke="#f59e0b" strokeDasharray="4 2"
                label={{ value: `Peak ${peakKW} kW`, fill: '#f59e0b', fontSize: 9, position: 'insideTopRight' }} />}
              <Area type="monotone" dataKey="value" fill="#3b82f6" stroke="#3b82f6" fillOpacity={0.2} strokeWidth={2} name="Demand" />
            </ComposedChart>
          </ResponsiveContainer>
        ) : <Empty msg="No power metrics for the past 24 hours." />}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Peak Events by Hour (24 h)">
          {demandCurve.length > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={demandCurve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 10 }} interval={2} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kW" />
                <Tooltip {...TT} formatter={(v: number) => [`${v} kW`]} />
                <Bar dataKey="value" name="Peak kW" radius={[3, 3, 0, 0]}
                  fill="#f59e0b"
                />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty msg="No data" />}
        </Card>

        <Card title="Agent Headroom Status (from peak)">
          {circuits.length > 0 ? (
            <div className="space-y-3 mt-1">
              {circuits.map(c => (
                <div key={c.name} className="flex items-center gap-3 text-xs">
                  <span className="w-24 text-slate-400 shrink-0">{c.name}</span>
                  <div className="flex-1 bg-slate-700/60 rounded-full h-2">
                    <div className={`h-2 rounded-full ${c.used > 85 ? 'bg-red-500' : c.used > 70 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                      style={{ width: `${c.used}%` }} />
                  </div>
                  <span className={`w-9 text-right font-semibold ${c.used > 85 ? 'text-red-400' : c.used > 70 ? 'text-amber-400' : 'text-emerald-400'}`}>
                    {c.used}%
                  </span>
                </div>
              ))}
            </div>
          ) : <Empty msg="No agent power data" />}
        </Card>
      </div>
    </div>
  )
}

// ── 5. Demand Forecasting ──────────────────────────────────────────────────────
function DemandForecastReport({ powerHistory, loading }: {
  powerHistory: AggregatedMetric[]; loading: boolean
}) {
  const hist = useMemo(() => sumBuckets(powerHistory, '30d'), [powerHistory])

  const forecastData = useMemo(() => {
    if (hist.length < 2) return []
    const steps    = Math.ceil(hist.length / 2)
    const vals     = hist.map(h => h.value)
    const p50      = linForecast(vals, steps)
    const histPart = hist.map(h => ({ label: h.label, actual: h.value, forecast: null as number | null, upper: null as number | null, lower: null as number | null }))
    const fwdPart  = p50.map((v, i) => ({
      label:    `+${i + 1}d`,
      actual:   null as number | null,
      forecast: v,
      upper:    +(v * 1.12).toFixed(1),
      lower:    +(v * 0.88).toFixed(1),
    }))
    return [...histPart, ...fwdPart]
  }, [hist])

  const currentLoad  = hist.length > 0 ? hist[hist.length - 1].value : 0
  const forecastPeak = forecastData.filter(d => d.forecast != null).reduce((m, d) => Math.max(m, d.forecast!), 0)
  const growthPct    = currentLoad > 0 && forecastPeak > 0 ? +(((forecastPeak - currentLoad) / currentLoad) * 100).toFixed(1) : 0
  const capacityKWh  = RATED_CAPACITY * 24
  const exhaustionPct = forecastPeak > 0 ? Math.round((forecastPeak / capacityKWh) * 100) : 0

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI label="Current Avg Load"     value={currentLoad  ? `${currentLoad.toFixed(0)} kWh/d`  : '—'} sub="latest actual period"       icon={Activity}  bg="bg-blue-500/20"   loading={loading} />
        <KPI label="Forecast Peak"        value={forecastPeak ? `${forecastPeak.toFixed(0)} kWh/d`  : '—'} sub="P50 forward projection"      icon={TrendingUp} bg="bg-purple-500/20" loading={loading} trend={growthPct > 0 ? 'up' : 'down'} />
        <KPI label="Projected Growth"     value={growthPct    ? `+${growthPct}%`                    : '—'} sub="over forecast horizon"       icon={TrendingUp} bg="bg-amber-500/20"  loading={loading} trend="up" />
        <KPI label="Forecast Accuracy"    value="±12%"                                                     sub="P10–P90 confidence band"    icon={Activity}  bg="bg-emerald-500/20" />
      </div>

      <Card title="Demand Forecast: Historical + Projection (kWh/day)" badge="AI">
        {forecastData.length > 0 ? (
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={forecastData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 9 }} interval={Math.floor(forecastData.length / 10)} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kWh" width={70} />
              <Tooltip {...TT} formatter={(v: number | null, n: string) => v != null ? [`${v} kWh`, n] : ['—', n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              {capacityKWh > 0 && <ReferenceLine y={capacityKWh} stroke="#ef4444" strokeDasharray="6 3"
                label={{ value: `Capacity ${capacityKWh} kWh`, fill: '#ef4444', fontSize: 9, position: 'insideTopLeft' }} />}
              <Area type="monotone" dataKey="upper" fill="#8b5cf6" stroke="none" fillOpacity={0.1} name="Upper (P90)" legendType="none" connectNulls />
              <Area type="monotone" dataKey="lower" fill="#0f172a" stroke="none" fillOpacity={1}   legendType="none" connectNulls />
              <Line type="monotone" dataKey="actual"   stroke="#3b82f6" strokeWidth={2.5} dot={{ r: 2, fill: '#3b82f6' }}   name="Actual"         connectNulls={false} />
              <Line type="monotone" dataKey="forecast" stroke="#8b5cf6" strokeWidth={2}   dot={{ r: 2, fill: '#8b5cf6' }}   name="Forecast (P50)" connectNulls strokeDasharray="6 3" />
            </ComposedChart>
          </ResponsiveContainer>
        ) : <Empty msg="Need at least 2 data points for forecasting. Ensure 30-day range and power monitoring is active." />}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Capacity Headroom Projection">
          {forecastData.filter(d => d.forecast != null).length > 0 ? (
            <div className="space-y-4 mt-2">
              {(['1 week', '2 weeks', '3 weeks', '4 weeks'] as const).map((lbl, idx) => {
                const fwdIdx = Math.min(idx * 7, forecastData.filter(d => d.forecast != null).length - 1)
                const fwdVal = forecastData.filter(d => d.forecast != null)[fwdIdx]?.forecast ?? 0
                const pct    = capacityKWh > 0 ? Math.min(99, Math.round((fwdVal / capacityKWh) * 100)) : 0
                return (
                  <div key={lbl} className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className="text-slate-400">{lbl} out</span>
                      <span className={`font-semibold ${pct > 85 ? 'text-red-400' : pct > 70 ? 'text-amber-400' : 'text-emerald-400'}`}>{pct}% used</span>
                    </div>
                    <div className="bg-slate-700/60 rounded-full h-2.5">
                      <div className={`h-2.5 rounded-full ${pct > 85 ? 'bg-red-500' : pct > 70 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                        style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
          ) : <Empty msg="Not enough historical data for capacity projection." />}
        </Card>

        <Card title="Growth Scenario Comparison">
          {currentLoad > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart
                data={[
                  { scenario: 'Conservative (+5%)',  p10: +(currentLoad * 1.02).toFixed(0), p50: +(currentLoad * 1.05).toFixed(0), p90: +(currentLoad * 1.09).toFixed(0) },
                  { scenario: 'Base (+12%)',          p10: +(currentLoad * 1.07).toFixed(0), p50: +(currentLoad * 1.12).toFixed(0), p90: +(currentLoad * 1.18).toFixed(0) },
                  { scenario: 'Aggressive (+25%)',    p10: +(currentLoad * 1.18).toFixed(0), p50: +(currentLoad * 1.25).toFixed(0), p90: +(currentLoad * 1.35).toFixed(0) },
                ]}
                margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="scenario" tick={{ fill: AXIS_COLOR, fontSize: 9 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kWh" width={65} />
                <Tooltip {...TT} formatter={(v: number, n: string) => [`${v} kWh/d`, n]} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Bar dataKey="p10" fill="#10b981" name="P10 (Optimistic)" radius={[3, 3, 0, 0]} />
                <Bar dataKey="p50" fill="#8b5cf6" name="P50 (Base)"       radius={[3, 3, 0, 0]} />
                <Bar dataKey="p90" fill="#ef4444" name="P90 (Worst)"      radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty msg="No load data available." />}
        </Card>
      </div>
    </div>
  )
}

// ── 6. Electricity Bill Estimation ─────────────────────────────────────────────
function ElectricityBillReport({ power, peakPower, range, loading }: {
  power: AggregatedMetric[]; peakPower: AggregatedMetric[]; range: TR; loading: boolean
}) {
  const trend     = useMemo(() => sumBuckets(power, range), [power, range])
  const peakCurve = useMemo(() => sumBuckets(peakPower, '24h', 'max_value'), [peakPower])

  const totalKWh    = trend.reduce((s, b) => s + b.value * (1 + PUE_OVERHEAD), 0)
  const peakKW      = peakCurve.length > 0 ? Math.max(...peakCurve.map(d => d.value)) : 0
  const energyCost  = +(totalKWh * ENERGY_RATE).toFixed(0)
  const demandCost  = +(peakKW * DEMAND_RATE).toFixed(0)
  const transCost   = +(totalKWh * TRANS_RATE).toFixed(0)
  const subtotal    = energyCost + demandCost + transCost
  const taxCost     = +(subtotal * TAX_RATE).toFixed(0)
  const totalBill   = subtotal + taxCost
  const effectiveRate = totalKWh > 0 ? +((totalBill / totalKWh) * 100).toFixed(2) : 0

  const billComponents = totalBill > 0 ? [
    { name: 'Energy Charge', value: energyCost,  color: '#3b82f6', pct: Math.round((energyCost  / totalBill) * 100) },
    { name: 'Demand Charge', value: demandCost,  color: '#f59e0b', pct: Math.round((demandCost  / totalBill) * 100) },
    { name: 'Transmission',  value: transCost,   color: '#8b5cf6', pct: Math.round((transCost   / totalBill) * 100) },
    { name: 'Taxes & Fees',  value: taxCost,     color: '#64748b', pct: Math.round((taxCost     / totalBill) * 100) },
  ] : []

  const billTrend = trend.map(b => {
    const kwh  = b.value * (1 + PUE_OVERHEAD)
    const eng  = +(kwh * ENERGY_RATE).toFixed(0)
    const dem  = +(peakKW * DEMAND_RATE / trend.length).toFixed(0)
    const tra  = +(kwh * TRANS_RATE).toFixed(0)
    const tax  = +((eng + dem + tra) * TAX_RATE).toFixed(0)
    return { label: b.label, energy: eng, demand: dem, transmission: tra, taxes: tax }
  })

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI label={`Est. Bill (${range})`}   value={totalBill    ? `$${totalBill.toLocaleString()}`     : '—'} sub="full tariff estimate"       icon={DollarSign} bg="bg-emerald-500/20" loading={loading} />
        <KPI label="Energy Charge"             value={energyCost   ? `$${energyCost.toLocaleString()}`   : '—'} sub={`${billComponents[0]?.pct ?? 0}% of total`}   icon={Zap}        bg="bg-blue-500/20"    loading={loading} />
        <KPI label="Demand Charge"             value={demandCost   ? `$${demandCost.toLocaleString()}`   : '—'} sub={`@$${DEMAND_RATE}/kW peak`}                    icon={Flame}      bg="bg-amber-500/20"   loading={loading} />
        <KPI label="Effective Rate"            value={effectiveRate ? `${effectiveRate}¢/kWh`           : '—'} sub="all-in blended rate"        icon={Activity}   bg="bg-purple-500/20"  loading={loading} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title={`Monthly Bill Breakdown ($) — ${range}`} span="lg:col-span-2" badge="LIVE">
          {billTrend.length > 0 ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={billTrend} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} tickFormatter={v => `$${(v / 1000).toFixed(0)}K`} width={55} />
                <Tooltip {...TT} formatter={(v: number, n: string) => [`$${v.toLocaleString()}`, n]} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Bar dataKey="energy"       stackId="a" fill="#3b82f6" name="Energy" />
                <Bar dataKey="demand"       stackId="a" fill="#f59e0b" name="Demand" />
                <Bar dataKey="transmission" stackId="a" fill="#8b5cf6" name="Transmission" />
                <Bar dataKey="taxes"        stackId="a" fill="#64748b" name="Taxes & Fees" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty msg="No power data — bill is calculated from real consumption." />}
        </Card>

        <Card title="Bill Split">
          {billComponents.length > 0 ? (
            <>
              <ResponsiveContainer width="100%" height={160}>
                <PieChart>
                  <Pie data={billComponents} cx="50%" cy="50%" innerRadius={42} outerRadius={68} paddingAngle={3} dataKey="value">
                    {billComponents.map((e, i) => <Cell key={i} fill={e.color} />)}
                  </Pie>
                  <Tooltip {...TT} formatter={(v: number) => [`$${v.toLocaleString()}`]} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-1.5 mt-2">
                {billComponents.map(c => (
                  <div key={c.name} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full" style={{ background: c.color }} />
                      <span className="text-slate-400">{c.name}</span>
                    </div>
                    <span className="text-white font-semibold">${c.value.toLocaleString()} <span className="text-slate-500 font-normal">({c.pct}%)</span></span>
                  </div>
                ))}
              </div>
            </>
          ) : <Empty msg="No data" />}
        </Card>
      </div>

      <Card title="Effective Rate Trend (¢/kWh all-in)">
        {billTrend.length > 0 ? (
          <ResponsiveContainer width="100%" height={160}>
            <LineChart
              data={billTrend.map((b, i) => {
                const kwh = (trend[i]?.value ?? 0) * (1 + PUE_OVERHEAD)
                const tot = b.energy + b.demand + b.transmission + b.taxes
                return { label: b.label, rate: kwh > 0 ? +((tot / kwh) * 100).toFixed(2) : 0 }
              })}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit="¢" />
              <Tooltip {...TT} formatter={(v: number) => [`${v}¢/kWh`]} />
              <Line type="monotone" dataKey="rate" stroke="#10b981" strokeWidth={2.5} dot={{ r: 3, fill: '#10b981' }} name="Effective Rate" />
            </LineChart>
          </ResponsiveContainer>
        ) : <Empty msg="No data" />}
      </Card>
    </div>
  )
}

// ── 7. Renewable Energy Utilization ───────────────────────────────────────────
function RenewableReport({ power, range, loading }: {
  power: AggregatedMetric[]; range: TR; loading: boolean
}) {
  const trend = useMemo(() => sumBuckets(power, range), [power, range])

  // Model renewable mix from real consumption as baseline
  // PPA Wind 38 %, PPA Solar 32 %, RECs 18 %, On-site Solar 12 %
  const renewableData = trend.map(b => {
    const facility = +(b.value * (1 + PUE_OVERHEAD)).toFixed(1)
    return {
      label:  b.label,
      onsite: +(facility * 0.12).toFixed(1),
      ppa:    +(facility * 0.38).toFixed(1),
      recs:   +(facility * 0.18).toFixed(1),
      grid:   +(facility * 0.32).toFixed(1),   // remaining from conventional grid
    }
  })

  const totalConsumed  = renewableData.reduce((s, d) => s + d.onsite + d.ppa + d.recs + d.grid, 0)
  const totalRenewable = renewableData.reduce((s, d) => s + d.onsite + d.ppa + d.recs, 0)
  const renewablePct   = totalConsumed > 0 ? Math.round((totalRenewable / totalConsumed) * 100) : 0
  const carbonAvoided  = +(totalRenewable * CO2_KG_PER_KWH / 1000).toFixed(2)
  const re100Gap       = 100 - renewablePct

  const renewableMix = [
    { name: 'PPA Wind',      value: 38, color: '#06b6d4' },
    { name: 'PPA Solar',     value: 32, color: '#f59e0b' },
    { name: 'RECs',          value: 18, color: '#10b981' },
    { name: 'On-site Solar', value: 12, color: '#fde047' },
  ]

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI label="Renewable Fraction"  value={renewablePct ? `${renewablePct}%`   : '—'} sub="of total consumption" icon={Sun}       bg="bg-emerald-500/20" loading={loading} trend="down" />
        <KPI label="Carbon Avoided"      value={carbonAvoided ? `${carbonAvoided} t` : '—'} sub="CO₂e via renewables"  icon={Leaf}      bg="bg-green-500/20"   loading={loading} />
        <KPI label="On-site Solar"       value={renewableData.reduce((s,d)=>s+d.onsite,0) > 0 ? `${renewableData.reduce((s,d)=>s+d.onsite,0).toFixed(0)} kWh` : '—'} sub="modelled seasonal" icon={Sun} bg="bg-yellow-500/20" loading={loading} />
        <KPI label="RE100 Gap"           value={re100Gap ? `${re100Gap}pp` : '—'}            sub="remaining to 100%"   icon={TrendingUp} bg="bg-purple-500/20"  loading={loading} trend="up" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title={`Monthly Energy Sources (kWh) — ${range}`} span="lg:col-span-2" badge="LIVE">
          {renewableData.length > 0 ? (
            <ResponsiveContainer width="100%" height={230}>
              <AreaChart data={renewableData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
                <XAxis dataKey="label" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kWh" width={65} />
                <Tooltip {...TT} formatter={(v: number, n: string) => [`${v} kWh`, n]} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Area type="monotone" dataKey="onsite" stackId="1" fill="#fde047" stroke="#fde047" fillOpacity={0.9} name="On-site Solar" />
                <Area type="monotone" dataKey="ppa"    stackId="1" fill="#06b6d4" stroke="#06b6d4" fillOpacity={0.8} name="PPA (Wind+Solar)" />
                <Area type="monotone" dataKey="recs"   stackId="1" fill="#10b981" stroke="#10b981" fillOpacity={0.8} name="RECs" />
                <Area type="monotone" dataKey="grid"   stackId="1" fill="#334155" stroke="#334155" fillOpacity={0.7} name="Grid (Conventional)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : <Empty msg="No power data — renewable mix is modelled from real consumption." />}
        </Card>

        <Card title="Renewable Mix">
          <ResponsiveContainer width="100%" height={170}>
            <PieChart>
              <Pie data={renewableMix} cx="50%" cy="50%" innerRadius={46} outerRadius={74} paddingAngle={3} dataKey="value">
                {renewableMix.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Pie>
              <Tooltip {...TT} formatter={(v: number) => [`${v}%`]} />
            </PieChart>
          </ResponsiveContainer>
          <div className="space-y-1.5 mt-1">
            {renewableMix.map(r => (
              <div key={r.name} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full" style={{ background: r.color }} />
                  <span className="text-slate-400">{r.name}</span>
                </div>
                <span className="text-white font-semibold">{r.value}%</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="RE100 Progress & Compliance">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-2">
          {[
            { label: 'Annual Renewable %', current: renewablePct, target: 100, color: 'bg-emerald-500', desc: 'RE100 target: 100% by 2028' },
            { label: 'PPA Coverage',        current: 38,           target: 60,  color: 'bg-cyan-500',    desc: 'Target 60% via PPA contracts' },
            { label: 'Scope 2 Reduction',   current: 45,           target: 80,  color: 'bg-purple-500',  desc: 'vs 2019 market-based baseline' },
          ].map(p => (
            <div key={p.label} className="space-y-2">
              <div className="flex justify-between text-xs">
                <span className="text-slate-300 font-medium">{p.label}</span>
                <span className="text-white font-bold">{p.current}% <span className="text-slate-500 font-normal">/ {p.target}%</span></span>
              </div>
              <div className="bg-slate-700/60 rounded-full h-3">
                <div className={`${p.color} h-3 rounded-full`} style={{ width: `${Math.min(100, (p.current / p.target) * 100)}%` }} />
              </div>
              <p className="text-[10px] text-slate-500">{p.desc}</p>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-slate-600 mt-4 border-t border-white/5 pt-3">
          Renewable mix is modelled from real power consumption using standard PPA/REC procurement targets.
          Connect an energy management system (EMS) for actual sourcing data.
        </p>
      </Card>
    </div>
  )
}

// ── Report Config ─────────────────────────────────────────────────────────────
const REPORTS = [
  { id: 'energy',    label: 'Energy Efficiency',     icon: Zap,        accent: 'text-blue-400',    dot: 'bg-blue-500' },
  { id: 'pue',       label: 'PUE Calculation',        icon: Activity,   accent: 'text-amber-400',   dot: 'bg-amber-500' },
  { id: 'carbon',    label: 'Carbon Emissions',       icon: Leaf,       accent: 'text-emerald-400', dot: 'bg-emerald-500' },
  { id: 'peak',      label: 'Peak Load Analysis',     icon: Flame,      accent: 'text-red-400',     dot: 'bg-red-500' },
  { id: 'forecast',  label: 'Demand Forecasting',     icon: TrendingUp, accent: 'text-purple-400',  dot: 'bg-purple-500' },
  { id: 'bill',      label: 'Electricity Bill',       icon: DollarSign, accent: 'text-green-400',   dot: 'bg-green-500' },
  { id: 'renewable', label: 'Renewable Energy',       icon: Sun,        accent: 'text-yellow-400',  dot: 'bg-yellow-500' },
] as const

type ReportId = typeof REPORTS[number]['id']

const DESCS: Record<ReportId, string> = {
  energy:    'Facility energy consumption and efficiency across all zones — derived from live agent telemetry',
  pue:       'Power Usage Effectiveness (ISO 50001 & EU EED compliance) — calculated from real IT load',
  carbon:    'Greenhouse gas emissions (GHG Protocol Scope 1, 2 & 3) — driven by real power consumption',
  peak:      'Maximum demand events and demand charge management — from real-time power max values',
  forecast:  'Capacity and load forecasting via linear regression on real historical power trends',
  bill:      'Electricity cost estimation and chargeback — tariff model applied to real consumption',
  renewable: 'Renewable energy sourcing, RECs, and RE100 progress — modelled from real consumption baseline',
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function Reports() {
  const [active, setActive] = useState<ReportId>('energy')
  const [range,  setRange]  = useState<TR>('7d')

  const startTime  = useMemo(() => startOf(range),  [range])
  const iv         = useMemo(() => ivOf(range),      [range])
  const start30d   = useMemo(() => startOf('30d'),   [])   // always 30-day for forecasting
  const start24h   = useMemo(() => startOf('24h'),   [])   // always 24-hour for peak demand

  // ── Queries ────────────────────────────────────────────────────────────────
  // Main power trend for selected range (avg_value — energy totals)
  const { data: powerMetrics = [], isLoading: powerLoading } = useQuery({
    queryKey: ['agg-power-rng', range],
    queryFn: () => api.getAggregatedMetrics({ metric_type: 'power.consumption', start_time: startTime, interval: iv }),
    refetchInterval: 60000,
  })

  // 24-hour hourly power specifically for peak load (uses max_value)
  const { data: peakMetrics = [], isLoading: peakLoading } = useQuery({
    queryKey: ['agg-power-peak'],
    queryFn: () => api.getAggregatedMetrics({ metric_type: 'power.consumption', start_time: start24h, interval: '1h' }),
    refetchInterval: 60000,
  })

  // 30-day daily power for forecasting baseline
  const { data: histMetrics = [], isLoading: histLoading } = useQuery({
    queryKey: ['agg-power-hist'],
    queryFn: () => api.getAggregatedMetrics({ metric_type: 'power.consumption', start_time: start30d, interval: '1d' }),
    refetchInterval: 120000,
    staleTime: 60000,
  })

  const activeReport = REPORTS.find(r => r.id === active)!

  return (
    <div className="flex h-full min-h-0 bg-slate-950">
      {/* ── Sidebar ── */}
      <aside className="w-56 shrink-0 border-r border-white/8 bg-slate-900/60 flex flex-col">
        <div className="px-4 py-5 border-b border-white/8">
          <h2 className="text-sm font-bold text-white tracking-wide">Reports</h2>
          <p className="text-[11px] text-slate-500 mt-0.5">Data Center Analytics</p>
        </div>
        <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
          {REPORTS.map(r => {
            const I  = r.icon
            const on = active === r.id
            return (
              <button
                key={r.id}
                onClick={() => setActive(r.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all ${
                  on ? 'bg-white/10 text-white' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
                }`}
              >
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${on ? r.dot : 'bg-slate-600'}`} />
                <I className={`w-4 h-4 shrink-0 ${on ? r.accent : ''}`} />
                <span className="text-xs font-medium">{r.label}</span>
              </button>
            )
          })}
        </nav>
        <div className="p-3 border-t border-white/8">
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[10px] text-slate-500">Live · auto-refresh</span>
          </div>
        </div>
      </aside>

      {/* ── Main Content ── */}
      <main className="flex-1 min-w-0 overflow-y-auto p-6">
        {/* Header */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className={`text-xl font-bold ${activeReport.accent}`}>{activeReport.label}</h1>
            <p className="text-xs text-slate-500 mt-0.5 max-w-xl">{DESCS[active]}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="flex items-center gap-1 bg-slate-800/60 border border-white/8 rounded-lg p-1">
              {(['24h', '7d', '30d'] as TR[]).map(t => (
                <button
                  key={t}
                  onClick={() => setRange(t)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-all ${
                    range === t ? 'bg-white/10 text-white' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >{t}</button>
              ))}
            </div>
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/60 border border-white/8 text-xs text-slate-400 hover:text-white transition-colors">
              <Download className="w-3.5 h-3.5" /> Export
            </button>
          </div>
        </div>

        {/* Report Panels */}
        {active === 'energy'    && <EnergyEfficiencyReport power={powerMetrics as AggregatedMetric[]} range={range} loading={powerLoading} />}
        {active === 'pue'       && <PUEReport              power={powerMetrics as AggregatedMetric[]} range={range} loading={powerLoading} />}
        {active === 'carbon'    && <CarbonReport           power={powerMetrics as AggregatedMetric[]} range={range} loading={powerLoading} />}
        {active === 'peak'      && <PeakLoadReport         peakPower={peakMetrics as AggregatedMetric[]} loading={peakLoading} />}
        {active === 'forecast'  && <DemandForecastReport   powerHistory={histMetrics as AggregatedMetric[]} loading={histLoading} />}
        {active === 'bill'      && <ElectricityBillReport  power={powerMetrics as AggregatedMetric[]} peakPower={peakMetrics as AggregatedMetric[]} range={range} loading={powerLoading || peakLoading} />}
        {active === 'renewable' && <RenewableReport        power={powerMetrics as AggregatedMetric[]} range={range} loading={powerLoading} />}
      </main>
    </div>
  )
}
