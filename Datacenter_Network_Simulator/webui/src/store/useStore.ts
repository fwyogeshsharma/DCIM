import { create } from 'zustand'
import { api, fetchWithAbort, getToken, errorMessage } from '../api/client'
import type {
  GraphDevice, GraphLink, SnmpStatus, GnmiStatus, SFlowStatus, BacnetStatus,
  RedfishStatus,
  BindingStatus, RulesTable, TrapRecord, LogEntry,
  HealthStatus, AdaptersResponse, DeviceInfo, JobStatus, EV2DeviceSnapshot,
  PlantDeviceSnapshot,
} from '../api/types'

let _logSeq = 0
const MAX_LOGS = 2000

// Buffer: collect log entries, flush to store in one batch every 200 ms
// so 700 rapid SSE log events become 1-3 Zustand set() calls instead of 700.
const _logBuffer: LogEntry[] = []
let _logFlushTimer: ReturnType<typeof setTimeout> | null = null
function _scheduleFlush() {
  if (_logFlushTimer) return
  _logFlushTimer = setTimeout(() => {
    _logFlushTimer = null
    if (_logBuffer.length === 0) return
    const batch = _logBuffer.splice(0)
    useStore.setState((s) => {
      const logs = [...s.logs, ...batch]
      return { logs: logs.length > MAX_LOGS ? logs.slice(-MAX_LOGS) : logs }
    })
  }, 200)
}

interface Store {
  // topology
  graphDevices: GraphDevice[]
  graphLinks:   GraphLink[]
  activeLayer:  string

  // devices list
  devices:    DeviceInfo[]
  ev2Metrics:   EV2DeviceSnapshot[]
  plantMetrics: PlantDeviceSnapshot[]

  // simulator status
  snmp:    SnmpStatus | null
  gnmi:    GnmiStatus | null
  sflow:   SFlowStatus | null
  bacnet:  BacnetStatus | null
  redfish: RedfishStatus | null
  binding: BindingStatus | null
  adapters: AdaptersResponse | null
  rules:   RulesTable | null
  traps:   TrapRecord[]
  health:  HealthStatus | null

  // jobs
  activeJobs: Record<string, JobStatus>

  // console logs
  logs: LogEntry[]

  // incremented on every simulator tick (SSE sync_devices) — drives live refresh
  tickSeq: number

  // selection
  selectedDeviceId: string | null

  // topology UI state
  linkMode:        boolean
  fitViewTrigger:  number
  layoutAlgo:      string | null

  // right panel
  rightTab: string

  // active view
  activeView: 'main' | 'metrics' | 'floorplan'
  setActiveView: (v: 'main' | 'metrics' | 'floorplan') => void

  // binding operation state (survives tab switches)
  bindingBusy:     boolean
  bindingProgress: { done: number; total: number } | null
  setBindingBusy:     (v: boolean) => void
  setBindingProgress: (p: { done: number; total: number } | null) => void

  // SNMP panel port config (survives tab switches)
  snmpPort: number
  mgmtPort: number
  setSnmpPort: (p: number) => void
  setMgmtPort: (p: number) => void

  // Redfish panel config (survives tab switches)
  redfishPort:     number
  redfishUsername: string
  redfishPassword: string
  setRedfishPort:     (p: number) => void
  setRedfishUsername: (u: string) => void
  setRedfishPassword: (p: string) => void
  // Start/stop in-flight state — kept here so it survives panel close/reopen
  redfishBusy: boolean
  redfishOp:   'start' | 'stop' | null
  startRedfish: () => Promise<void>
  stopRedfish:  () => Promise<void>

  // gNMI panel config (survives tab switches)
  gnmiPort: number
  setGnmiPort: (p: number) => void
  gnmiProxyPort: number
  setGnmiProxyPort: (p: number) => void

  // sFlow panel config (survives tab switches)
  sflowCollectorIp: string
  sflowPort:        number
  sflowInterval:    number
  sflowRate:        number
  setSflowCollectorIp: (ip: string) => void
  setSflowPort:        (p: number) => void
  setSflowInterval:    (i: number) => void
  setSflowRate:        (r: number) => void
  sflowBusy: boolean
  sflowOp:   'start' | 'stop' | null
  startSflow: () => Promise<void>
  stopSflow:  () => Promise<void>

