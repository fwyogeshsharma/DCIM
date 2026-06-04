import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, InventoryDevice, InventoryLink } from '@/lib/api'
import {
  Plus, Search, X, Server, Network, HardDrive, Cpu, MemoryStick,
  Zap, Package, Link as LinkIcon, Edit3, Trash2, ChevronDown,
  ChevronRight, Activity, Wifi, WifiOff, AlertCircle, CheckCircle2,
  Wrench, Archive, RefreshCw,
} from 'lucide-react'
import toast from 'react-hot-toast'

// ── helpers ────────────────────────────────────────────────────────────────

const STATUS_META: Record<string, { label: string; color: string; dot: string }> = {
  idle:          { label: 'Idle',          color: 'text-emerald-400', dot: 'bg-emerald-400' },
  in_use:        { label: 'In Use',        color: 'text-blue-400',    dot: 'bg-blue-400'    },
  maintenance:   { label: 'Maintenance',   color: 'text-amber-400',   dot: 'bg-amber-400'   },
  decommissioned:{ label: 'Decommissioned',color: 'text-slate-500',   dot: 'bg-slate-500'   },
}

const DEVICE_TYPES = [
  'server','switch','router','firewall','storage','pdu','ups','crac',
  'ap','workstation','camera','iot','custom',
]

const LINK_TYPES = ['ethernet','fiber','dac','infiniband','usb','other']

function pct(used?: number | null, total?: number | null): number | null {
  if (used == null || total == null || total === 0) return null
  return Math.round((used / total) * 100)
}

function fmtGb(v?: number | null) {
  if (v == null) return '—'
  return v >= 1024 ? `${(v / 1024).toFixed(1)} TB` : `${v.toFixed(0)} GB`
}

function fmtNum(v?: number | null, unit = '') {
  if (v == null) return '—'
  return `${v}${unit}`
}

// ── Small bar component ────────────────────────────────────────────────────

