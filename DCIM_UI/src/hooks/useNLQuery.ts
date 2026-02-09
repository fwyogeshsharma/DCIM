import { useMutation } from '@tanstack/react-query'
import { aiAPI } from '@/lib/ai-api'
import { api } from '@/lib/api'

export function useNLQuery() {
  return useMutation({
    mutationFn: async (query: string) => {
      // Process query with AI
      const nlResult = await aiAPI.processNaturalLanguageQuery({ query })

      // Execute the query with backend API
      let data: any[] = []

      if (nlResult.filters.metric_type) {
        data = await api.getMetrics({
          metric_type: nlResult.filters.metric_type,
          time_range: nlResult.filters.time_range || '24h',
        })

        // Apply threshold filter if present
        if (nlResult.filters.threshold) {
          const thresholdMatch = nlResult.filters.threshold.match(/([><]=?)\s*(\d+)/)
          if (thresholdMatch) {
            const operator = thresholdMatch[1]
            const value = parseFloat(thresholdMatch[2])

            data = data.filter((metric) => {
              switch (operator) {
                case '>':
                  return metric.value > value
                case '>=':
                  return metric.value >= value
                case '<':
                  return metric.value < value
                case '<=':
                  return metric.value <= value
                default:
                  return true
              }
            })
          }
        }
      }

      return {
        ...nlResult,
        data,
      }
    },
  })
}