  // BACnet panel config (survives tab switches)
  bacnetBaseInstance: number
  bacnetFreqHz:       number
  bacnetPort:         number
  setBacnetBaseInstance: (i: number) => void
  setBacnetFreqHz:       (f: number) => void
  setBacnetPort:         (p: number) => void
  bacnetBusy:  boolean
  bacnetOp:    'start' | 'stop' | null
  bacnetError: string | null
  startBacnet: () => Promise<void>
  stopBacnet:  () => Promise<void>

  // actions
  setLayer:          (l: string) => void
  setRightTab:       (t: string) => void
  setSelectedDevice: (id: string | null) => void
  setLinkMode:       (v: boolean) => void
  triggerFitView:    () => void
  setLayoutAlgo:     (algo: string | null) => void
  appendLog:    (tab: LogEntry['tab'], msg: string, level: string) => void

  fetchGraph:   () => Promise<void>
  fetchDevices: () => Promise<void>
  fetchSnmp:    () => Promise<void>
  fetchGnmi:    () => Promise<void>
  fetchSflow:   () => Promise<void>
  fetchBacnet:       () => Promise<void>
  fetchRedfish:      () => Promise<void>
  fetchEV2Metrics:   () => Promise<void>
  fetchPlantMetrics: () => Promise<void>
  fetchBinding: () => Promise<void>
  fetchAdapters:() => Promise<void>
  fetchRules:   () => Promise<void>
  fetchTraps:   () => Promise<void>
  fetchHealth:  () => Promise<void>
  pollJob:      (id: string) => Promise<JobStatus>

  startPolling: () => void
  connectSSE:   () => void
}