function UsageBar({ pct: p, color = 'bg-blue-500' }: { pct: number | null; color?: string }) {
  if (p == null) return <span className="text-slate-600 text-xs">—</span>
  const safe = Math.min(Math.max(p, 0), 100)
  const barColor = safe > 85 ? 'bg-red-500' : safe > 65 ? 'bg-amber-500' : color
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${safe}%` }} />
      </div>
      <span className="text-[10px] text-slate-400 w-8 text-right">{safe}%</span>
    </div>
  )
}

// ── Summary card ───────────────────────────────────────────────────────────

function SummaryCard({
  icon: Icon, label, value, sub, color,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string; value: string | number; sub?: string; color: string
}) {
  return (
    <div className="bg-slate-900/60 border border-white/10 rounded-xl p-4 flex items-start gap-3">
      <div className={`p-2 rounded-lg ${color} bg-opacity-15 flex-shrink-0`}>
        <Icon className={`w-5 h-5 ${color}`} />
      </div>
      <div className="min-w-0">
        <p className="text-slate-400 text-xs">{label}</p>
        <p className="text-white text-xl font-bold leading-tight">{value}</p>
        {sub && <p className="text-slate-500 text-[11px] mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

// ── Device form ────────────────────────────────────────────────────────────

const EMPTY_DEVICE: Partial<InventoryDevice> = {
  hostname: '', device_type: 'server', vendor: '', model: '', serial_number: '',
  datacenter_name: '', room: '', rack: '', rack_unit: undefined,
  status: 'idle', notes: '', specs: {},
  cpu_cores: undefined, ram_gb: undefined, storage_gb: undefined,
  power_capacity_w: undefined, port_count: undefined,
}

function DeviceModal({
  initial, onClose, onSaved,
}: {
  initial?: InventoryDevice
  onClose: () => void
  onSaved: () => void
}) {
  const [tab, setTab] = useState<'basic' | 'specs'>('basic')
  const [form, setForm] = useState<Partial<InventoryDevice>>(initial ?? EMPTY_DEVICE)
  const [specs, setSpecs] = useState<Record<string, string>>(initial?.specs ?? {})
  const [err, setErr] = useState('')
  const isEdit = !!initial

  function set(k: keyof InventoryDevice, v: any) {
    setForm(p => ({ ...p, [k]: v }))
  }

  async function handleSave() {
    if (!form.hostname) { setErr('Hostname is required'); return }
    setErr('')
    try {
      const payload = { ...form, specs }
      if (isEdit) {
        await api.updateInventoryDevice(initial!.id, payload)
        toast.success('Device updated')
      } else {
        await api.createInventoryDevice(payload)
        toast.success('Device added to inventory')
      }
      onSaved()
      onClose()
    } catch (e: any) {
      setErr(e.message)
    }
  }

  const input = 'w-full bg-slate-800 border border-white/10 rounded px-2.5 py-1.5 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500'
  const sel   = 'w-full bg-slate-800 border border-white/10 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500'
  const label = 'block text-[10px] text-slate-400 mb-0.5'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-slate-900 border border-white/10 rounded-2xl w-[520px] max-h-[88vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Package className="w-4 h-4 text-blue-400" />
            <span className="text-sm font-semibold text-white">{isEdit ? 'Edit Device' : 'Add Device to Inventory'}</span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-4 h-4" /></button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-white/10 flex-shrink-0">
          {(['basic', 'specs'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 py-2 text-xs font-medium transition-colors ${tab === t ? 'text-white border-b-2 border-blue-500 bg-slate-800/40' : 'text-slate-500 hover:text-slate-300'}`}
            >
              {t === 'basic' ? '1 · Basic Info' : '2 · Hardware Specs'}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {err && (
            <div className="text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded px-3 py-2">{err}</div>
          )}

          {tab === 'basic' ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={label}>Hostname <span className="text-red-400">*</span></label>
                  <input className={input} value={form.hostname ?? ''} onChange={e => set('hostname', e.target.value)} placeholder="server-01" />
                </div>
                <div>
                  <label className={label}>Device Type</label>
                  <select className={sel} value={form.device_type ?? 'server'} onChange={e => set('device_type', e.target.value)}>
                    {DEVICE_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={label}>Vendor</label>
                  <input className={input} value={form.vendor ?? ''} onChange={e => set('vendor', e.target.value)} placeholder="Dell, Cisco, HP…" />
                </div>
                <div>
                  <label className={label}>Model</label>
                  <input className={input} value={form.model ?? ''} onChange={e => set('model', e.target.value)} placeholder="PowerEdge R750" />
                </div>
              </div>

              <div>
                <label className={label}>Serial Number</label>
                <input className={input} value={form.serial_number ?? ''} onChange={e => set('serial_number', e.target.value)} placeholder="SN-XXXXXX" />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={label}>Status</label>
                  <select className={sel} value={form.status ?? 'idle'} onChange={e => set('status', e.target.value as any)}>
                    <option value="idle">Idle</option>
                    <option value="in_use">In Use</option>
                    <option value="maintenance">Maintenance</option>
                    <option value="decommissioned">Decommissioned</option>
                  </select>
                </div>
                <div>
                  <label className={label}>Datacenter</label>
                  <input className={input} value={form.datacenter_name ?? ''} onChange={e => set('datacenter_name', e.target.value)} placeholder="DC-East-1" />
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className={label}>Room</label>
                  <input className={input} value={form.room ?? ''} onChange={e => set('room', e.target.value)} placeholder="Hall A" />
                </div>
                <div>
                  <label className={label}>Rack</label>
                  <input className={input} value={form.rack ?? ''} onChange={e => set('rack', e.target.value)} placeholder="R-01" />
                </div>
                <div>
                  <label className={label}>Rack Unit</label>
                  <input className={input} type="number" value={form.rack_unit ?? ''} onChange={e => set('rack_unit', e.target.value ? parseInt(e.target.value) : undefined)} placeholder="1" />
                </div>
              </div>

              <div>
                <label className={label}>Notes</label>
                <textarea
                  className={`${input} resize-none h-16`}
                  value={form.notes ?? ''}
                  onChange={e => set('notes', e.target.value)}
                  placeholder="Optional notes…"
                />
              </div>
            </>
          ) : (
            <>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider">Compute</p>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className={label}>CPU Cores</label>
                  <input className={input} type="number" value={form.cpu_cores ?? ''} onChange={e => set('cpu_cores', e.target.value ? parseInt(e.target.value) : undefined)} placeholder="64" />
                </div>
                <div>
                  <label className={label}>RAM (GB)</label>
                  <input className={input} type="number" value={form.ram_gb ?? ''} onChange={e => set('ram_gb', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="256" />
                </div>
                <div>
                  <label className={label}>Storage (GB)</label>
                  <input className={input} type="number" value={form.storage_gb ?? ''} onChange={e => set('storage_gb', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="4096" />
                </div>
              </div>

              <p className="text-[10px] text-slate-500 uppercase tracking-wider pt-1">Power & Network</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={label}>Power Capacity (W)</label>
                  <input className={input} type="number" value={form.power_capacity_w ?? ''} onChange={e => set('power_capacity_w', e.target.value ? parseInt(e.target.value) : undefined)} placeholder="1200" />
                </div>
                <div>
                  <label className={label}>Port Count</label>
                  <input className={input} type="number" value={form.port_count ?? ''} onChange={e => set('port_count', e.target.value ? parseInt(e.target.value) : undefined)} placeholder="48" />
                </div>
              </div>

              <p className="text-[10px] text-slate-500 uppercase tracking-wider pt-1">Current Utilisation</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={label}>CPU Used (%)</label>
                  <input className={input} type="number" value={form.cpu_used_pct ?? ''} onChange={e => set('cpu_used_pct', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="45" />
                </div>
                <div>
                  <label className={label}>RAM Used (%)</label>
                  <input className={input} type="number" value={form.ram_used_pct ?? ''} onChange={e => set('ram_used_pct', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="62" />
                </div>
                <div>
                  <label className={label}>Storage Used (GB)</label>
                  <input className={input} type="number" value={form.storage_used_gb ?? ''} onChange={e => set('storage_used_gb', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="2048" />
                </div>
                <div>
                  <label className={label}>Power Draw (W)</label>
                  <input className={input} type="number" value={form.power_draw_w ?? ''} onChange={e => set('power_draw_w', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="800" />
                </div>
              </div>

              <p className="text-[10px] text-slate-500 uppercase tracking-wider pt-1">Additional Spec Tags</p>
              {Object.entries(specs).map(([k, v]) => (
                <div key={k} className="flex gap-2 items-center">
                  <input className={`${input} flex-1`} value={k} readOnly />
                  <input className={`${input} flex-1`} value={v} onChange={e => setSpecs(p => ({ ...p, [k]: e.target.value }))} />
                  <button onClick={() => setSpecs(p => { const n = { ...p }; delete n[k]; return n })} className="text-red-400 hover:text-red-300">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              <button
                onClick={() => {
                  const key = prompt('Spec key (e.g. bios_version, nics):')
                  if (key) setSpecs(p => ({ ...p, [key]: '' }))
                }}
                className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
              >
                <Plus className="w-3 h-3" /> Add spec tag
              </button>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-white/10 flex-shrink-0">
          <div className="flex gap-2">
            {tab === 'specs' && (
              <button onClick={() => setTab('basic')} className="px-3 py-1.5 text-xs text-slate-400 hover:text-white border border-white/10 rounded-lg">Back</button>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-3 py-1.5 text-xs text-slate-400 hover:text-white border border-white/10 rounded-lg">Cancel</button>
            {tab === 'basic' ? (
              <button onClick={() => setTab('specs')} className="px-4 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium">Next: Specs →</button>
            ) : (
              <button onClick={handleSave} className="px-4 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium">
                {isEdit ? 'Save Changes' : 'Add Device'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Link form modal ────────────────────────────────────────────────────────

function LinkModal({
  devices, initial, onClose, onSaved,
}: {
  devices: InventoryDevice[]
  initial?: InventoryLink
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Partial<InventoryLink>>(initial ?? {
    src_device_id: '', dst_device_id: '', link_type: 'ethernet', status: 'active',
  })
  const [err, setErr] = useState('')
  const isEdit = !!initial

  function set(k: keyof InventoryLink, v: any) {
    setForm(p => ({ ...p, [k]: v }))
  }

  async function handleSave() {
    if (!form.src_device_id || !form.dst_device_id) { setErr('Both endpoints are required'); return }
    setErr('')
    try {
      if (isEdit) {
        await api.updateInventoryLink(initial!.id, form)
        toast.success('Link updated')
      } else {
        await api.createInventoryLink(form)
        toast.success('Link added to inventory')
      }
      onSaved()
      onClose()
    } catch (e: any) {
      setErr(e.message)
    }
  }

  const input = 'w-full bg-slate-800 border border-white/10 rounded px-2.5 py-1.5 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-500'
  const sel   = 'w-full bg-slate-800 border border-white/10 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500'
  const label = 'block text-[10px] text-slate-400 mb-0.5'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-slate-900 border border-white/10 rounded-2xl w-[440px] shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
          <div className="flex items-center gap-2">
            <LinkIcon className="w-4 h-4 text-blue-400" />
            <span className="text-sm font-semibold text-white">{isEdit ? 'Edit Link' : 'Add Physical Link'}</span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-4 h-4" /></button>
        </div>

        <div className="p-5 space-y-3">
          {err && <div className="text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded px-3 py-2">{err}</div>}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>Source Device <span className="text-red-400">*</span></label>
              <select className={sel} value={form.src_device_id ?? ''} onChange={e => set('src_device_id', e.target.value)}>
                <option value="">Select…</option>
                {devices.map(d => <option key={d.id} value={d.id}>{d.hostname}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Source Port</label>
              <input className={input} value={form.src_port ?? ''} onChange={e => set('src_port', e.target.value)} placeholder="eth0" />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>Destination Device <span className="text-red-400">*</span></label>
              <select className={sel} value={form.dst_device_id ?? ''} onChange={e => set('dst_device_id', e.target.value)}>
                <option value="">Select…</option>
                {devices.map(d => <option key={d.id} value={d.id}>{d.hostname}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Destination Port</label>
              <input className={input} value={form.dst_port ?? ''} onChange={e => set('dst_port', e.target.value)} placeholder="Gi0/1" />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={label}>Link Type</label>
              <select className={sel} value={form.link_type ?? 'ethernet'} onChange={e => set('link_type', e.target.value)}>
                {LINK_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Speed (Mbps)</label>
              <input className={input} type="number" value={form.speed_mbps ?? ''} onChange={e => set('speed_mbps', e.target.value ? parseInt(e.target.value) : undefined)} placeholder="10000" />
            </div>
            <div>
              <label className={label}>Status</label>
              <select className={sel} value={form.status ?? 'active'} onChange={e => set('status', e.target.value as any)}>
                <option value="active">Active</option>
                <option value="disconnected">Disconnected</option>
              </select>
            </div>
          </div>

          <div>
            <label className={label}>Notes</label>
            <input className={input} value={form.notes ?? ''} onChange={e => set('notes', e.target.value)} placeholder="Optional" />
          </div>
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-white/10">
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-slate-400 hover:text-white border border-white/10 rounded-lg">Cancel</button>
          <button onClick={handleSave} className="px-4 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium">
            {isEdit ? 'Save Changes' : 'Add Link'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Analytics side panel ───────────────────────────────────────────────────

function AnalyticsPanel({ device, onClose }: { device: InventoryDevice; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['inventory-analytics', device.id],
    queryFn:  () => api.getInventoryDeviceAnalytics(device.id, 24),
    staleTime: 30000,
  })

  const storageRemaining = device.storage_gb != null && device.storage_used_gb != null
    ? device.storage_gb - device.storage_used_gb
    : null

  const latestMap = useMemo(() => {
    const m: Record<string, number> = {}
    for (const row of data?.latest ?? []) m[row.metric_name] = row.value
    return m
  }, [data?.latest])

  const cpuPct  = latestMap['cpu_usage']    ?? device.cpu_used_pct    ?? null
  const ramPct  = latestMap['memory_usage'] ?? device.ram_used_pct    ?? null
  const diskPct = device.storage_gb ? pct(device.storage_used_gb, device.storage_gb) : null

  const sm = STATUS_META[device.status] ?? STATUS_META['idle']

  return (
    <div className="w-80 flex-shrink-0 bg-slate-900/80 border-l border-white/10 flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/10 flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-white font-semibold text-sm truncate">{device.hostname}</p>
          <p className="text-slate-400 text-xs">{device.vendor} {device.model}</p>
          <div className="flex items-center gap-1.5 mt-1">
            <span className={`w-1.5 h-1.5 rounded-full ${sm.dot}`} />
            <span className={`text-[11px] ${sm.color}`}>{sm.label}</span>
            {device.monitoring_status && (
              <>
                <span className="text-slate-700">·</span>
                {device.monitoring_status === 'online'
                  ? <><Wifi className="w-3 h-3 text-emerald-400" /><span className="text-[11px] text-emerald-400">Monitored</span></>
                  : <><WifiOff className="w-3 h-3 text-slate-500" /><span className="text-[11px] text-slate-500">Offline</span></>
                }
              </>
            )}
          </div>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-white flex-shrink-0 mt-0.5">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Location */}
        {(device.datacenter_name || device.rack) && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Location</p>
            <div className="bg-slate-800/50 rounded-lg p-3 text-xs text-slate-300 space-y-0.5">
              {device.datacenter_name && <p><span className="text-slate-500">DC:</span> {device.datacenter_name}</p>}
              {device.room && <p><span className="text-slate-500">Room:</span> {device.room}</p>}
              {device.rack && <p><span className="text-slate-500">Rack:</span> {device.rack} {device.rack_unit != null ? `U${device.rack_unit}` : ''}</p>}
            </div>
          </div>
        )}

        {/* Capacity */}
        <div>
          <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Capacity</p>
          <div className="space-y-2">
            <div>
              <div className="flex justify-between text-xs mb-0.5">
                <span className="text-slate-400 flex items-center gap-1"><Cpu className="w-3 h-3" /> CPU</span>
                <span className="text-slate-300">{fmtNum(device.cpu_cores, ' cores')}</span>
              </div>
              <UsageBar pct={cpuPct} color="bg-blue-500" />
            </div>
            <div>
              <div className="flex justify-between text-xs mb-0.5">
                <span className="text-slate-400 flex items-center gap-1"><MemoryStick className="w-3 h-3" /> RAM</span>
                <span className="text-slate-300">{fmtGb(device.ram_gb)}</span>
              </div>
              <UsageBar pct={ramPct} color="bg-purple-500" />
            </div>
            <div>
              <div className="flex justify-between text-xs mb-0.5">
                <span className="text-slate-400 flex items-center gap-1"><HardDrive className="w-3 h-3" /> Storage</span>
                <span className="text-slate-300">{fmtGb(device.storage_gb)}</span>
              </div>
              <UsageBar pct={diskPct} color="bg-emerald-500" />
            </div>
            {storageRemaining != null && (
              <p className="text-[11px] text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" /> {fmtGb(storageRemaining)} storage available
              </p>
            )}
            {device.power_capacity_w != null && (
              <div className="flex justify-between text-xs">
                <span className="text-slate-400 flex items-center gap-1"><Zap className="w-3 h-3" /> Power cap.</span>
                <span className="text-slate-300">{device.power_capacity_w} W</span>
              </div>
            )}
            {device.power_draw_w != null && (
              <div className="flex justify-between text-xs">
                <span className="text-slate-400 flex items-center gap-1"><Activity className="w-3 h-3" /> Current draw</span>
                <span className="text-slate-300">{device.power_draw_w} W</span>
              </div>
            )}
          </div>
        </div>

        {/* Live metrics */}
        {isLoading ? (
          <div className="flex items-center gap-2 text-slate-500 text-xs">
            <RefreshCw className="w-3 h-3 animate-spin" /> Loading metrics…
          </div>
        ) : !data?.linked ? (
          <div className="text-slate-600 text-xs bg-slate-800/40 rounded-lg p-3 text-center">
            <AlertCircle className="w-4 h-4 mx-auto mb-1 text-slate-600" />
            Not linked to monitoring system.
            <br />Set a Monitoring Device ID to see live metrics.
          </div>
        ) : data?.latest && data.latest.length > 0 ? (
          <div>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Live Metrics (latest)</p>
            <div className="bg-slate-800/50 rounded-lg p-3 space-y-1.5">
              {data.latest.map(m => (
                <div key={m.metric_name} className="flex justify-between text-xs">
                  <span className="text-slate-400">{m.metric_name.replace(/_/g, ' ')}</span>
                  <span className="text-slate-200 font-mono">{m.value.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Spec tags */}
        {device.specs && Object.keys(device.specs).length > 0 && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Spec Tags</p>
            <div className="bg-slate-800/50 rounded-lg p-3 space-y-1">
              {Object.entries(device.specs).map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs">
                  <span className="text-slate-400">{k}</span>
                  <span className="text-slate-200">{v}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {device.notes && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Notes</p>
            <p className="text-xs text-slate-400 bg-slate-800/40 rounded-lg p-3">{device.notes}</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function Inventory() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'devices' | 'links'>('devices')
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterType, setFilterType] = useState('')
  const [selectedDevice, setSelectedDevice] = useState<InventoryDevice | null>(null)
  const [showDeviceModal, setShowDeviceModal] = useState(false)
  const [editDevice, setEditDevice] = useState<InventoryDevice | undefined>()
  const [showLinkModal, setShowLinkModal] = useState(false)
  const [editLink, setEditLink] = useState<InventoryLink | undefined>()

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['inventory'] })

  const { data: summary, isLoading: sumLoading } = useQuery({
    queryKey: ['inventory', 'summary'],
    queryFn:  api.getInventorySummary.bind(api),
    staleTime: 30000,
  })

  const { data: devices = [], isLoading: devLoading } = useQuery({
    queryKey: ['inventory', 'devices'],
    queryFn:  () => api.getInventoryDevices(),
    staleTime: 30000,
  })

  const { data: links = [], isLoading: linksLoading } = useQuery({
    queryKey: ['inventory', 'links'],
    queryFn:  () => api.getInventoryLinks(),
    staleTime: 30000,
    enabled: tab === 'links',
  })

  const deleteDev = useMutation({
    mutationFn: (id: string) => api.deleteInventoryDevice(id),
    onSuccess: () => { toast.success('Device removed'); refresh() },
    onError: (e: any) => toast.error(e.message),
  })

  const deleteLink = useMutation({
    mutationFn: (id: string) => api.deleteInventoryLink(id),
    onSuccess: () => { toast.success('Link removed'); refresh() },
    onError: (e: any) => toast.error(e.message),
  })

  const filteredDevices = useMemo(() => {
    return devices.filter(d => {
      if (filterStatus && d.status !== filterStatus) return false
      if (filterType && d.device_type !== filterType) return false
      if (search) {
        const s = search.toLowerCase()
        if (!d.hostname.toLowerCase().includes(s) &&
            !d.vendor?.toLowerCase().includes(s) &&
            !d.model?.toLowerCase().includes(s) &&
            !d.serial_number?.toLowerCase().includes(s)) return false
      }
      return true
    })
  }, [devices, search, filterStatus, filterType])

  const devStats = summary?.devices
  const linkStats = summary?.links

  function openAddDevice() { setEditDevice(undefined); setShowDeviceModal(true) }
  function openEditDevice(d: InventoryDevice) { setEditDevice(d); setShowDeviceModal(true) }
  function openAddLink() { setEditLink(undefined); setShowLinkModal(true) }
  function openEditLink(l: InventoryLink) { setEditLink(l); setShowLinkModal(true) }

  function confirmDeleteDevice(d: InventoryDevice) {
    if (confirm(`Remove "${d.hostname}" from inventory?`)) deleteDev.mutate(d.id)
  }
  function confirmDeleteLink(l: InventoryLink) {
    if (confirm(`Remove link ${l.src_hostname} → ${l.dst_hostname}?`)) deleteLink.mutate(l.id)
  }

  return (
    <div className="flex h-full bg-slate-950">
      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 flex-shrink-0">
          <div>
            <h1 className="text-lg font-bold text-white flex items-center gap-2">
              <Package className="w-5 h-5 text-blue-400" /> Datacenter Inventory
            </h1>
            <p className="text-slate-500 text-xs mt-0.5">Physical assets, capacity & availability</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => { refresh(); toast.success('Refreshed') }}
              className="p-2 text-slate-400 hover:text-white border border-white/10 rounded-lg hover:bg-white/5 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
            <button
              onClick={openAddLink}
              className="flex items-center gap-1.5 px-3 py-2 text-xs border border-white/10 rounded-lg text-slate-300 hover:bg-white/5 transition-colors"
            >
              <LinkIcon className="w-3.5 h-3.5" /> Add Link
            </button>
            <button
              onClick={openAddDevice}
              className="flex items-center gap-1.5 px-3 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors"
            >
              <Plus className="w-3.5 h-3.5" /> Add Device
            </button>
          </div>
        </div>

        {/* Summary cards */}
        <div className="px-6 py-4 grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-3 flex-shrink-0">
          <SummaryCard icon={Server}     label="Total Devices"   value={devStats?.total ?? '—'}         color="text-blue-400" />
          <SummaryCard icon={CheckCircle2} label="Idle / Available" value={devStats?.idle ?? '—'}       color="text-emerald-400" sub={`${devStats?.in_use ?? '—'} in use`} />
          <SummaryCard icon={Wrench}     label="Maintenance"    value={devStats?.maintenance ?? '—'}     color="text-amber-400" />
          <SummaryCard icon={HardDrive}  label="Storage Free"   value={devStats ? fmtGb(devStats.available_storage_gb) : '—'} color="text-violet-400" sub={`of ${devStats ? fmtGb(devStats.total_storage_gb) : '—'} total`} />
          <SummaryCard icon={MemoryStick} label="RAM Available" value={devStats ? fmtGb(devStats.available_ram_gb) : '—'}   color="text-cyan-400"   sub={`of ${devStats ? fmtGb(devStats.total_ram_gb) : '—'} total`} />
          <SummaryCard icon={Network}    label="Links"          value={linkStats?.total_links ?? '—'}   color="text-slate-400" sub={`${linkStats?.active_links ?? 0} active`} />
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-6 mb-0 flex-shrink-0">
          {(['devices', 'links'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-xs font-medium rounded-t-lg transition-colors ${tab === t ? 'bg-slate-800 text-white border border-white/10 border-b-transparent' : 'text-slate-500 hover:text-slate-300'}`}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
              {t === 'devices' && devices.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 bg-slate-700 rounded text-[10px] text-slate-300">{devices.length}</span>
              )}
              {t === 'links' && links.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 bg-slate-700 rounded text-[10px] text-slate-300">{links.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Content area */}
        <div className="flex-1 flex overflow-hidden border-t border-white/10">
          {/* Table panel */}
          <div className={`flex-1 flex flex-col overflow-hidden transition-all ${selectedDevice ? 'border-r border-white/10' : ''}`}>
            {tab === 'devices' ? (
              <>
                {/* Filters */}
                <div className="flex gap-2 px-4 py-3 border-b border-white/10 flex-shrink-0 flex-wrap">
                  <div className="relative flex-1 min-w-[180px]">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
                    <input
                      className="w-full bg-slate-800 border border-white/10 rounded-lg pl-8 pr-3 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                      placeholder="Search hostname, vendor, model…"
                      value={search}
                      onChange={e => setSearch(e.target.value)}
                    />
                  </div>
                  <select
                    value={filterStatus}
                    onChange={e => setFilterStatus(e.target.value)}
                    className="bg-slate-800 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"
                  >
                    <option value="">All Statuses</option>
                    {Object.entries(STATUS_META).map(([k, v]) => (
                      <option key={k} value={k}>{v.label}</option>
                    ))}
                  </select>
                  <select
                    value={filterType}
                    onChange={e => setFilterType(e.target.value)}
                    className="bg-slate-800 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"
                  >
                    <option value="">All Types</option>
                    {DEVICE_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                  </select>
                  {(search || filterStatus || filterType) && (
                    <button
                      onClick={() => { setSearch(''); setFilterStatus(''); setFilterType('') }}
                      className="text-slate-500 hover:text-white text-xs flex items-center gap-1"
                    >
                      <X className="w-3 h-3" /> Clear
                    </button>
                  )}
                </div>

                {/* Device table */}
                <div className="flex-1 overflow-auto">
                  {devLoading ? (
                    <div className="flex items-center justify-center h-32 text-slate-500 text-sm">Loading inventory…</div>
                  ) : filteredDevices.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-48 text-slate-600">
                      <Package className="w-8 h-8 mb-2" />
                      <p className="text-sm">No devices found</p>
                      <button onClick={openAddDevice} className="mt-3 text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
                        <Plus className="w-3 h-3" /> Add first device
                      </button>
                    </div>
                  ) : (
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-slate-900/95 backdrop-blur z-10">
                        <tr className="text-slate-500 uppercase tracking-wide text-[10px] border-b border-white/10">
                          <th className="text-left px-4 py-2 font-medium">Hostname</th>
                          <th className="text-left px-3 py-2 font-medium">Type</th>
                          <th className="text-left px-3 py-2 font-medium">Vendor / Model</th>
                          <th className="text-left px-3 py-2 font-medium">Location</th>
                          <th className="text-left px-3 py-2 font-medium">Status</th>
                          <th className="text-left px-3 py-2 font-medium">CPU</th>
                          <th className="text-left px-3 py-2 font-medium">RAM</th>
                          <th className="text-left px-3 py-2 font-medium">Storage</th>
                          <th className="px-3 py-2"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredDevices.map(d => {
                          const sm = STATUS_META[d.status] ?? STATUS_META['idle']
                          const storageAvail = d.storage_gb != null && d.storage_used_gb != null
                            ? d.storage_gb - d.storage_used_gb : null
                          const isSelected = selectedDevice?.id === d.id

                          return (
                            <tr
                              key={d.id}
                              onClick={() => setSelectedDevice(isSelected ? null : d)}
                              className={`border-b border-white/5 cursor-pointer transition-colors ${isSelected ? 'bg-blue-600/10' : 'hover:bg-white/3'}`}
                            >
                              <td className="px-4 py-2.5">
                                <div className="flex items-center gap-2">
                                  <ChevronRight className={`w-3 h-3 flex-shrink-0 transition-transform text-slate-500 ${isSelected ? 'rotate-90 text-blue-400' : ''}`} />
                                  <div>
                                    <p className="text-white font-medium">{d.hostname}</p>
                                    {d.serial_number && <p className="text-slate-600 text-[10px]">{d.serial_number}</p>}
                                  </div>
                                </div>
                              </td>
                              <td className="px-3 py-2.5 text-slate-400 capitalize">{d.device_type}</td>
                              <td className="px-3 py-2.5">
                                <p className="text-slate-300">{d.vendor || '—'}</p>
                                <p className="text-slate-600 text-[10px]">{d.model || ''}</p>
                              </td>
                              <td className="px-3 py-2.5 text-slate-400">
                                {d.rack ? `${d.rack}${d.rack_unit != null ? ` U${d.rack_unit}` : ''}` : '—'}
                              </td>
                              <td className="px-3 py-2.5">
                                <div className="flex items-center gap-1.5">
                                  <span className={`w-1.5 h-1.5 rounded-full ${sm.dot}`} />
                                  <span className={sm.color}>{sm.label}</span>
                                </div>
                              </td>
                              <td className="px-3 py-2.5 text-slate-300">
                                {d.cpu_cores != null ? (
                                  <div>
                                    <span>{d.cpu_cores}c</span>
                                    {d.cpu_used_pct != null && (
                                      <div className="w-12 mt-0.5"><UsageBar pct={d.cpu_used_pct} color="bg-blue-500" /></div>
                                    )}
                                  </div>
                                ) : '—'}
                              </td>
                              <td className="px-3 py-2.5 text-slate-300">
                                {d.ram_gb != null ? (
                                  <div>
                                    <span>{fmtGb(d.ram_gb)}</span>
                                    {d.ram_used_pct != null && (
                                      <div className="w-12 mt-0.5"><UsageBar pct={d.ram_used_pct} color="bg-purple-500" /></div>
                                    )}
                                  </div>
                                ) : '—'}
                              </td>
                              <td className="px-3 py-2.5">
                                {d.storage_gb != null ? (
                                  <div>
                                    <span className="text-slate-300">{fmtGb(d.storage_gb)}</span>
                                    {storageAvail != null && (
                                      <p className="text-emerald-500 text-[10px]">{fmtGb(storageAvail)} free</p>
                                    )}
                                  </div>
                                ) : '—'}
                              </td>
                              <td className="px-3 py-2.5">
                                <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                                  <button
                                    onClick={() => openEditDevice(d)}
                                    className="p-1 text-slate-500 hover:text-blue-400 transition-colors"
                                    title="Edit"
                                  >
                                    <Edit3 className="w-3.5 h-3.5" />
                                  </button>
                                  <button
                                    onClick={() => confirmDeleteDevice(d)}
                                    className="p-1 text-slate-500 hover:text-red-400 transition-colors"
                                    title="Delete"
                                  >
                                    <Trash2 className="w-3.5 h-3.5" />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </>
            ) : (
              /* Links tab */
              <>
                <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 flex-shrink-0">
                  <p className="text-xs text-slate-400">{links.length} physical links</p>
                  <button onClick={openAddLink} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
                    <Plus className="w-3 h-3" /> Add Link
                  </button>
                </div>
                <div className="flex-1 overflow-auto">
                  {linksLoading ? (
                    <div className="flex items-center justify-center h-32 text-slate-500 text-sm">Loading links…</div>
                  ) : links.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-48 text-slate-600">
                      <LinkIcon className="w-8 h-8 mb-2" />
                      <p className="text-sm">No physical links recorded</p>
                      <button onClick={openAddLink} className="mt-3 text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
                        <Plus className="w-3 h-3" /> Add first link
                      </button>
                    </div>
                  ) : (
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-slate-900/95 backdrop-blur z-10">
                        <tr className="text-slate-500 uppercase tracking-wide text-[10px] border-b border-white/10">
                          <th className="text-left px-4 py-2 font-medium">Source</th>
                          <th className="text-left px-3 py-2 font-medium">Destination</th>
                          <th className="text-left px-3 py-2 font-medium">Type</th>
                          <th className="text-left px-3 py-2 font-medium">Speed</th>
                          <th className="text-left px-3 py-2 font-medium">Status</th>
                          <th className="px-3 py-2"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {links.map(l => (
                          <tr key={l.id} className="border-b border-white/5 hover:bg-white/3">
                            <td className="px-4 py-2.5">
                              <p className="text-white font-medium">{l.src_hostname}</p>
                              {l.src_port && <p className="text-slate-500 text-[10px]">{l.src_port}</p>}
                            </td>
                            <td className="px-3 py-2.5">
                              <p className="text-white font-medium">{l.dst_hostname}</p>
                              {l.dst_port && <p className="text-slate-500 text-[10px]">{l.dst_port}</p>}
                            </td>
                            <td className="px-3 py-2.5 text-slate-400 capitalize">{l.link_type}</td>
                            <td className="px-3 py-2.5 text-slate-300">
                              {l.speed_mbps != null ? (l.speed_mbps >= 1000 ? `${l.speed_mbps/1000}G` : `${l.speed_mbps}M`) : '—'}
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="flex items-center gap-1.5">
                                <span className={`w-1.5 h-1.5 rounded-full ${l.status === 'active' ? 'bg-emerald-400' : 'bg-slate-500'}`} />
                                <span className={l.status === 'active' ? 'text-emerald-400' : 'text-slate-500'}>
                                  {l.status === 'active' ? 'Active' : 'Disconnected'}
                                </span>
                              </div>
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="flex items-center gap-1">
                                <button onClick={() => openEditLink(l)} className="p-1 text-slate-500 hover:text-blue-400" title="Edit">
                                  <Edit3 className="w-3.5 h-3.5" />
                                </button>
                                <button onClick={() => confirmDeleteLink(l)} className="p-1 text-slate-500 hover:text-red-400" title="Delete">
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Analytics side panel */}
          {selectedDevice && tab === 'devices' && (
            <AnalyticsPanel
              device={selectedDevice}
              onClose={() => setSelectedDevice(null)}
            />
          )}
        </div>
      </div>

      {/* Modals */}
      {showDeviceModal && (
        <DeviceModal
          initial={editDevice}
          onClose={() => setShowDeviceModal(false)}
          onSaved={refresh}
        />
      )}
      {showLinkModal && (
        <LinkModal
          devices={devices}
          initial={editLink}
          onClose={() => setShowLinkModal(false)}
          onSaved={refresh}
        />
      )}
    </div>
  )
}
