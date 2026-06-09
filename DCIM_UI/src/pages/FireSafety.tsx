import { useState, useMemo, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Flame, Wind, Droplets, AlertTriangle, CheckCircle, Clock, Plus, X,
  Trash2, FileText, Download, ShieldAlert, Thermometer, Eye, MapPin,
  RefreshCw, ChevronDown, ChevronUp, Bell, Box,
} from 'lucide-react'
import { api } from '@/lib/api'
import { buildFacility, isFacilityEmpty, demoFacility } from '@/lib/fireSafetyFacility'

const FireSafety3DScene = lazy(() => import('@/components/FireSafety3DScene'))

// ── Persistence ────────────────────────────────────────────────────────────────

function loadLS<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback } catch { return fallback }
}
function saveLS(key: string, v: unknown) { localStorage.setItem(key, JSON.stringify(v)) }
function uid() { return crypto.randomUUID() }
function daysUntil(dateStr: string) {
  return Math.ceil((new Date(dateStr).getTime() - Date.now()) / 86_400_000)
}

// ── Types ──────────────────────────────────────────────────────────────────────

type Tab = 'tests' | 'sensors' | 'epa' | 'egress' | 'maintenance' | 'postmortem' | '3d'

interface SuppressionTest {
  id: string; type: 'VESDA' | 'FM200' | 'Sprinkler' | 'CO2' | 'Novec1230'
  location: string; scheduledDate: string; technician: string
  status: 'scheduled' | 'in-progress' | 'passed' | 'failed' | 'cancelled'
  outcome?: string; certificateNo?: string; completedAt?: string; notes?: string
}

interface SafetySensor {
  id: string; name: string
  type: 'smoke' | 'heat' | 'water-leak' | 'co2' | 'vesda'
  zone: string; x: number; y: number
  status: 'normal' | 'alarm' | 'fault' | 'offline' | 'testing'
  lastChecked?: string; notes?: string
}

interface ComplianceSystem {
  id: string; system: string; agentType: string
  currentKg: number; capacityKg: number
  lastServiced: string; nextInspection: string
  technician?: string; certificate?: string; notes?: string
}

interface ChargeLog {
  id: string; systemId: string; date: string; technician: string
  action: 'add' | 'remove' | 'inspect'; amountKg: number; reason: string
}

interface ZoneCount {
  zoneId: string; zoneName: string; expected: number; accounted: number
}

interface MaintenanceItem {
  id: string; name: string; system: string; regulation: string
  lastDone?: string; dueDate: string; intervalDays: number
  priority: 'critical' | 'high' | 'medium' | 'low'
  assignedTo?: string; notes?: string
}

interface SafetyIncident {
  id: string; title: string; date: string
  type: 'fire' | 'smoke' | 'flood' | 'chemical' | 'power' | 'evacuation' | 'other'
  zone: string; severity: 'minor' | 'moderate' | 'major' | 'critical'
  status: 'open' | 'investigating' | 'closed'
  systemsTriggered: string; timeline: string; responseActions: string
  rootCause?: string; correctiveActions?: string
}

// ── Floor plan zones (shared by Sensor board + Egress map) ────────────────────

const ZONES = [
  { id: 'sra', label: 'Server Room A', x: 0, y: 0, w: 48, h: 48 },
  { id: 'srb', label: 'Server Room B', x: 52, y: 0, w: 48, h: 48 },
  { id: 'noc', label: 'NOC / Control', x: 0, y: 52, w: 48, h: 48 },
  { id: 'cor', label: 'Corridor', x: 52, y: 52, w: 48, h: 48 },
]

const EXITS = [
  { x: 18, y: 0, label: 'EXIT A', dir: 'up' },
  { x: 78, y: 0, label: 'EXIT B', dir: 'up' },
  { x: 100, y: 75, label: 'EXIT C', dir: 'right' },
]

const SENSOR_DEFAULTS: SafetySensor[] = [
  { id: 's1', name: 'SRA-VESDA-01', type: 'vesda', zone: 'sra', x: 15, y: 15, status: 'normal' },
  { id: 's2', name: 'SRA-SMOKE-01', type: 'smoke', zone: 'sra', x: 35, y: 30, status: 'normal' },
  { id: 's3', name: 'SRB-VESDA-01', type: 'vesda', zone: 'srb', x: 65, y: 15, status: 'normal' },
  { id: 's4', name: 'SRB-HEAT-01', type: 'heat', zone: 'srb', x: 80, y: 35, status: 'normal' },
  { id: 's5', name: 'NOC-SMOKE-01', type: 'smoke', zone: 'noc', x: 20, y: 70, status: 'normal' },
  { id: 's6', name: 'COR-WATER-01', type: 'water-leak', zone: 'cor', x: 72, y: 80, status: 'normal' },
]

// ── Colours & helpers ──────────────────────────────────────────────────────────

const SENSOR_COLORS: Record<SafetySensor['status'], string> = {
  normal: '#22c55e', alarm: '#ef4444', fault: '#f59e0b', offline: '#64748b', testing: '#3b82f6',
}

const SENSOR_ICONS: Record<SafetySensor['type'], React.ReactNode> = {
  smoke: <Wind className="w-3 h-3" />,
  heat: <Thermometer className="w-3 h-3" />,
  'water-leak': <Droplets className="w-3 h-3" />,
  co2: <AlertTriangle className="w-3 h-3" />,
  vesda: <Eye className="w-3 h-3" />,
}

function priorityBadge(p: MaintenanceItem['priority']) {
  const m: Record<string, string> = {
    critical: 'bg-red-500/20 text-red-400 border-red-500/30',
    high: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
    medium: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    low: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
  }
  return `text-xs px-2 py-0.5 rounded-full border font-medium ${m[p]}`
}

function dueBadge(days: number) {
  if (days < 0) return 'bg-red-500/20 text-red-400 border border-red-500/30'
  if (days <= 7) return 'bg-red-500/20 text-red-400 border border-red-500/30'
  if (days <= 30) return 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30'
  return 'bg-green-500/20 text-green-400 border border-green-500/30'
}

function severityBadge(s: SafetyIncident['severity']) {
  const m: Record<string, string> = {
    minor: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
    moderate: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    major: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
    critical: 'bg-red-500/20 text-red-400 border-red-500/30',
  }
  return `text-xs px-2 py-0.5 rounded-full border font-medium ${m[s]}`
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function TabBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button onClick={onClick} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${active ? 'bg-red-600 text-white shadow' : 'text-slate-400 hover:text-white hover:bg-white/5'}`}>
      {icon}{label}
    </button>
  )
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-slate-800/50 border border-white/10 rounded-xl ${className}`}>{children}</div>
}

