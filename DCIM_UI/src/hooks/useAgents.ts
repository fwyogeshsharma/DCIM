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
  return useQuery({
    queryKey: ['agents', agentId],
    queryFn: () => api.getAgent(agentId),
    enabled: !!agentId,
    staleTime: 30000,
  })
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
    queryFn: () => api.getLatestMetrics(agentId),
    enabled: !!agentId,
    staleTime: 10000, // 10 seconds
    refetchInterval: 30000, // Refetch every 30 seconds
  })
}
