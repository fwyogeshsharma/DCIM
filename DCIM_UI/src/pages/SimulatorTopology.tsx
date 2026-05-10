import { useState, useRef, useEffect, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { toast } from 'react-hot-toast'
import {
  ArrowLeft,
  Zap,
  Loader2,
  Activity,
  Server,
  Router,
  Network,
  PlugZap,
  MousePointerClick,
  ChevronRight,
} from 'lucide-react'

// ── Constants ─────────────────────────────────────────────────────────────────

const ORC = '/orchestrator'

const CONTAINER_META: Record<string, { server: string; color: string }> = {
  'sim-network-a': { server: 'dcim-server-a', color: '#3b82f6' },
  'sim-network-b': { server: 'dcim-server-b', color: '#8b5cf6' },
  'sim-network-c': { server: 'dcim-server-c', color: '#10b981' },
}

const TRAP_TYPES = [
  'CPU_HIGH',
  'MEMORY_HIGH',
  'LINK_DOWN',
  'LINK_UP',
  'TEMPERATURE_HIGH',
  'DEVICE_DOWN',
  'DEVICE_UP',
  'FAN_FAILURE',
  'POWER_FAILURE',
] as const

const SEVERITIES = ['info', 'minor', 'major', 'critical'] as const

type TrapType = (typeof TRAP_TYPES)[number]
type Severity = (typeof SEVERITIES)[number]

const SEVERITY_COLORS: Record<Severity, { dot: string; label: string }> = {
  info: { dot: 'bg-blue-500', label: 'text-blue-400' },
  minor: { dot: 'bg-yellow-400', label: 'text-yellow-400' },
  major: { dot: 'bg-orange-400', label: 'text-orange-400' },
  critical: { dot: 'bg-red-500', label: 'text-red-400' },
}

const NODE_COLORS = {
  ROUTER: '#3b82f6',
  AGG_SWITCH: '#8b5cf6',
  TOR_SWITCH: '#06b6d4',
  SERVER_GROUP: '#10b981',
  INFRA_GROUP: '#f59e0b',
}

const ALERT_ROW_COLORS: Record<string, string> = {
  CRITICAL: 'bg-red-950/60 border-l-2 border-red-600',
  WARNING: 'bg-yellow-950/60 border-l-2 border-yellow-600',
  INFO: 'bg-blue-950/60 border-l-2 border-blue-700',
  MINOR: 'bg-yellow-950/40 border-l-2 border-yellow-700',
  MAJOR: 'bg-orange-950/60 border-l-2 border-orange-600',
}

const ALERT_BADGE_COLORS: Record<string, string> = {
  CRITICAL: 'bg-red-900 text-red-300',
  WARNING: 'bg-yellow-900 text-yellow-300',
  INFO: 'bg-blue-900 text-blue-300',
  MINOR: 'bg-yellow-900 text-yellow-400',
  MAJOR: 'bg-orange-900 text-orange-300',
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface RawDevice {
  name: string
  ip_address: string
  type: string
  vendor?: string
}

interface DeviceGroup {
  container: string
  data?: RawDevice[]
}

interface ContainerStatus {
  [name: string]: string
}

interface AlertRow {
  agent_id: string
  severity: string
  metric_type: string
  message: string
  timestamp: string
}

interface SelectedNode {
  kind: 'device' | 'server_group' | 'infra_group'
  container: string
  device?: RawDevice
  devices?: RawDevice[]
}

interface FiredTrap {
  trap_type: TrapType
  device_name: string
  container: string
  time: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function normalizeType(raw: string): string {
  return raw.replace('DeviceType.', '').toUpperCase()
}

function classifyDevice(d: RawDevice) {
  const t = normalizeType(d.type)
  if (t === 'ROUTER') return 'ROUTER'
  if (t === 'SWITCH' || t === 'AGG_SWITCH') {
    if (d.name.startsWith('Agg-')) return 'AGG_SWITCH'
    if (d.name.startsWith('ToR-')) return 'TOR_SWITCH'
    return 'SWITCH'
  }
  if (t === 'SERVER') return 'SERVER'
  return 'INFRA'
}

function extractDeviceName(agent_id: string) {
  return agent_id.split('-').slice(2).join('-') || agent_id
}

function deriveServer(agent_id: string): string {
  if (agent_id.startsWith('network-a')) return 'dcim-server-a'
  if (agent_id.startsWith('network-b')) return 'dcim-server-b'
  if (agent_id.startsWith('network-c')) return 'dcim-server-c'
  return '—'
}

async function orcFetch(path: string, opts?: RequestInit) {
  const r = await fetch(ORC + path, opts)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// ── SVG Topology Column ───────────────────────────────────────────────────────

interface TopologyColumnProps {
  container: string
  devices: RawDevice[]
  selected: SelectedNode | null
  onSelect: (node: SelectedNode) => void
}

function TopologyColumn({ container, devices, selected, onSelect }: TopologyColumnProps) {
  const meta = CONTAINER_META[container]

  const routers = devices.filter(d => classifyDevice(d) === 'ROUTER')
  const aggSwitches = devices.filter(d => classifyDevice(d) === 'AGG_SWITCH')
  const torSwitches = devices.filter(d => classifyDevice(d) === 'TOR_SWITCH' || (classifyDevice(d) === 'SWITCH' && d.name.startsWith('ToR-')))
  const servers = devices.filter(d => classifyDevice(d) === 'SERVER')
  const infra = devices.filter(d => ['INFRA', 'OOB_SWITCH', 'UPS', 'PDU', 'FLOOR_PDU', 'SENSOR'].includes(classifyDevice(d)))

  const SVG_W = 280
  const SVG_H = 500
  const CX = SVG_W / 2

  const TIER_Y = { router: 45, agg: 145, tor: 255, server: 360, infra: 450 }
  const NODE_R = { router: 22, agg: 16, tor: 13 }

  function nodeXPositions(count: number, y: number, spacing: number) {
    const totalW = (count - 1) * spacing
    const startX = CX - totalW / 2
    return Array.from({ length: count }, (_, i) => ({ x: startX + i * spacing, y }))
  }

  const routerPos = nodeXPositions(routers.length, TIER_Y.router, 70)
  const aggPos = nodeXPositions(Math.min(aggSwitches.length, 4), TIER_Y.agg, 60)
  const torPos = nodeXPositions(Math.min(torSwitches.length, 10), TIER_Y.tor, 24)

  function isNodeSelected(d: RawDevice) {
    return selected?.kind === 'device' && selected.device?.name === d.name && selected.container === container
  }

  function isGroupSelected(kind: 'server_group' | 'infra_group') {
    return selected?.kind === kind && selected.container === container
  }

  return (
    <div className="flex flex-col items-center">
      <div
        className="text-xs font-bold tracking-widest uppercase mb-2 px-3 py-1 rounded-full"
        style={{ color: meta.color, background: `${meta.color}20`, border: `1px solid ${meta.color}40` }}
      >
        {container}
      </div>
      <div
        className="text-[10px] text-slate-500 mb-3 font-mono"
      >
        → {meta.server}
      </div>

      <svg
        width={SVG_W}
        height={SVG_H}
        className="overflow-visible"
        style={{ filter: 'drop-shadow(0 0 0px transparent)' }}
      >
        <defs>
          <filter id={`glow-${container}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Lines: routers → agg */}
        {routerPos.flatMap((rp, ri) =>
          aggPos.map((ap, ai) => (
            <line
              key={`ra-${ri}-${ai}`}
              x1={rp.x} y1={rp.y + NODE_R.router}
              x2={ap.x} y2={ap.y - NODE_R.agg}
              stroke={meta.color}
              strokeWidth="1"
              strokeOpacity="0.2"
            />
          ))
        )}

        {/* Lines: agg → tor */}
        {aggPos.flatMap((ap, ai) =>
          torPos.map((tp, ti) => (
            <line
              key={`at-${ai}-${ti}`}
              x1={ap.x} y1={ap.y + NODE_R.agg}
              x2={tp.x} y2={tp.y - NODE_R.tor}
              stroke={NODE_COLORS.AGG_SWITCH}
              strokeWidth="0.8"
              strokeOpacity="0.15"
            />
          ))
        )}

        {/* Lines: tor → server group */}
        {torPos.map((tp, ti) => (
          <line
            key={`ts-${ti}`}
            x1={tp.x} y1={tp.y + NODE_R.tor}
            x2={CX} y2={TIER_Y.server - 18}
            stroke={NODE_COLORS.TOR_SWITCH}
            strokeWidth="0.8"
            strokeOpacity="0.12"
          />
        ))}

        {/* ROUTER nodes */}
        {routers.map((d, i) => {
          const pos = routerPos[i]
          if (!pos) return null
          const sel = isNodeSelected(d)
          return (
            <g
              key={d.name}
              style={{ cursor: 'pointer' }}
              onClick={() => onSelect({ kind: 'device', container, device: d })}
            >
              <circle
                cx={pos.x} cy={pos.y} r={NODE_R.router + 6}
                fill={NODE_COLORS.ROUTER}
                fillOpacity="0.08"
              />
              <circle
                cx={pos.x} cy={pos.y} r={NODE_R.router}
                fill={sel ? NODE_COLORS.ROUTER : '#1e293b'}
                stroke={NODE_COLORS.ROUTER}
                strokeWidth={sel ? 2.5 : 1.5}
                filter={sel ? `url(#glow-${container})` : undefined}
                style={{ transition: 'all 0.15s' }}
              />
              <text x={pos.x} y={pos.y + 1} textAnchor="middle" dominantBaseline="middle"
                fontSize="8" fontWeight="700" fill={sel ? '#fff' : NODE_COLORS.ROUTER}>
                RTR
              </text>
              <text x={pos.x} y={pos.y + NODE_R.router + 11} textAnchor="middle"
                fontSize="7.5" fill="#94a3b8" fontFamily="monospace">
                {d.name.length > 9 ? d.name.slice(0, 9) : d.name}
              </text>
            </g>
          )
        })}

        {/* AGG SWITCH nodes */}
        {aggSwitches.slice(0, 4).map((d, i) => {
          const pos = aggPos[i]
          if (!pos) return null
          const sel = isNodeSelected(d)
          return (
            <g
              key={d.name}
              style={{ cursor: 'pointer' }}
              onClick={() => onSelect({ kind: 'device', container, device: d })}
            >
              <circle
                cx={pos.x} cy={pos.y} r={NODE_R.agg + 5}
                fill={NODE_COLORS.AGG_SWITCH}
                fillOpacity="0.08"
              />
              <circle
                cx={pos.x} cy={pos.y} r={NODE_R.agg}
                fill={sel ? NODE_COLORS.AGG_SWITCH : '#1e293b'}
                stroke={NODE_COLORS.AGG_SWITCH}
                strokeWidth={sel ? 2.5 : 1.5}
                filter={sel ? `url(#glow-${container})` : undefined}
                style={{ transition: 'all 0.15s' }}
              />
              <text x={pos.x} y={pos.y + 1} textAnchor="middle" dominantBaseline="middle"
                fontSize="6.5" fontWeight="700" fill={sel ? '#fff' : NODE_COLORS.AGG_SWITCH}>
                AGG
              </text>
              <text x={pos.x} y={pos.y + NODE_R.agg + 10} textAnchor="middle"
                fontSize="6.5" fill="#94a3b8" fontFamily="monospace">
                {d.name.length > 8 ? d.name.slice(0, 8) : d.name}
              </text>
            </g>
          )
        })}

        {/* TOR SWITCH nodes */}
        {torSwitches.slice(0, 10).map((d, i) => {
          const pos = torPos[i]
          if (!pos) return null
          const sel = isNodeSelected(d)
          return (
            <g
              key={d.name}
              style={{ cursor: 'pointer' }}
              onClick={() => onSelect({ kind: 'device', container, device: d })}
            >
              <circle
                cx={pos.x} cy={pos.y} r={NODE_R.tor}
                fill={sel ? NODE_COLORS.TOR_SWITCH : '#1e293b'}
                stroke={NODE_COLORS.TOR_SWITCH}
                strokeWidth={sel ? 2 : 1}
                filter={sel ? `url(#glow-${container})` : undefined}
                style={{ transition: 'all 0.15s' }}
              />
            </g>
          )
        })}

        {/* ToR count label */}
        {torSwitches.length > 0 && (
          <text x={CX} y={TIER_Y.tor + 30} textAnchor="middle"
            fontSize="7" fill="#64748b" fontFamily="monospace">
            {torSwitches.length} ToR switches
          </text>
        )}

        {/* SERVER GROUP rect */}
        {servers.length > 0 && (
          <g
            style={{ cursor: 'pointer' }}
            onClick={() => onSelect({ kind: 'server_group', container, devices: servers })}
          >
            <rect
              x={CX - 70} y={TIER_Y.server - 18}
              width={140} height={36}
              rx={8}
              fill={isGroupSelected('server_group') ? NODE_COLORS.SERVER_GROUP : '#134e2e'}
              stroke={NODE_COLORS.SERVER_GROUP}
              strokeWidth={isGroupSelected('server_group') ? 2 : 1}
              strokeOpacity={isGroupSelected('server_group') ? 1 : 0.5}
              filter={isGroupSelected('server_group') ? `url(#glow-${container})` : undefined}
              style={{ transition: 'all 0.15s' }}
            />
            <text x={CX} y={TIER_Y.server + 1} textAnchor="middle" dominantBaseline="middle"
              fontSize="9" fontWeight="600"
              fill={isGroupSelected('server_group') ? '#fff' : NODE_COLORS.SERVER_GROUP}>
              {servers.length} Servers
            </text>
          </g>
        )}

        {/* INFRA GROUP rect */}
        {infra.length > 0 && (
          <g
            style={{ cursor: 'pointer' }}
            onClick={() => onSelect({ kind: 'infra_group', container, devices: infra })}
          >
            <rect
              x={CX - 60} y={TIER_Y.infra - 15}
              width={120} height={30}
              rx={6}
              fill={isGroupSelected('infra_group') ? NODE_COLORS.INFRA_GROUP : '#3a2a00'}
              stroke={NODE_COLORS.INFRA_GROUP}
              strokeWidth={isGroupSelected('infra_group') ? 2 : 1}
              strokeOpacity={isGroupSelected('infra_group') ? 1 : 0.5}
              filter={isGroupSelected('infra_group') ? `url(#glow-${container})` : undefined}
              style={{ transition: 'all 0.15s' }}
            />
            <text x={CX} y={TIER_Y.infra} textAnchor="middle" dominantBaseline="middle"
              fontSize="8.5" fontWeight="600"
              fill={isGroupSelected('infra_group') ? '#fff' : NODE_COLORS.INFRA_GROUP}>
              {infra.length} Infra
            </text>
          </g>
        )}
      </svg>
    </div>
  )
}

// ── Right Panel ───────────────────────────────────────────────────────────────

interface RightPanelProps {
  selected: SelectedNode | null
  dockerStatus: ContainerStatus
  recentTraps: FiredTrap[]
  onTrapFired: (t: FiredTrap) => void
}

function RightPanel({ selected, dockerStatus, recentTraps, onTrapFired }: RightPanelProps) {
  const [subDevice, setSubDevice] = useState<RawDevice | null>(null)
  const [trapType, setTrapType] = useState<TrapType>('CPU_HIGH')
  const [severity, setSeverity] = useState<Severity>('major')
  const [message, setMessage] = useState('')
  const [firing, setFiring] = useState(false)

  useEffect(() => {
    setSubDevice(null)
  }, [selected])

  const effectiveDevice: RawDevice | null =
    selected?.kind === 'device' ? (selected.device ?? null) :
    subDevice

  const containerName = selected?.container ?? ''
  const meta = CONTAINER_META[containerName]
  const dockerState = dockerStatus[containerName] ?? 'unknown'

  async function fireTrap() {
    if (!effectiveDevice || !containerName) return
    setFiring(true)
    try {
      const body = {
        container: containerName,
        device_name: effectiveDevice.name,
        trap_type: trapType,
        severity,
        message: message.trim() || undefined,
      }
      const r = await fetch('/orchestrator/trap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(`${r.status}`)
      toast.success(`Trap fired: ${trapType} → ${effectiveDevice.name}`)
      onTrapFired({
        trap_type: trapType,
        device_name: effectiveDevice.name,
        container: containerName,
        time: new Date().toLocaleTimeString(),
      })
      setMessage('')
    } catch (e) {
      toast.error('Trap failed: ' + (e instanceof Error ? e.message : 'unknown error'))
    } finally {
      setFiring(false)
    }
  }

  if (!selected) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-4 py-12 gap-4">
        <MousePointerClick className="w-10 h-10 text-slate-600" />
        <p className="text-slate-500 text-sm">Click any node to select a device</p>
        <p className="text-slate-600 text-xs">Select a router, switch, or group to inject traps</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Device card */}
      <div
        className="p-4 border-b border-white/10 space-y-3"
        style={{ borderLeftColor: meta?.color, borderLeftWidth: 3 }}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-base font-bold text-white leading-tight">
              {selected.kind === 'device'
                ? selected.device?.name
                : selected.kind === 'server_group'
                ? `${selected.devices?.length} Servers`
                : `${selected.devices?.length} Infra Devices`}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">{containerName}</p>
          </div>
          <span
            className="text-[10px] px-2 py-0.5 rounded-full font-medium shrink-0"
            style={{ background: `${meta?.color}20`, color: meta?.color }}
          >
            {dockerState}
          </span>
        </div>

        {selected.kind === 'device' && selected.device && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <div>
              <span className="text-slate-500">IP</span>
              <p className="text-slate-300 font-mono">{selected.device.ip_address || '—'}</p>
            </div>
            <div>
              <span className="text-slate-500">Type</span>
              <p className="text-slate-300">{normalizeType(selected.device.type)}</p>
            </div>
            <div>
              <span className="text-slate-500">Vendor</span>
              <p className="text-slate-300">{selected.device.vendor || '—'}</p>
            </div>
            <div>
              <span className="text-slate-500">Server</span>
              <p className="text-slate-300 font-mono text-[10px]">{meta?.server}</p>
            </div>
          </div>
        )}

        {(selected.kind === 'server_group' || selected.kind === 'infra_group') && (
          <div className="space-y-1">
            <p className="text-xs text-slate-500 mb-1">
              {subDevice ? `Selected: ${subDevice.name}` : 'Pick a device to target:'}
            </p>
            <div className="max-h-40 overflow-y-auto space-y-0.5 pr-1">
              {selected.devices?.map(d => (
                <button
                  key={d.name}
                  onClick={() => setSubDevice(prev => prev?.name === d.name ? null : d)}
                  className={`w-full text-left px-2.5 py-1.5 rounded-md text-xs flex items-center justify-between transition-colors ${
                    subDevice?.name === d.name
                      ? 'bg-blue-600/20 text-blue-300 border border-blue-600/40'
                      : 'hover:bg-white/5 text-slate-400 border border-transparent'
                  }`}
                >
                  <span className="font-mono">{d.name}</span>
                  <span className="text-[10px] text-slate-600">{normalizeType(d.type)}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Trap injection form */}
      <div className="p-4 space-y-3 border-b border-white/10 flex-shrink-0">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Inject Trap</p>

        {!effectiveDevice && (selected.kind !== 'device') && (
          <p className="text-xs text-slate-600 italic">Select a device above first</p>
        )}

        <div>
          <label className="text-[11px] text-slate-500 mb-1 block">Trap Type</label>
          <select
            value={trapType}
            onChange={e => setTrapType(e.target.value as TrapType)}
            className="w-full bg-slate-800 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500/50"
          >
            {TRAP_TYPES.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-[11px] text-slate-500 mb-1 block">Severity</label>
          <div className="grid grid-cols-2 gap-1.5">
            {SEVERITIES.map(s => {
              const sc = SEVERITY_COLORS[s]
              return (
                <button
                  key={s}
                  onClick={() => setSeverity(s)}
                  className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs border transition-colors ${
                    severity === s
                      ? 'border-white/20 bg-white/10'
                      : 'border-white/5 bg-white/3 hover:bg-white/8'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full shrink-0 ${sc.dot}`} />
                  <span className={`capitalize ${sc.label}`}>{s}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <label className="text-[11px] text-slate-500 mb-1 block">Message (optional)</label>
          <textarea
            value={message}
            onChange={e => setMessage(e.target.value)}
            placeholder="Custom trap message…"
            rows={2}
            className="w-full bg-slate-800 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 resize-none focus:outline-none focus:border-blue-500/50 placeholder:text-slate-600"
          />
        </div>

        <button
          onClick={fireTrap}
          disabled={firing || !effectiveDevice}
          className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-40 bg-red-600 hover:bg-red-500 text-white"
        >
          {firing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
          Fire Trap
          {effectiveDevice && <span className="text-red-200 text-xs font-normal">→ {effectiveDevice.name}</span>}
        </button>
      </div>

      {/* Recent traps */}
      <div className="p-4 flex-1 overflow-hidden flex flex-col">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Recent Traps</p>
        {recentTraps.length === 0 ? (
          <p className="text-xs text-slate-600">No traps fired this session</p>
        ) : (
          <div className="overflow-y-auto space-y-1.5 flex-1">
            {recentTraps.slice(0, 5).map((t, i) => (
              <div key={i} className="flex items-start gap-2 p-2 rounded-md bg-white/4 text-xs">
                <Zap className="w-3 h-3 text-red-400 mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-slate-300 font-medium truncate">{t.trap_type}</p>
                  <p className="text-slate-500 font-mono truncate">{t.device_name}</p>
                  <p className="text-slate-600">{t.time} · {t.container}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Live Alert Feed ───────────────────────────────────────────────────────────

interface AlertFeedProps {
  alerts: AlertRow[]
  isLoading: boolean
  lastFetched: number
}

function AlertFeed({ alerts, isLoading, lastFetched }: AlertFeedProps) {
  const now = Date.now()

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-white/10 shrink-0">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" />
          </span>
          <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Live Alert Feed</span>
        </div>
        <span className="text-[10px] text-slate-600">polls every 5s</span>
        {isLoading && <Loader2 className="w-3 h-3 animate-spin text-slate-500 ml-auto" />}
      </div>

      <div className="flex-1 overflow-x-auto overflow-y-auto">
        {alerts.length === 0 ? (
          <div className="flex items-center justify-center h-full text-slate-600 text-xs">
            No alerts yet — fire a trap to generate one
          </div>
        ) : (
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 bg-slate-950 z-10">
              <tr className="text-slate-500 border-b border-white/10">
                <th className="text-left px-3 py-1.5 font-medium whitespace-nowrap">Time</th>
                <th className="text-left px-3 py-1.5 font-medium whitespace-nowrap">Severity</th>
                <th className="text-left px-3 py-1.5 font-medium whitespace-nowrap">Device</th>
                <th className="text-left px-3 py-1.5 font-medium whitespace-nowrap">Metric</th>
                <th className="text-left px-3 py-1.5 font-medium whitespace-nowrap">Server</th>
                <th className="text-left px-3 py-1.5 font-medium whitespace-nowrap min-w-[220px]">Message</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a, i) => {
                const sev = (a.severity || 'INFO').toUpperCase()
                const ts = a.timestamp ? new Date(a.timestamp) : null
                const tsMs = ts?.getTime() ?? 0
                const isNew = now - tsMs < 30000
                const rowCls = ALERT_ROW_COLORS[sev] ?? 'border-l-2 border-slate-700'
                const badgeCls = ALERT_BADGE_COLORS[sev] ?? 'bg-slate-800 text-slate-400'
                const device = extractDeviceName(a.agent_id)
                const server = deriveServer(a.agent_id)

                return (
                  <tr
                    key={i}
                    className={`border-b border-white/5 transition-colors ${rowCls} ${isNew ? 'animate-pulse-once' : ''}`}
                  >
                    <td className="px-3 py-1.5 font-mono text-slate-500 whitespace-nowrap">
                      {ts ? ts.toLocaleTimeString() : '—'}
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${badgeCls}`}>
                        {sev}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 font-mono text-slate-300 whitespace-nowrap">{device || '—'}</td>
                    <td className="px-3 py-1.5 text-slate-400 whitespace-nowrap">{a.metric_type || '—'}</td>
                    <td className="px-3 py-1.5 font-mono text-slate-500 whitespace-nowrap text-[10px]">{server}</td>
                    <td className="px-3 py-1.5 text-slate-400 max-w-xs truncate">{a.message || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Container Status Pill ─────────────────────────────────────────────────────

function ContainerPill({ name, status }: { name: string; status: string }) {
  const meta = CONTAINER_META[name]
  const isRunning = status === 'running'
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] bg-white/5 border border-white/10">
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${isRunning ? 'bg-green-400' : 'bg-red-400'}`}
        style={isRunning ? { boxShadow: `0 0 6px ${meta?.color ?? '#22c55e'}` } : undefined}
      />
      <span className="text-slate-400 font-mono">{name}</span>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SimulatorTopology() {
  const [selected, setSelected] = useState<SelectedNode | null>(null)
  const [recentTraps, setRecentTraps] = useState<FiredTrap[]>([])
  const alertFeedRef = useRef<HTMLDivElement>(null)

  const { data: rawDeviceGroups = [] } = useQuery<DeviceGroup[]>({
    queryKey: ['sim-topo-devices'],
    queryFn: () => orcFetch('/devices'),
    refetchInterval: 60000,
    staleTime: 30000,
  })

  const { data: dockerStatus = {} } = useQuery<ContainerStatus>({
    queryKey: ['sim-topo-docker'],
    queryFn: () => orcFetch('/containers'),
    refetchInterval: 10000,
  })

  const { data: alertsRaw = [], isLoading: alertsLoading, dataUpdatedAt } = useQuery<AlertRow[]>({
    queryKey: ['sim-topo-alerts'],
    queryFn: async () => {
      const r = await fetch('/api/v1/alerts?limit=40')
      if (!r.ok) throw new Error(`${r.status}`)
      const json = await r.json()
      return (json.data ?? json) as AlertRow[]
    },
    refetchInterval: 5000,
    staleTime: 4000,
  })

  const SIM_CONTAINERS = ['sim-network-a', 'sim-network-b', 'sim-network-c']

  function getDevicesForContainer(container: string): RawDevice[] {
    const group = rawDeviceGroups.find(g => g.container === container)
    return group?.data ?? []
  }

  const handleTrapFired = useCallback((t: FiredTrap) => {
    setRecentTraps(prev => [t, ...prev].slice(0, 20))
  }, [])

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-white overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="flex items-center gap-4 px-4 py-2.5 border-b border-white/10 bg-slate-900/80 backdrop-blur shrink-0 flex-wrap gap-y-2">
        <Link
          to="/app/dashboard"
          className="flex items-center gap-1.5 text-slate-400 hover:text-white transition-colors text-xs font-medium shrink-0"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Dashboard
        </Link>

        <div className="w-px h-4 bg-white/10 shrink-0" />

        <div className="flex items-center gap-2 shrink-0">
          <Network className="w-4 h-4 text-blue-400" />
          <span className="font-bold text-sm">Simulator Topology</span>
        </div>

        <div className="flex items-center gap-2 ml-4 flex-wrap">
          {SIM_CONTAINERS.map(c => (
            <ContainerPill key={c} name={c} status={dockerStatus[c] ?? 'unknown'} />
          ))}
        </div>

        <div className="ml-auto flex items-center gap-3 shrink-0">
          <span className="text-[10px] text-slate-600 font-mono">
            {rawDeviceGroups.reduce((sum, g) => sum + (g.data?.length ?? 0), 0)} devices
          </span>
          <Link
            to="/app/simulator"
            className="flex items-center gap-1 text-xs text-slate-400 hover:text-white border border-white/10 hover:border-white/20 px-2.5 py-1 rounded-lg transition-colors"
          >
            Control Panel
            <ChevronRight className="w-3 h-3" />
          </Link>
        </div>
      </header>

      {/* ── Body (topology + sidebar) ───────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        {/* Left: Topology columns */}
        <div className="flex-1 overflow-auto p-4">
          <div className="flex flex-col sm:flex-row gap-6 justify-center min-w-0">
            {SIM_CONTAINERS.map(container => (
              <div key={container} className="flex-1 min-w-[260px]">
                <TopologyColumn
                  container={container}
                  devices={getDevicesForContainer(container)}
                  selected={selected}
                  onSelect={setSelected}
                />
              </div>
            ))}
          </div>

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-4 mt-6 justify-center">
            {([
              { label: 'Router', color: NODE_COLORS.ROUTER },
              { label: 'Agg Switch', color: NODE_COLORS.AGG_SWITCH },
              { label: 'ToR Switch', color: NODE_COLORS.TOR_SWITCH },
              { label: 'Servers', color: NODE_COLORS.SERVER_GROUP },
              { label: 'Infra', color: NODE_COLORS.INFRA_GROUP },
            ] as const).map(item => (
              <div key={item.label} className="flex items-center gap-1.5 text-xs text-slate-500">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: item.color }} />
                {item.label}
              </div>
            ))}
          </div>
        </div>

        {/* Right: Selected device panel */}
        <aside className="w-72 shrink-0 border-l border-white/10 bg-slate-900/60 flex flex-col overflow-hidden">
          <RightPanel
            selected={selected}
            dockerStatus={dockerStatus}
            recentTraps={recentTraps}
            onTrapFired={handleTrapFired}
          />
        </aside>
      </div>

      {/* ── Bottom: Live alert feed ─────────────────────────────────────────── */}
      <div
        ref={alertFeedRef}
        className="h-48 shrink-0 border-t border-white/10 bg-slate-950 flex flex-col"
      >
        <AlertFeed
          alerts={alertsRaw}
          isLoading={alertsLoading}
          lastFetched={dataUpdatedAt}
        />
      </div>
    </div>
  )
}
