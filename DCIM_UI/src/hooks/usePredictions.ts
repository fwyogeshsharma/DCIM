import { useQuery } from '@tanstack/react-query'
import { aiAPI } from '@/lib/ai-api'
import type { Metric } from '@/lib/types'

export function usePrediction(
  agentId: string,
  metricType: string,
  history: Metric[],
  enabled: boolean = true
) {
  return useQuery({
    queryKey: ['prediction', agentId, metricType],
    queryFn: async () => {
      const historyData = history.map((m) => ({
        timestamp: m.timestamp,
        value: m.value,
      }))

      const result = await aiAPI.getPrediction(metricType, historyData, 7)
      return {
        ...result,
        agent_id: agentId,
      }
    },
    enabled: enabled && history.length >= 7, // Need at least 7 data points
    staleTime: 300000, // 5 minutes (predictions don't change often)
  })
}

export function useAnomalies(agentId?: string) {
  return useQuery({
    queryKey: ['anomalies', agentId],
    queryFn: () => {
      // This would call the backend API
      // For now, using a placeholder
      return Promise.resolve([])
    },
    staleTime: 60000, // 1 minute
  })
}

export function useAIInsights() {
  return useQuery({
    queryKey: ['ai-insights'],
    queryFn: async () => {
      // Fetch anomalies from backend
      const anomalies: any[] = [] // await api.getAnomalies()

      // Generate insights
      return aiAPI.generateInsights(anomalies)
    },
    staleTime: 60000, // 1 minute
    refetchInterval: 120000, // Refetch every 2 minutes
  })
}
