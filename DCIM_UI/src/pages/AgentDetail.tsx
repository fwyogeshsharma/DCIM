import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useAgent, useLatestMetrics } from '@/hooks/useAgents'
import { BarChart3, ArrowLeft, Radio, Waves, RefreshCw, Zap, BatteryCharging, Thermometer, Server, Network, Router, Shield, HelpCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { getDeviceTypeMeta, POWER_DEVICE_DESCRIPTIONS } from '@/lib/deviceType'

const ICON_MAP: Record<string, React.ElementType> = {
  Zap, BatteryCharging, Thermometer, Server, Network, Router, Shield, HelpCircle,
}

// ── Types ──────────────────────────────────────────────────────────────────────

interface GnmiEvent {
  timestamp: string
  device: string
  agent_id: string
  protocol: string
  event: string
  path: string
  metrics_count: number
  values: Record<string, { value: number; unit: string }>
}

interface SflowEvent {
  timestamp: string
  device: string
  agent_id: string
  protocol: string
  event: string
  interface: string
  if_index: number
  in_octets: number
  out_octets: number
  oper_status: number
  in_mbps: number
  out_mbps: number
}

interface TelemetryResult {
  container: string
  data?: { gnmi: GnmiEvent[]; sflow: SflowEvent[] }
  error?: string
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtTs(ts: string) {
  try { return new Date(ts).toLocaleTimeString() } catch { return ts }
}

function statusBadge(ok: number) {
  return ok === 1
    ? <span className="text-green-400 text-xs">up</span>
    : <span className="text-red-400 text-xs">down</span>
}

// ── Telemetry hook ─────────────────────────────────────────────────────────────

function useTelemetry(hostname: string | undefined) {
  return useQuery<TelemetryResult[]>({
    queryKey: ['telemetry', hostname],
    queryFn: () =>
      fetch(`/orchestrator/telemetry?device=${encodeURIComponent(hostname ?? '')}&limit=50`)
        .then(r => r.json()),
    enabled: !!hostname,
    refetchInterval: 15000,
    staleTime: 10000,
  })
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function CombinedTelemetryTable({
  gnmiEvents,
  sflowEvents,
}: {
  gnmiEvents: GnmiEvent[]
  sflowEvents: SflowEvent[]
}) {
  // Flatten latest-per-path::metric from gNMI events (newest-first already sorted)
  const gnmiRows: { path: string; key: string; value: number; unit: string; time: string }[] = []
  const seenGnmi = new Set<string>()
  for (const e of gnmiEvents) {
    for (const [k, v] of Object.entries(e.values ?? {})) {
      const uid = `${e.path}::${k}`
      if (!seenGnmi.has(uid)) {
        seenGnmi.add(uid)
        gnmiRows.push({ path: e.path, key: k, value: v.value, unit: v.unit, time: e.timestamp })
      }
    }
  }

  // Latest reading per sFlow interface
  const sflowByIface: Record<string, SflowEvent> = {}
  for (const e of sflowEvents) {
    if (!sflowByIface[e.interface]) sflowByIface[e.interface] = e
  }
  const sflowRows = Object.values(sflowByIface)

  if (!gnmiRows.length && !sflowRows.length) {
    return (
      <p className="text-slate-500 text-sm py-3">
        No telemetry data yet — gNMI posts every 30 s, sFlow appears when interfaces carry traffic.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-slate-300">
        <thead>
          <tr className="text-slate-500 border-b border-white/10">
            <th className="text-left py-2 pr-3 font-medium">Protocol</th>
            <th className="text-left py-2 pr-3 font-medium">Path / Interface</th>
            <th className="text-left py-2 pr-3 font-medium">Metric</th>
            <th className="text-right py-2 pr-3 font-medium">Value</th>
            <th className="text-left py-2 font-medium">Time</th>
          </tr>
        </thead>
        <tbody>
          {gnmiRows.map((row, i) => (
            <tr key={`gnmi-${i}`} className="border-b border-white/5 hover:bg-white/5">
              <td className="py-1.5 pr-3">
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-500/20 text-blue-300 border border-blue-500/30">
                  gNMI
                </span>
              </td>
              <td className="py-1.5 pr-3 font-mono text-blue-300 truncate max-w-[160px]" title={row.path}>
                {row.path}
              </td>
              <td className="py-1.5 pr-3 text-slate-300">{row.key}</td>
              <td className="py-1.5 pr-3 text-right font-mono text-white">
                {row.value} <span className="text-slate-500">{row.unit}</span>
              </td>
              <td className="py-1.5 font-mono text-slate-500">{fmtTs(row.time)}</td>
            </tr>
          ))}
          {sflowRows.map((e, i) => (
            <tr key={`sflow-${i}`} className="border-b border-white/5 hover:bg-white/5">
              <td className="py-1.5 pr-3">
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                  sFlow
                </span>
              </td>
              <td className="py-1.5 pr-3 font-mono text-cyan-300">{e.interface}</td>
              <td className="py-1.5 pr-3 text-slate-300">
                Traffic &nbsp;{statusBadge(e.oper_status)}
              </td>
              <td className="py-1.5 pr-3 text-right font-mono text-white">
                <span className="text-green-300">↓{e.in_mbps.toFixed(2)}</span>
                {' / '}
                <span className="text-blue-300">↑{e.out_mbps.toFixed(2)}</span>
                <span className="text-slate-500"> Mbps</span>
              </td>
              <td className="py-1.5 font-mono text-slate-500">{fmtTs(e.timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GnmiTable({ events }: { events: GnmiEvent[] }) {
  if (!events.length)
    return <p className="text-slate-500 text-sm py-3">No gNMI events yet — metrics post every 30 s.</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-slate-300">
        <thead>
          <tr className="text-slate-500 border-b border-white/10">
            <th className="text-left py-2 pr-4 font-medium">Time</th>
            <th className="text-left py-2 pr-4 font-medium">Path</th>
            <th className="text-left py-2 pr-4 font-medium">Metrics</th>
            <th className="text-left py-2 font-medium">Sample values</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i} className="border-b border-white/5 hover:bg-white/5">
              <td className="py-1.5 pr-4 font-mono text-slate-400">{fmtTs(e.timestamp)}</td>
              <td className="py-1.5 pr-4 font-mono text-blue-300">{e.path}</td>
              <td className="py-1.5 pr-4">{e.metrics_count}</td>
              <td className="py-1.5 text-slate-400">
                {Object.entries(e.values ?? {}).slice(0, 3).map(([k, v]) =>
                  <span key={k} className="mr-2">{k}: {v.value}{v.unit}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SflowTable({ events }: { events: SflowEvent[] }) {
  if (!events.length)
    return <p className="text-slate-500 text-sm py-3">No sFlow events yet — flow samples appear when interfaces have traffic.</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-slate-300">
        <thead>
          <tr className="text-slate-500 border-b border-white/10">
            <th className="text-left py-2 pr-4 font-medium">Time</th>
            <th className="text-left py-2 pr-4 font-medium">Interface</th>
            <th className="text-left py-2 pr-4 font-medium">Status</th>
            <th className="text-right py-2 pr-4 font-medium">In (Mbps)</th>
            <th className="text-right py-2 font-medium">Out (Mbps)</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i} className="border-b border-white/5 hover:bg-white/5">
              <td className="py-1.5 pr-4 font-mono text-slate-400">{fmtTs(e.timestamp)}</td>
              <td className="py-1.5 pr-4 font-mono text-cyan-300">{e.interface}</td>
              <td className="py-1.5 pr-4">{statusBadge(e.oper_status)}</td>
              <td className="py-1.5 pr-4 text-right text-green-300">{e.in_mbps.toFixed(3)}</td>
              <td className="py-1.5 text-right text-blue-300">{e.out_mbps.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>()
  const { data: agent, isLoading } = useAgent(agentId!)
  const { data: latestMetrics } = useLatestMetrics(agentId!)
  const { data: telemetryRaw, refetch: refetchTel, isFetching: telFetching } =
    useTelemetry(agent?.hostname)

  // Merge events from all containers for this device
  const gnmiEvents: GnmiEvent[] = []
  const sflowEvents: SflowEvent[] = []
  for (const row of telemetryRaw ?? []) {
    if (row.data) {
      gnmiEvents.push(...(row.data.gnmi ?? []))
      sflowEvents.push(...(row.data.sflow ?? []))
    }
  }
  // Sort newest first
  gnmiEvents.sort((a, b) => b.timestamp.localeCompare(a.timestamp))
  sflowEvents.sort((a, b) => b.timestamp.localeCompare(a.timestamp))

  if (isLoading) {
    return <div className="text-slate-400">Loading agent details...</div>
  }

  if (!agent) {
    return <div className="text-slate-400">Agent not found</div>
  }

  const deviceMeta = getDeviceTypeMeta(agent.agent_id, agent.device_type, agent.device_role)
  const DeviceIcon = ICON_MAP[deviceMeta.icon] ?? HelpCircle
  const powerDesc = POWER_DEVICE_DESCRIPTIONS[deviceMeta.category]

  return (
    <div className="space-y-6">
      {/* Power device callout */}
      {deviceMeta.isPowerDevice && (
        <div
          className="flex items-start gap-3 px-4 py-3 rounded-xl border"
          style={{
            background: `color-mix(in srgb, ${deviceMeta.color} 8%, transparent)`,
            borderColor: `color-mix(in srgb, ${deviceMeta.color} 30%, transparent)`,
          }}
        >
          <DeviceIcon className="w-5 h-5 mt-0.5 shrink-0" style={{ color: deviceMeta.color }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: deviceMeta.color }}>
              Power Infrastructure Device — {deviceMeta.label}
            </p>
            {powerDesc && <p className="text-xs text-slate-400 mt-0.5">{powerDesc}</p>}
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <Link to="/app/agents">
              <Button variant="ghost" size="sm" className="gap-2">
                <ArrowLeft className="w-4 h-4" />
                Back to Agents
              </Button>
            </Link>
          </div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white">{agent.hostname}</h1>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${deviceMeta.badgeClass}`}
            >
              <DeviceIcon className="w-3.5 h-3.5" />
              {deviceMeta.label}
            </span>
          </div>
          <p className="text-slate-400 mt-2">Unique ID: {agent.agent_id}</p>
        </div>
        <Link to={`/app/agents/${agentId}/analytics`}>
          <Button className="gap-2 bg-blue-600 hover:bg-blue-700">
            <BarChart3 className="w-4 h-4" />
            View Analytics
          </Button>
        </Link>
      </div>

      {/* Info cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {agent.server_name && (
          <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-4">
            <p className="text-sm text-slate-400">Server</p>
            <p className="text-lg font-semibold mt-1 text-white">{agent.server_name}</p>
          </div>
        )}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-4">
          <p className="text-sm text-slate-400">IP Address</p>
          <p className="text-lg font-mono mt-1 text-white">{agent.ip_address}</p>
        </div>
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-4">
          <p className="text-sm text-slate-400">Status</p>
          <p className="text-lg font-semibold mt-1 capitalize text-white">{agent.status}</p>
        </div>
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-4">
          <p className="text-sm text-slate-400">Group</p>
          <p className="text-lg font-semibold mt-1 text-white">{agent.group || 'None'}</p>
        </div>
      </div>

      {/* Combined telemetry metrics */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            Latest Metrics
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-500/20 text-blue-300 border border-blue-500/30">gNMI</span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">sFlow</span>
          </h3>
          <Button
            variant="ghost" size="sm"
            onClick={() => refetchTel()}
            disabled={telFetching}
            className="text-slate-400 hover:text-white"
          >
            <RefreshCw className={`w-4 h-4 ${telFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>
        {/* SNMP/standard metrics summary row */}
        {latestMetrics && Object.keys(latestMetrics).length > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5 pb-5 border-b border-white/10">
            {Object.entries(latestMetrics).map(([key, metric]) => (
              <div key={key} className="border border-white/10 rounded p-3">
                <p className="text-xs text-slate-400">{key}</p>
                <p className="text-lg font-semibold mt-1 text-white">
                  {metric.value.toFixed(2)} {metric.unit}
                </p>
              </div>
            ))}
          </div>
        )}
        <CombinedTelemetryTable gnmiEvents={gnmiEvents} sflowEvents={sflowEvents} />
      </div>

      {/* gNMI telemetry */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Radio className="w-5 h-5 text-blue-400" />
            gNMI Telemetry
            <span className="text-xs font-normal text-slate-500 ml-1">({gnmiEvents.length} events)</span>
          </h3>
          <Button
            variant="ghost" size="sm"
            onClick={() => refetchTel()}
            disabled={telFetching}
            className="text-slate-400 hover:text-white"
          >
            <RefreshCw className={`w-4 h-4 ${telFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>
        <GnmiTable events={gnmiEvents.slice(0, 20)} />
      </div>

      {/* sFlow telemetry */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Waves className="w-5 h-5 text-cyan-400" />
            sFlow Interface Events
            <span className="text-xs font-normal text-slate-500 ml-1">({sflowEvents.length} events)</span>
          </h3>
        </div>
        <SflowTable events={sflowEvents.slice(0, 30)} />
      </div>

      {/* Metrics charts placeholder */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-white mb-4">Metrics Charts</h3>
        <div className="text-slate-400 text-sm">
          Time-series charts will appear here
        </div>
      </div>
    </div>
  )
}
