import { useState } from 'react'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  PieChart, Pie, Cell, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  Zap, Leaf, TrendingUp, DollarSign, Sun,
  Download, ArrowUpRight, ArrowDownRight,
  Flame, Activity, Wind, Minus,
} from 'lucide-react'

// ── Mock Data ─────────────────────────────────────────────────────────────────

const energyData = [
  { month:'Jan', it:295, cooling:98, ups:18, other:14, total:425 },
  { month:'Feb', it:280, cooling:88, ups:17, other:13, total:398 },
  { month:'Mar', it:295, cooling:95, ups:18, other:14, total:422 },
  { month:'Apr', it:302, cooling:108, ups:18, other:14, total:442 },
  { month:'May', it:318, cooling:128, ups:19, other:15, total:480 },
  { month:'Jun', it:335, cooling:155, ups:20, other:16, total:526 },
  { month:'Jul', it:348, cooling:172, ups:21, other:16, total:557 },
  { month:'Aug', it:345, cooling:168, ups:21, other:16, total:550 },
  { month:'Sep', it:330, cooling:138, ups:20, other:15, total:503 },
  { month:'Oct', it:312, cooling:110, ups:19, other:14, total:455 },
  { month:'Nov', it:295, cooling:92,  ups:18, other:13, total:418 },
  { month:'Dec', it:290, cooling:90,  ups:17, other:13, total:410 },
]

const pueData = [
  { month:'Jan', pue:1.44, target:1.40, industry:1.56 },
  { month:'Feb', pue:1.42, target:1.40, industry:1.56 },
  { month:'Mar', pue:1.43, target:1.40, industry:1.56 },
  { month:'Apr', pue:1.46, target:1.40, industry:1.56 },
  { month:'May', pue:1.51, target:1.40, industry:1.56 },
  { month:'Jun', pue:1.57, target:1.40, industry:1.56 },
  { month:'Jul', pue:1.60, target:1.40, industry:1.56 },
  { month:'Aug', pue:1.59, target:1.40, industry:1.56 },
  { month:'Sep', pue:1.52, target:1.40, industry:1.56 },
  { month:'Oct', pue:1.46, target:1.40, industry:1.56 },
  { month:'Nov', pue:1.42, target:1.40, industry:1.56 },
  { month:'Dec', pue:1.41, target:1.40, industry:1.56 },
]

const carbonData = [
  { month:'Jan', scope1:8,  scope2Market:90,  avoided:74  },
  { month:'Feb', scope1:7,  scope2Market:85,  avoided:69  },
  { month:'Mar', scope1:8,  scope2Market:90,  avoided:73  },
  { month:'Apr', scope1:8,  scope2Market:94,  avoided:77  },
  { month:'May', scope1:9,  scope2Market:102, avoided:83  },
  { month:'Jun', scope1:10, scope2Market:112, avoided:91  },
  { month:'Jul', scope1:11, scope2Market:118, avoided:97  },
  { month:'Aug', scope1:10, scope2Market:117, avoided:95  },
  { month:'Sep', scope1:10, scope2Market:107, avoided:87  },
  { month:'Oct', scope1:9,  scope2Market:97,  avoided:79  },
  { month:'Nov', scope1:8,  scope2Market:89,  avoided:72  },
  { month:'Dec', scope1:8,  scope2Market:87,  avoided:71  },
]

const emissionSources = [
  { name: 'Cooling',       value: 42, color: '#3b82f6' },
  { name: 'IT Equipment',  value: 35, color: '#8b5cf6' },
  { name: 'UPS Losses',    value: 12, color: '#06b6d4' },
  { name: 'Diesel Gen.',   value: 6,  color: '#f59e0b' },
  { name: 'Refrigerants',  value: 3,  color: '#ef4444' },
  { name: 'Other',         value: 2,  color: '#64748b' },
]

const peakDemandData = [
  { hour:'00:00', demand:380 }, { hour:'01:00', demand:365 },
  { hour:'02:00', demand:358 }, { hour:'03:00', demand:352 },
  { hour:'04:00', demand:348 }, { hour:'05:00', demand:355 },
  { hour:'06:00', demand:375 }, { hour:'07:00', demand:418 },
  { hour:'08:00', demand:462 }, { hour:'09:00', demand:495 },
  { hour:'10:00', demand:518 }, { hour:'11:00', demand:532 },
  { hour:'12:00', demand:541 }, { hour:'13:00', demand:545 },
  { hour:'14:00', demand:558 }, { hour:'15:00', demand:551 },
  { hour:'16:00', demand:538 }, { hour:'17:00', demand:512 },
  { hour:'18:00', demand:478 }, { hour:'19:00', demand:445 },
  { hour:'20:00', demand:425 }, { hour:'21:00', demand:412 },
  { hour:'22:00', demand:400 }, { hour:'23:00', demand:390 },
]

const peakByHour = Array.from({ length: 24 }, (_, h) => ({
  hour: `${String(h).padStart(2,'0')}:00`,
  events: [3,1,0,0,0,0,1,2,4,6,8,9,10,11,12,10,9,7,5,4,3,2,2,3][h],
  avgPeak: [382,368,355,350,346,352,374,416,460,492,515,530,540,543,556,549,536,510,476,443,423,410,398,388][h],
}))

