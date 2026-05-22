import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { getDeviceTypeMeta } from '@/lib/deviceType'
import type { SNMPDevice, TopologyLink, SNMPMetric } from '@/lib/types'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import {
  Network, GitBranch, Server, Layers, Search, Plus, CheckCircle,
  XCircle, Clock, AlertTriangle, Trash2, X, ExternalLink, Globe,
  Shield, ChevronRight, Tag, Edit2, Check,
} from 'lucide-react'

// ── Persisted store helpers ────────────────────────────────────────────────────

function loadLS<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback } catch { return fallback }
}
function saveLS(key: string, val: unknown) {
  localStorage.setItem(key, JSON.stringify(val))
}

// ── Types ──────────────────────────────────────────────────────────────────────

type Tab = 'inventory' | 'ports' | 'patch' | 'changes' | 'ipam' | 'bgpvlan'

interface PatchPort { label: string; connectedTo?: string; note?: string }
interface PatchPanel { id: string; rack: string; name: string; ports: PatchPort[] }

interface ChangeRequest {
  id: string; title: string; description: string
  affectedDevices: string; scheduledDate: string
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'implemented'
  createdAt: string; resolvedAt?: string; resolvedBy?: string; comment?: string
}

interface Subnet {
  id: string; cidr: string; name: string; purpose: string
  vlan?: string; gateway?: string
}

interface BGPPeer {
  id: string; peerIp: string; remoteAs: string; localAs: string
  state: 'established' | 'idle' | 'active' | 'connect' | 'opensent'
  prefixesRx: number; prefixesTx: number; uptime?: string; device?: string
}

interface Vlan {
  id: string; vlanId: number; name: string; purpose: string
  devices: string; status: 'active' | 'inactive'
}

// ── Small helpers ──────────────────────────────────────────────────────────────

function uid() { return crypto.randomUUID() }

function ipToNumber(ip: string): number {
  const parts = ip.split('.').map(Number)
  if (parts.length !== 4) return 0
  return parts.reduce((acc, v) => (acc << 8) + v, 0) >>> 0
}

function cidrContains(cidr: string, ip: string): boolean {
  try {
    const [base, bits] = cidr.split('/')
    const mask = bits ? ~((1 << (32 - parseInt(bits))) - 1) >>> 0 : 0xffffffff
    return (ipToNumber(base) & mask) === (ipToNumber(ip) & mask)
  } catch { return false }
}

function statusBadge(status: ChangeRequest['status']) {
  const map: Record<ChangeRequest['status'], string> = {
    draft: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
    pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    approved: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    rejected: 'bg-red-500/20 text-red-400 border-red-500/30',
    implemented: 'bg-green-500/20 text-green-400 border-green-500/30',
  }
  return `text-xs px-2.5 py-0.5 rounded-full font-medium border ${map[status]}`
}

// ── Tab button ─────────────────────────────────────────────────────────────────

function TabBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button onClick={onClick} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${active ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white hover:bg-white/5'}`}>
      {icon}{label}
    </button>
  )
}