export const useStore = create<Store>((set, get) => ({
  graphDevices: [],
  graphLinks:   [],
  activeLayer:  'all',
  devices:      [],
  ev2Metrics:   [],
  plantMetrics: [],
  snmp:         null,
  gnmi:         null,
  sflow:        null,
  bacnet:       null,
  redfish:      null,
  binding:      null,
  adapters:     null,
  rules:        null,
  traps:        [],
  health:       null,
  activeJobs:   {},
  logs:             [],
  tickSeq:          0,
  selectedDeviceId: null,
  linkMode:         false,
  fitViewTrigger:   0,
  layoutAlgo:       null,
  rightTab:         'binding',
  activeView:       'main',

  bindingBusy:     false,
  bindingProgress: null,
  setBindingBusy:     (v) => set({ bindingBusy: v }),
  setBindingProgress: (p) => set({ bindingProgress: p }),

  snmpPort: 161,
  mgmtPort: 1161,
  setSnmpPort: (p) => set({ snmpPort: p }),
  setMgmtPort: (p) => set({ mgmtPort: p }),

  redfishPort:     8443,
  redfishUsername: 'admin',
  redfishPassword: 'password',
  setRedfishPort:     (p) => set({ redfishPort: p }),
  setRedfishUsername: (u) => set({ redfishUsername: u }),
  setRedfishPassword: (p) => set({ redfishPassword: p }),

  redfishBusy: false,
  redfishOp:   null,
  startRedfish: async () => {
    if (get().redfishBusy) return
    const { redfishPort, redfishUsername, redfishPassword } = get()
    set({ redfishBusy: true, redfishOp: 'start' })
    try {
      await api.redfishStart({ port: redfishPort, username: redfishUsername, password: redfishPassword })
      await get().fetchRedfish()
    } catch { /* ignore */ }
    finally { set({ redfishBusy: false, redfishOp: null }) }
  },
  stopRedfish: async () => {
    if (get().redfishBusy) return
    set({ redfishBusy: true, redfishOp: 'stop' })
    try { await api.redfishStop(); await get().fetchRedfish() }
    catch { /* ignore */ }
    finally { set({ redfishBusy: false, redfishOp: null }) }
  },

  gnmiPort: 50051,
  setGnmiPort: (p) => set({ gnmiPort: p }),
  gnmiProxyPort: 50051,
  setGnmiProxyPort: (p) => set({ gnmiProxyPort: p }),

  sflowCollectorIp: '127.0.0.1',
  sflowPort:        6343,
  sflowInterval:    30,
  sflowRate:        1000,
  setSflowCollectorIp: (ip) => set({ sflowCollectorIp: ip }),
  setSflowPort:        (p) => set({ sflowPort: p }),
  setSflowInterval:    (i) => set({ sflowInterval: i }),
  setSflowRate:        (r) => set({ sflowRate: r }),

  sflowBusy: false,
  sflowOp:   null,
  startSflow: async () => {
    if (get().sflowBusy) return
    const { sflowCollectorIp, sflowPort, sflowInterval, sflowRate } = get()
    set({ sflowBusy: true, sflowOp: 'start' })
    try {
      await api.sflowStart({ collector_ip: sflowCollectorIp, collector_port: sflowPort,
                             interval: sflowInterval, sample_rate: sflowRate })
      await get().fetchSflow()
    } catch { /* ignore */ }
    finally { set({ sflowBusy: false, sflowOp: null }) }
  },
  stopSflow: async () => {
    if (get().sflowBusy) return
    set({ sflowBusy: true, sflowOp: 'stop' })
    try { await api.sflowStop(); await get().fetchSflow() }
    catch { /* ignore */ }
    finally { set({ sflowBusy: false, sflowOp: null }) }
  },

  bacnetBaseInstance: 40001,
  bacnetFreqHz:       50.0,
  bacnetPort:         47808,
  setBacnetBaseInstance: (i) => set({ bacnetBaseInstance: i }),
  setBacnetFreqHz:       (f) => set({ bacnetFreqHz: f }),
  setBacnetPort:         (p) => set({ bacnetPort: p }),

  bacnetBusy:  false,
  bacnetOp:    null,
  bacnetError: null,
  startBacnet: async () => {
    if (get().bacnetBusy) return
    const { bacnetBaseInstance, bacnetFreqHz, bacnetPort } = get()
    set({ bacnetBusy: true, bacnetOp: 'start', bacnetError: null })
    try {
      await api.bacnetStart({ base_instance: bacnetBaseInstance, frequency_hz: bacnetFreqHz, port: bacnetPort })
      await get().fetchBacnet()
    } catch (e: unknown) {
      set({ bacnetError: errorMessage(e) })
    }
    finally { set({ bacnetBusy: false, bacnetOp: null }) }
  },
  stopBacnet: async () => {
    if (get().bacnetBusy) return
    set({ bacnetBusy: true, bacnetOp: 'stop', bacnetError: null })
    try { await api.bacnetStop(); await get().fetchBacnet() }
    catch (e: unknown) {
      set({ bacnetError: errorMessage(e) })
    }
    finally { set({ bacnetBusy: false, bacnetOp: null }) }
  },

  setLayer:          (l) => { set({ activeLayer: l }); get().fetchGraph() },
  setRightTab:       (t) => set({ rightTab: t }),
  setActiveView:     (v) => set({ activeView: v }),
  setSelectedDevice: (id) => set({ selectedDeviceId: id }),
  setLinkMode:       (v) => set({ linkMode: v }),
  triggerFitView:    () => set(s => ({ fitViewTrigger: s.fitViewTrigger + 1 })),
  setLayoutAlgo:     (algo) => set({ layoutAlgo: algo }),

  appendLog: (tab, msg, level) => {
    _logBuffer.push({ id: _logSeq++, tab, msg, level, ts: Date.now() })
    _scheduleFlush()
  },

  fetchGraph: async () => {
    try {
      const layer = get().activeLayer === 'all' ? undefined : get().activeLayer
      const data = await api.graph(layer) as { devices: GraphDevice[]; links: GraphLink[] }
      set({ graphDevices: data.devices, graphLinks: data.links })
    } catch { /* ignore */ }
  },

  fetchDevices: async () => {
    try {
      const data = await api.devices() as { devices: DeviceInfo[] }
      set({ devices: data.devices })
    } catch { /* ignore */ }
  },

  fetchSnmp: async () => {
    try { set({ snmp: await fetchWithAbort<SnmpStatus>('/snmp/status') }) } catch { /* ignore */ }
  },

  fetchGnmi: async () => {
    try { set({ gnmi: await fetchWithAbort<GnmiStatus>('/gnmi/status') }) } catch { /* ignore */ }
  },

  fetchSflow: async () => {
    try { set({ sflow: await fetchWithAbort<SFlowStatus>('/sflow/status') }) } catch { /* ignore */ }
  },

  fetchBacnet: async () => {
    try { set({ bacnet: await fetchWithAbort<BacnetStatus>('/bacnet/status') }) } catch { /* ignore */ }
  },

  fetchRedfish: async () => {
    try { set({ redfish: await fetchWithAbort<RedfishStatus>('/redfish/status') }) } catch { /* ignore */ }
  },

  fetchEV2Metrics: async () => {
    try {
      const data = await api.ev2Metrics() as EV2DeviceSnapshot[]
      set({ ev2Metrics: data })
    } catch { /* ignore */ }
  },

  fetchPlantMetrics: async () => {
    try {
      const data = await api.plantMetrics() as PlantDeviceSnapshot[]
      set({ plantMetrics: data })
    } catch { /* ignore */ }
  },

  fetchBinding: async () => {
    try { set({ binding: await fetchWithAbort<BindingStatus>('/binding/status') }) } catch { /* ignore */ }
  },

  fetchAdapters: async () => {
    try { set({ adapters: await api.adapters() as AdaptersResponse }) } catch { /* ignore */ }
  },

  fetchRules: async () => {
    try { set({ rules: await api.rules() as RulesTable }) } catch { /* ignore */ }
  },

  fetchTraps: async () => {
    try {
      const data = await api.traps() as { traps: TrapRecord[] }
      set({ traps: data.traps })
    } catch { /* ignore */ }
  },

  fetchHealth: async () => {
    try { set({ health: await fetchWithAbort<HealthStatus>('/health') }) } catch { /* ignore */ }
  },

  pollJob: async (id: string): Promise<JobStatus> => {
    const job = await api.job(id) as JobStatus
    set((s) => ({ activeJobs: { ...s.activeJobs, [id]: job } }))
    return job
  },

  startPolling: () => {
    const s = get()
    // Initial load
    s.fetchGraph()
    s.fetchDevices()
    s.fetchSnmp()
    s.fetchGnmi()
    s.fetchSflow()
    s.fetchBacnet()
    s.fetchRedfish()
    s.fetchBinding()
    s.fetchRules()
    s.fetchHealth()
    s.fetchAdapters()
    s.fetchTraps()
    // Status-only refresh every 4 seconds — graph + devices excluded (driven by SSE sync events)
    setInterval(() => {
      const st = get()
      st.fetchSnmp()
      st.fetchGnmi()
      st.fetchSflow()
      st.fetchBacnet()
      st.fetchRedfish()
      st.fetchBinding()
      st.fetchHealth()
      st.fetchTraps()
      st.fetchRules()
    }, 4000)
  },

  connectSSE: () => {
    const connect = () => {
      const token = getToken()
      if (!token) { setTimeout(connect, 3000); return }  // not logged in yet
      const sseUrl = (import.meta.env.DEV ? 'http://localhost:8000' : '')
        + '/api/events?token=' + encodeURIComponent(token)
      const es = new EventSource(sseUrl)
      es.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data) as Record<string, unknown>
          const s = get()
          if (ev.type === 'log') {
            s.appendLog(
              (ev.tab as LogEntry['tab']) || 'snmp',
              String(ev.msg || ''),
              String(ev.level || 'info'),
            )
          } else if (ev.type === 'sync') {
            const t = ev.target as string
            if (t === 'snmp')     s.fetchSnmp()
            if (t === 'gnmi')     s.fetchGnmi()
            if (t === 'sflow')    s.fetchSflow()
            if (t === 'bacnet')   s.fetchBacnet()
            if (t === 'redfish')  { s.fetchRedfish(); s.fetchGraph() }  // power ops fade/unfade nodes
            if (t === 'binding')  s.fetchBinding()
            if (t === 'rules')    s.fetchRules()
            if (t === 'devices')  { s.fetchDevices(); set(st => ({ tickSeq: st.tickSeq + 1 })) }
            if (t === 'topology') s.fetchGraph()
            if (t === 'traps')    s.fetchTraps()
          } else if (ev.type === 'link_changed') {
            s.fetchGraph()
          } else if (ev.type === 'binding_started') {
            get().fetchBinding()
          } else if (ev.type === 'progress') {
            // optionally handle progress here
          }
        } catch { /* ignore parse errors */ }
      }
      es.onerror = () => {
        es.close()
        setTimeout(connect, 3000)
      }
    }
    connect()
  },
}))
