import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { AgentFilter } from '@/lib/types'

export function useAgents(filter?: AgentFilter) {
  return useQuery({
    queryKey: ['agents', filter],
    queryFn: () => api.getAgents(filter),
    staleTime: 30000, // 30 seconds
  })
}

export function useAgent(agentId: string) {
  // Get agent from the agents list instead of individual endpoint
  const { data: agents, ...rest } = useAgents()

  return {
    ...rest,
    data: agents?.find(agent => agent.agent_id === agentId),
  }
}

export function useApproveAgent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (agentId: string) => api.approveAgent(agentId),
    onSuccess: (data, agentId) => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.setQueryData(['agents', agentId], data)
    },
  })
}

export function useDeleteAgent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (agentId: string) => api.deleteAgent(agentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
    },
  })
}

export function useUpdateAgentGroup() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ agentId, group }: { agentId: string; group: string }) =>
      api.updateAgentGroup(agentId, group),
    onSuccess: (data, { agentId }) => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.setQueryData(['agents', agentId], data)
    },
  })
}

export function useLatestMetrics(agentId: string) {
  return useQuery({
    queryKey: ['agents', agentId, 'latest-metrics'],
    queryFn: async () => {
      // Get recent metrics and extract latest values per type
      const metrics = await api.getMetrics({ agent_id: agentId, limit: 100 })

      if (!metrics || metrics.length === 0) return {}

      // Group by metric_type and get the latest (first) value for each
      const latest: Record<string, any> = {}
      metrics.forEach(metric => {
        const key = metric.metric_type
        if (!latest[key]) {
          latest[key] = {
            value: metric.value,
            unit: metric.unit,
            timestamp: metric.timestamp,
          }
        }
      })

      return latest
    },
    enabled: !!agentId,
    staleTime: 10000, // 10 seconds
    refetchInterval: 30000, // Refetch every 30 seconds
  })
}
