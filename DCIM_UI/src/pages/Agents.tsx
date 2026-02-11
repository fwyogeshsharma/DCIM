import { useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { formatDistanceToNow } from 'date-fns'
import { BarChart3, Filter } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Agents() {
  const { data: agents, isLoading } = useAgents()
  const { data: servers } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })

  const [serverFilter, setServerFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const filteredAgents = agents?.filter((agent) => {
    if (serverFilter !== 'all' && agent.server_name !== serverFilter) return false
    if (statusFilter !== 'all' && agent.status !== statusFilter) return false
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

  // Unique server names from agents
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

      {/* Filters */}
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
        <input
          type="text"
          placeholder="Search hostname, ID, IP..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white placeholder:text-slate-500 w-64"
        />
        {(serverFilter !== 'all' || statusFilter !== 'all' || searchQuery) && (
          <button
            onClick={() => { setServerFilter('all'); setStatusFilter('all'); setSearchQuery('') }}
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
              // Find server color
              const serverColor = servers?.find((s) => s.name === agent.server_name)?.metadata?.color || '#3b82f6'
              return (
                <tr key={agent.id} className="hover:bg-white/5 transition-colors">
                  <td className="p-4">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-6 rounded-full" style={{ backgroundColor: serverColor }} />
                      <span className="text-sm text-slate-300">{agent.server_name || 'Unknown'}</span>
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
                  <td className="p-4 font-mono text-sm text-slate-300">{agent.ip_address}</td>
                  <td className="p-4">
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