function FieldInput({ label, value, onChange, type = 'text', placeholder = '', required = false }: {
  label: string; value: string | number; onChange: (v: string) => void
  type?: string; placeholder?: string; required?: boolean
}) {
  return (
    <div>
      <label className="block text-xs text-slate-400 mb-1.5">{label}{required && ' *'}</label>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500" />
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ══════════════════════════════════════════════════════════════════════════════

export default function FireSafety() {
  const [tab, setTab] = useState<Tab>('tests')

  // ── State ─────────────────────────────────────────────────────────────────

  const [tests, setTests] = useState<SuppressionTest[]>(() => loadLS('fs_tests', []))
  const [sensors, setSensors] = useState<SafetySensor[]>(() => loadLS('fs_sensors', SENSOR_DEFAULTS))
  const [compSystems, setCompSystems] = useState<ComplianceSystem[]>(() => loadLS('fs_comp', []))
  const [chargeLogs, setChargeLogs] = useState<ChargeLog[]>(() => loadLS('fs_charges', []))
  const [zoneCounts, setZoneCounts] = useState<ZoneCount[]>(() => loadLS('fs_egress', ZONES.map((z) => ({ zoneId: z.id, zoneName: z.label, expected: 0, accounted: 0 }))))
  const [maintItems, setMaintItems] = useState<MaintenanceItem[]>(() => loadLS('fs_maint', []))
  const [incidents, setIncidents] = useState<SafetyIncident[]>(() => loadLS('fs_incidents', []))

  // ── UI state ──────────────────────────────────────────────────────────────

  const [showForm, setShowForm] = useState(false)
  const [selectedSensor, setSelectedSensor] = useState<SafetySensor | null>(null)
  const [drillMode, setDrillMode] = useState(false)
  const [expandedIncident, setExpandedIncident] = useState<string | null>(null)

  // Test form
  const emptyTest: Omit<SuppressionTest, 'id'> = { type: 'VESDA', location: '', scheduledDate: '', technician: '', status: 'scheduled', notes: '' }
  const [newTest, setNewTest] = useState(emptyTest)

  // Compliance form
  const emptyComp: Omit<ComplianceSystem, 'id'> = { system: '', agentType: 'FM200', currentKg: 0, capacityKg: 0, lastServiced: '', nextInspection: '', technician: '', certificate: '', notes: '' }
  const [newComp, setNewComp] = useState(emptyComp)
  const [showCompForm, setShowCompForm] = useState(false)
  const newEmptyLog: Omit<ChargeLog, 'id'> = { systemId: '', date: '', technician: '', action: 'inspect', amountKg: 0, reason: '' }
  const [newLog, setNewLog] = useState(newEmptyLog)
  const [showLogForm, setShowLogForm] = useState(false)

  // Maintenance form
  const emptyMaint: Omit<MaintenanceItem, 'id'> = { name: '', system: '', regulation: '', lastDone: '', dueDate: '', intervalDays: 365, priority: 'high', assignedTo: '', notes: '' }
  const [newMaint, setNewMaint] = useState(emptyMaint)
  const [showMaintForm, setShowMaintForm] = useState(false)

  // Incident form
  const emptyIncident: Omit<SafetyIncident, 'id'> = { title: '', date: '', type: 'smoke', zone: '', severity: 'minor', status: 'open', systemsTriggered: '', timeline: '', responseActions: '', rootCause: '', correctiveActions: '' }
  const [newIncident, setNewIncident] = useState(emptyIncident)
  const [showIncidentForm, setShowIncidentForm] = useState(false)

  // ── Helpers ────────────────────────────────────────────────────────────────

  function persist<T>(key: string, setter: React.Dispatch<React.SetStateAction<T>>, val: T) {
    setter(val); saveLS(key, val)
  }

  // Tests
  function addTest() {
    if (!newTest.location || !newTest.scheduledDate) return
    const updated = [...tests, { ...newTest, id: uid() }]
    persist('fs_tests', setTests, updated); setShowForm(false); setNewTest(emptyTest)
  }
  function updateTestStatus(id: string, status: SuppressionTest['status'], outcome?: string) {
    const updated = tests.map((t) => t.id === id ? { ...t, status, outcome: outcome ?? t.outcome, completedAt: new Date().toISOString() } : t)
    persist('fs_tests', setTests, updated)
  }
  function deleteTest(id: string) { const u = tests.filter((t) => t.id !== id); persist('fs_tests', setTests, u) }

  // Sensors
  function updateSensor(id: string, patch: Partial<SafetySensor>) {
    const updated = sensors.map((s) => s.id === id ? { ...s, ...patch, lastChecked: new Date().toISOString() } : s)
    persist('fs_sensors', setSensors, updated)
    setSelectedSensor((s) => s?.id === id ? { ...s, ...patch } : s)
  }
  function resetAllSensors() {
    const updated = sensors.map((s) => ({ ...s, status: 'normal' as const, lastChecked: new Date().toISOString() }))
    persist('fs_sensors', setSensors, updated); setSelectedSensor(null)
  }

  // Compliance
  function addCompSystem() {
    if (!newComp.system) return
    const updated = [...compSystems, { ...newComp, id: uid() }]
    persist('fs_comp', setCompSystems, updated); setShowCompForm(false); setNewComp(emptyComp)
  }
  function deleteComp(id: string) { persist('fs_comp', setCompSystems, compSystems.filter((c) => c.id !== id)) }
  function addChargeLog() {
    if (!newLog.systemId || !newLog.date) return
    const updated = [...chargeLogs, { ...newLog, id: uid() }]
    persist('fs_charges', setChargeLogs, updated); setShowLogForm(false); setNewLog(newEmptyLog)
  }

  // Egress
  function updateZoneCount(zoneId: string, field: 'expected' | 'accounted', val: number) {
    const updated = zoneCounts.map((z) => z.zoneId === zoneId ? { ...z, [field]: val } : z)
    persist('fs_egress', setZoneCounts, updated)
  }

  // Maintenance
  function addMaint() {
    if (!newMaint.name || !newMaint.dueDate) return
    const updated = [...maintItems, { ...newMaint, id: uid() }]
    persist('fs_maint', setMaintItems, updated); setShowMaintForm(false); setNewMaint(emptyMaint)
  }
  function deleteMaint(id: string) { persist('fs_maint', setMaintItems, maintItems.filter((m) => m.id !== id)) }
  function markMaintDone(id: string) {
    const now = new Date().toISOString().split('T')[0]
    const updated = maintItems.map((m) => {
      if (m.id !== id) return m
      const next = new Date(Date.now() + m.intervalDays * 86_400_000).toISOString().split('T')[0]
      return { ...m, lastDone: now, dueDate: next }
    })
    persist('fs_maint', setMaintItems, updated)
  }

  // Incidents
  function addIncident() {
    if (!newIncident.title || !newIncident.date) return
    const updated = [...incidents, { ...newIncident, id: uid() }]
    persist('fs_incidents', setIncidents, updated); setShowIncidentForm(false); setNewIncident(emptyIncident)
  }
  function updateIncidentStatus(id: string, status: SafetyIncident['status']) {
    const updated = incidents.map((i) => i.id === id ? { ...i, status } : i)
    persist('fs_incidents', setIncidents, updated)
  }
  function deleteIncident(id: string) { persist('fs_incidents', setIncidents, incidents.filter((i) => i.id !== id)) }

  function exportPostMortem(inc: SafetyIncident) {
    const text = [
      `SAFETY INCIDENT POST-MORTEM REPORT`,
      `Generated: ${new Date().toLocaleString()}`,
      `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
      `Title       : ${inc.title}`,
      `Date        : ${inc.date}`,
      `Type        : ${inc.type.toUpperCase()}`,
      `Zone        : ${inc.zone}`,
      `Severity    : ${inc.severity.toUpperCase()}`,
      `Status      : ${inc.status}`,
      ``,
      `SYSTEMS TRIGGERED`,
      inc.systemsTriggered || 'None recorded',
      ``,
      `TIMELINE OF EVENTS`,
      inc.timeline || 'Not recorded',
      ``,
      `RESPONSE ACTIONS`,
      inc.responseActions || 'Not recorded',
      ``,
      `ROOT CAUSE`,
      inc.rootCause || 'Under investigation',
      ``,
      `CORRECTIVE ACTIONS`,
      inc.correctiveActions || 'Pending',
    ].join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    a.download = `postmortem-${inc.id.slice(0, 8)}.txt`; a.click()
  }

  // ── Derived ────────────────────────────────────────────────────────────────

  const alarmSensors = sensors.filter((s) => s.status === 'alarm' || s.status === 'fault')
  const overdueItems = maintItems.filter((m) => daysUntil(m.dueDate) < 0)
  const dueSoonItems = maintItems.filter((m) => { const d = daysUntil(m.dueDate); return d >= 0 && d <= 30 })
  const totalExpected = zoneCounts.reduce((s, z) => s + z.expected, 0)
  const totalAccounted = zoneCounts.reduce((s, z) => s + z.accounted, 0)
  const sortedMaint = useMemo(() => [...maintItems].sort((a, b) => new Date(a.dueDate).getTime() - new Date(b.dueDate).getTime()), [maintItems])

  // ── 3D facility (data-driven) ───────────────────────────────────────────────
  // Environment metrics carry each device's physical placement in metadata
  // (room + floor); we collapse them into a floor/room facility model. Only
  // fetched while the 3D tab is open. Falls back to a demo building when no
  // device reports a room/floor.
  const { data: envMetrics } = useQuery({
    queryKey: ['firesafety-env-metrics'],
    queryFn: () => api.getMetrics({ metric_type: 'environment.temperature_c', time_range: '1h', limit: 10000 }),
    enabled: tab === '3d',
    refetchInterval: tab === '3d' ? 30000 : false,
    staleTime: 20000,
  })

  const liveFacility = useMemo(() => buildFacility(envMetrics), [envMetrics])
  const facility = useMemo(
    () => (isFacilityEmpty(liveFacility) ? demoFacility() : liveFacility),
    [liveFacility],
  )
  const usingDemo = isFacilityEmpty(liveFacility)

  // Manual sensors carry a room hint (their zone's human label) so the scene can
  // match them into the matching generated room.
  const sceneSensors = useMemo(
    () => sensors.map((s) => ({
      id: s.id, name: s.name, type: s.type, zone: s.zone, status: s.status,
      room: ZONES.find((z) => z.id === s.zone)?.label ?? s.zone,
    })),
    [sensors],
  )

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-white">Fire &amp; Safety Systems</h1>
          <p className="text-slate-400 mt-1 text-lg">Lifecycle monitoring of suppression, environmental &amp; emergency systems</p>
        </div>
        {/* Status pills */}
        <div className="flex items-center gap-2 flex-wrap">
          {alarmSensors.length > 0 && (
            <div className="flex items-center gap-2 bg-red-500/20 border border-red-500/30 rounded-lg px-3 py-1.5 text-sm text-red-400 animate-pulse">
              <AlertTriangle className="w-4 h-4" />{alarmSensors.length} sensor alarm{alarmSensors.length > 1 ? 's' : ''}
            </div>
          )}
          {overdueItems.length > 0 && (
            <div className="flex items-center gap-2 bg-orange-500/20 border border-orange-500/30 rounded-lg px-3 py-1.5 text-sm text-orange-400">
              <Clock className="w-4 h-4" />{overdueItems.length} overdue
            </div>
          )}
          {alarmSensors.length === 0 && overdueItems.length === 0 && (
            <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-1.5 text-sm text-green-400">
              <CheckCircle className="w-4 h-4" />All systems nominal
            </div>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 bg-slate-800/30 border border-white/10 rounded-xl p-2">
        <TabBtn active={tab === 'tests'} onClick={() => setTab('tests')} icon={<Wind className="w-4 h-4" />} label="Suppression Tests" />
        <TabBtn active={tab === 'sensors'} onClick={() => setTab('sensors')} icon={<Eye className="w-4 h-4" />} label="Sensor Board" />
        <TabBtn active={tab === 'epa'} onClick={() => setTab('epa')} icon={<ShieldAlert className="w-4 h-4" />} label="EPA Compliance" />
        <TabBtn active={tab === 'egress'} onClick={() => setTab('egress')} icon={<MapPin className="w-4 h-4" />} label="Egress Map" />
        <TabBtn active={tab === 'maintenance'} onClick={() => setTab('maintenance')} icon={<Bell className="w-4 h-4" />} label="Maintenance" />
        <TabBtn active={tab === 'postmortem'} onClick={() => setTab('postmortem')} icon={<FileText className="w-4 h-4" />} label="Incidents" />
        <TabBtn active={tab === '3d'} onClick={() => setTab('3d')} icon={<Box className="w-4 h-4" />} label="3D Facility" />
      </div>

      {/* ══ TAB: SUPPRESSION TESTS ══════════════════════════════════════════ */}
      {tab === 'tests' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Suppression Test Logs</h2>
              <p className="text-sm text-slate-400 mt-0.5">VESDA, FM200, Sprinkler and CO₂ system drills with outcomes</p>
            </div>
            <button onClick={() => setShowForm(true)} className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium transition-all">
              <Plus className="w-4 h-4" />Schedule Test
            </button>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Scheduled', val: tests.filter((t) => t.status === 'scheduled').length, color: 'text-blue-400' },
              { label: 'Passed', val: tests.filter((t) => t.status === 'passed').length, color: 'text-green-400' },
              { label: 'Failed', val: tests.filter((t) => t.status === 'failed').length, color: 'text-red-400' },
              { label: 'Total', val: tests.length, color: 'text-white' },
            ].map(({ label, val, color }) => (
              <Card key={label} className="p-4 text-center">
                <p className={`text-2xl font-bold ${color}`}>{val}</p>
                <p className="text-xs text-slate-400 mt-1">{label}</p>
              </Card>
            ))}
          </div>

          {/* Add form */}
          {showForm && (
            <Card className="p-5 border-red-500/20 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Suppression Test</h3>
                <button onClick={() => setShowForm(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">System Type *</label>
                  <select value={newTest.type} onChange={(e) => setNewTest((p) => ({ ...p, type: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
                    {['VESDA', 'FM200', 'Sprinkler', 'CO2', 'Novec1230'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <FieldInput label="Location / Zone *" value={newTest.location} onChange={(v) => setNewTest((p) => ({ ...p, location: v }))} placeholder="Server Room A" required />
                <FieldInput label="Scheduled Date *" value={newTest.scheduledDate} onChange={(v) => setNewTest((p) => ({ ...p, scheduledDate: v }))} type="datetime-local" required />
                <FieldInput label="Technician" value={newTest.technician} onChange={(v) => setNewTest((p) => ({ ...p, technician: v }))} placeholder="John Smith" />
                <FieldInput label="Certificate No." value={newTest.certificateNo || ''} onChange={(v) => setNewTest((p) => ({ ...p, certificateNo: v }))} placeholder="CERT-2025-001" />
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Status</label>
                  <select value={newTest.status} onChange={(e) => setNewTest((p) => ({ ...p, status: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
                    {['scheduled', 'in-progress', 'passed', 'failed', 'cancelled'].map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div className="sm:col-span-3">
                  <label className="block text-xs text-slate-400 mb-1.5">Notes</label>
                  <textarea value={newTest.notes || ''} onChange={(e) => setNewTest((p) => ({ ...p, notes: e.target.value }))} rows={2}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500 resize-none" />
                </div>
              </div>
              <div className="flex gap-3">
                <button onClick={addTest} className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium">Schedule</button>
                <button onClick={() => setShowForm(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {/* Test list */}
          {tests.length === 0 ? (
            <Card className="p-12 text-center">
              <Wind className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No suppression tests logged</p>
            </Card>
          ) : (
            <div className="space-y-3">
              {[...tests].sort((a, b) => b.scheduledDate.localeCompare(a.scheduledDate)).map((t) => {
                const statusColors: Record<string, string> = { scheduled: 'text-blue-400 bg-blue-500/20 border-blue-500/30', 'in-progress': 'text-yellow-400 bg-yellow-500/20 border-yellow-500/30', passed: 'text-green-400 bg-green-500/20 border-green-500/30', failed: 'text-red-400 bg-red-500/20 border-red-500/30', cancelled: 'text-slate-400 bg-slate-500/20 border-slate-500/30' }
                return (
                  <Card key={t.id} className="p-5 hover:border-white/20 transition-all">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-3 mb-2">
                          <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${statusColors[t.status]}`}>{t.status}</span>
                          <span className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full font-mono">{t.type}</span>
                          <span className="font-semibold text-white">{t.location}</span>
                        </div>
                        <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                          <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(t.scheduledDate).toLocaleString()}</span>
                          {t.technician && <span>{t.technician}</span>}
                          {t.certificateNo && <span>Cert: {t.certificateNo}</span>}
                        </div>
                        {t.outcome && <p className="text-xs text-slate-400 italic mt-1">{t.outcome}</p>}
                        {t.notes && <p className="text-xs text-slate-500 mt-0.5">{t.notes}</p>}
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        {t.status === 'scheduled' && <>
                          <button onClick={() => updateTestStatus(t.id, 'passed', 'Test completed successfully')} className="p-1.5 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-400 transition-all" title="Pass"><CheckCircle className="w-4 h-4" /></button>
                          <button onClick={() => updateTestStatus(t.id, 'failed', 'Test failed — see notes')} className="p-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-all" title="Fail"><AlertTriangle className="w-4 h-4" /></button>
                        </>}
                        <button onClick={() => deleteTest(t.id)} className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"><Trash2 className="w-4 h-4" /></button>
                      </div>
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: SENSOR STATUS BOARD ════════════════════════════════════════ */}
      {tab === 'sensors' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Sensor Status Board</h2>
              <p className="text-sm text-slate-400 mt-0.5">Smoke, heat, VESDA and water-leak sensors mapped to floor layout</p>
            </div>
            <button onClick={resetAllSensors} className="flex items-center gap-2 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm text-slate-300 transition-all">
              <RefreshCw className="w-4 h-4" />Reset All to Normal
            </button>
          </div>

          {/* Sensor summary */}
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-3">
            {(['normal', 'alarm', 'fault', 'offline', 'testing'] as const).map((s) => {
              const count = sensors.filter((x) => x.status === s).length
              return (
                <Card key={s} className="p-3 text-center">
                  <div className="w-3 h-3 rounded-full mx-auto mb-1" style={{ backgroundColor: SENSOR_COLORS[s] }} />
                  <p className="text-lg font-bold text-white">{count}</p>
                  <p className="text-xs text-slate-400 capitalize">{s}</p>
                </Card>
              )
            })}
          </div>

          {/* Floor plan SVG */}
          <Card className="p-5">
            <h3 className="font-semibold text-white mb-3 text-sm">Floor Plan — click a sensor to inspect</h3>
            <div className="relative w-full" style={{ paddingBottom: '56.25%' }}>
              <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
                {/* Rooms */}
                {ZONES.map((z) => (
                  <g key={z.id}>
                    <rect x={z.x + 0.5} y={z.y + 0.5} width={z.w - 1} height={z.h - 1} fill="#1e293b" stroke="#334155" strokeWidth="0.5" rx="1" />
                    <text x={z.x + z.w / 2} y={z.y + z.h / 2} textAnchor="middle" dominantBaseline="middle" fill="#475569" fontSize="4" fontFamily="monospace">{z.label}</text>
                  </g>
                ))}
                {/* Grid lines */}
                <line x1="50" y1="0" x2="50" y2="100" stroke="#334155" strokeWidth="0.3" strokeDasharray="1,1" />
                <line x1="0" y1="50" x2="100" y2="50" stroke="#334155" strokeWidth="0.3" strokeDasharray="1,1" />
                {/* Sensors */}
                {sensors.map((s) => {
                  const isSelected = selectedSensor?.id === s.id
                  return (
                    <g key={s.id} onClick={() => setSelectedSensor(s === selectedSensor ? null : s)} className="cursor-pointer">
                      {isSelected && <circle cx={s.x} cy={s.y} r="4" fill="none" stroke="white" strokeWidth="0.5" strokeDasharray="1,0.5" />}
                      {(s.status === 'alarm' || s.status === 'fault') && <circle cx={s.x} cy={s.y} r="3.5" fill={SENSOR_COLORS[s.status]} opacity="0.3"><animate attributeName="r" from="2.5" to="4.5" dur="1.2s" repeatCount="indefinite" /></circle>}
                      <circle cx={s.x} cy={s.y} r="2.5" fill={SENSOR_COLORS[s.status]} stroke="#0f172a" strokeWidth="0.4" />
                      <title>{s.name} — {s.status}</title>
                    </g>
                  )
                })}
              </svg>
            </div>

            {/* Legend */}
            <div className="flex flex-wrap gap-4 mt-3 text-xs text-slate-400">
              {Object.entries(SENSOR_COLORS).map(([s, c]) => (
                <span key={s} className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: c }} />
                  {s}
                </span>
              ))}
            </div>
          </Card>

          {/* Selected sensor panel */}
          {selectedSensor && (
            <Card className="p-5 border-blue-500/20">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h3 className="font-semibold text-white">{selectedSensor.name}</h3>
                  <p className="text-xs text-slate-500 mt-0.5">Zone: {selectedSensor.zone} · Type: {selectedSensor.type}</p>
                </div>
                <button onClick={() => setSelectedSensor(null)}><X className="w-4 h-4 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="flex flex-wrap gap-3">
                {(['normal', 'alarm', 'fault', 'offline', 'testing'] as const).map((s) => (
                  <button key={s} onClick={() => updateSensor(selectedSensor.id, { status: s })}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${selectedSensor.status === s ? 'border-white/30 bg-white/10 text-white' : 'border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: SENSOR_COLORS[s] }} />
                    {s}
                  </button>
                ))}
              </div>
              {selectedSensor.lastChecked && <p className="text-xs text-slate-500 mt-3">Last updated: {new Date(selectedSensor.lastChecked).toLocaleString()}</p>}
            </Card>
          )}

          {/* Sensor table */}
          <Card className="overflow-hidden">
            <div className="p-5 border-b border-white/10"><h3 className="font-semibold text-white">All Sensors</h3></div>
            <table className="w-full text-sm">
              <thead><tr className="border-b border-white/5">
                {['Status', 'Name', 'Type', 'Zone', 'Last Checked'].map((h) => (
                  <th key={h} className="text-left px-5 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                ))}
              </tr></thead>
              <tbody className="divide-y divide-white/5">
                {sensors.map((s) => (
                  <tr key={s.id} onClick={() => setSelectedSensor(s)} className="hover:bg-white/5 transition-colors cursor-pointer">
                    <td className="px-5 py-3"><span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: SENSOR_COLORS[s.status] }} /></td>
                    <td className="px-5 py-3 font-medium text-white">{s.name}</td>
                    <td className="px-5 py-3"><span className="flex items-center gap-1.5 text-slate-400">{SENSOR_ICONS[s.type]}{s.type}</span></td>
                    <td className="px-5 py-3 text-slate-400">{s.zone}</td>
                    <td className="px-5 py-3 text-slate-500 text-xs">{s.lastChecked ? new Date(s.lastChecked).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}

      {/* ══ TAB: EPA COMPLIANCE ═════════════════════════════════════════════ */}
      {tab === 'epa' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">EPA Compliance Tracker</h2>
              <p className="text-sm text-slate-400 mt-0.5">Refrigerant charge logs and suppression agent inventory</p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setShowLogForm(true)} className="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm text-slate-300 transition-all">
                <Plus className="w-4 h-4" />Log Entry
              </button>
              <button onClick={() => setShowCompForm(true)} className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium transition-all">
                <Plus className="w-4 h-4" />Add System
              </button>
            </div>
          </div>

          {/* Add system form */}
          {showCompForm && (
            <Card className="p-5 border-red-500/20 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Compliance System</h3>
                <button onClick={() => setShowCompForm(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <FieldInput label="System Name *" value={newComp.system} onChange={(v) => setNewComp((p) => ({ ...p, system: v }))} placeholder="Server Room A FM200" required />
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Agent Type</label>
                  <select value={newComp.agentType} onChange={(e) => setNewComp((p) => ({ ...p, agentType: e.target.value }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
                    {['FM200', 'CO2', 'Novec1230', 'R-410A', 'R-134a', 'Argonite', 'INERGEN'].map((a) => <option key={a}>{a}</option>)}
                  </select>
                </div>
                <FieldInput label="Current Charge (kg)" value={newComp.currentKg} onChange={(v) => setNewComp((p) => ({ ...p, currentKg: parseFloat(v) || 0 }))} type="number" />
                <FieldInput label="Rated Capacity (kg)" value={newComp.capacityKg} onChange={(v) => setNewComp((p) => ({ ...p, capacityKg: parseFloat(v) || 0 }))} type="number" />
                <FieldInput label="Last Serviced" value={newComp.lastServiced} onChange={(v) => setNewComp((p) => ({ ...p, lastServiced: v }))} type="date" />
                <FieldInput label="Next Inspection" value={newComp.nextInspection} onChange={(v) => setNewComp((p) => ({ ...p, nextInspection: v }))} type="date" />
                <FieldInput label="Technician" value={newComp.technician || ''} onChange={(v) => setNewComp((p) => ({ ...p, technician: v }))} />
                <FieldInput label="Certificate No." value={newComp.certificate || ''} onChange={(v) => setNewComp((p) => ({ ...p, certificate: v }))} />
              </div>
              <div className="flex gap-3">
                <button onClick={addCompSystem} className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium">Add System</button>
                <button onClick={() => setShowCompForm(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {/* Add charge log form */}
          {showLogForm && (
            <Card className="p-5 border-yellow-500/20 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Charge Log Entry</h3>
                <button onClick={() => setShowLogForm(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">System *</label>
                  <select value={newLog.systemId} onChange={(e) => setNewLog((p) => ({ ...p, systemId: e.target.value }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-yellow-500">
                    <option value="">Select…</option>
                    {compSystems.map((c) => <option key={c.id} value={c.id}>{c.system}</option>)}
                  </select>
                </div>
                <FieldInput label="Date *" value={newLog.date} onChange={(v) => setNewLog((p) => ({ ...p, date: v }))} type="date" required />
                <FieldInput label="Technician" value={newLog.technician} onChange={(v) => setNewLog((p) => ({ ...p, technician: v }))} />
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Action</label>
                  <select value={newLog.action} onChange={(e) => setNewLog((p) => ({ ...p, action: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-yellow-500">
                    <option value="inspect">Inspect</option>
                    <option value="add">Add Agent</option>
                    <option value="remove">Remove Agent</option>
                  </select>
                </div>
                <FieldInput label="Amount (kg)" value={newLog.amountKg} onChange={(v) => setNewLog((p) => ({ ...p, amountKg: parseFloat(v) || 0 }))} type="number" />
                <FieldInput label="Reason" value={newLog.reason} onChange={(v) => setNewLog((p) => ({ ...p, reason: v }))} placeholder="Annual inspection" />
              </div>
              <div className="flex gap-3">
                <button onClick={addChargeLog} className="px-4 py-2 bg-yellow-600 hover:bg-yellow-700 rounded-lg text-sm text-white font-medium">Log Entry</button>
                <button onClick={() => setShowLogForm(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {compSystems.length === 0 ? (
            <Card className="p-12 text-center">
              <ShieldAlert className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No compliance systems registered</p>
            </Card>
          ) : (
            <div className="space-y-4">
              {compSystems.map((c) => {
                const pct = c.capacityKg > 0 ? (c.currentKg / c.capacityKg) * 100 : 0
                const inspDays = c.nextInspection ? daysUntil(c.nextInspection) : null
                const systemLogs = chargeLogs.filter((l) => l.systemId === c.id)
                return (
                  <Card key={c.id} className="p-5 hover:border-white/20 transition-all">
                    <div className="flex items-start justify-between mb-4">
                      <div>
                        <h3 className="font-semibold text-white">{c.system}</h3>
                        <p className="text-xs text-slate-500 mt-0.5">Agent: <span className="text-slate-300 font-mono">{c.agentType}</span>{c.technician ? ` · ${c.technician}` : ''}{c.certificate ? ` · Cert: ${c.certificate}` : ''}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        {inspDays !== null && (
                          <span className={`text-xs px-2 py-0.5 rounded-full border ${dueBadge(inspDays)}`}>
                            {inspDays < 0 ? `${Math.abs(inspDays)}d overdue` : inspDays === 0 ? 'Due today' : `${inspDays}d to inspection`}
                          </span>
                        )}
                        <button onClick={() => deleteComp(c.id)} className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all"><Trash2 className="w-4 h-4" /></button>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-4 mb-4">
                      <div className="text-center">
                        <p className="text-xs text-slate-500 mb-1">Current Charge</p>
                        <p className="text-lg font-bold text-white">{c.currentKg}<span className="text-xs text-slate-400 ml-1">kg</span></p>
                      </div>
                      <div className="text-center">
                        <p className="text-xs text-slate-500 mb-1">Capacity</p>
                        <p className="text-lg font-bold text-white">{c.capacityKg}<span className="text-xs text-slate-400 ml-1">kg</span></p>
                      </div>
                      <div className="text-center">
                        <p className="text-xs text-slate-500 mb-1">Fill Level</p>
                        <p className={`text-lg font-bold ${pct < 80 ? 'text-red-400' : pct < 95 ? 'text-yellow-400' : 'text-green-400'}`}>{pct.toFixed(0)}%</p>
                      </div>
                    </div>
                    <div className="h-2 bg-slate-700 rounded-full overflow-hidden mb-3">
                      <div className={`h-full rounded-full ${pct < 80 ? 'bg-red-500' : pct < 95 ? 'bg-yellow-500' : 'bg-green-500'}`} style={{ width: `${pct}%` }} />
                    </div>
                    {systemLogs.length > 0 && (
                      <div className="mt-3 border-t border-white/5 pt-3">
                        <p className="text-xs text-slate-500 mb-2">Recent logs ({systemLogs.length} total)</p>
                        {systemLogs.slice(-3).reverse().map((l) => (
                          <div key={l.id} className="flex items-center gap-3 text-xs text-slate-400 py-1">
                            <span className="text-slate-600">{l.date}</span>
                            <span className={`capitalize px-1.5 py-0.5 rounded text-xs ${l.action === 'add' ? 'bg-green-500/20 text-green-400' : l.action === 'remove' ? 'bg-red-500/20 text-red-400' : 'bg-slate-500/20 text-slate-400'}`}>{l.action}</span>
                            {l.amountKg > 0 && <span>{l.amountKg} kg</span>}
                            <span className="text-slate-500">{l.reason}</span>
                            {l.technician && <span>— {l.technician}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: EMERGENCY EGRESS MAP ════════════════════════════════════════ */}
      {tab === 'egress' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Emergency Egress Map</h2>
              <p className="text-sm text-slate-400 mt-0.5">Live head count and evacuation route visualisation</p>
            </div>
            <button onClick={() => setDrillMode((d) => !d)} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${drillMode ? 'bg-red-600 text-white animate-pulse' : 'bg-slate-700 hover:bg-slate-600 text-slate-300'}`}>
              <ShieldAlert className="w-4 h-4" />{drillMode ? 'DRILL ACTIVE — Click to End' : 'Start Drill'}
            </button>
          </div>

          {/* Head count summary */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Card className="p-4 text-center col-span-2 sm:col-span-1">
              <p className="text-3xl font-bold text-white">{totalAccounted}<span className="text-lg text-slate-400">/{totalExpected}</span></p>
              <p className="text-xs text-slate-400 mt-1">Total Accounted</p>
            </Card>
            <Card className={`p-4 text-center ${totalExpected > 0 && totalAccounted < totalExpected ? 'border-red-500/30 bg-red-500/5' : ''}`}>
              <p className={`text-3xl font-bold ${totalExpected > 0 && totalAccounted < totalExpected ? 'text-red-400' : 'text-green-400'}`}>{totalExpected - totalAccounted}</p>
              <p className="text-xs text-slate-400 mt-1">Unaccounted</p>
            </Card>
            <Card className="p-4 text-center">
              <p className="text-3xl font-bold text-white">{zoneCounts.filter((z) => z.expected > 0 && z.accounted >= z.expected).length}</p>
              <p className="text-xs text-slate-400 mt-1">Zones Clear</p>
            </Card>
            <Card className="p-4 text-center">
              <p className={`text-lg font-bold ${totalExpected > 0 ? ((totalAccounted / totalExpected) * 100).toFixed(0) + '%' : '—'}`} style={{ color: totalExpected > 0 && totalAccounted >= totalExpected ? '#22c55e' : '#ef4444' }}>
                {totalExpected > 0 ? ((totalAccounted / totalExpected) * 100).toFixed(0) + '%' : '—'}
              </p>
              <p className="text-xs text-slate-400 mt-1">Accountability</p>
            </Card>
          </div>

          {/* Floor plan with egress overlays */}
          <Card className="p-5">
            <h3 className="font-semibold text-white mb-3 text-sm">Evacuation Routes</h3>
            <div className="relative w-full" style={{ paddingBottom: '56.25%' }}>
              <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
                {/* Rooms */}
                {ZONES.map((z) => {
                  const zCount = zoneCounts.find((c) => c.zoneId === z.id)
                  const isClear = zCount && zCount.expected > 0 && zCount.accounted >= zCount.expected
                  return (
                    <g key={z.id}>
                      <rect x={z.x + 0.5} y={z.y + 0.5} width={z.w - 1} height={z.h - 1}
                        fill={isClear ? 'rgba(34,197,94,0.08)' : '#1e293b'} stroke={isClear ? '#22c55e' : '#334155'} strokeWidth="0.5" rx="1" />
                      <text x={z.x + z.w / 2} y={z.y + z.h / 2 - 4} textAnchor="middle" fill="#475569" fontSize="3.5" fontFamily="monospace">{z.label}</text>
                      {zCount && zCount.expected > 0 && (
                        <text x={z.x + z.w / 2} y={z.y + z.h / 2 + 4} textAnchor="middle" fill={isClear ? '#22c55e' : '#f87171'} fontSize="5" fontWeight="bold">{zCount.accounted}/{zCount.expected}</text>
                      )}
                    </g>
                  )
                })}
                {/* Evacuation arrows (Server Room A → EXIT A) */}
                <path d="M 24 24 L 24 3" stroke="#22c55e" strokeWidth="1" fill="none" markerEnd="url(#arrow)" />
                <path d="M 76 24 L 76 3" stroke="#22c55e" strokeWidth="1" fill="none" markerEnd="url(#arrow)" />
                <path d="M 76 76 L 97 76" stroke="#22c55e" strokeWidth="1" fill="none" markerEnd="url(#arrow)" />
                <path d="M 24 76 L 52 76 L 76 76" stroke="#22c55e" strokeWidth="0.8" fill="none" strokeDasharray="2,1" />
                {/* Exit markers */}
                {EXITS.map((e) => (
                  <g key={e.label}>
                    <rect x={e.dir === 'right' ? e.x - 1 : e.x - 5} y={e.dir === 'up' ? e.y - 1 : e.y - 2.5} width={e.dir === 'right' ? 4 : 10} height={e.dir === 'up' ? 4 : 5} fill="#22c55e" opacity="0.8" rx="0.5" />
                    <text x={e.dir === 'right' ? e.x + 1 : e.x} y={e.dir === 'up' ? e.y + 2.5 : e.y + 1.5} textAnchor="middle" fill="white" fontSize="2.5" fontWeight="bold">{e.label}</text>
                  </g>
                ))}
                {/* Assembly point */}
                <circle cx="50" cy="108" r="4" fill="#3b82f6" opacity="0.6" />
                <text x="50" y="108.5" textAnchor="middle" fill="white" fontSize="2.5">MUSTER</text>
                {/* Arrow marker */}
                <defs>
                  <marker id="arrow" markerWidth="4" markerHeight="4" refX="2" refY="2" orient="auto">
                    <path d="M 0 0 L 4 2 L 0 4 Z" fill="#22c55e" />
                  </marker>
                </defs>
              </svg>
            </div>
            <div className="flex gap-4 mt-2 text-xs text-slate-500">
              <span className="flex items-center gap-1.5"><span className="w-3 h-1 bg-green-500 inline-block rounded" />Evacuation route</span>
              <span className="flex items-center gap-1.5"><span className="w-3 h-3 bg-green-500/80 inline-block rounded-sm" />Emergency exit</span>
              <span className="flex items-center gap-1.5"><span className="w-3 h-3 bg-blue-500/60 inline-block rounded-full" />Muster point</span>
            </div>
          </Card>

          {/* Zone head count editor */}
          <Card className="overflow-hidden">
            <div className="p-5 border-b border-white/10"><h3 className="font-semibold text-white">Zone Head Count</h3></div>
            <div className="divide-y divide-white/5">
              {zoneCounts.map((z) => {
                const clear = z.expected > 0 && z.accounted >= z.expected
                return (
                  <div key={z.zoneId} className="flex items-center gap-4 px-5 py-4">
                    <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${clear ? 'bg-green-400' : z.accounted < z.expected && z.expected > 0 ? 'bg-red-400' : 'bg-slate-500'}`} />
                    <span className="flex-1 text-sm font-medium text-white">{z.zoneName}</span>
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-slate-500">Expected</label>
                        <input type="number" min={0} value={z.expected}
                          onChange={(e) => updateZoneCount(z.zoneId, 'expected', parseInt(e.target.value) || 0)}
                          className="w-16 bg-slate-900 border border-white/10 rounded px-2 py-1 text-xs text-white text-center focus:outline-none focus:border-blue-500" />
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-slate-500">Accounted</label>
                        <input type="number" min={0} value={z.accounted}
                          onChange={(e) => updateZoneCount(z.zoneId, 'accounted', parseInt(e.target.value) || 0)}
                          className="w-16 bg-slate-900 border border-white/10 rounded px-2 py-1 text-xs text-white text-center focus:outline-none focus:border-blue-500" />
                      </div>
                    </div>
                    {clear && <CheckCircle className="w-4 h-4 text-green-400 flex-shrink-0" />}
                    {z.expected > 0 && z.accounted < z.expected && <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />}
                  </div>
                )
              })}
            </div>
          </Card>
        </div>
      )}

      {/* ══ TAB: MAINTENANCE DUE ALERTS ══════════════════════════════════════ */}
      {tab === 'maintenance' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Maintenance Due Alerts</h2>
              <p className="text-sm text-slate-400 mt-0.5">Regulatory inspection countdowns with escalation</p>
            </div>
            <button onClick={() => setShowMaintForm(true)} className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium transition-all">
              <Plus className="w-4 h-4" />Add Item
            </button>
          </div>

          {/* Summary */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Overdue', val: overdueItems.length, color: 'text-red-400', bg: 'bg-red-500/5 border-red-500/20' },
              { label: 'Due ≤ 7d', val: maintItems.filter((m) => { const d = daysUntil(m.dueDate); return d >= 0 && d <= 7 }).length, color: 'text-orange-400', bg: '' },
              { label: 'Due ≤ 30d', val: dueSoonItems.length, color: 'text-yellow-400', bg: '' },
              { label: 'Compliant', val: maintItems.filter((m) => daysUntil(m.dueDate) > 30).length, color: 'text-green-400', bg: '' },
            ].map(({ label, val, color, bg }) => (
              <Card key={label} className={`p-4 text-center ${bg}`}>
                <p className={`text-2xl font-bold ${color}`}>{val}</p>
                <p className="text-xs text-slate-400 mt-1">{label}</p>
              </Card>
            ))}
          </div>

          {/* Add form */}
          {showMaintForm && (
            <Card className="p-5 border-yellow-500/20 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Maintenance Item</h3>
                <button onClick={() => setShowMaintForm(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <FieldInput label="Item Name *" value={newMaint.name} onChange={(v) => setNewMaint((p) => ({ ...p, name: v }))} placeholder="VESDA Annual Service" required />
                <FieldInput label="System" value={newMaint.system} onChange={(v) => setNewMaint((p) => ({ ...p, system: v }))} placeholder="VESDA" />
                <FieldInput label="Regulation / Standard" value={newMaint.regulation} onChange={(v) => setNewMaint((p) => ({ ...p, regulation: v }))} placeholder="NFPA 72 · AS 1851" />
                <FieldInput label="Last Done" value={newMaint.lastDone || ''} onChange={(v) => setNewMaint((p) => ({ ...p, lastDone: v }))} type="date" />
                <FieldInput label="Due Date *" value={newMaint.dueDate} onChange={(v) => setNewMaint((p) => ({ ...p, dueDate: v }))} type="date" required />
                <FieldInput label="Interval (days)" value={newMaint.intervalDays} onChange={(v) => setNewMaint((p) => ({ ...p, intervalDays: parseInt(v) || 365 }))} type="number" />
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Priority</label>
                  <select value={newMaint.priority} onChange={(e) => setNewMaint((p) => ({ ...p, priority: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
                    {['critical', 'high', 'medium', 'low'].map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
                <FieldInput label="Assigned To" value={newMaint.assignedTo || ''} onChange={(v) => setNewMaint((p) => ({ ...p, assignedTo: v }))} placeholder="Facilities Team" />
              </div>
              <div className="flex gap-3">
                <button onClick={addMaint} className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium">Add</button>
                <button onClick={() => setShowMaintForm(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {maintItems.length === 0 ? (
            <Card className="p-12 text-center">
              <Bell className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No maintenance items configured</p>
              <p className="text-xs text-slate-500 mt-1">Add inspection schedules to receive countdown alerts</p>
            </Card>
          ) : (
            <div className="space-y-3">
              {sortedMaint.map((m) => {
                const days = daysUntil(m.dueDate)
                return (
                  <Card key={m.id} className={`p-5 hover:border-white/20 transition-all ${days < 0 ? 'border-red-500/30' : days <= 7 ? 'border-orange-500/20' : days <= 30 ? 'border-yellow-500/20' : ''}`}>
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-3 mb-2">
                          <span className={dueBadge(days) + ' text-xs px-2 py-0.5 rounded-full border'}>
                            {days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? 'Due today' : `${days}d remaining`}
                          </span>
                          <span className={priorityBadge(m.priority)}>{m.priority}</span>
                          <span className="font-semibold text-white">{m.name}</span>
                        </div>
                        <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                          {m.system && <span className="text-slate-400">{m.system}</span>}
                          {m.regulation && <span className="text-cyan-600">{m.regulation}</span>}
                          {m.dueDate && <span className="flex items-center gap-1"><Clock className="w-3 h-3" />Due: {m.dueDate}</span>}
                          {m.lastDone && <span>Last: {m.lastDone}</span>}
                          {m.assignedTo && <span>→ {m.assignedTo}</span>}
                        </div>
                        {days <= 30 && days >= 0 && (
                          <div className="mt-2 h-1.5 bg-slate-700 rounded-full overflow-hidden w-48">
                            <div className={`h-full rounded-full ${days <= 7 ? 'bg-red-500' : 'bg-yellow-500'}`} style={{ width: `${Math.max(5, (days / 30) * 100)}%` }} />
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <button onClick={() => markMaintDone(m.id)} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-400 text-xs transition-all" title="Mark done">
                          <CheckCircle className="w-3.5 h-3.5" />Done
                        </button>
                        <button onClick={() => deleteMaint(m.id)} className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all"><Trash2 className="w-4 h-4" /></button>
                      </div>
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: INCIDENT POST-MORTEM ════════════════════════════════════════ */}
      {tab === 'postmortem' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Incident Post-Mortem</h2>
              <p className="text-sm text-slate-400 mt-0.5">Structured reports for all safety-related events</p>
            </div>
            <button onClick={() => setShowIncidentForm(true)} className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium transition-all">
              <Plus className="w-4 h-4" />Log Incident
            </button>
          </div>

          {/* Status summary */}
          <div className="grid grid-cols-3 gap-3">
            {['open', 'investigating', 'closed'].map((s) => {
              const count = incidents.filter((i) => i.status === s).length
              const colors: Record<string, string> = { open: 'text-red-400', investigating: 'text-yellow-400', closed: 'text-green-400' }
              return (
                <Card key={s} className="p-4 text-center">
                  <p className={`text-2xl font-bold ${colors[s]}`}>{count}</p>
                  <p className="text-xs text-slate-400 mt-1 capitalize">{s}</p>
                </Card>
              )
            })}
          </div>

          {/* Add form */}
          {showIncidentForm && (
            <Card className="p-5 border-red-500/20 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white">New Safety Incident</h3>
                <button onClick={() => setShowIncidentForm(false)}><X className="w-5 h-5 text-slate-400 hover:text-white" /></button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="sm:col-span-2">
                  <FieldInput label="Title *" value={newIncident.title} onChange={(v) => setNewIncident((p) => ({ ...p, title: v }))} placeholder="FM200 accidental discharge — Server Room A" required />
                </div>
                <FieldInput label="Date *" value={newIncident.date} onChange={(v) => setNewIncident((p) => ({ ...p, date: v }))} type="datetime-local" required />
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Type</label>
                  <select value={newIncident.type} onChange={(e) => setNewIncident((p) => ({ ...p, type: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
                    {['fire', 'smoke', 'flood', 'chemical', 'power', 'evacuation', 'other'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <FieldInput label="Zone / Location" value={newIncident.zone} onChange={(v) => setNewIncident((p) => ({ ...p, zone: v }))} placeholder="Server Room A" />
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Severity</label>
                  <select value={newIncident.severity} onChange={(e) => setNewIncident((p) => ({ ...p, severity: e.target.value as any }))}
                    className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
                    {['minor', 'moderate', 'major', 'critical'].map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div className="sm:col-span-3">
                  <FieldInput label="Systems Triggered" value={newIncident.systemsTriggered} onChange={(v) => setNewIncident((p) => ({ ...p, systemsTriggered: v }))} placeholder="VESDA, FM200 release, evacuation alarm, access control lockout" />
                </div>
                {[
                  { label: 'Timeline of Events', key: 'timeline', ph: '14:02 VESDA pre-alarm · 14:03 FM200 release · 14:04 evacuation initiated…' },
                  { label: 'Response Actions', key: 'responseActions', ph: 'Evacuation completed in 4 min · Fire brigade notified · Room isolated…' },
                  { label: 'Root Cause', key: 'rootCause', ph: 'Accidental activation during maintenance work…' },
                  { label: 'Corrective Actions', key: 'correctiveActions', ph: 'Procedure updated · Staff retrained · Isolation valve fitted…' },
                ].map(({ label, key, ph }) => (
                  <div key={key} className="sm:col-span-3">
                    <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
                    <textarea value={(newIncident as any)[key] || ''} onChange={(e) => setNewIncident((p) => ({ ...p, [key]: e.target.value }))}
                      placeholder={ph} rows={2}
                      className="w-full bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500 resize-none" />
                  </div>
                ))}
              </div>
              <div className="flex gap-3">
                <button onClick={addIncident} className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-white font-medium">Log Incident</button>
                <button onClick={() => setShowIncidentForm(false)} className="px-4 py-2 bg-slate-700 rounded-lg text-sm text-slate-300">Cancel</button>
              </div>
            </Card>
          )}

          {incidents.length === 0 ? (
            <Card className="p-12 text-center">
              <FileText className="w-12 h-12 text-slate-600 mx-auto mb-3" />
              <p className="text-slate-400">No incidents recorded</p>
              <p className="text-xs text-slate-500 mt-1">Log any safety-related event to generate a structured post-mortem report</p>
            </Card>
          ) : (
            <div className="space-y-3">
              {[...incidents].sort((a, b) => b.date.localeCompare(a.date)).map((inc) => {
                const isExpanded = expandedIncident === inc.id
                return (
                  <Card key={inc.id} className={`overflow-hidden hover:border-white/20 transition-all ${inc.status === 'open' ? 'border-red-500/20' : ''}`}>
                    <div className="p-5">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-3 mb-2">
                            <span className={severityBadge(inc.severity)}>{inc.severity}</span>
                            <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${inc.status === 'open' ? 'bg-red-500/20 text-red-400 border-red-500/30' : inc.status === 'investigating' ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' : 'bg-green-500/20 text-green-400 border-green-500/30'}`}>{inc.status}</span>
                            <span className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded font-mono capitalize">{inc.type}</span>
                            <span className="font-semibold text-white">{inc.title}</span>
                          </div>
                          <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                            <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(inc.date).toLocaleString()}</span>
                            {inc.zone && <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{inc.zone}</span>}
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          {inc.status === 'open' && <button onClick={() => updateIncidentStatus(inc.id, 'investigating')} className="px-2.5 py-1 rounded-lg bg-yellow-500/10 hover:bg-yellow-500/20 text-yellow-400 text-xs transition-all">Investigate</button>}
                          {inc.status === 'investigating' && <button onClick={() => updateIncidentStatus(inc.id, 'closed')} className="px-2.5 py-1 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-400 text-xs transition-all">Close</button>}
                          <button onClick={() => exportPostMortem(inc)} className="p-1.5 rounded-lg bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 transition-all" title="Export report"><Download className="w-4 h-4" /></button>
                          <button onClick={() => setExpandedIncident(isExpanded ? null : inc.id)} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-all">
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                          </button>
                          <button onClick={() => deleteIncident(inc.id)} className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"><Trash2 className="w-4 h-4" /></button>
                        </div>
                      </div>

                      {/* Expanded post-mortem details */}
                      {isExpanded && (
                        <div className="mt-4 border-t border-white/10 pt-4 space-y-3">
                          {[
                            { label: 'Systems Triggered', val: inc.systemsTriggered },
                            { label: 'Timeline', val: inc.timeline },
                            { label: 'Response Actions', val: inc.responseActions },
                            { label: 'Root Cause', val: inc.rootCause },
                            { label: 'Corrective Actions', val: inc.correctiveActions },
                          ].map(({ label, val }) => val ? (
                            <div key={label}>
                              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
                              <p className="text-sm text-slate-300 bg-slate-900/50 rounded-lg p-3">{val}</p>
                            </div>
                          ) : null)}
                          <div className="flex justify-end">
                            <button onClick={() => exportPostMortem(inc)} className="flex items-center gap-2 px-4 py-2 bg-blue-600/20 border border-blue-500/30 text-blue-400 hover:bg-blue-600/30 rounded-lg text-sm transition-all">
                              <Download className="w-4 h-4" />Export Post-Mortem Report
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: 3D FACILITY VIEW ════════════════════════════════════════════ */}
      {tab === '3d' && (
        <div className="space-y-4">
          <div>
            <h2 className="text-xl font-semibold text-white">3D Facility View</h2>
            <p className="text-sm text-slate-400 mt-0.5">
              Building generated live from device telemetry — each environment metric's
              <span className="text-slate-300"> room</span> and <span className="text-slate-300">floor</span> place its
              device inside the matching room. Manual sensors from the Sensor Board are overlaid into their rooms.
            </p>
          </div>

          {/* Facility summary bar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 bg-slate-800/50 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs">
              <Box className="w-3.5 h-3.5 text-blue-400" />
              <span className="text-slate-300">{facility.floors.length} floor{facility.floors.length > 1 ? 's' : ''}</span>
              <span className="text-slate-500">·</span>
              <span className="text-slate-300">{facility.floors.reduce((s, f) => s + f.rooms.length, 0)} rooms</span>
              <span className="text-slate-500">·</span>
              <span className="text-slate-300">{facility.devices.length} devices</span>
            </div>
            {(['alarm', 'fault', 'normal', 'offline'] as const).map((st) => {
              const n = facility.devices.filter((d) => d.status === st).length
              if (n === 0) return null
              const c = st === 'alarm' ? '#ef4444' : st === 'fault' ? '#f59e0b' : st === 'offline' ? '#64748b' : '#22c55e'
              return (
                <div key={st} className="flex items-center gap-1.5 bg-slate-800/50 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: c }} />
                  <span className="text-slate-300">{n}</span>
                  <span className="text-slate-500 capitalize">{st}</span>
                </div>
              )
            })}
            {usingDemo && (
              <div className="flex items-center gap-1.5 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-2.5 py-1.5 text-xs text-yellow-400">
                <AlertTriangle className="w-3.5 h-3.5" />
                Demo facility — no device is reporting room/floor metadata yet
              </div>
            )}
          </div>

          <Suspense fallback={
            <div className="w-full rounded-xl border border-white/10 bg-slate-900 flex items-center justify-center" style={{ height: 600 }}>
              <div className="flex flex-col items-center gap-4">
                <div className="w-12 h-12 border-4 border-red-500/30 border-t-red-500 rounded-full animate-spin" />
                <p className="text-slate-400 text-sm">Loading 3D scene…</p>
              </div>
            </div>
          }>
            <FireSafety3DScene
              facility={facility}
              sensors={sceneSensors}
              onSensorClick={(id) => {
                setSelectedSensor(sensors.find((s) => s.id === id) ?? null)
                setTab('sensors')
              }}
            />
          </Suspense>

          <div className="bg-slate-800/30 border border-white/10 rounded-xl p-4">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Scene Key</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs text-slate-400">
              <span>🟩 Floors &amp; rooms — built from device room/floor metadata</span>
              <span>🟢 Green dot — device temperature normal</span>
              <span>🟠 Amber dot — temperature elevated (≥30°C)</span>
              <span>🔴 Red dot / beacon — temperature alarm (≥35°C)</span>
              <span>⬜ White pipes — VESDA sampling lines</span>
              <span>Ceiling cones — FM-200 suppression nozzles</span>
              <span>Click a device — inspect floor / room / reading</span>
              <span>Floor buttons — isolate a single floor</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
