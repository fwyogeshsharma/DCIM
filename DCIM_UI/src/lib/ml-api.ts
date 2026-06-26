const ML_BASE = '/api/v1/ml'

export interface MLHealth {
  status: string
  service: string
  db_reachable: boolean
  last_run_at: string | null
}

export interface MLForecastPoint {
  scope: string
  scope_id: string
  target: string
  forecast_for: string
  predicted_value: number
  lower_bound: number | null
  upper_bound: number | null
  unit: string | null
  model_name: string | null
  model_version: string | null
  generated_at: string
}

export interface MLCapacitySummary {
  scope: string
  scope_id: string
  days_until_rack_full: number | null
  rack_exhaustion_date: string | null
  days_until_storage_full: number | null
  storage_exhaustion_date: string | null
  predicted_new_racks_30d: number | null
  predicted_new_racks_60d: number | null
  predicted_new_racks_90d: number | null
  expected_server_growth_30d: number | null
  expected_server_growth_60d: number | null
  expected_server_growth_90d: number | null
  power_headroom_pct: number | null
  details: Record<string, unknown>
  updated_at: string
}

export interface MLRecommendation {
  id: number
  scope: string
  scope_id: string
  category: string
  severity: 'info' | 'warning' | 'critical'
  message: string
  confidence: number
  predicted_date: string | null
  details: Record<string, unknown>
  status: string
  created_at: string
}

export interface MLModelRun {
  scope: string
  scope_id: string
  target: string
  model_name: string
  model_version: string | null
  n_points: number | null
  mae: number | null
  mape: number | null
  accepted: boolean
  trained_at: string
}

export interface MLTrainResponse {
  run_id: string
  scopes: number
  forecasts: number
  recommendations: number
}

async function mlFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${ML_BASE}${path}`)
  if (!res.ok) {
    const msg = await res.text().catch(() => res.statusText)
    throw new Error(`ML API ${path} → ${res.status}: ${msg}`)
  }
  return res.json()
}

async function mlPost<T>(path: string): Promise<T> {
  const res = await fetch(`${ML_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  if (!res.ok) {
    const msg = await res.text().catch(() => res.statusText)
    throw new Error(`ML API ${path} → ${res.status}: ${msg}`)
  }
  return res.json()
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const mlApi = {
  health: () => mlFetch<MLHealth>('/health'),

  forecasts: (params?: {
    scope?: string
    scope_id?: string
    target?: string
    horizon?: number
    limit?: number
  }) =>
    mlFetch<MLForecastPoint[]>(
      `/forecasts${buildQuery({ scope: params?.scope, scope_id: params?.scope_id, target: params?.target, horizon: params?.horizon, limit: params?.limit ?? 2000 })}`
    ),

  capacitySummary: (params?: { scope?: string; scope_id?: string }) =>
    mlFetch<MLCapacitySummary[]>(
      `/capacity/summary${buildQuery({ scope: params?.scope, scope_id: params?.scope_id })}`
    ),

  recommendations: (params?: {
    scope?: string
    scope_id?: string
    category?: string
    severity?: string
    status?: string
  }) =>
    mlFetch<MLRecommendation[]>(
      `/recommendations${buildQuery({
        scope: params?.scope,
        scope_id: params?.scope_id,
        category: params?.category,
        severity: params?.severity,
        status: params?.status ?? 'open',
      })}`
    ),

  modelRuns: (params?: { scope?: string; scope_id?: string; limit?: number }) =>
    mlFetch<MLModelRun[]>(
      `/models${buildQuery({ scope: params?.scope, scope_id: params?.scope_id, limit: params?.limit ?? 50 })}`
    ),

  triggerTrain: () => mlPost<MLTrainResponse>('/train'),
}
