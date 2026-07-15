// In dev mode (Vite), bypass the proxy and hit uvicorn directly.
// The Vite proxy stalls during IP binding (Windows routing table churn
// disrupts the proxy's keep-alive connection), causing 30–120s hangs.
// CORS is enabled on the API server (allow_origins=["*"]) so direct works.
const BASE = (import.meta.env.DEV ? 'http://localhost:8000' : '') + '/api'

// ── Auth token (single shared password → JWT) ───────────────────────────────
const TOKEN_KEY = 'dcim_token'
export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string): void => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY)

export const AUTH_EXPIRED_EVENT = 'dcim-auth-expired'

// Called when the server rejects our token (expired / invalid). Drops the
// stored token and fires an event so App bounces back to the login screen.
// No page reload — re-login restarts polling/SSE in place.
function onAuthExpired() {
  const had = getToken()
  clearToken()
  if (had) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = body ? { 'Content-Type': 'application/json' } : {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const opts: RequestInit = {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (res.status === 401 && path !== '/auth/login') {
    onAuthExpired()
    throw apiError(401)
  }
  if (!res.ok) {
    // Keep the technical detail in the console for developers; never surface
    // the raw backend body (paths, status codes, stack/validation internals)
    // to end users. Components display only the friendly message below.
    const text = await res.text().catch(() => '')
    console.error(`[api] ${method} ${path} → ${res.status}: ${text}`)
    throw apiError(res.status)
  }
  return res.json() as Promise<T>
}

// Error type carrying the HTTP status, so callers can branch (e.g. 401 vs
// network) without string-parsing. `.message` is always user-safe.
export interface ApiError extends Error { status: number }

function apiError(status: number): ApiError {
  const e = new Error(friendlyError(status)) as ApiError
  e.status = status
  return e
}

// Safe extractor for catch blocks — always returns a user-presentable string,
// never a raw object or technical detail.
export function errorMessage(e: unknown): string {
  return e instanceof Error && e.message ? e.message : 'The request could not be completed.'
}

// Map an HTTP status to a user-safe message. No paths, no codes, no internals.
function friendlyError(status: number): string {
  if (status === 401) return 'Your session expired. Please sign in again.'
  if (status === 403) return 'You do not have permission to do that.'
  if (status === 404) return 'The requested item was not found.'
  if (status === 409) return 'That conflicts with the current state. Refresh and try again.'
  if (status === 422 || status === 400) return 'Some of the information provided is invalid.'
  if (status === 503) return 'The simulator is not ready yet. Start it and try again.'
  if (status >= 500) return 'Something went wrong on the server. Please try again.'
  return 'The request could not be completed.'
}

const get  = <T>(p: string, signal?: AbortSignal) => request<T>('GET', p, undefined, signal)
const post = <T>(p: string, b?: unknown)           => request<T>('POST',   p, b)
const put  = <T>(p: string, b?: unknown)           => request<T>('PUT',    p, b)
const del  = <T>(p: string)                        => request<T>('DELETE', p)

// One AbortController per polled endpoint — aborts the previous in-flight
// request before issuing a new one, keeping the browser connection pool free.
const _ctrl: Record<string, AbortController> = {}
export function fetchWithAbort<T>(path: string): Promise<T> {
  _ctrl[path]?.abort()
  const c = new AbortController()
  _ctrl[path] = c
  return get<T>(path, c.signal)
}

export const api = {
  // auth
  login: (username: string, password: string) =>
    post<{ token: string; expires_in: number; username: string; role: string }>(
      '/auth/login', { username, password }),
  checkAuth: () => get<{ ok: boolean; username: string; role: string }>('/auth/check'),

  // health
  health: ()                          => get('/health'),

  // topology graph
  graph: (layer?: string)             => get(`/topology/graph${layer ? `?layer=${layer}` : ''}`),
  uploadTopology: (file: File)        => {
    const fd = new FormData()
    fd.append('file', file)
    const token = getToken()
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    return fetch(`${BASE}/topology/upload`, { method: 'POST', body: fd, headers }).then(async r => {
      if (r.status === 401) { onAuthExpired(); throw apiError(401) }
      if (!r.ok) {
        const text = await r.text().catch(() => '')
        console.error(`[api] POST /topology/upload → ${r.status}: ${text}`)
        throw apiError(r.status)
      }
      return r.json()
    })
  },
  exportTopology: ()                  => get('/topology/export'),
  clearTopology:  ()                  => post('/topology/clear'),
  uploadFloorplan: (file: File)       => {
    const fd = new FormData()
    fd.append('file', file)
    const token = getToken()
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    return fetch(`${BASE}/floorplan/upload`, { method: 'POST', body: fd, headers }).then(async r => {
      if (r.status === 401) { onAuthExpired(); throw apiError(401) }
      if (!r.ok) {
        const text = await r.text().catch(() => '')
        console.error(`[api] POST /floorplan/upload → ${r.status}: ${text}`)
        throw apiError(r.status)
      }
      return r.json()
    })
  },
  clearFloorplan: ()                  => post('/floorplan/clear'),
  getFloorplan:   ()                  => get('/floorplan'),
  breakLink:    (src: string, dst: string, layer = 'production') =>
    post('/topology/links/break',   { src_id: src, dst_id: dst, layer }),
  restoreLink:  (src: string, dst: string, layer = 'production') =>
    post('/topology/links/restore', { src_id: src, dst_id: dst, layer }),
  createLink:   (src: string, dst: string, layer = 'production') =>
    post('/topology/links/create',  { src_id: src, dst_id: dst, layer }),
  layoutGraph:  (algorithm: string) =>
    post<{ positions: Record<string, { x: number; y: number }> }>('/topology/layout', { algorithm }),
  // One call per drag, carrying every node that moved.
  savePositions: (positions: Record<string, { x: number; y: number }>) =>
    post('/topology/positions', { positions }),
  // Reset Layout: restore the canonical layout server-side, then refetch.
  resetPositions: () => post('/topology/positions/reset', {}),

  // devices
  devices:    (layer?: string)        => get(`/devices${layer ? `?layer=${layer}` : ''}`),
  device:     (id: string)            => get(`/devices/${id}`),
  addDevice:  (d: unknown)            => post('/devices', d),
  rackOccupancy: (datacenter: string) =>
    get(`/devices/rack-occupancy?datacenter=${encodeURIComponent(datacenter)}`),
  provisionRack: (datacenter: string) => post('/fleet/provision-rack', { datacenter }),
  provisionHall: (datacenter: string) => post('/fleet/provision-hall', { datacenter }),
  editDevice: (id: string, d: unknown)=> put(`/devices/${id}`, d),
  delDevice:  (id: string)            => del(`/devices/${id}`),
  deviceOverridable: (id: string)     => get(`/devices/${id}/overridable`),
  deviceOverrides:   (id: string)     => get(`/devices/${id}/overrides`),
  setDeviceOverride: (id: string, metric: string, value: number | string | null) =>
    post(`/devices/${id}/override`, { metric, value }),
  deviceFaults:      (id: string)     => get(`/devices/${id}/faults`),
  setDeviceFault:    (id: string, fault: string, action: 'start' | 'clear') =>
    post(`/devices/${id}/fault`, { fault, action }),

  // binding
  adapters:    ()                     => get('/binding/adapters'),
  setAdapter:  (adapter: string)      => post('/binding/adapter', { adapter }),
  setMask:     (mask: string)         => post('/binding/subnet-mask', { mask }),
  bindIPs:     ()                     => post('/binding/bind'),
  unbindIPs:   ()                     => post('/binding/unbind'),
  bindStatus:  ()                     => get('/binding/status'),

  // snmp
  snmpStatus:     ()                  => get('/snmp/status'),
  genSnmp:        ()                  => post('/snmp/datasets/generate'),
  startSnmp:      (port = 161, mgmtPort = 1161) => post('/snmp/start', { port, mgmt_port: mgmtPort }),
  stopSnmp:       ()                  => post('/snmp/stop'),
  clearSnmp:      ()                  => post('/snmp/clear'),
  setTrapReceiver:(ip: string, port: number) => post('/snmp/trap-receiver', { ip, port }),

  // gnmi
  gnmiStatus:     ()                  => get('/gnmi/status'),
  genGnmi:        ()                  => post('/gnmi/datasets/generate'),
  startGnmi:      (port = 50051)      => post('/gnmi/start', { port }),
  stopGnmi:       ()                  => post('/gnmi/stop'),
  clearGnmi:      ()                  => post('/gnmi/clear'),
  startProxy:     (port = 50051)      => post('/gnmi/proxy/start', { port }),
  stopProxy:      ()                  => post('/gnmi/proxy/stop'),

  // sflow
  sflowStatus: ()                         => get('/sflow/status'),
  sflowStart:  (cfg: { collector_ip: string; collector_port: number; interval: number; sample_rate: number }) =>
    post('/sflow/start', cfg),
  sflowStop:   ()                         => post('/sflow/stop'),

  // bacnet
  bacnetStatus: ()                        => get('/bacnet/status'),
  powerSummary: ()                        => get('/bacnet/power-summary'),
  bacnetStart:  (cfg: { base_instance: number; frequency_hz: number; port: number }) =>
    post('/bacnet/start', cfg),
  bacnetStop:   ()                        => post('/bacnet/stop'),
  ev2Metrics:   ()                        => get('/bacnet/ev2/metrics'),
  plantMetrics: ()                        => get('/bacnet/plant/metrics'),
  electricalMetrics: ()                   => get('/bacnet/electrical/devices'),
  plantOverrides:   (device: string)      => get('/bacnet/plant/overrides?device=' + encodeURIComponent(device)),
  setPlantOverride: (device: string, point: string, value: number | null) =>
    post('/bacnet/plant/overrides', { device, point, value }),

  // redfish
  redfishStatus:   ()                     => get('/redfish/status'),
  redfishStart:    (cfg: { port: number; username: string; password: string }) =>
    post('/redfish/start', cfg),
  redfishStop:     ()                     => post('/redfish/stop'),
  redfishSessions: ()                     => get('/redfish/sessions'),
  redfishAction:   (ip: string, action: string) => post('/redfish/action', { ip, action }),
  redfishLog:      (ip: string)           => get('/redfish/log?ip=' + encodeURIComponent(ip)),
  redfishSubscriptions: ()                => get('/redfish/subscriptions'),
  redfishTestEvent:     (ip: string)      => post('/redfish/test-event', { ip }),
  redfishSubscribe:     (ip: string, destination: string, context?: string) =>
    post('/redfish/subscribe', { ip, destination, context: context ?? '' }),
  redfishUnsubscribe:   (ip: string, id: string) =>
    post('/redfish/unsubscribe', { ip, id }),

  // rules
  rules:          ()                  => get('/rules'),
  setAutonomousFaults: (enabled: boolean) => post('/rules/autonomous-faults', { enabled }),
  resetCounts:    ()                  => post('/rules/reset-counts'),
  enableRule:     (name: string)      => post(`/rules/${encodeURIComponent(name)}/enable`),
  disableRule:    (name: string)      => post(`/rules/${encodeURIComponent(name)}/disable`),

  // traps
  traps:          ()                  => get('/traps'),
  clearTraps:     ()                  => del('/traps'),
  sendTrap: (device_id: string, trap_type: string, kwargs: Record<string, unknown> = {}) =>
    post('/traps/send', { device_id, trap_type, kwargs }),

  // jobs
  job: (id: string)                   => get(`/jobs/${id}`),

  // tick settings
  tickSettings:      ()              => get('/tick/settings'),
  applyTickSettings: (body: unknown) => post('/tick/settings', body),

  // fleet lifecycle (day-by-day server churn)
  fleetStatus:  ()             => get('/fleet/status'),
  fleetStart:   (cfg: unknown) => post('/fleet/start', cfg),
  fleetStop:    ()             => post('/fleet/stop'),
  fleetAdvance: ()             => post('/fleet/advance'),
  fleetConfig:  (cfg: unknown) => post('/fleet/config', cfg),
  snmpReload:   ()             => post('/snmp/reload'),
}