const forecastData = [
  { month:'Jan 24', actual:425, forecast:null, upper:null, lower:null },
  { month:'Feb 24', actual:398, forecast:null, upper:null, lower:null },
  { month:'Mar 24', actual:422, forecast:null, upper:null, lower:null },
  { month:'Apr 24', actual:442, forecast:null, upper:null, lower:null },
  { month:'May 24', actual:480, forecast:null, upper:null, lower:null },
  { month:'Jun 24', actual:526, forecast:null, upper:null, lower:null },
  { month:'Jul 24', actual:557, forecast:null, upper:null, lower:null },
  { month:'Aug 24', actual:550, forecast:null, upper:null, lower:null },
  { month:'Sep 24', actual:503, forecast:null, upper:null, lower:null },
  { month:'Oct 24', actual:455, forecast:null, upper:null, lower:null },
  { month:'Nov 24', actual:418, forecast:null, upper:null, lower:null },
  { month:'Dec 24', actual:410, forecast:410, upper:435, lower:385 },
  { month:'Jan 25', actual:null, forecast:448, upper:478, lower:418 },
  { month:'Feb 25', actual:null, forecast:428, upper:460, lower:396 },
  { month:'Mar 25', actual:null, forecast:455, upper:490, lower:420 },
  { month:'Apr 25', actual:null, forecast:475, upper:512, lower:438 },
  { month:'May 25', actual:null, forecast:518, upper:558, lower:478 },
  { month:'Jun 25', actual:null, forecast:572, upper:616, lower:528 },
  { month:'Jul 25', actual:null, forecast:602, upper:650, lower:554 },
  { month:'Aug 25', actual:null, forecast:594, upper:642, lower:546 },
  { month:'Sep 25', actual:null, forecast:546, upper:590, lower:502 },
  { month:'Oct 25', actual:null, forecast:494, upper:535, lower:453 },
  { month:'Nov 25', actual:null, forecast:456, upper:492, lower:420 },
  { month:'Dec 25', actual:null, forecast:448, upper:483, lower:413 },
]

const billData = [
  { month:'Jan', energy:36125, demand:6300, transmission:2800, taxes:2250 },
  { month:'Feb', energy:33830, demand:5880, transmission:2620, taxes:2100 },
  { month:'Mar', energy:35870, demand:6160, transmission:2760, taxes:2180 },
  { month:'Apr', energy:37570, demand:6440, transmission:2890, taxes:2260 },
  { month:'May', energy:40800, demand:6860, transmission:3080, taxes:2420 },
  { month:'Jun', energy:44710, demand:7420, transmission:3360, taxes:2640 },
  { month:'Jul', energy:47345, demand:7770, transmission:3520, taxes:2760 },
  { month:'Aug', energy:46750, demand:7700, transmission:3490, taxes:2740 },
  { month:'Sep', energy:42755, demand:7140, transmission:3220, taxes:2530 },
  { month:'Oct', energy:38675, demand:6580, transmission:2960, taxes:2325 },
  { month:'Nov', energy:35530, demand:6020, transmission:2710, taxes:2120 },
  { month:'Dec', energy:34850, demand:5880, transmission:2640, taxes:2070 },
]

const renewableData = [
  { month:'Jan', onsite:0,  ppa:118, recs:18, grid:289 },
  { month:'Feb', onsite:0,  ppa:111, recs:17, grid:270 },
  { month:'Mar', onsite:0,  ppa:118, recs:18, grid:286 },
  { month:'Apr', onsite:0,  ppa:121, recs:18, grid:303 },
  { month:'May', onsite:12, ppa:127, recs:19, grid:322 },
  { month:'Jun', onsite:28, ppa:134, recs:20, grid:344 },
  { month:'Jul', onsite:35, ppa:139, recs:21, grid:362 },
  { month:'Aug', onsite:32, ppa:138, recs:21, grid:359 },
  { month:'Sep', onsite:18, ppa:132, recs:20, grid:333 },
  { month:'Oct', onsite:5,  ppa:125, recs:19, grid:306 },
  { month:'Nov', onsite:0,  ppa:118, recs:18, grid:282 },
  { month:'Dec', onsite:0,  ppa:116, recs:17, grid:277 },
]

const renewableMix = [
  { name: 'PPA Wind',    value: 38, color: '#06b6d4' },
  { name: 'PPA Solar',   value: 32, color: '#f59e0b' },
  { name: 'RECs',        value: 18, color: '#10b981' },
  { name: 'On-site Solar', value: 12, color: '#fde047' },
]

// ── Shared Chart Config ────────────────────────────────────────────────────────

const GRID_COLOR = '#1e293b'
const AXIS_COLOR = '#475569'
const TOOLTIP_STYLE = {
  contentStyle: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#94a3b8' },
  itemStyle: { color: '#e2e8f0' },
}

// ── Helper Components ──────────────────────────────────────────────────────────

