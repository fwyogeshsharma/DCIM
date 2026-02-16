import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { AlertFilter } from '@/lib/types'

export function useAlerts(filter?: AlertFilter) {
  return useQuery({
    queryKey: ['alerts', filter],
    queryFn: () => api.getAlerts(filter),
    staleTime: 15000, // 15 seconds
    refetchInterval: 30000, // Refetch every 30 seconds
  })
}

export function useAlert(alertId: number) {
  return useQuery({
    queryKey: ['alerts', alertId],
    queryFn: () => api.getAlert(alertId),
    enabled: !!alertId,
  })
}

export function useResolveAlert() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (alertId: number) => api.resolveAlert(alertId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      queryClient.invalidateQueries({ queryKey: ['alert-counts'] })
    },
  })
}

export function useBulkResolveAlerts() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (alertIds: number[]) => api.bulkResolveAlerts(alertIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      queryClient.invalidateQueries({ queryKey: ['alert-counts'] })
    },
  })
}
