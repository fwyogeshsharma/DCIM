import { useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Link } from 'react-router-dom'
import { formatDistanceToNow } from 'date-fns'
import { BarChart3, Filter, AlertTriangle, Zap, BatteryCharging, Thermometer, Server, Network, Router, Shield, HelpCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { getDeviceTypeMeta } from '@/lib/deviceType'
import type { DeviceCategory } from '@/lib/deviceType'

const ICON_MAP: Record<string, React.ElementType> = {
  Zap, BatteryCharging, Thermometer, Server, Network, Router, Shield, HelpCircle,
}

function DeviceTypeBadge({ agentId }: { agentId: string }) {
  const meta = getDeviceTypeMeta(agentId)
  const Icon = ICON_MAP[meta.icon] ?? HelpCircle
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${meta.badgeClass}`}>
      <Icon className="w-3 h-3" />
      {meta.label}
    </span>
  )
}

type TypeFilter = 'all' | 'power' | DeviceCategory

export default function Agents() {
  const { data: agents, isLoading } = useAgents()
  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })

  const [serverFilter, setServerFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const filteredAgents = agents?.filter((agent) => {
    if (serverFilter !== 'all' && agent.server_name !== serverFilter) return false
    if (statusFilter !== 'all' && agent.status !== statusFilter) return false
    if (typeFilter !== 'all') {
      const meta = getDeviceTypeMeta(agent.agent_id)
      if (typeFilter === 'power' && !meta.isPowerDevice) return false
      else if (typeFilter !== 'power' && meta.category !== typeFilter) return false
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      return (
        agent.agent_id.toLowerCase().includes(q) ||
        agent.hostname.toLowerCase().includes(q) ||
        agent.ip_address.toLowerCase().includes(q)
      )
    }
    return true
  })

  const serverNames = [...new Set(agents?.map((a) => a.server_name).filter(Boolean) as string[])]

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading agents...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white">Agents</h1>
        <p className="text-slate-400 mt-2 text-lg">
          Manage and monitor all registered agents across servers
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400" />
          <span className="text-sm text-slate-400">Filter:</span>
        </div>
        <select
          value={serverFilter}
          onChange={(e) => setServerFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Servers</option>
          {serverNames.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Status</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
        </select>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as TypeFilter)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white"
        >
          <option value="all">All Types</option>
          <option value="power">⚡ Power Devices</option>
          <option value="PDU">PDU</option>
          <option value="UPS">UPS</option>
          <option value="SENSOR">Sensor</option>
          <option value="SERVER">Server</option>
          <option value="TOR_SWITCH">ToR Switch</option>
          <option value="AGG_SWITCH">Agg Switch</option>
          <option value="ROUTER">Router</option>
          <option value="OOB_SWITCH">OOB Switch</option>
        </select>
        <input
          type="text"
          placeholder="Search hostname, ID, IP..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white placeholder:text-slate-500 w-64"
        />
        {(serverFilter !== 'all' || statusFilter !== 'all' || typeFilter !== 'all' || searchQuery) && (
          <button
            onClick={() => { setServerFilter('all'); setStatusFilter('all'); setTypeFilter('all'); setSearchQuery('') }}
            className="text-xs text-slate-400 hover:text-white"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-slate-500 ml-auto">
          {filteredAgents?.length || 0} of {agents?.length || 0} agents
        </span>
      </div>

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-slate-900/50">
            <tr>
              <th className="text-left p-4 font-medium text-slate-300">Server</th>
              <th className="text-left p-4 font-medium text-slate-300">Agent ID</th>
              <th className="text-left p-4 font-medium text-slate-300">Hostname</th>
              <th className="text-left p-4 font-medium text-slate-300">Type</th>
              <th className="text-left p-4 font-medium text-slate-300">IP Address</th>
              <th className="text-left p-4 font-medium text-slate-300">Status</th>
              <th className="text-left p-4 font-medium text-slate-300">Last Seen</th>
              <th className="text-left p-4 font-medium text-slate-300">Metrics</th>
              <th className="text-left p-4 font-medium text-slate-300">Alerts</th>
              <th className="text-right p-4 font-medium text-slate-300">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {filteredAgents?.map((agent) => {
              const server = servers?.find((s) => s.name === agent.server_name)
              const serverColor = server?.metadata?.color || '#3b82f6'
              const serverDown = server ? server.health?.status !== 'healthy' : false
              const meta = getDeviceTypeMeta(agent.agent_id)

              const rowBg = serverDown
                ? 'bg-red-500/5 hover:bg-red-500/10 border-l-2 border-l-red-500'
                : agent.status === 'offline'
                ? `bg-yellow-500/5 hover:bg-yellow-500/10 ${meta.isPowerDevice ? `border-l-2` : ''}`
                : meta.isPowerDevice
                ? `${meta.bgClass} border-l-2`
                : meta.bgClass

              return (
                <tr
                  key={agent.id}
                  className={`transition-colors ${rowBg}`}
                  style={meta.isPowerDevice && !serverDown ? { borderLeftColor: meta.color } : undefined}
                >
                  <td className="p-4">
                    <div className="flex items-center gap-2">
                      <div className="relative">
                        <div className="w-2 h-6 rounded-full" style={{ backgroundColor: serverDown ? '#ef4444' : serverColor }} />
                        {serverDown && (
                          <div className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                        )}
                      </div>
                      <div>
                        <span className="text-sm text-slate-300">{agent.server_name || 'Unknown'}</span>
                        {serverDown && (
                          <div className="flex items-center gap-1 mt-0.5">
                            <AlertTriangle className="w-3 h-3 text-red-400" />
                            <span className="text-xs text-red-400 font-medium">Server Down</span>
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="p-4">
                    <Link
                      to={`/app/agents/${agent.agent_id}`}
                      className="text-blue-400 hover:text-blue-300 hover:underline font-mono text-sm cursor-pointer"
                    >
                      {agent.agent_id}
                    </Link>
                  </td>
                  <td className="p-4 font-medium text-white">{agent.hostname}</td>
                  <td className="p-4">
                    <DeviceTypeBadge agentId={agent.agent_id} />
                  </td>
                  <td className="p-4 font-mono text-sm text-slate-300">{agent.ip_address}</td>
                  <td className="p-4">
                    {serverDown ? (
                      <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
                        <AlertTriangle className="w-3 h-3" />
                        Unreachable
                      </span>
                    ) : (
                      <span
                        className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${
                          agent.status === 'online'
                            ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                            : agent.status === 'offline'
                            ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                            : 'bg-slate-500/20 text-slate-400 border border-slate-500/30'
                        }`}
                      >
                        {agent.status}
                      </span>
                    )}
                  </td>
                  <td className="p-4 text-sm text-slate-400">
                    {formatDistanceToNow(new Date(agent.last_seen), { addSuffix: true })}
                  </td>
                  <td className="p-4 font-mono text-white">
                    {agent.total_metrics.toLocaleString()}
                  </td>
                  <td className="p-4">
                    {agent.total_alerts > 0 ? (
                      <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-red-500/20 text-red-400 border border-red-500/30">
                        {agent.total_alerts}
                      </span>
                    ) : (
                      <span className="text-slate-500 text-sm">0</span>
                    )}
                  </td>
                  <td className="p-4">
                    <div className="flex items-center justify-end">
                      <Link to={`/app/agents/${agent.agent_id}/analytics`}>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="gap-2 hover:bg-blue-500/20 text-blue-400 hover:text-blue-300"
                          title="View Analytics"
                        >
                          <BarChart3 className="w-4 h-4" />
                          Analytics
                        </Button>
                      </Link>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {(!filteredAgents || filteredAgents.length === 0) && (
          <div className="text-center py-12 text-slate-400">
            {agents && agents.length > 0 ? 'No agents match the current filters' : 'No agents found'}
          </div>
        )}
      </div>
    </div>
  )
}