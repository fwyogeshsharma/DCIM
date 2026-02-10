import { useAgents } from '@/hooks/useAgents'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { formatDistanceToNow } from 'date-fns'
import { BarChart3 } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Agents() {
  const { data: agents, isLoading } = useAgents()

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
          Manage and monitor all registered agents
        </p>
      </div>

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-slate-900/50">
            <tr>
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
            {agents?.map((agent) => (
              <tr key={agent.id} className="hover:bg-white/5 transition-colors">
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
            ))}
          </tbody>
        </table>
        {(!agents || agents.length === 0) && (
          <div className="text-center py-12 text-slate-400">
            No agents found
          </div>
        )}
      </div>
    </div>
  )
}
