import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, Play, RotateCcw, Square, Zap, Cpu, MemoryStick, Wifi, WifiOff, CheckCircle, XCircle, Loader2, ChevronDown, ChevronUp, Bell } from 'lucide-react'
import { toast } from 'react-hot-toast'
import { api } from '@/lib/api'

const ORC = '/orchestrator'

async function orcFetch(path: string, opts?: RequestInit) {
  const r = await fetch(ORC + path, opts)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface ContainerStatus { [name: string]: string }
interface SimStatus {
  network_id: string
  device_count: number
  store_running: boolean
  snmp: { enabled: boolean; running: boolean }
  gnmi: { enabled: boolean; running: boolean }
  sflow: { enabled: boolean; running: boolean }
}
interface SimRow { container: string; data?: SimStatus; error?: string }

// ── Small helpers ─────────────────────────────────────────────────────────────

const statusColor = (s: string) => {
  if (s === 'running') return 'text-green-400'
  if (s === 'exited' || s === 'not_found') return 'text-red-400'
  return 'text-yellow-400'
}

const Proto = ({ label, ok }: { label: string; ok: boolean }) => (
  <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${ok ? 'bg-green-500/15 text-green-400' : 'bg-slate-700 text-slate-500'}`}>
    {ok ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
    {label}
  </span>
)

// ── Main component ────────────────────────────────────────────────────────────

export default function SimulatorControl() {
  const qc = useQueryClient()
  const [expandedLogs, setExpandedLogs] = useState<string | null>(null)
  const [logs, setLogs] = useState<Record<string, string>>({})
  const [customFault, setCustomFault] = useState({ container: '', fault_type: 'cpu_spike', device_name: '', value: '95' })
  const [customTrap, setCustomTrap] = useState({ container: 'sim-network-a', device_name: '', trap_type: 'CPU_HIGH', severity: 'major' })

  // ── Queries ─────────────────────────────────────────────────────────────────

  const { data: dockerStatus = {} } = useQuery<ContainerStatus>({
    queryKey: ['orch-docker'],
    queryFn: () => orcFetch('/containers'),
    refetchInterval: 5000,
  })

  const { data: simStatus = [] } = useQuery<SimRow[]>({
    queryKey: ['orch-sim-status'],
    queryFn: () => orcFetch('/status').then(d => d.sim_containers ?? []),
    refetchInterval: 10000,
  })

  const { data: scenarios = [] } = useQuery<string[]>({
    queryKey: ['orch-scenarios'],
    queryFn: () => orcFetch('/scenarios').then(d => d.scenarios ?? []),
  })

  const { data: devices = [] } = useQuery<{ container: string; data?: any[] }[]>({
    queryKey: ['orch-devices'],
    queryFn: () => orcFetch('/devices'),
    refetchInterval: 30000,
  })

  const { data: recentAlerts = [] } = useQuery<any[]>({
    queryKey: ['sim-recent-alerts'],
    queryFn: () => api.getAlerts({ limit: 30 }),
    refetchInterval: 10000,
  })

  const { data: telemetryRaw = [] } = useQuery<{ container: string; data?: { gnmi: any[]; sflow: any[] }; error?: string }[]>({
    queryKey: ['orch-telemetry'],
    queryFn: () => orcFetch('/telemetry?limit=20'),
    refetchInterval: 15000,
  })

  // Flatten + sort telemetry events across all containers
  const gnmiEvents = telemetryRaw
    .flatMap(r => r.data?.gnmi ?? [])
    .sort((a: any, b: any) => b.timestamp.localeCompare(a.timestamp))
    .slice(0, 20)
  const sflowEvents = telemetryRaw
    .flatMap(r => r.data?.sflow ?? [])
    .sort((a: any, b: any) => b.timestamp.localeCompare(a.timestamp))
    .slice(0, 20)

  // ── Mutations ────────────────────────────────────────────────────────────────

  const restart = useMutation({
    mutationFn: (name: string) => orcFetch(`/containers/${name}/restart`, { method: 'POST' }),
    onSuccess: (_, name) => { toast.success(`Restarted ${name}`); qc.invalidateQueries({ queryKey: ['orch-docker'] }) },
    onError: (_, name) => toast.error(`Failed to restart ${name}`),
  })

  const runScenario = useMutation({
    mutationFn: (name: string) => orcFetch(`/scenarios/${name}/run`, { method: 'POST' }),
    onSuccess: (_, name) => toast.success(`Scenario "${name}" running`),
    onError: (_, name) => toast.error(`Scenario "${name}" failed`),
  })

  const clearFaults = useMutation({
    mutationFn: () => fetch(ORC + '/fault', { method: 'DELETE' }).then(r => r.json()),
    onSuccess: () => { toast.success('All faults cleared'); qc.invalidateQueries({ queryKey: ['orch-sim-status'] }) },
  })

  const injectFault = useMutation({
    mutationFn: (body: object) => orcFetch('/fault', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    onSuccess: () => toast.success('Fault injected'),
    onError: () => toast.error('Fault injection failed'),
  })

  const fireScenarioFault = (fault_type: string, value?: number) =>
    injectFault.mutate({ fault_type, value, container: null })

  const fetchLogs = async (name: string) => {
    if (expandedLogs === name) { setExpandedLogs(null); return }
    setExpandedLogs(name)
    try {
      const r = await orcFetch(`/containers/${name}/logs`)
      setLogs(prev => ({ ...prev, [name]: r.logs ?? '' }))
    } catch { setLogs(prev => ({ ...prev, [name]: 'Failed to fetch logs' })) }
  }

  const fireTrap = useMutation({
    mutationFn: (body: object) => orcFetch('/trap', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    onSuccess: () => toast.success('Trap fired'),
    onError: () => toast.error('Trap failed'),
  })

  // ── Derived ─────────────────────────────────────────────────────────────────

  const simContainers = ['sim-network-a', 'sim-network-b', 'sim-network-c']
  const dcimContainers = ['dcim-server-a', 'dcim-server-b', 'dcim-server-c', 'aggregator', 'dcim-ui']
  const infraContainers = ['timescaledb', 'redis', 'orchestrator']

  const totalDevices = devices.reduce((sum, d) => sum + (d.data?.length ?? 0), 0)

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6 text-white max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Simulator Control</h1>
          <p className="text-slate-400 text-sm mt-1">{totalDevices} simulated devices across {simContainers.length} networks</p>
        </div>
        <button
          onClick={() => clearFaults.mutate()}
          disabled={clearFaults.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm font-medium transition-colors"
        >
          {clearFaults.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
          Clear All Faults
        </button>
      </div>

      {/* ── Network Simulators ─────────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Network Simulators</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {simContainers.map(name => {
            const docker = dockerStatus[name] ?? 'unknown'
            const sim = simStatus.find(s => s.container === name)
            const devCount = devices.find(d => d.container === name)?.data?.length ?? 0

            return (
              <div key={name} className="bg-slate-900/60 border border-white/10 rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-semibold text-sm">{name}</p>
                    <p className={`text-xs font-medium ${statusColor(docker)}`}>{docker}</p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => restart.mutate(name)}
                      disabled={restart.isPending}
                      className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors"
                      title="Restart container"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => fetchLogs(name)}
                      className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors"
                      title="View logs"
                    >
                      {expandedLogs === name ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>

                {sim?.data && (
                  <>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      <Activity className="w-3.5 h-3.5" />
                      <span>{devCount} devices</span>
                      <span className="text-slate-600">·</span>
                      <span className={sim.data.store_running ? 'text-green-400' : 'text-red-400'}>
                        {sim.data.store_running ? 'ticking' : 'stopped'}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Proto label="SNMP" ok={sim.data.snmp.running} />
                      <Proto label="gNMI" ok={sim.data.gnmi.running} />
                      <Proto label="sFlow" ok={sim.data.sflow.running} />
                    </div>
                  </>
                )}

                {sim?.error && <p className="text-xs text-red-400">{sim.error}</p>}

                {expandedLogs === name && (
                  <pre className="text-[10px] bg-black/50 rounded p-2 max-h-40 overflow-y-auto text-slate-300 whitespace-pre-wrap break-all">
                    {logs[name] ?? 'Loading…'}
                  </pre>
                )}
              </div>
            )
          })}
        </div>
      </section>

      {/* ── Quick Fault Injection ─────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Quick Fault Injection — All Networks</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'CPU Spike 95%', icon: Cpu, fault: 'cpu_spike', value: 95, color: 'hover:border-orange-500/50' },
            { label: 'Memory 90%', icon: MemoryStick, fault: 'memory_spike', value: 90, color: 'hover:border-yellow-500/50' },
            { label: 'Stress (all)', icon: Zap, fault: null, profile: 'stress', color: 'hover:border-red-500/50' },
            { label: 'Idle (all)', icon: Activity, fault: null, profile: 'idle', color: 'hover:border-blue-500/50' },
          ].map(({ label, icon: Icon, fault, value, profile, color }) => (
            <button
              key={label}
              onClick={() => {
                if (fault) fireScenarioFault(fault, value)
                else orcFetch('/profile', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile }) }).then(() => toast.success(`Profile: ${profile}`))
              }}
              disabled={injectFault.isPending}
              className={`flex flex-col items-center gap-2 p-4 bg-slate-900/60 border border-white/10 rounded-xl text-sm font-medium transition-all cursor-pointer ${color}`}
            >
              <Icon className="w-5 h-5 text-slate-400" />
              <span className="text-xs text-center">{label}</span>
            </button>
          ))}
        </div>
      </section>

      {/* ── Custom Fault ─────────────────────────────────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Custom fault */}
        <div className="bg-slate-900/60 border border-white/10 rounded-xl p-5 space-y-3">
          <h2 className="font-semibold text-sm">Custom Fault</h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400">Container</label>
              <select value={customFault.container} onChange={e => setCustomFault(p => ({ ...p, container: e.target.value }))}
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
                <option value="">All</option>
                {simContainers.map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400">Fault Type</label>
              <select value={customFault.fault_type} onChange={e => setCustomFault(p => ({ ...p, fault_type: e.target.value }))}
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
                {['cpu_spike','memory_spike','link_down','link_up','device_down','device_up'].map(f => <option key={f}>{f}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400">Device Name (optional)</label>
              <input value={customFault.device_name} onChange={e => setCustomFault(p => ({ ...p, device_name: e.target.value }))}
                placeholder="e.g. SRV-A1-01"
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm" />
            </div>
            <div>
              <label className="text-xs text-slate-400">Value</label>
              <input type="number" value={customFault.value} onChange={e => setCustomFault(p => ({ ...p, value: e.target.value }))}
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm" />
            </div>
          </div>
          <button
            onClick={() => injectFault.mutate({ fault_type: customFault.fault_type, container: customFault.container || null, device_name: customFault.device_name || null, value: parseFloat(customFault.value) || null })}
            disabled={injectFault.isPending}
            className="w-full flex items-center justify-center gap-2 py-2 bg-orange-600 hover:bg-orange-500 rounded-lg text-sm font-medium transition-colors"
          >
            {injectFault.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <AlertTriangle className="w-4 h-4" />}
            Inject Fault
          </button>
        </div>

        {/* Fire trap */}
        <div className="bg-slate-900/60 border border-white/10 rounded-xl p-5 space-y-3">
          <h2 className="font-semibold text-sm">Fire SNMP Trap</h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400">Container</label>
              <select value={customTrap.container} onChange={e => setCustomTrap(p => ({ ...p, container: e.target.value }))}
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
                {simContainers.map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400">Trap Type</label>
              <select value={customTrap.trap_type} onChange={e => setCustomTrap(p => ({ ...p, trap_type: e.target.value }))}
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
                {['CPU_HIGH','MEMORY_HIGH','LINK_DOWN','LINK_UP','DEVICE_DOWN','FAN_FAILURE','POWER_LOSS'].map(t => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400">Severity</label>
              <select value={customTrap.severity} onChange={e => setCustomTrap(p => ({ ...p, severity: e.target.value }))}
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
                {['critical','major','minor','informational'].map(s => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400">Device Name</label>
              <input value={customTrap.device_name} onChange={e => setCustomTrap(p => ({ ...p, device_name: e.target.value }))}
                placeholder="e.g. ToR-A1"
                className="w-full mt-1 bg-slate-800 border border-white/10 rounded-lg px-3 py-1.5 text-sm" />
            </div>
          </div>
          <button
            onClick={() => fireTrap.mutate({ container: customTrap.container, device_name: customTrap.device_name, trap_type: customTrap.trap_type, severity: customTrap.severity })}
            disabled={fireTrap.isPending || !customTrap.device_name}
            className="w-full flex items-center justify-center gap-2 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors"
          >
            {fireTrap.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            Fire Trap
          </button>
        </div>
      </section>

      {/* ── Scenarios ─────────────────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Pre-Built Scenarios</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {scenarios.map(name => (
            <button
              key={name}
              onClick={() => runScenario.mutate(name)}
              disabled={runScenario.isPending}
              className="flex items-center gap-2 px-4 py-3 bg-slate-900/60 border border-white/10 hover:border-blue-500/50 rounded-xl text-sm font-medium text-left transition-all cursor-pointer"
            >
              <Play className="w-4 h-4 text-blue-400 shrink-0" />
              <span className="text-xs">{name.replace(/_/g, ' ')}</span>
            </button>
          ))}
        </div>
      </section>

      {/* ── Active Alerts ────────────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Bell className="w-4 h-4" />
          Active Alerts
          {recentAlerts.filter((a: any) => !a.resolved).length > 0 && (
            <span className="text-xs bg-red-500/20 text-red-400 px-2 py-0.5 rounded-full font-mono">
              {recentAlerts.filter((a: any) => !a.resolved).length} active
            </span>
          )}
        </h2>
        <div className="bg-slate-900/60 border border-white/10 rounded-xl overflow-hidden">
          {recentAlerts.length === 0
            ? <p className="text-slate-600 text-sm p-4">No recent alerts. Inject a fault or run a stress profile to generate them.</p>
            : <table className="w-full text-xs text-slate-300">
                <thead>
                  <tr className="border-b border-white/10 text-slate-500">
                    <th className="text-left px-4 py-2 font-medium">Severity</th>
                    <th className="text-left px-4 py-2 font-medium">Device</th>
                    <th className="text-left px-4 py-2 font-medium">Type</th>
                    <th className="text-left px-4 py-2 font-medium">Message</th>
                    <th className="text-left px-4 py-2 font-medium">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {recentAlerts.slice(0, 15).map((a: any, i: number) => {
                    const sev = (a.severity || 'INFO').toUpperCase()
                    const sevColor = sev === 'CRITICAL' ? 'text-red-400' : sev === 'WARNING' ? 'text-yellow-400' : 'text-blue-400'
                    const device = a.agent_id?.split('-').slice(2).join('-') || a.agent_id || '—'
                    const ts = a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : '—'
                    return (
                      <tr key={i} className={`border-b border-white/5 hover:bg-white/5 ${a.resolved ? 'opacity-40' : ''}`}>
                        <td className={`px-4 py-2 font-semibold ${sevColor}`}>{sev}</td>
                        <td className="px-4 py-2 font-mono">{device}</td>
                        <td className="px-4 py-2 text-slate-400">{a.metric_type || '—'}</td>
                        <td className="px-4 py-2 text-slate-400 max-w-xs truncate">{a.message || '—'}</td>
                        <td className="px-4 py-2 text-slate-500 font-mono">{ts}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
          }
        </div>
      </section>

      {/* ── Telemetry Live Feed ──────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Telemetry Live Feed</h2>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {/* gNMI */}
          <div className="bg-slate-900/60 border border-white/10 rounded-xl p-4">
            <p className="text-xs font-semibold text-blue-400 mb-3">gNMI — last {gnmiEvents.length} updates</p>
            {gnmiEvents.length === 0
              ? <p className="text-slate-600 text-xs">No events yet. Metrics post every 30 s after containers start.</p>
              : <div className="space-y-1 max-h-56 overflow-y-auto text-xs font-mono">
                  {gnmiEvents.map((e: any, i: number) => (
                    <div key={i} className="flex gap-2 text-slate-300 border-b border-white/5 pb-1">
                      <span className="text-slate-500 shrink-0">{new Date(e.timestamp).toLocaleTimeString()}</span>
                      <span className="text-green-300 shrink-0">{e.device}</span>
                      <span className="text-slate-400">{e.path}</span>
                      <span className="text-slate-500 ml-auto">{e.metrics_count}m</span>
                    </div>
                  ))}
                </div>
            }
          </div>
          {/* sFlow */}
          <div className="bg-slate-900/60 border border-white/10 rounded-xl p-4">
            <p className="text-xs font-semibold text-cyan-400 mb-3">sFlow — last {sflowEvents.length} interface samples</p>
            {sflowEvents.length === 0
              ? <p className="text-slate-600 text-xs">No flow samples yet.</p>
              : <div className="space-y-1 max-h-56 overflow-y-auto text-xs font-mono">
                  {sflowEvents.map((e: any, i: number) => (
                    <div key={i} className="flex gap-2 text-slate-300 border-b border-white/5 pb-1">
                      <span className="text-slate-500 shrink-0">{new Date(e.timestamp).toLocaleTimeString()}</span>
                      <span className="text-green-300 shrink-0">{e.device}</span>
                      <span className="text-cyan-300">{e.interface}</span>
                      <span className="text-slate-400 ml-auto">↓{e.in_mbps.toFixed(2)} ↑{e.out_mbps.toFixed(2)} Mbps</span>
                    </div>
                  ))}
                </div>
            }
          </div>
        </div>
      </section>

      {/* ── All Containers ────────────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">All Containers</h2>
        <div className="bg-slate-900/60 border border-white/10 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-slate-400 text-xs">
                <th className="text-left px-4 py-3 font-medium">Container</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-right px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {[...simContainers, ...dcimContainers, ...infraContainers].map(name => {
                const status = dockerStatus[name] ?? 'unknown'
                return (
                  <tr key={name} className="border-b border-white/5 hover:bg-white/3 transition-colors">
                    <td className="px-4 py-3 font-medium">{name}</td>
                    <td className="px-4 py-3">
                      <span className={`flex items-center gap-1.5 text-xs font-medium ${statusColor(status)}`}>
                        {status === 'running' ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
                        {status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => restart.mutate(name)}
                        disabled={restart.isPending}
                        className="px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
                      >
                        Restart
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