// ── Section card ───────────────────────────────────────────────────────────────

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-slate-800/50 border border-white/10 rounded-xl ${className}`}>{children}</div>
}

// ══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ══════════════════════════════════════════════════════════════════════════════

export default function NetworkOps() {
  const [tab, setTab] = useState<Tab>('inventory')
  const [search, setSearch] = useState('')

  // ── Queries ────────────────────────────────────────────────────────────────

  const { data: snmpDevices = [] } = useQuery<SNMPDevice[]>({
    queryKey: ['snmp-devices'],
    queryFn: () => api.getSNMPDevices(),
  })

  const { data: topoLinks = [] } = useQuery<TopologyLink[]>({
    queryKey: ['topology-links'],
    queryFn: () => api.getTopologyLinks(),
  })

  const { data: snmpMetrics = [] } = useQuery<SNMPMetric[]>({
    queryKey: ['snmp-metrics'],
    queryFn: () => api.getSNMPMetrics(),
    staleTime: 60000,
  })

  // ── Derived ────────────────────────────────────────────────────────────────

  const devicesByType = useMemo(() => {
    const map: Record<string, SNMPDevice[]> = {}
    for (const d of snmpDevices) {
      const cat = getDeviceTypeMeta(d.agent_id || d.device_name).category
      if (!map[cat]) map[cat] = []
      map[cat].push(d)
    }
    return map
  }, [snmpDevices])

  const filteredDevices = useMemo(() => {
    const q = search.toLowerCase()
    if (!q) return snmpDevices
    return snmpDevices.filter((d) => d.device_name.toLowerCase().includes(q) || d.device_ip.includes(q))
  }, [snmpDevices, search])

  // Port utilisation: group SNMPMetrics by device then interface
  const portData = useMemo(() => {
    const ifPatterns = ['ifInOctets', 'ifOutOctets', 'ifInErrors', 'ifOutErrors', 'ifOperStatus',
      'ifSpeed', 'in_octets', 'out_octets', 'in_errors', 'out_errors',
      'bytes_in', 'bytes_out', 'port', 'interface', 'eth', 'gi', 'te', 'ge']

    const relevant = snmpMetrics.filter((m) =>
      ifPatterns.some((p) => m.metric_name.toLowerCase().includes(p))
    )

    const byDevice: Record<string, Record<string, SNMPMetric[]>> = {}
    for (const m of relevant) {
      const dev = m.device_name || m.device_host || m.agent_id
      if (!byDevice[dev]) byDevice[dev] = {}
      const iface = m.metadata?.interface || m.metadata?.port_index || m.metric_name
      if (!byDevice[dev][iface]) byDevice[dev][iface] = []
      byDevice[dev][iface].push(m)
    }
    return byDevice
  }, [snmpMetrics])

  const portDevices = Object.keys(portData)

  // ── Local state (persisted) ────────────────────────────────────────────────

  const [panels, setPanels] = useState<PatchPanel[]>(() => loadLS('dcim_patch_panels', []))
  const [changes, setChanges] = useState<ChangeRequest[]>(() => loadLS('dcim_changes', []))
  const [subnets, setSubnets] = useState<Subnet[]>(() => loadLS('dcim_subnets', []))
  const [bgpPeers, setBgpPeers] = useState<BGPPeer[]>(() => loadLS('dcim_bgp_peers', []))
  const [vlans, setVlans] = useState<Vlan[]>(() => loadLS('dcim_vlans', []))

  // Patch panel form
  const [showAddPanel, setShowAddPanel] = useState(false)
  const [newPanel, setNewPanel] = useState({ rack: '', name: '', portCount: 24 })
  const [patchSearch, setPatchSearch] = useState('')

  // Change management form
  const [showAddChange, setShowAddChange] = useState(false)
  const [changeFilter, setChangeFilter] = useState<ChangeRequest['status'] | 'all'>('all')
  const [newChange, setNewChange] = useState<Omit<ChangeRequest, 'id' | 'createdAt'>>({
    title: '', description: '', affectedDevices: '', scheduledDate: '', status: 'draft',
  })
  const [approveComment, setApproveComment] = useState<Record<string, string>>({})

  // IPAM form
  const [showAddSubnet, setShowAddSubnet] = useState(false)
  const [ipSearch, setIpSearch] = useState('')
  const [newSubnet, setNewSubnet] = useState({ cidr: '', name: '', purpose: '', vlan: '', gateway: '' })

  // BGP/VLAN forms
  const [showAddPeer, setShowAddPeer] = useState(false)
  const [showAddVlan, setShowAddVlan] = useState(false)
  const [bgpVlanTab, setBgpVlanTab] = useState<'vlan' | 'bgp'>('vlan')
  const [newPeer, setNewPeer] = useState<Omit<BGPPeer, 'id'>>({ peerIp: '', remoteAs: '', localAs: '', state: 'idle', prefixesRx: 0, prefixesTx: 0, uptime: '', device: '' })
  const [newVlan, setNewVlan] = useState<Omit<Vlan, 'id'>>({ vlanId: 0, name: '', purpose: '', devices: '', status: 'active' })

  // ── Patch panel helpers ────────────────────────────────────────────────────

  function addPanel() {
    if (!newPanel.rack || !newPanel.name) return
    const panel: PatchPanel = {
      id: uid(), rack: newPanel.rack, name: newPanel.name,
      ports: Array.from({ length: newPanel.portCount }, (_, i) => ({ label: `P${String(i + 1).padStart(2, '0')}` })),
    }
    const updated = [...panels, panel]
    setPanels(updated); saveLS('dcim_patch_panels', updated)
    setShowAddPanel(false); setNewPanel({ rack: '', name: '', portCount: 24 })
  }

  function updatePort(panelId: string, portIdx: number, field: keyof PatchPort, value: string) {
    const updated = panels.map((p) =>
      p.id === panelId ? { ...p, ports: p.ports.map((pt, i) => i === portIdx ? { ...pt, [field]: value } : pt) } : p
    )
    setPanels(updated); saveLS('dcim_patch_panels', updated)
  }

  function deletePanel(id: string) {
    const updated = panels.filter((p) => p.id !== id)
    setPanels(updated); saveLS('dcim_patch_panels', updated)
  }

  // ── Change management helpers ──────────────────────────────────────────────

  function addChange() {
    if (!newChange.title) return
    const rec: ChangeRequest = { ...newChange, id: uid(), createdAt: new Date().toISOString() }
    const updated = [...changes, rec]
    setChanges(updated); saveLS('dcim_changes', updated)
    setShowAddChange(false)
    setNewChange({ title: '', description: '', affectedDevices: '', scheduledDate: '', status: 'draft' })
  }

  function advanceChange(id: string, status: ChangeRequest['status'], resolvedBy?: string) {
    const updated = changes.map((c) =>
      c.id === id ? { ...c, status, resolvedAt: new Date().toISOString(), resolvedBy, comment: approveComment[id] || c.comment } : c
    )
    setChanges(updated); saveLS('dcim_changes', updated)
    setApproveComment((p) => { const n = { ...p }; delete n[id]; return n })
  }

  function deleteChange(id: string) {
    const updated = changes.filter((c) => c.id !== id)
    setChanges(updated); saveLS('dcim_changes', updated)
  }

  // ── IPAM helpers ───────────────────────────────────────────────────────────

  function addSubnet() {
    if (!newSubnet.cidr || !newSubnet.name) return
    const updated = [...subnets, { ...newSubnet, id: uid() }]
    setSubnets(updated); saveLS('dcim_subnets', updated)
    setShowAddSubnet(false); setNewSubnet({ cidr: '', name: '', purpose: '', vlan: '', gateway: '' })
  }

  function deleteSubnet(id: string) {
    const updated = subnets.filter((s) => s.id !== id)
    setSubnets(updated); saveLS('dcim_subnets', updated)
  }

  const allDeviceIps = useMemo(() =>
    snmpDevices.map((d) => d.device_ip).filter(Boolean), [snmpDevices])

  const subnetStats = useMemo(() =>
    subnets.map((s) => {
      const allocated = allDeviceIps.filter((ip) => cidrContains(s.cidr, ip))
      const [, bits] = s.cidr.split('/')
      const total = bits ? Math.pow(2, 32 - parseInt(bits)) - 2 : 0
      const pct = total > 0 ? (allocated.length / total) * 100 : 0
      return { ...s, allocated, total: Math.max(0, total), pct }
    }), [subnets, allDeviceIps])

  const ipSearchResults = useMemo(() => {
    if (!ipSearch) return []
    return allDeviceIps.filter((ip) => ip.includes(ipSearch))
  }, [allDeviceIps, ipSearch])

  // ── BGP / VLAN helpers ─────────────────────────────────────────────────────

  function addPeer() {
    if (!newPeer.peerIp) return
    const updated = [...bgpPeers, { ...newPeer, id: uid() }]
    setBgpPeers(updated); saveLS('dcim_bgp_peers', updated)
    setShowAddPeer(false); setNewPeer({ peerIp: '', remoteAs: '', localAs: '', state: 'idle', prefixesRx: 0, prefixesTx: 0, uptime: '', device: '' })
  }

  function deletePeer(id: string) {
    const updated = bgpPeers.filter((p) => p.id !== id)
    setBgpPeers(updated); saveLS('dcim_bgp_peers', updated)
  }

  function addVlan() {
    if (!newVlan.vlanId || !newVlan.name) return
    const updated = [...vlans, { ...newVlan, id: uid() }]
    setVlans(updated); saveLS('dcim_vlans', updated)
    setShowAddVlan(false); setNewVlan({ vlanId: 0, name: '', purpose: '', devices: '', status: 'active' })
  }

  function deleteVlan(id: string) {
    const updated = vlans.filter((v) => v.id !== id)
    setVlans(updated); saveLS('dcim_vlans', updated)
  }

  // ── Filtered changes ───────────────────────────────────────────────────────

  const filteredChanges = useMemo(() =>
    changeFilter === 'all' ? changes : changes.filter((c) => c.status === changeFilter),
    [changes, changeFilter]
  )

  // ── Port utilisation chart data ────────────────────────────────────────────

  const [selectedPortDevice, setSelectedPortDevice] = useState('')
  const portChartData = useMemo(() => {
    const dev = selectedPortDevice || portDevices[0] || ''
    if (!dev || !portData[dev]) return []
    return Object.entries(portData[dev]).slice(0, 16).map(([iface, metrics]) => {
      const inM = metrics.find((m) => /in|rx/i.test(m.metric_name))
      const outM = metrics.find((m) => /out|tx/i.test(m.metric_name))
      const errM = metrics.find((m) => /err/i.test(m.metric_name))
      return {
        port: iface.length > 12 ? iface.slice(-10) : iface,
        fullName: iface,
        inBytes: inM ? Math.round(inM.value) : 0,
        outBytes: outM ? Math.round(outM.value) : 0,
        errors: errM ? Math.round(errM.value) : 0,
        lastSeen: metrics[0]?.timestamp || '',
      }
    })
  }, [selectedPortDevice, portDevices, portData])

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-white">Network Operations</h1>
          <p className="text-slate-400 mt-1 text-lg">Physical &amp; logical inventory, IPAM, and change control</p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <div className="flex items-center gap-2 bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2">
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-slate-300">{snmpDevices.length} devices</span>
          </div>
          <div className="flex items-center gap-2 bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2">
            <GitBranch className="w-4 h-4 text-slate-400" />
            <span className="text-slate-300">{topoLinks.length} links</span>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 bg-slate-800/30 border border-white/10 rounded-xl p-2">
        <TabBtn active={tab === 'inventory'} onClick={() => setTab('inventory')} icon={<Network className="w-4 h-4" />} label="Topology" />
        <TabBtn active={tab === 'ports'} onClick={() => setTab('ports')} icon={<Layers className="w-4 h-4" />} label="Port Utilisation" />
        <TabBtn active={tab === 'patch'} onClick={() => setTab('patch')} icon={<GitBranch className="w-4 h-4" />} label="Patch Panel" />
        <TabBtn active={tab === 'changes'} onClick={() => setTab('changes')} icon={<Shield className="w-4 h-4" />} label="Change Mgmt" />
        <TabBtn active={tab === 'ipam'} onClick={() => setTab('ipam')} icon={<Globe className="w-4 h-4" />} label="IPAM" />
        <TabBtn active={tab === 'bgpvlan'} onClick={() => setTab('bgpvlan')} icon={<Tag className="w-4 h-4" />} label="BGP / VLAN" />
      </div>

      {/* ══ TAB: TOPOLOGY INVENTORY ══════════════════════════════════════════ */}
      {tab === 'inventory' && (
        <div className="space-y-6">
          {/* Controls */}
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search device name or IP…"
                className="w-full bg-slate-800 border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            <Link to="/app/topology" className="flex items-center gap-2 px-4 py-2 bg-blue-600/20 border border-blue-500/30 text-blue-400 hover:bg-blue-600/30 rounded-lg text-sm font-medium transition-all">
              <ExternalLink className="w-4 h-4" />Full Topology View
            </Link>
            <Link to="/app/topology-3d" className="flex items-center gap-2 px-4 py-2 bg-purple-600/20 border border-purple-500/30 text-purple-400 hover:bg-purple-600/30 rounded-lg text-sm font-medium transition-all">
              <ExternalLink className="w-4 h-4" />3D View
            </Link>
          </div>

          {/* Device type summary */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {Object.entries(devicesByType).map(([cat, devs]) => {
              const meta = getDeviceTypeMeta(devs[0]?.agent_id || cat)
              return (
                <Card key={cat} className="p-4 text-center hover:border-white/20 transition-all">
                  <p className="text-2xl font-bold text-white">{devs.length}</p>
                  <p className="text-xs text-slate-400 mt-1">{meta.label}</p>
                </Card>
              )
            })}
            {snmpDevices.length === 0 && (
              <div className="col-span-full text-center py-6">
                <Network className="w-10 h-10 text-slate-600 mx-auto mb-2" />
                <p className="text-slate-400 text-sm">No SNMP devices discovered yet</p>
                <p className="text-xs text-slate-500 mt-1">Start an SNMP walk from the Topology page</p>
              </div>
            )}
          </div>

          {/* Device inventory table */}
          {filteredDevices.length > 0 && (
            <Card className="overflow-hidden">
              <div className="p-5 border-b border-white/10 flex items-center justify-between">
                <h3 className="font-semibold text-white">Device Inventory</h3>
                <span className="text-xs text-slate-500">{filteredDevices.length} devices</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-white/5">
                    {['Device Name', 'IP Address', 'Type', 'Agent ID', 'Last Seen'].map((h) => (
                      <th key={h} className="text-left px-5 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody className="divide-y divide-white/5">
                    {filteredDevices.map((d, i) => {
                      const meta = getDeviceTypeMeta(d.agent_id || d.device_name)
                      const age = Date.now() - new Date(d.last_seen).getTime()
                      const isOnline = age < 5 * 60 * 1000
                      return (
                        <tr key={i} className="hover:bg-white/5 transition-colors">
                          <td className="px-5 py-3">
                            <div className="flex items-center gap-2">
                              <div className={`w-2 h-2 rounded-full ${isOnline ? 'bg-green-400' : 'bg-red-400'}`} />
                              <span className="font-medium text-white">{d.device_name || '—'}</span>
                            </div>
                          </td>
                          <td className="px-5 py-3 text-slate-300 font-mono text-xs">{d.device_ip}</td>
                          <td className="px-5 py-3">
                            <span className={`text-xs px-2 py-0.5 rounded-full border ${meta.badgeClass}`}>{meta.label}</span>
                          </td>
                          <td className="px-5 py-3 text-slate-500 font-mono text-xs truncate max-w-[180px]">{d.agent_id}</td>
                          <td className="px-5 py-3 text-slate-400 text-xs">{new Date(d.last_seen).toLocaleString()}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {/* L1 Link table */}
          {topoLinks.length > 0 && (
            <Card className="overflow-hidden">
              <div className="p-5 border-b border-white/10 flex items-center justify-between">
                <h3 className="font-semibold text-white">Physical Links (L1)</h3>
                <span className="text-xs text-slate-500">{topoLinks.length} links discovered via LLDP/CDP/ARP</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-white/5">
                    {['Source Device', 'Port', '', 'Target Device', 'Target Port', 'Last Seen'].map((h, i) => (
                      <th key={i} className="text-left px-5 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody className="divide-y divide-white/5">
                    {topoLinks.slice(0, 100).map((l, i) => (
                      <tr key={i} className="hover:bg-white/5 transition-colors">
                        <td className="px-5 py-3">
                          <div className="font-medium text-white">{l.source_name}</div>
                          <div className="text-xs text-slate-500 font-mono">{l.source_ip}</div>
                        </td>
                        <td className="px-5 py-3 text-slate-400 text-xs font-mono">{l.source_port ?? '—'}</td>
                        <td className="px-5 py-3 text-slate-600"><ChevronRight className="w-4 h-4" /></td>
                        <td className="px-5 py-3">
                          <div className="font-medium text-white">{l.target_name}</div>
                          <div className="text-xs text-slate-500 font-mono">{l.target_ip}</div>
                        </td>
                        <td className="px-5 py-3 text-slate-400 text-xs font-mono">{l.target_port ?? '—'}</td>
                        <td className="px-5 py-3 text-slate-400 text-xs">{new Date(l.last_seen).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ══ TAB: PORT UTILISATION ════════════════════════════════════════════ */}
      {tab === 'ports' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-semibold text-white">Port Utilisation</h2>
            <p className="text-sm text-slate-400 mt-0.5">Per-port traffic, error rates and aging from SNMP interface metrics</p>
          </div>

          {portDevices.length === 0 ? (
            <Card className="p-12 text-center">
              <Layers className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No interface metrics available</p>
              <p className="text-xs text-slate-500 mt-1">Configure SNMP polling for IF-MIB OIDs (ifInOctets, ifOutOctets) on your switches</p>
            </Card>
          ) : (
            <>
              {/* Device picker */}
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-400">Device:</label>
                <select
                  value={selectedPortDevice || portDevices[0]}
                  onChange={(e) => setSelectedPortDevice(e.target.value)}
                  className="bg-slate-800 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                >
                  {portDevices.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
                <span className="text-xs text-slate-500">{portChartData.length} ports</span>
              </div>

              {/* Traffic chart */}
              <Card className="p-6">
                <h3 className="text-base font-semibold text-white mb-4">Traffic Volume per Port</h3>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={portChartData} margin={{ top: 4, right: 8, left: 0, bottom: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="port" tick={{ fill: '#94a3b8', fontSize: 10 }} angle={-35} textAnchor="end" interval={0} />
                      <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} width={60} tickFormatter={(v) => v >= 1e9 ? `${(v / 1e9).toFixed(1)}G` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : String(v)} />
                      <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#e2e8f0' }}
                        formatter={(v: number, name: string) => [v >= 1e9 ? `${(v / 1e9).toFixed(2)} GB` : v >= 1e6 ? `${(v / 1e6).toFixed(2)} MB` : `${v} B`, name === 'inBytes' ? 'In' : 'Out']} />
                      <Bar dataKey="inBytes" name="inBytes" fill="#3b82f6" radius={[2, 2, 0, 0]} />
                      <Bar dataKey="outBytes" name="outBytes" fill="#10b981" radius={[2, 2, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex gap-4 mt-2 justify-center text-xs text-slate-400">
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-blue-500 inline-block" />In (bytes)</span>
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-green-500 inline-block" />Out (bytes)</span>
                </div>
              </Card>

              {/* Port table */}
              <Card className="overflow-hidden">
                <div className="p-5 border-b border-white/10">
                  <h3 className="font-semibold text-white">Port Detail Table</h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr className="border-b border-white/5">
                      {['Interface', 'In (bytes)', 'Out (bytes)', 'Errors', 'Last Seen'].map((h) => (
                        <th key={h} className={`${h === 'Interface' ? 'text-left' : 'text-right'} px-5 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider`}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody className="divide-y divide-white/5">
                      {portChartData.map((row, i) => (
                        <tr key={i} className="hover:bg-white/5 transition-colors">
                          <td className="px-5 py-3 font-medium text-white font-mono text-xs">{row.fullName}</td>
                          <td className="px-5 py-3 text-right text-blue-400 font-mono text-xs">
                            {row.inBytes >= 1e9 ? `${(row.inBytes / 1e9).toFixed(2)} GB` : row.inBytes >= 1e6 ? `${(row.inBytes / 1e6).toFixed(2)} MB` : `${row.inBytes} B`}
                          </td>
                          <td className="px-5 py-3 text-right text-green-400 font-mono text-xs">
                            {row.outBytes >= 1e9 ? `${(row.outBytes / 1e9).toFixed(2)} GB` : row.outBytes >= 1e6 ? `${(row.outBytes / 1e6).toFixed(2)} MB` : `${row.outBytes} B`}
                          </td>
                          <td className={`px-5 py-3 text-right font-mono text-xs ${row.errors > 0 ? 'text-red-400' : 'text-slate-500'}`}>{row.errors}</td>
                          <td className="px-5 py-3 text-right text-slate-500 text-xs">{row.lastSeen ? new Date(row.lastSeen).toLocaleString() : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </>
          )}
        </div>
      )}

      {/* ══ TAB: PATCH PANEL ════════════════════════════════════════════════ */}
      {tab === 'patch' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Patch Panel Manager</h2>
              <p className="text-sm text-slate-400 mt-0.5">Cabling records and cross-connect documentation</p>
            </div>
            <button onClick={() => setShowAddPanel(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">
              <Plus className="w-4 h-4" />Add Panel
            </button>
          </div>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input value={patchSearch} onChange={(e) => setPatchSearch(e.target.value)} placeholder="Search port label or connected device…"
              className="w-full bg-slate-800 border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>

          {/* Add panel form */}
          {showAddPanel && (
            <Card className="p-5 border-blue-500/30 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Patch Panel</h3>
                <button onClick={() => setShowAddPanel(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                {[
                  { label: 'Rack Label', key: 'rack', ph: 'e.g. Rack-A1' },
                  { label: 'Panel Name', key: 'name', ph: 'e.g. PP-01' },
                ].map(({ label, key, ph }) => (
                  <div key={key}>
                    <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
                    <input value={(newPanel as any)[key]} onChange={(e) => setNewPanel((p) => ({ ...p, [key]: e.target.value }))}
                      placeholder={ph} className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                  </div>
                ))}
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Port Count</label>
                  <select value={newPanel.portCount} onChange={(e) => setNewPanel((p) => ({ ...p, portCount: parseInt(e.target.value) }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                    {[12, 24, 48].map((n) => <option key={n} value={n}>{n} ports</option>)}
                  </select>
                </div>
              </div>
              <div className="flex gap-3">
                <button onClick={addPanel} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">Create Panel</button>
                <button onClick={() => setShowAddPanel(false)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm text-slate-300 transition-all">Cancel</button>
              </div>
            </Card>
          )}

          {panels.length === 0 && !showAddPanel ? (
            <Card className="p-12 text-center">
              <GitBranch className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No patch panels configured</p>
            </Card>
          ) : (
            <div className="space-y-4">
              {panels.map((panel) => {
                const visiblePorts = patchSearch
                  ? panel.ports.map((p, i) => ({ ...p, idx: i })).filter((p) =>
                      p.label.toLowerCase().includes(patchSearch.toLowerCase()) ||
                      (p.connectedTo || '').toLowerCase().includes(patchSearch.toLowerCase()))
                  : panel.ports.map((p, i) => ({ ...p, idx: i }))

                const occupied = panel.ports.filter((p) => p.connectedTo).length
                return (
                  <Card key={panel.id} className="overflow-hidden">
                    <div className="p-5 border-b border-white/10 flex items-center justify-between">
                      <div>
                        <h3 className="font-semibold text-white">{panel.name}</h3>
                        <p className="text-xs text-slate-500 mt-0.5">Rack: {panel.rack} · {occupied}/{panel.ports.length} ports occupied</p>
                      </div>
                      <button onClick={() => deletePanel(panel.id)} className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                    {/* Port grid */}
                    <div className="p-4 grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
                      {visiblePorts.map(({ label, connectedTo, note, idx }) => (
                        <PortCell key={idx} label={label} connectedTo={connectedTo} note={note}
                          onChange={(field, val) => updatePort(panel.id, idx, field as keyof PatchPort, val)} />
                      ))}
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: CHANGE MANAGEMENT ══════════════════════════════════════════ */}
      {tab === 'changes' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Change Management</h2>
              <p className="text-sm text-slate-400 mt-0.5">Structured approval workflow for all network configuration changes</p>
            </div>
            <button onClick={() => setShowAddChange(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">
              <Plus className="w-4 h-4" />New Change
            </button>
          </div>

          {/* Status summary */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {(['all', 'draft', 'pending', 'approved', 'implemented', 'rejected'] as const).map((s) => {
              if (s === 'all') return (
                <button key="all" onClick={() => setChangeFilter('all')} className={`rounded-xl p-3 text-center border transition-all ${changeFilter === 'all' ? 'bg-white/10 border-white/20' : 'bg-slate-800/50 border-white/10 hover:border-white/20'}`}>
                  <p className="text-xl font-bold text-white">{changes.length}</p>
                  <p className="text-xs text-slate-400 mt-0.5">All</p>
                </button>
              )
              const count = changes.filter((c) => c.status === s).length
              const colors: Record<string, string> = { draft: 'text-slate-400', pending: 'text-yellow-400', approved: 'text-blue-400', implemented: 'text-green-400', rejected: 'text-red-400' }
              return (
                <button key={s} onClick={() => setChangeFilter(s)} className={`rounded-xl p-3 text-center border transition-all ${changeFilter === s ? 'bg-white/10 border-white/20' : 'bg-slate-800/50 border-white/10 hover:border-white/20'}`}>
                  <p className={`text-xl font-bold ${colors[s]}`}>{count}</p>
                  <p className="text-xs text-slate-400 mt-0.5 capitalize">{s}</p>
                </button>
              )
            })}
          </div>

          {/* Add change form */}
          {showAddChange && (
            <Card className="p-5 border-blue-500/30 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Change Request</h3>
                <button onClick={() => setShowAddChange(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="sm:col-span-2">
                  <label className="block text-xs text-slate-400 mb-1.5">Title *</label>
                  <input value={newChange.title} onChange={(e) => setNewChange((p) => ({ ...p, title: e.target.value }))} placeholder="e.g. Add VLAN 100 to core switches"
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs text-slate-400 mb-1.5">Description</label>
                  <textarea value={newChange.description} onChange={(e) => setNewChange((p) => ({ ...p, description: e.target.value }))} rows={2}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 resize-none" />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Affected Devices</label>
                  <input value={newChange.affectedDevices} onChange={(e) => setNewChange((p) => ({ ...p, affectedDevices: e.target.value }))} placeholder="core-sw-01, agg-sw-02"
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Scheduled Date</label>
                  <input type="datetime-local" value={newChange.scheduledDate} onChange={(e) => setNewChange((p) => ({ ...p, scheduledDate: e.target.value }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Initial Status</label>
                  <select value={newChange.status} onChange={(e) => setNewChange((p) => ({ ...p, status: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                    <option value="draft">Draft</option>
                    <option value="pending">Pending Approval</option>
                  </select>
                </div>
              </div>
              <div className="flex gap-3">
                <button onClick={addChange} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium">Submit</button>
                <button onClick={() => setShowAddChange(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {filteredChanges.length === 0 ? (
            <Card className="p-12 text-center">
              <Shield className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No change requests</p>
            </Card>
          ) : (
            <div className="space-y-3">
              {[...filteredChanges].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).map((c) => (
                <Card key={c.id} className="p-5 hover:border-white/20 transition-all">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 flex-wrap mb-2">
                        <span className={statusBadge(c.status)}>{c.status}</span>
                        <h3 className="font-semibold text-white">{c.title}</h3>
                      </div>
                      {c.description && <p className="text-sm text-slate-400 mb-2">{c.description}</p>}
                      <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                        {c.affectedDevices && <span className="flex items-center gap-1"><Server className="w-3 h-3" />{c.affectedDevices}</span>}
                        {c.scheduledDate && <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(c.scheduledDate).toLocaleString()}</span>}
                        <span>Created: {new Date(c.createdAt).toLocaleDateString()}</span>
                        {c.resolvedBy && <span>By: {c.resolvedBy}</span>}
                      </div>
                      {c.comment && <p className="text-xs text-slate-500 italic mt-1">"{c.comment}"</p>}
                    </div>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      {c.status === 'draft' && (
                        <button onClick={() => advanceChange(c.id, 'pending')} className="px-2.5 py-1 rounded-lg bg-yellow-500/10 hover:bg-yellow-500/20 text-yellow-400 text-xs transition-all">Submit</button>
                      )}
                      {c.status === 'pending' && (
                        <>
                          <input value={approveComment[c.id] || ''} onChange={(e) => setApproveComment((p) => ({ ...p, [c.id]: e.target.value }))}
                            placeholder="Comment…" className="w-28 bg-slate-900 border border-white/10 rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500" />
                          <button onClick={() => advanceChange(c.id, 'approved', 'Network Team')} className="p-1.5 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-400 transition-all" title="Approve"><CheckCircle className="w-4 h-4" /></button>
                          <button onClick={() => advanceChange(c.id, 'rejected', 'Network Team')} className="p-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-all" title="Reject"><XCircle className="w-4 h-4" /></button>
                        </>
                      )}
                      {c.status === 'approved' && (
                        <button onClick={() => advanceChange(c.id, 'implemented')} className="px-2.5 py-1 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-400 text-xs transition-all">Mark Implemented</button>
                      )}
                      <button onClick={() => deleteChange(c.id)} className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"><Trash2 className="w-4 h-4" /></button>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: IPAM ═══════════════════════════════════════════════════════ */}
      {tab === 'ipam' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">IP Address Management</h2>
              <p className="text-sm text-slate-400 mt-0.5">Subnet occupancy and allocation — device IPs auto-populated from SNMP discovery</p>
            </div>
            <button onClick={() => setShowAddSubnet(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">
              <Plus className="w-4 h-4" />Add Subnet
            </button>
          </div>

          {/* IP search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input value={ipSearch} onChange={(e) => setIpSearch(e.target.value)} placeholder="Search IP address across all subnets…"
              className="w-full bg-slate-800 border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>

          {ipSearchResults.length > 0 && (
            <Card className="p-4">
              <p className="text-xs text-slate-400 mb-2">Search results:</p>
              <div className="flex flex-wrap gap-2">
                {ipSearchResults.map((ip) => {
                  const device = snmpDevices.find((d) => d.device_ip === ip)
                  return (
                    <div key={ip} className="flex items-center gap-2 bg-slate-900 border border-white/10 rounded-lg px-3 py-1.5 text-xs">
                      <span className="font-mono text-white">{ip}</span>
                      {device && <span className="text-slate-500">{device.device_name}</span>}
                    </div>
                  )
                })}
              </div>
            </Card>
          )}

          {/* Add subnet form */}
          {showAddSubnet && (
            <Card className="p-5 border-blue-500/30 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Subnet</h3>
                <button onClick={() => setShowAddSubnet(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                {[
                  { label: 'CIDR *', key: 'cidr', ph: '10.0.0.0/24' },
                  { label: 'Name *', key: 'name', ph: 'Management LAN' },
                  { label: 'Purpose', key: 'purpose', ph: 'Server management' },
                  { label: 'VLAN', key: 'vlan', ph: '100' },
                  { label: 'Gateway', key: 'gateway', ph: '10.0.0.1' },
                ].map(({ label, key, ph }) => (
                  <div key={key}>
                    <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
                    <input value={(newSubnet as any)[key]} onChange={(e) => setNewSubnet((p) => ({ ...p, [key]: e.target.value }))}
                      placeholder={ph} className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                  </div>
                ))}
              </div>
              <div className="flex gap-3">
                <button onClick={addSubnet} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium">Add Subnet</button>
                <button onClick={() => setShowAddSubnet(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {/* Auto-discovered IPs without a subnet */}
          {snmpDevices.length > 0 && subnets.length === 0 && (
            <Card className="p-5">
              <p className="text-sm font-medium text-white mb-3">Discovered Device IPs ({snmpDevices.length})</p>
              <div className="flex flex-wrap gap-2">
                {snmpDevices.slice(0, 40).map((d) => (
                  <div key={d.device_ip} className="flex items-center gap-1.5 bg-slate-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs">
                    <span className="font-mono text-cyan-400">{d.device_ip}</span>
                    <span className="text-slate-500">{d.device_name}</span>
                  </div>
                ))}
                {snmpDevices.length > 40 && <span className="text-xs text-slate-500">+{snmpDevices.length - 40} more</span>}
              </div>
              <p className="text-xs text-slate-500 mt-3">Add subnets above to see occupancy analysis.</p>
            </Card>
          )}

          {subnets.length === 0 && snmpDevices.length === 0 ? (
            <Card className="p-12 text-center">
              <Globe className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No subnets defined</p>
              <p className="text-xs text-slate-500 mt-1">Add subnets to track IP allocation</p>
            </Card>
          ) : (
            <div className="space-y-4">
              {subnetStats.map((s) => (
                <Card key={s.id} className="p-5 hover:border-white/20 transition-all">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className="font-mono font-semibold text-white">{s.cidr}</span>
                        <span className="text-sm text-slate-300">{s.name}</span>
                        {s.vlan && <span className="text-xs bg-purple-500/20 text-purple-400 border border-purple-500/30 px-2 py-0.5 rounded-full">VLAN {s.vlan}</span>}
                      </div>
                      <div className="flex gap-4 text-xs text-slate-500 mt-1">
                        {s.purpose && <span>{s.purpose}</span>}
                        {s.gateway && <span>GW: {s.gateway}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-right">
                        <p className="text-sm font-bold text-white">{s.allocated.length}<span className="text-slate-400 font-normal">/{s.total}</span></p>
                        <p className="text-xs text-slate-500">{s.pct.toFixed(1)}% used</p>
                      </div>
                      <button onClick={() => deleteSubnet(s.id)} className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all"><Trash2 className="w-4 h-4" /></button>
                    </div>
                  </div>
                  <div className="h-2 bg-slate-700 rounded-full overflow-hidden mb-3">
                    <div className={`h-full rounded-full transition-all ${s.pct >= 90 ? 'bg-red-500' : s.pct >= 70 ? 'bg-yellow-500' : 'bg-blue-500'}`} style={{ width: `${s.pct}%` }} />
                  </div>
                  {s.allocated.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {s.allocated.slice(0, 20).map((ip) => {
                        const dev = snmpDevices.find((d) => d.device_ip === ip)
                        return (
                          <span key={ip} className="text-xs font-mono bg-slate-900 border border-white/10 rounded px-2 py-0.5 text-cyan-400" title={dev?.device_name || ip}>{ip}</span>
                        )
                      })}
                      {s.allocated.length > 20 && <span className="text-xs text-slate-500">+{s.allocated.length - 20} more</span>}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: BGP / VLAN ═════════════════════════════════════════════════ */}
      {tab === 'bgpvlan' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-semibold text-white">BGP / VLAN Tracking</h2>
            <p className="text-sm text-slate-400 mt-0.5">Routing table peers and VLAN segment health per device</p>
          </div>

          {/* Sub-tabs */}
          <div className="flex gap-2 bg-slate-800/30 border border-white/10 rounded-xl p-1.5 w-fit">
            {(['vlan', 'bgp'] as const).map((t) => (
              <button key={t} onClick={() => setBgpVlanTab(t)} className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${bgpVlanTab === t ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                {t.toUpperCase()}
              </button>
            ))}
          </div>

          {/* VLAN view */}
          {bgpVlanTab === 'vlan' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-sm text-slate-400">{vlans.length} VLANs tracked</span>
                <button onClick={() => setShowAddVlan(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">
                  <Plus className="w-4 h-4" />Add VLAN
                </button>
              </div>

              {showAddVlan && (
                <Card className="p-5 border-blue-500/30 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-white">New VLAN</h3>
                    <button onClick={() => setShowAddVlan(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    {[
                      { label: 'VLAN ID *', key: 'vlanId', type: 'number', ph: '100' },
                      { label: 'Name *', key: 'name', ph: 'Management' },
                      { label: 'Purpose', key: 'purpose', ph: 'Server OOB management' },
                      { label: 'Devices', key: 'devices', ph: 'core-sw-01, agg-sw-02' },
                    ].map(({ label, key, ph, type }) => (
                      <div key={key}>
                        <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
                        <input type={type || 'text'} value={(newVlan as any)[key]} onChange={(e) => setNewVlan((p) => ({ ...p, [key]: type === 'number' ? parseInt(e.target.value) || 0 : e.target.value }))}
                          placeholder={ph} className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                      </div>
                    ))}
                    <div>
                      <label className="block text-xs text-slate-400 mb-1.5">Status</label>
                      <select value={newVlan.status} onChange={(e) => setNewVlan((p) => ({ ...p, status: e.target.value as any }))}
                        className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                        <option value="active">Active</option>
                        <option value="inactive">Inactive</option>
                      </select>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <button onClick={addVlan} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium">Add VLAN</button>
                    <button onClick={() => setShowAddVlan(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
                  </div>
                </Card>
              )}

              {vlans.length === 0 ? (
                <Card className="p-12 text-center">
                  <Tag className="w-12 h-12 text-slate-600 mx-auto mb-3" />
                  <p className="text-slate-400">No VLANs tracked</p>
                  <p className="text-xs text-slate-500 mt-1">Add VLAN entries manually or configure SNMP polling for VLAN MIBs</p>
                </Card>
              ) : (
                <Card className="overflow-hidden">
                  <table className="w-full text-sm">
                    <thead><tr className="border-b border-white/5">
                      {['VLAN ID', 'Name', 'Purpose', 'Devices', 'Status', ''].map((h) => (
                        <th key={h} className="text-left px-5 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                      ))}
                    </tr></thead>
                    <tbody className="divide-y divide-white/5">
                      {vlans.map((v) => (
                        <tr key={v.id} className="hover:bg-white/5 transition-colors">
                          <td className="px-5 py-3 font-mono font-bold text-purple-400">{v.vlanId}</td>
                          <td className="px-5 py-3 font-medium text-white">{v.name}</td>
                          <td className="px-5 py-3 text-slate-400 text-xs">{v.purpose || '—'}</td>
                          <td className="px-5 py-3 text-slate-400 text-xs">{v.devices || '—'}</td>
                          <td className="px-5 py-3">
                            <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${v.status === 'active' ? 'bg-green-500/20 text-green-400 border-green-500/30' : 'bg-slate-500/20 text-slate-400 border-slate-500/30'}`}>{v.status}</span>
                          </td>
                          <td className="px-5 py-3">
                            <button onClick={() => deleteVlan(v.id)} className="p-1 text-slate-500 hover:text-red-400 transition-all"><Trash2 className="w-3.5 h-3.5" /></button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </div>
          )}

          {/* BGP view */}
          {bgpVlanTab === 'bgp' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-sm text-slate-400">{bgpPeers.length} BGP peers</span>
                <button onClick={() => setShowAddPeer(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium transition-all">
                  <Plus className="w-4 h-4" />Add Peer
                </button>
              </div>

              {/* BGP summary stats */}
              {bgpPeers.length > 0 && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {[
                    { label: 'Established', count: bgpPeers.filter((p) => p.state === 'established').length, color: 'text-green-400' },
                    { label: 'Idle / Down', count: bgpPeers.filter((p) => p.state !== 'established').length, color: 'text-red-400' },
                    { label: 'Total Prefixes Rx', count: bgpPeers.reduce((s, p) => s + p.prefixesRx, 0), color: 'text-blue-400' },
                    { label: 'Total Prefixes Tx', count: bgpPeers.reduce((s, p) => s + p.prefixesTx, 0), color: 'text-cyan-400' },
                  ].map(({ label, count, color }) => (
                    <Card key={label} className="p-4 text-center">
                      <p className={`text-2xl font-bold ${color}`}>{count}</p>
                      <p className="text-xs text-slate-400 mt-1">{label}</p>
                    </Card>
                  ))}
                </div>
              )}

              {showAddPeer && (
                <Card className="p-5 border-blue-500/30 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-white">New BGP Peer</h3>
                    <button onClick={() => setShowAddPeer(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    {[
                      { label: 'Peer IP *', key: 'peerIp', ph: '192.168.1.1' },
                      { label: 'Remote AS *', key: 'remoteAs', ph: '65001' },
                      { label: 'Local AS', key: 'localAs', ph: '65000' },
                      { label: 'Device', key: 'device', ph: 'core-router-01' },
                      { label: 'Uptime', key: 'uptime', ph: '3d 14h 22m' },
                    ].map(({ label, key, ph }) => (
                      <div key={key}>
                        <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
                        <input value={(newPeer as any)[key]} onChange={(e) => setNewPeer((p) => ({ ...p, [key]: e.target.value }))}
                          placeholder={ph} className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                      </div>
                    ))}
                    <div>
                      <label className="block text-xs text-slate-400 mb-1.5">State</label>
                      <select value={newPeer.state} onChange={(e) => setNewPeer((p) => ({ ...p, state: e.target.value as any }))}
                        className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                        {['established', 'idle', 'active', 'connect', 'opensent'].map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </div>
                    {[
                      { label: 'Prefixes Rx', key: 'prefixesRx' },
                      { label: 'Prefixes Tx', key: 'prefixesTx' },
                    ].map(({ label, key }) => (
                      <div key={key}>
                        <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
                        <input type="number" min={0} value={(newPeer as any)[key]} onChange={(e) => setNewPeer((p) => ({ ...p, [key]: parseInt(e.target.value) || 0 }))}
                          className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-3">
                    <button onClick={addPeer} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm text-white font-medium">Add Peer</button>
                    <button onClick={() => setShowAddPeer(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
                  </div>
                </Card>
              )}

              {bgpPeers.length === 0 ? (
                <Card className="p-12 text-center">
                  <Network className="w-12 h-12 text-slate-600 mx-auto mb-3" />
                  <p className="text-slate-400">No BGP peers configured</p>
                  <p className="text-xs text-slate-500 mt-1">Add peers manually or configure SNMP polling for BGP4-MIB</p>
                </Card>
              ) : (
                <Card className="overflow-hidden">
                  <table className="w-full text-sm">
                    <thead><tr className="border-b border-white/5">
                      {['Peer IP', 'Device', 'Remote AS', 'Local AS', 'State', 'Prefixes Rx', 'Prefixes Tx', 'Uptime', ''].map((h) => (
                        <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                      ))}
                    </tr></thead>
                    <tbody className="divide-y divide-white/5">
                      {bgpPeers.map((p) => (
                        <tr key={p.id} className="hover:bg-white/5 transition-colors">
                          <td className="px-4 py-3 font-mono text-xs text-cyan-400">{p.peerIp}</td>
                          <td className="px-4 py-3 text-slate-300 text-xs">{p.device || '—'}</td>
                          <td className="px-4 py-3 font-mono text-xs text-white">{p.remoteAs || '—'}</td>
                          <td className="px-4 py-3 font-mono text-xs text-slate-400">{p.localAs || '—'}</td>
                          <td className="px-4 py-3">
                            <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${p.state === 'established' ? 'bg-green-500/20 text-green-400 border-green-500/30' : p.state === 'idle' ? 'bg-red-500/20 text-red-400 border-red-500/30' : 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'}`}>{p.state}</span>
                          </td>
                          <td className="px-4 py-3 text-right text-blue-400 font-mono text-xs">{p.prefixesRx.toLocaleString()}</td>
                          <td className="px-4 py-3 text-right text-cyan-400 font-mono text-xs">{p.prefixesTx.toLocaleString()}</td>
                          <td className="px-4 py-3 text-slate-400 text-xs">{p.uptime || '—'}</td>
                          <td className="px-4 py-3">
                            <button onClick={() => deletePeer(p.id)} className="p-1 text-slate-500 hover:text-red-400 transition-all"><Trash2 className="w-3.5 h-3.5" /></button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Port cell with inline edit ─────────────────────────────────────────────────

function PortCell({ label, connectedTo, note, onChange }: {
  label: string; connectedTo?: string; note?: string
  onChange: (field: string, val: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(connectedTo || '')

  function commit() {
    onChange('connectedTo', draft)
    setEditing(false)
  }

  const isOccupied = !!connectedTo

  return (
    <div className={`rounded-lg border p-2 text-center transition-all ${isOccupied ? 'border-blue-500/40 bg-blue-500/5' : 'border-white/10 bg-slate-900/50'}`}>
      <p className="text-xs font-mono text-slate-400 mb-1">{label}</p>
      {editing ? (
        <div className="flex items-center gap-1">
          <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
            className="w-full bg-slate-800 border border-blue-500/50 rounded px-1 py-0.5 text-xs text-white focus:outline-none" placeholder="device:port" />
          <button onClick={commit} className="text-green-400"><Check className="w-3 h-3" /></button>
        </div>
      ) : (
        <button onClick={() => setEditing(true)} className="w-full text-xs truncate hover:text-white transition-colors" title={connectedTo || 'Click to assign'}>
          {connectedTo ? (
            <span className="text-blue-300 flex items-center gap-1 justify-center"><Edit2 className="w-2.5 h-2.5" />{connectedTo}</span>
          ) : (
            <span className="text-slate-600">free</span>
          )}
        </button>
      )}
    </div>
  )
}
