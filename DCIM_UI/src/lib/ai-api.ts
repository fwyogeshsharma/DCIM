import type { PredictionResult, NLQueryRequest, NLQueryResponse } from './types'

const AI_API_URL = import.meta.env.VITE_AI_API_URL || '/ai'
const OPENAI_API_KEY = import.meta.env.VITE_OPENAI_API_KEY

class AIAPIClient {
  private baseURL: string

  constructor(baseURL: string) {
    this.baseURL = baseURL
  }

  // Prediction service endpoints
  async getPrediction(
    metricType: string,
    history: Array<{ timestamp: string; value: number }>,
    forecastDays: number = 7
  ): Promise<PredictionResult> {
    const response = await fetch(`${this.baseURL}/predict`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        metric_type: metricType,
        history,
        forecast_days: forecastDays,
      }),
    })

    if (!response.ok) {
      throw new Error(`Prediction API error: ${response.status}`)
    }

    const data = await response.json()

    return {
      metric_type: metricType,
      agent_id: '', // Will be set by the caller
      predictions: data.predictions.map((p: any) => ({
        timestamp: p.ds,
        value: p.yhat,
        lower_bound: p.yhat_lower,
        upper_bound: p.yhat_upper,
      })),
      confidence: 0.85, // Default confidence
      model: 'Prophet',
    }
  }

  // Natural language query using OpenAI/Claude
  async processNaturalLanguageQuery(request: NLQueryRequest): Promise<NLQueryResponse> {
    const systemPrompt = `You are a DCIM query assistant. Convert natural language queries to structured filters.

Available metrics: cpu.usage, memory.usage, disk.usage, temperature.cpu, network.bytes_sent, network.bytes_recv, etc.
Available filters: agent_id, time_range (1h, 24h, 7d, 30d), metric_type, threshold
Available visualizations: line_chart, bar_chart, table, gauge, heatmap

User query: "${request.query}"

Respond with JSON only:
{
  "filters": {
    "metric_type": "cpu.usage",
    "threshold": "> 80",
    "time_range": "1h"
  },
  "visualization": "line_chart",
  "explanation": "Showing agents with CPU above 80% in the last hour"
}`

    try {
      // Use OpenAI API
      const response = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${OPENAI_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'gpt-4',
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: request.query },
          ],
          temperature: 0.3,
        }),
      })

      if (!response.ok) {
        throw new Error(`OpenAI API error: ${response.status}`)
      }

      const data = await response.json()
      const content = data.choices[0].message.content

      // Parse JSON response
      const result = JSON.parse(content)

      return {
        filters: result.filters,
        visualization: result.visualization,
        explanation: result.explanation,
        data: [], // Will be filled by backend API call
      }
    } catch (error) {
      console.error('NL query processing error:', error)

      // Fallback: Simple keyword-based parsing
      return this.fallbackQueryParsing(request.query)
    }
  }

  private fallbackQueryParsing(query: string): NLQueryResponse {
    const lowerQuery = query.toLowerCase()
    const filters: Record<string, any> = {}
    let visualization: NLQueryResponse['visualization'] = 'table'

    // Detect metric type
    if (lowerQuery.includes('cpu')) {
      filters.metric_type = 'cpu.usage'
      visualization = 'line_chart'
    } else if (lowerQuery.includes('memory')) {
      filters.metric_type = 'memory.usage'
      visualization = 'line_chart'
    } else if (lowerQuery.includes('disk')) {
      filters.metric_type = 'disk.usage'
      visualization = 'bar_chart'
    } else if (lowerQuery.includes('temperature') || lowerQuery.includes('temp')) {
      filters.metric_type = 'temperature.cpu'
      visualization = 'gauge'
    }

    // Detect time range
    if (lowerQuery.includes('last hour') || lowerQuery.includes('1 hour')) {
      filters.time_range = '1h'
    } else if (lowerQuery.includes('today') || lowerQuery.includes('24 hour')) {
      filters.time_range = '24h'
    } else if (lowerQuery.includes('week') || lowerQuery.includes('7 day')) {
      filters.time_range = '7d'
    }

    // Detect threshold
    const thresholdMatch = lowerQuery.match(/above (\d+)%?|over (\d+)%?|> ?(\d+)%?/)
    if (thresholdMatch) {
      const value = thresholdMatch[1] || thresholdMatch[2] || thresholdMatch[3]
      filters.threshold = `> ${value}`
    }

    return {
      filters,
      visualization,
      explanation: `Searching for ${filters.metric_type || 'metrics'} based on your query`,
      data: [],
    }
  }

  // Generate AI insights from anomalies
  async generateInsights(anomalies: any[]): Promise<any[]> {
    // This could be enhanced with GPT to generate natural language insights
    return anomalies.map((anomaly) => ({
      id: anomaly.id,
      title: `${anomaly.metric_type} anomaly detected`,
      description: `Detected ${anomaly.deviation.toFixed(2)}% deviation from expected value`,
      severity: anomaly.severity.toLowerCase(),
      affected_agents: [anomaly.agent_id],
      metric_type: anomaly.metric_type,
      action: 'Investigate root cause',
      timestamp: anomaly.timestamp,
      confidence: anomaly.confidence,
    }))
  }
}

export const aiAPI = new AIAPIClient(AI_API_URL)
