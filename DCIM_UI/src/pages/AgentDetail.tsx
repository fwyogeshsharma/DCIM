import { useParams, Link } from 'react-router-dom'
import { useAgent, useLatestMetrics } from '@/hooks/useAgents'
import { BarChart3, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>()
  const { data: agent, isLoading } = useAgent(agentId!)
  const { data: latestMetrics } = useLatestMetrics(agentId!)

  if (isLoading) {
    return <div className="text-slate-400">Loading agent details...</div>
  }

  if (!agent) {
    return <div className="text-slate-400">Agent not found</div>
  }

  return (
    <div className="space-y-6">
      {/* Header with Actions */}
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
          <h1 className="text-3xl font-bold text-white">{agent.hostname}</h1>
          <p className="text-slate-400 mt-2">Agent ID: {agent.agent_id}</p>
        </div>
        <Link to={`/app/agents/${agentId}/analytics`}>
          <Button className="gap-2 bg-blue-600 hover:bg-blue-700">
            <BarChart3 className="w-4 h-4" />
            View Analytics
          </Button>
        </Link>
      </div>

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

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-white mb-4">Latest Metrics</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {latestMetrics &&
            Object.entries(latestMetrics).map(([key, metric]) => (
              <div key={key} className="border border-white/10 rounded p-3">
                <p className="text-xs text-slate-400">{key}</p>
                <p className="text-lg font-semibold mt-1 text-white">
                  {metric.value.toFixed(2)} {metric.unit}
                </p>
              </div>
            ))}
        </div>
      </div>

      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-white mb-4">Metrics Charts</h3>
        <div className="text-slate-400 text-sm">
          Time-series charts will appear here
        </div>
      </div>
    </div>
  )
}
