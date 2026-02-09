import { useParams } from 'react-router-dom'
import { useAgent, useLatestMetrics } from '@/hooks/useAgents'

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>()
  const { data: agent, isLoading } = useAgent(agentId!)
  const { data: latestMetrics } = useLatestMetrics(agentId!)

  if (isLoading) {
    return <div className="text-muted-foreground">Loading agent details...</div>
  }

  if (!agent) {
    return <div className="text-muted-foreground">Agent not found</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">{agent.hostname}</h1>
        <p className="text-muted-foreground mt-2">Agent ID: {agent.agent_id}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground">IP Address</p>
          <p className="text-lg font-mono mt-1">{agent.ip_address}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground">Status</p>
          <p className="text-lg font-semibold mt-1 capitalize">{agent.status}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground">Group</p>
          <p className="text-lg font-semibold mt-1">{agent.group || 'None'}</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-semibold mb-4">Latest Metrics</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {latestMetrics &&
            Object.entries(latestMetrics).map(([key, metric]) => (
              <div key={key} className="border border-border rounded p-3">
                <p className="text-xs text-muted-foreground">{key}</p>
                <p className="text-lg font-semibold mt-1">
                  {metric.value.toFixed(2)} {metric.unit}
                </p>
              </div>
            ))}
        </div>
      </div>

      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-semibold mb-4">Metrics Charts</h3>
        <div className="text-muted-foreground text-sm">
          Time-series charts will appear here
        </div>
      </div>
    </div>
  )
}