function KPICard({
  label, value, sub, icon: Icon, color, trend, trendLabel,
}: {
  label: string; value: string; sub?: string
  icon: React.ElementType; color: string
  trend?: 'up' | 'down' | 'neutral'; trendLabel?: string
}) {
  return (
    <div className="bg-slate-800/60 border border-white/8 rounded-xl p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-400 uppercase tracking-wide">{label}</span>
        <div className={`p-1.5 rounded-lg ${color}`}>
          <Icon className="w-3.5 h-3.5 text-white" />
        </div>
      </div>
      <p className="text-2xl font-bold text-white leading-none">{value}</p>
      {(sub || trendLabel) && (
        <div className="flex items-center gap-1.5 text-xs">
          {trend === 'up' && <ArrowUpRight className="w-3 h-3 text-red-400" />}
          {trend === 'down' && <ArrowDownRight className="w-3 h-3 text-emerald-400" />}
          {trend === 'neutral' && <Minus className="w-3 h-3 text-slate-400" />}
          <span className={
            trend === 'up' ? 'text-red-400' :
            trend === 'down' ? 'text-emerald-400' : 'text-slate-400'
          }>{trendLabel || sub}</span>
          {sub && trendLabel && <span className="text-slate-500">{sub}</span>}
        </div>
      )}
      {sub && !trendLabel && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function ChartCard({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-slate-800/60 border border-white/8 rounded-xl p-5 ${className}`}>
      <p className="text-sm font-semibold text-slate-300 mb-4">{title}</p>
      {children}
    </div>
  )
}

// ── Report: Energy Efficiency ──────────────────────────────────────────────────

function EnergyEfficiencyReport({ data }: { data: typeof energyData }) {
  const totalFacility = data.reduce((s, d) => s + d.total, 0)
  const totalIT = data.reduce((s, d) => s + d.it, 0)
  const totalCooling = data.reduce((s, d) => s + d.cooling, 0)
  const efficiency = Math.round((totalIT / totalFacility) * 100)

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard label="Facility Energy" value={`${(totalFacility/1000).toFixed(1)} GWh`} icon={Zap} color="bg-blue-500/20" trend="up" trendLabel="+4.2% YoY" sub="vs last year" />
        <KPICard label="IT Load" value={`${(totalIT/1000).toFixed(1)} GWh`} icon={Activity} color="bg-cyan-500/20" trend="up" trendLabel="+3.8% YoY" />
        <KPICard label="Cooling Overhead" value={`${(totalCooling/1000).toFixed(1)} GWh`} icon={Wind} color="bg-amber-500/20" trend="down" trendLabel="-1.2% vs target" />
        <KPICard label="IT Efficiency" value={`${efficiency}%`} sub="IT load / facility" icon={Activity} color="bg-emerald-500/20" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Monthly Energy Breakdown (MWh)" className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" MWh" width={70} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} MWh`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Area type="monotone" dataKey="it"      stackId="1" fill="#3b82f6" stroke="#3b82f6" fillOpacity={0.8} name="IT Load" />
              <Area type="monotone" dataKey="cooling" stackId="1" fill="#06b6d4" stroke="#06b6d4" fillOpacity={0.8} name="Cooling" />
              <Area type="monotone" dataKey="ups"     stackId="1" fill="#8b5cf6" stroke="#8b5cf6" fillOpacity={0.8} name="UPS Losses" />
              <Area type="monotone" dataKey="other"   stackId="1" fill="#f59e0b" stroke="#f59e0b" fillOpacity={0.8} name="Other" />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Energy Split">
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie data={[
                { name:'IT Load', value: totalIT, color:'#3b82f6' },
                { name:'Cooling', value: totalCooling, color:'#06b6d4' },
                { name:'UPS',     value: data.reduce((s,d)=>s+d.ups,0), color:'#8b5cf6' },
                { name:'Other',   value: data.reduce((s,d)=>s+d.other,0), color:'#f59e0b' },
              ]} cx="50%" cy="50%" innerRadius={55} outerRadius={85} paddingAngle={3} dataKey="value">
                {[{color:'#3b82f6'},{color:'#06b6d4'},{color:'#8b5cf6'},{color:'#f59e0b'}].map((e,i)=>(
                  <Cell key={i} fill={e.color} />
                ))}
              </Pie>
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [`${v} MWh`]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <ChartCard title="Overhead vs IT Load Trend (MWh)">
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
            <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
            <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} MWh`, n]} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
            <Line type="monotone" dataKey="total"   stroke="#94a3b8" strokeWidth={1.5} dot={false} name="Facility Total" strokeDasharray="4 2" />
            <Line type="monotone" dataKey="it"      stroke="#3b82f6" strokeWidth={2}   dot={false} name="IT Load" />
            <Line type="monotone" dataKey="cooling" stroke="#06b6d4" strokeWidth={2}   dot={false} name="Cooling" />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  )
}

// ── Report: PUE ───────────────────────────────────────────────────────────────

function PUEReport({ data }: { data: typeof pueData }) {
  const avgPUE = (data.reduce((s, d) => s + d.pue, 0) / data.length).toFixed(2)
  const minPUE = Math.min(...data.map(d => d.pue)).toFixed(2)
  const maxPUE = Math.max(...data.map(d => d.pue)).toFixed(2)
  const currentPUE = data[data.length - 1].pue
  const pueColor = currentPUE <= 1.45 ? 'text-emerald-400' : currentPUE <= 1.55 ? 'text-amber-400' : 'text-red-400'
  const pueBg = currentPUE <= 1.45 ? 'border-emerald-500/40 bg-emerald-500/5' : currentPUE <= 1.55 ? 'border-amber-500/40 bg-amber-500/5' : 'border-red-500/40 bg-red-500/5'

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* PUE Gauge Display */}
        <div className={`rounded-xl border p-6 flex flex-col items-center justify-center gap-3 ${pueBg}`}>
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">Current PUE</p>
          <p className={`text-7xl font-black tabular-nums ${pueColor}`}>{currentPUE.toFixed(2)}</p>
          <div className="flex flex-col items-center gap-1">
            <p className="text-xs text-slate-400">Target: <span className="text-white font-semibold">1.40</span></p>
            <p className="text-xs text-slate-400">Industry avg: <span className="text-slate-300">1.56</span></p>
            <p className="text-xs text-slate-400">Google/Hyperscale: <span className="text-emerald-400">1.10</span></p>
          </div>
          <div className="w-full bg-slate-700/60 rounded-full h-2 mt-2">
            <div
              className={`h-2 rounded-full ${currentPUE <= 1.45 ? 'bg-emerald-500' : currentPUE <= 1.55 ? 'bg-amber-500' : 'bg-red-500'}`}
              style={{ width: `${Math.min(100, ((currentPUE - 1.0) / (2.0 - 1.0)) * 100)}%` }}
            />
          </div>
          <div className="flex justify-between w-full text-[10px] text-slate-500">
            <span>1.0 (ideal)</span><span>2.0 (poor)</span>
          </div>
        </div>

        <div className="lg:col-span-2 grid grid-cols-3 gap-3 content-start">
          <KPICard label="Annual Avg PUE" value={avgPUE} icon={Activity} color="bg-blue-500/20" trend="down" trendLabel="-0.03 vs last yr" />
          <KPICard label="Best Month" value={minPUE} sub="Feb (low cooling load)" icon={Activity} color="bg-emerald-500/20" />
          <KPICard label="Worst Month" value={maxPUE} sub="Jul (peak summer)" icon={Flame} color="bg-red-500/20" />
          <div className="col-span-3 bg-slate-700/30 border border-white/5 rounded-lg p-3 text-xs text-slate-400 leading-relaxed">
            <span className="font-semibold text-slate-300">EU EED Compliance: </span>
            The EU Energy Efficiency Directive requires facilities &gt;500 kW to report annual PUE. Your 2024 annual average of <span className="text-amber-400 font-semibold">{avgPUE}</span> is above the 2030 target of <span className="text-emerald-400 font-semibold">1.40</span>. Summer cooling is the primary driver — consider free-cooling expansion or raised setpoints.
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="12-Month PUE Trend">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis domain={[1.3, 1.7]} tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <ReferenceLine y={1.40} stroke="#10b981" strokeDasharray="6 3" label={{ value: 'Target 1.40', fill: '#10b981', fontSize: 10, position: 'insideTopRight' }} />
              <ReferenceLine y={1.56} stroke="#64748b" strokeDasharray="4 2" label={{ value: 'Industry 1.56', fill: '#64748b', fontSize: 10, position: 'insideBottomRight' }} />
              <Line type="monotone" dataKey="pue" stroke="#f59e0b" strokeWidth={2.5} dot={{ fill: '#f59e0b', r: 3 }} name="PUE" />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="PUE Components Breakdown">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Bar dataKey="pue"    fill="#f59e0b" name="Actual PUE" radius={[3,3,0,0]} />
              <Bar dataKey="target" fill="#10b981" name="Target"     radius={[3,3,0,0]} opacity={0.5} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </div>
  )
}

// ── Report: Carbon Emissions ──────────────────────────────────────────────────

function CarbonReport({ data }: { data: typeof carbonData }) {
  const totalScope1 = data.reduce((s, d) => s + d.scope1, 0)
  const totalScope2 = data.reduce((s, d) => s + d.scope2Market, 0)
  const totalAvoided = data.reduce((s, d) => s + d.avoided, 0)
  const netCarbon = totalScope1 + totalScope2
  const cue = (netCarbon / (energyData.reduce((s,d)=>s+d.it,0) * 1000)).toFixed(3)

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard label="Total CO₂e (Scope 1+2)" value={`${netCarbon} t`} icon={Leaf} color="bg-red-500/20" trend="down" trendLabel="-8.3% YoY" />
        <KPICard label="Carbon Use Effect. (CUE)" value={cue} sub="kgCO₂/kWh IT" icon={Activity} color="bg-amber-500/20" />
        <KPICard label="Carbon Avoided" value={`${totalAvoided} t`} sub="via renewables" icon={Leaf} color="bg-emerald-500/20" />
        <KPICard label="Scope 1 Emissions" value={`${totalScope1} t`} sub="diesel + refrigerants" icon={Flame} color="bg-orange-500/20" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Monthly Emissions vs Avoided (tCO₂e)" className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" t" />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} tCO₂e`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Bar dataKey="scope2Market" stackId="a" fill="#8b5cf6" name="Scope 2 (Market)" radius={[0,0,0,0]} />
              <Bar dataKey="scope1"       stackId="a" fill="#f59e0b" name="Scope 1" radius={[3,3,0,0]} />
              <Bar dataKey="avoided"      fill="#10b981" name="Avoided (Renewables)" radius={[3,3,0,0]} opacity={0.7} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Emission Sources">
          <ResponsiveContainer width="100%" height={230}>
            <PieChart>
              <Pie data={emissionSources} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value">
                {emissionSources.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Pie>
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [`${v}%`]} />
              <Legend wrapperStyle={{ fontSize: 10, color: '#94a3b8' }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <ChartCard title="Net Carbon Position (tCO₂e) — Market-Based Scope 2">
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
            <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
            <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} tCO₂e`, n]} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
            <Area type="monotone" dataKey="scope2Market" fill="#8b5cf6" stroke="#8b5cf6" fillOpacity={0.3} name="Scope 2 (Market)" />
            <Area type="monotone" dataKey="scope1"       fill="#f59e0b" stroke="#f59e0b" fillOpacity={0.3} name="Scope 1" />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  )
}

// ── Report: Peak Load Analysis ────────────────────────────────────────────────

function PeakLoadReport() {
  const peakKW = 558
  const headroomKW = 600 - peakKW
  const headroomPct = Math.round((headroomKW / 600) * 100)
  const demandCharge = Math.round(peakKW * 14.5)

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard label="Monthly Peak Demand" value={`${peakKW} kW`} sub="Today 14:00 — record high" icon={Flame} color="bg-red-500/20" trend="up" trendLabel="+12 kW vs last month" />
        <KPICard label="Demand Charge" value={`$${demandCharge.toLocaleString()}`} sub="@$14.50/kW" icon={DollarSign} color="bg-amber-500/20" />
        <KPICard label="Capacity Headroom" value={`${headroomKW} kW`} sub={`${headroomPct}% of rated 600 kW`} icon={Activity} color="bg-blue-500/20" />
        <KPICard label="Peak-to-Average Ratio" value="1.49×" sub="high — load flattening recommended" icon={TrendingUp} color="bg-purple-500/20" trend="up" trendLabel="risk of demand ratchet" />
      </div>

      <ChartCard title="Today's Demand Curve (kW) — Peak at 14:00">
        <ResponsiveContainer width="100%" height={230}>
          <ComposedChart data={peakDemandData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis dataKey="hour" tick={{ fill: AXIS_COLOR, fontSize: 10 }} interval={2} />
            <YAxis domain={[300, 650]} tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" kW" width={65} />
            <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} kW`, n]} />
            <ReferenceLine y={600} stroke="#ef4444" strokeDasharray="6 3" label={{ value: 'Rated Capacity 600 kW', fill: '#ef4444', fontSize: 10, position: 'insideTopLeft' }} />
            <ReferenceLine y={558} stroke="#f59e0b" strokeDasharray="4 2" label={{ value: 'Peak 558 kW', fill: '#f59e0b', fontSize: 10, position: 'insideTopRight' }} />
            <Area type="monotone" dataKey="demand" fill="#3b82f6" stroke="#3b82f6" fillOpacity={0.2} name="Demand" strokeWidth={2} />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartCard>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="Peak Events by Hour of Day">
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={peakByHour.filter((_,i) => i % 2 === 0)} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="hour" tick={{ fill: AXIS_COLOR, fontSize: 10 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [`${v} events`]} />
              <Bar dataKey="events" fill="#f59e0b" name="Peak Events" radius={[3,3,0,0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Circuit Headroom Status">
          <div className="space-y-3 mt-1">
            {[
              { name: 'PDU-A1 / Row-1', used: 92, cap: 100, color: 'bg-red-500' },
              { name: 'PDU-A2 / Row-2', used: 78, cap: 100, color: 'bg-amber-500' },
              { name: 'PDU-B1 / Row-3', used: 65, cap: 100, color: 'bg-blue-500' },
              { name: 'PDU-B2 / Row-4', used: 58, cap: 100, color: 'bg-blue-500' },
              { name: 'PDU-C1 / Row-5', used: 43, cap: 100, color: 'bg-emerald-500' },
              { name: 'PDU-C2 / Row-6', used: 38, cap: 100, color: 'bg-emerald-500' },
            ].map(c => (
              <div key={c.name} className="flex items-center gap-3 text-xs">
                <span className="w-32 text-slate-400 shrink-0">{c.name}</span>
                <div className="flex-1 bg-slate-700/60 rounded-full h-2">
                  <div className={`${c.color} h-2 rounded-full transition-all`} style={{ width: `${c.used}%` }} />
                </div>
                <span className={`w-8 text-right font-semibold ${c.used > 85 ? 'text-red-400' : c.used > 70 ? 'text-amber-400' : 'text-emerald-400'}`}>{c.used}%</span>
              </div>
            ))}
          </div>
        </ChartCard>
      </div>
    </div>
  )
}

// ── Report: Demand Forecasting ────────────────────────────────────────────────

function DemandForecastReport() {
  const currentLoad = 455
  const forecastedLoad = 602
  const installedCapacity = 750
  const exhaustionMonths = 18

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard label="Current Avg Load" value={`${currentLoad} MWh/mo`} sub="Oct 2024 actual" icon={Activity} color="bg-blue-500/20" />
        <KPICard label="12-Month Forecast" value={`${forecastedLoad} MWh/mo`} sub="Jul 2025 peak" icon={TrendingUp} color="bg-purple-500/20" trend="up" trendLabel="+32% growth" />
        <KPICard label="Capacity Exhaustion" value={`${exhaustionMonths} months`} sub="at current growth rate" icon={Flame} color="bg-amber-500/20" trend="neutral" trendLabel="Jun 2026" />
        <KPICard label="Forecast Accuracy" value="96.8%" sub="MAPE 3.2% on 90-day test" icon={Activity} color="bg-emerald-500/20" />
      </div>

      <ChartCard title="Demand Forecast: Historical + 12-Month Projection (MWh/month)">
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={forecastData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 9 }} interval={2} />
            <YAxis domain={[350, 700]} tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" MWh" width={70} />
            <Tooltip {...TOOLTIP_STYLE} formatter={(v: number | null, n: string) => v != null ? [`${v} MWh`, n] : ['—', n]} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
            <ReferenceLine y={installedCapacity} stroke="#ef4444" strokeDasharray="6 3" label={{ value: `Capacity ${installedCapacity} MWh`, fill: '#ef4444', fontSize: 10, position: 'insideTopLeft' }} />
            <Area type="monotone" dataKey="upper" fill="#8b5cf6" stroke="none" fillOpacity={0.1} name="Upper Bound" legendType="none" connectNulls />
            <Area type="monotone" dataKey="lower" fill="#0f172a" stroke="none" fillOpacity={1} legendType="none" connectNulls />
            <Line type="monotone" dataKey="actual"   stroke="#3b82f6" strokeWidth={2.5} dot={{ r: 3, fill: '#3b82f6' }}   name="Actual"            connectNulls={false} />
            <Line type="monotone" dataKey="forecast" stroke="#8b5cf6" strokeWidth={2}   dot={{ r: 3, fill: '#8b5cf6' }}   name="Forecast (P50)"    connectNulls strokeDasharray="6 3" />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartCard>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="Capacity Headroom Projection">
          <div className="space-y-4 mt-2">
            {[
              { label: 'Today',    used: 61, month: 'Nov 2024' },
              { label: '3 months', used: 68, month: 'Feb 2025' },
              { label: '6 months', used: 74, month: 'May 2025' },
              { label: '9 months', used: 82, month: 'Aug 2025' },
              { label: '12 months',used: 90, month: 'Nov 2025' },
            ].map(r => (
              <div key={r.label} className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">{r.label} <span className="text-slate-600">({r.month})</span></span>
                  <span className={`font-semibold ${r.used > 85 ? 'text-red-400' : r.used > 75 ? 'text-amber-400' : 'text-emerald-400'}`}>{r.used}% used</span>
                </div>
                <div className="bg-slate-700/60 rounded-full h-2.5">
                  <div
                    className={`h-2.5 rounded-full transition-all ${r.used > 85 ? 'bg-red-500' : r.used > 75 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                    style={{ width: `${r.used}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </ChartCard>

        <ChartCard title="Scenario Comparison (12-Month Avg kWh/mo)">
          <ResponsiveContainer width="100%" height={210}>
            <BarChart
              data={[
                { scenario: 'Conservative\n(+8%)',  p10: 488, p50: 520, p90: 558 },
                { scenario: 'Base\n(+12%)',          p10: 515, p50: 570, p90: 625 },
                { scenario: 'Aggressive\n(+20%)',    p10: 558, p50: 642, p90: 726 },
              ]}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="scenario" tick={{ fill: AXIS_COLOR, fontSize: 10 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" MWh" width={65} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} MWh`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Bar dataKey="p10" fill="#10b981" name="P10 (Optimistic)" radius={[3,3,0,0]} />
              <Bar dataKey="p50" fill="#8b5cf6" name="P50 (Base)"       radius={[3,3,0,0]} />
              <Bar dataKey="p90" fill="#ef4444" name="P90 (Worst)"      radius={[3,3,0,0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </div>
  )
}

// ── Report: Electricity Bill Estimation ───────────────────────────────────────

function ElectricityBillReport({ data }: { data: typeof billData }) {
  const currentMonth = data[data.length - 1]
  const total = currentMonth.energy + currentMonth.demand + currentMonth.transmission + currentMonth.taxes
  const annualTotal = data.reduce((s, d) => s + d.energy + d.demand + d.transmission + d.taxes, 0)
  const effectiveRate = (total / (energyData[energyData.length - 1].total * 1000) * 100).toFixed(2)

  const billComponents = [
    { name: 'Energy Charge', value: currentMonth.energy, color: '#3b82f6', pct: Math.round(currentMonth.energy/total*100) },
    { name: 'Demand Charge', value: currentMonth.demand, color: '#f59e0b', pct: Math.round(currentMonth.demand/total*100) },
    { name: 'Transmission',  value: currentMonth.transmission, color: '#8b5cf6', pct: Math.round(currentMonth.transmission/total*100) },
    { name: 'Taxes & Fees',  value: currentMonth.taxes, color: '#64748b', pct: Math.round(currentMonth.taxes/total*100) },
  ]

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard label="Estimated Dec Bill" value={`$${total.toLocaleString()}`} icon={DollarSign} color="bg-emerald-500/20" trend="down" trendLabel="-2.1% vs Nov" />
        <KPICard label="Annual Total (2024)" value={`$${(annualTotal/1000).toFixed(0)}K`} icon={DollarSign} color="bg-blue-500/20" trend="up" trendLabel="+6.4% vs 2023" />
        <KPICard label="Demand Charge" value={`$${currentMonth.demand.toLocaleString()}`} sub={`${Math.round(currentMonth.demand/total*100)}% of total bill`} icon={Flame} color="bg-amber-500/20" />
        <KPICard label="Effective Rate" value={`${effectiveRate}¢/kWh`} sub="all-in blended rate" icon={Activity} color="bg-purple-500/20" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Monthly Bill Breakdown ($)" className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} tickFormatter={v => `$${(v/1000).toFixed(0)}K`} width={55} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`$${v.toLocaleString()}`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Bar dataKey="energy"       stackId="a" fill="#3b82f6" name="Energy"       />
              <Bar dataKey="demand"       stackId="a" fill="#f59e0b" name="Demand"       />
              <Bar dataKey="transmission" stackId="a" fill="#8b5cf6" name="Transmission" />
              <Bar dataKey="taxes"        stackId="a" fill="#64748b" name="Taxes & Fees" radius={[3,3,0,0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Current Month Bill Split">
          <ResponsiveContainer width="100%" height={160}>
            <PieChart>
              <Pie data={billComponents} cx="50%" cy="50%" innerRadius={45} outerRadius={72} paddingAngle={3} dataKey="value">
                {billComponents.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Pie>
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [`$${v.toLocaleString()}`]} />
            </PieChart>
          </ResponsiveContainer>
          <div className="space-y-2 mt-3">
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
        </ChartCard>
      </div>

      <ChartCard title="Cost per kWh Trend (effective all-in rate ¢/kWh)">
        <ResponsiveContainer width="100%" height={160}>
          <LineChart
            data={data.map((d, i) => ({
              month: d.month,
              rate: +((d.energy + d.demand + d.transmission + d.taxes) / (energyData[i].total * 1000) * 100).toFixed(2),
            }))}
            margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
            <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit="¢" />
            <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [`${v}¢/kWh`]} />
            <Line type="monotone" dataKey="rate" stroke="#10b981" strokeWidth={2.5} dot={{ r: 3, fill: '#10b981' }} name="Effective Rate" />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  )
}

// ── Report: Renewable Energy Utilization ──────────────────────────────────────

function RenewableEnergyReport({ data }: { data: typeof renewableData }) {
  const totalRenewable = data.reduce((s, d) => s + d.onsite + d.ppa + d.recs, 0)
  const totalConsumed  = data.reduce((s, d) => s + d.onsite + d.ppa + d.recs + d.grid, 0)
  const renewablePct = Math.round((totalRenewable / totalConsumed) * 100)
  const carbonAvoided = Math.round(totalRenewable * 0.386)
  const re100Gap = 100 - renewablePct

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard label="Renewable Fraction" value={`${renewablePct}%`} sub="of total consumption" icon={Sun} color="bg-emerald-500/20" trend="up" trendLabel="+5pp vs 2023" />
        <KPICard label="Carbon Avoided" value={`${carbonAvoided} t`} sub="CO₂e via renewables" icon={Leaf} color="bg-green-500/20" />
        <KPICard label="On-site Solar" value="130 MWh" sub="May–Oct only (seasonal)" icon={Sun} color="bg-yellow-500/20" />
        <KPICard label="RE100 Gap" value={`${re100Gap}pp`} sub="remaining to 100% target" icon={TrendingUp} color="bg-purple-500/20" trend="up" trendLabel="target: 2028" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Monthly Energy Sources (MWh)" className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={230}>
            <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
              <XAxis dataKey="month" tick={{ fill: AXIS_COLOR, fontSize: 11 }} />
              <YAxis tick={{ fill: AXIS_COLOR, fontSize: 11 }} unit=" MWh" width={65} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number, n: string) => [`${v} MWh`, n]} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              <Area type="monotone" dataKey="onsite" stackId="1" fill="#fde047" stroke="#fde047" fillOpacity={0.9} name="On-site Solar" />
              <Area type="monotone" dataKey="ppa"    stackId="1" fill="#06b6d4" stroke="#06b6d4" fillOpacity={0.8} name="PPA" />
              <Area type="monotone" dataKey="recs"   stackId="1" fill="#10b981" stroke="#10b981" fillOpacity={0.8} name="RECs" />
              <Area type="monotone" dataKey="grid"   stackId="1" fill="#334155" stroke="#334155" fillOpacity={0.7} name="Grid (Conventional)" />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Renewable Mix">
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie data={renewableMix} cx="50%" cy="50%" innerRadius={48} outerRadius={76} paddingAngle={3} dataKey="value">
                {renewableMix.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Pie>
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [`${v}%`]} />
            </PieChart>
          </ResponsiveContainer>
          <div className="space-y-2 mt-2">
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
        </ChartCard>
      </div>

      <ChartCard title="RE100 Progress & Compliance">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-2">
          {[
            { label: 'Annual Renewable %', current: renewablePct, target: 100, color: 'bg-emerald-500', desc: 'RE100 target: 100% by 2028' },
            { label: 'PPA Coverage',       current: 38, target: 60, color: 'bg-cyan-500', desc: 'Target 60% via PPA contracts' },
            { label: 'Scope 2 Reduction',  current: 45, target: 80, color: 'bg-purple-500', desc: 'Market-based vs 2019 baseline' },
          ].map(p => (
            <div key={p.label} className="space-y-2">
              <div className="flex justify-between text-xs">
                <span className="text-slate-300 font-medium">{p.label}</span>
                <span className="text-white font-bold">{p.current}%</span>
              </div>
              <div className="bg-slate-700/60 rounded-full h-3">
                <div className={`${p.color} h-3 rounded-full`} style={{ width: `${(p.current/p.target)*100}%` }} />
              </div>
              <div className="flex justify-between text-[10px] text-slate-500">
                <span>{p.desc}</span>
                <span>Target {p.target}%</span>
              </div>
            </div>
          ))}
        </div>
      </ChartCard>
    </div>
  )
}

// ── Report Types Config ────────────────────────────────────────────────────────

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

// ── Main Reports Page ──────────────────────────────────────────────────────────

export default function Reports() {
  const [active, setActive] = useState<ReportId>('energy')
  const [timeRange, setTimeRange] = useState<'3M' | '6M' | '12M'>('12M')

  const slice = <T,>(arr: T[]) => {
    const n = timeRange === '3M' ? 3 : timeRange === '6M' ? 6 : 12
    return arr.slice(-n)
  }

  const activeReport = REPORTS.find(r => r.id === active)!

  return (
    <div className="flex h-full min-h-0 bg-slate-950">
      {/* ── Left Report Selector ── */}
      <aside className="w-56 shrink-0 border-r border-white/8 bg-slate-900/60 flex flex-col">
        <div className="px-4 py-5 border-b border-white/8">
          <h2 className="text-sm font-bold text-white tracking-wide">Reports</h2>
          <p className="text-[11px] text-slate-500 mt-0.5">Data Center Analytics</p>
        </div>
        <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
          {REPORTS.map(r => {
            const Icon = r.icon
            const isActive = active === r.id
            return (
              <button
                key={r.id}
                onClick={() => setActive(r.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left text-sm transition-all ${
                  isActive
                    ? 'bg-white/10 text-white'
                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
                }`}
              >
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${isActive ? r.dot : 'bg-slate-600'}`} />
                <Icon className={`w-4 h-4 shrink-0 ${isActive ? r.accent : ''}`} />
                <span className="text-xs font-medium leading-tight">{r.label}</span>
              </button>
            )
          })}
        </nav>
        <div className="p-3 border-t border-white/8 text-[10px] text-slate-600">
          Based on 2024 sensor data
        </div>
      </aside>

      {/* ── Main Content ── */}
      <main className="flex-1 min-w-0 overflow-y-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className={`text-xl font-bold ${activeReport.accent}`}>{activeReport.label}</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {active === 'energy'    && 'Facility energy consumption and efficiency across all zones'}
              {active === 'pue'       && 'Power Usage Effectiveness — ISO 50001 & EU EED compliance'}
              {active === 'carbon'    && 'Greenhouse gas emissions — GHG Protocol Scope 1, 2 & 3'}
              {active === 'peak'      && 'Maximum demand events and demand charge management'}
              {active === 'forecast'  && 'ML-powered capacity and load forecasting (LSTM model)'}
              {active === 'bill'      && 'Electricity cost estimation and tenant chargeback'}
              {active === 'renewable' && 'Renewable energy sourcing, RECs, and RE100 progress'}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* Time range */}
            <div className="flex items-center gap-1 bg-slate-800/60 border border-white/8 rounded-lg p-1">
              {(['3M','6M','12M'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setTimeRange(t)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-all ${
                    timeRange === t ? 'bg-white/10 text-white' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >{t}</button>
              ))}
            </div>
            <button className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/60 border border-white/8 text-xs text-slate-400 hover:text-white transition-colors">
              <Download className="w-3.5 h-3.5" />
              Export PDF
            </button>
          </div>
        </div>

        {/* Report Content */}
        {active === 'energy'    && <EnergyEfficiencyReport data={slice(energyData)} />}
        {active === 'pue'       && <PUEReport data={slice(pueData)} />}
        {active === 'carbon'    && <CarbonReport data={slice(carbonData)} />}
        {active === 'peak'      && <PeakLoadReport />}
        {active === 'forecast'  && <DemandForecastReport />}
        {active === 'bill'      && <ElectricityBillReport data={slice(billData)} />}
        {active === 'renewable' && <RenewableEnergyReport data={slice(renewableData)} />}
      </main>
    </div>
  )
}
