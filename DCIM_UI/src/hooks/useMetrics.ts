import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { MetricFilter } from '@/lib/types'

export function useMetrics(filter: MetricFilter) {
  return useQuery({
    queryKey: ['metrics', filter],
    queryFn: () => api.getMetrics(filter),
    enabled: !!filter.agent_id || !!filter.metric_type,
    staleTime: 20000, // 20 seconds
  })
}

export function useAggregatedMetrics(filter: MetricFilter & { interval?: string }) {
  return useQuery({
    queryKey: ['metrics', 'aggregated', filter],
    queryFn: () => api.getAggregatedMetrics(filter),
    enabled: !!filter.agent_id || !!filter.metric_type,
    staleTime: 30000, // 30 seconds
  })
}
