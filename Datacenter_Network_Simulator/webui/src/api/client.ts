// In dev mode (Vite), bypass the proxy and hit uvicorn directly.
// The Vite proxy stalls during IP binding (Windows routing table churn
// disrupts the proxy's keep-alive connection), causing 30–120s hangs.
// CORS is enabled on the API server (allow_origins=["*"]) so direct works.
const BASE = (import.meta.env.DEV ? 'http://localhost:8000' : '') + '/api'

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    signal,
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${method} ${path} → ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
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
  // health
  health: ()                          => get('/health'),

  // topology graph
  graph: (layer?: string)             => get(`/topology/graph${layer ? `?layer=${layer}` : ''}`),
  uploadTopology: (file: File)        => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`${BASE}/topology/upload`, { method: 'POST', body: fd }).then(r => r.json())
  },
  exportTopology: ()                  => get('/topology/export'),
  clearTopology:  ()                  => post('/topology/clear'),
  breakLink:    (src: string, dst: string, layer = 'production') =>
    post('/topology/links/break',   { src_id: src, dst_id: dst, layer }),
  restoreLink:  (src: string, dst: string, layer = 'production') =>
    post('/topology/links/restore', { src_id: src, dst_id: dst, layer }),
  createLink:   (src: string, dst: string, layer = 'production') =>
    post('/topology/links/create',  { src_id: src, dst_id: dst, layer }),
  layoutGraph:  (algorithm: string) =>
    post<{ positions: Record<string, { x: number; y: number }> }>('/topology/layout', { algorithm }),

  // devices
  devices:    (layer?: string)        => get(`/devices${layer ? `?layer=${layer}` : ''}`),
  device:     (id: string)            => get(`/devices/${id}`),
  addDevice:  (d: unknown)            => post('/devices', d),
  editDevice: (id: string, d: unknown)=> put(`/devices/${id}`, d),
  delDevice:  (id: string)            => del(`/devices/${id}`),

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
  bacnetStart:  (cfg: { base_instance: number; frequency_hz: number; port: number }) =>
    post('/bacnet/start', cfg),
  bacnetStop:   ()                        => post('/bacnet/stop'),
  ev2Metrics:   ()                        => get('/bacnet/ev2/metrics'),

  // rules
  rules:          ()                  => get('/rules'),
  enableEngine:   ()                  => post('/rules/enable'),
  disableEngine:  ()                  => post('/rules/disable'),
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
}
