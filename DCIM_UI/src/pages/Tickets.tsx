import { useState, useMemo } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { format, formatDistanceToNow } from 'date-fns'
import toast from 'react-hot-toast'
import {
  Ticket as TicketIcon, AlertTriangle, Loader2, Filter, Plus,
  MessageSquare, CheckCircle2, Clock, ChevronRight,
} from 'lucide-react'
import type {
  Ticket, TicketActivity, TicketStatus, TicketPriority, TicketCategory, DeduplicatedAlert,
} from '@/lib/types'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

// No auth/user system yet — this stands in as the actor recorded on the audit
// trail and the default roster offered when assigning. Swap for the real user
// list once auth lands.
const CURRENT_USER = 'operator'
const TEAM = ['Aman Yadav', 'NOC Team', 'Facilities', 'Network On-Call', 'Power & Cooling']

const STATUSES: TicketStatus[] = ['open', 'acknowledged', 'in_progress', 'resolved', 'closed']
const PRIORITIES: TicketPriority[] = ['P1', 'P2', 'P3', 'P4']
const CATEGORIES: TicketCategory[] = ['power', 'cooling', 'network', 'compliance', 'security', 'other']

const statusStyle: Record<TicketStatus, string> = {
  open:          'bg-red-500/20 text-red-400 border-red-500/30',
  acknowledged:  'bg-amber-500/20 text-amber-400 border-amber-500/30',
  in_progress:   'bg-blue-500/20 text-blue-400 border-blue-500/30',
  resolved:      'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  closed:        'bg-slate-500/20 text-slate-400 border-slate-500/30',
}
const priorityStyle: Record<TicketPriority, string> = {
  P1: 'bg-red-500/20 text-red-400 border-red-500/30',
  P2: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  P3: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  P4: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
}

const fmt = (s: string | null) => {
  if (!s) return '—'
  try { return format(new Date(s), 'MMM dd, HH:mm') } catch { return '—' }
}
const ago = (s: string | null) => {
  if (!s) return ''
  try { return formatDistanceToNow(new Date(s), { addSuffix: true }) } catch { return '' }
}

export default function Tickets() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['tickets'] })
    qc.invalidateQueries({ queryKey: ['ticket-stats'] })
  }

  const [statusFilter, setStatusFilter] = useState<string>('open')
  const [priorityFilter, setPriorityFilter] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [genAlert, setGenAlert] = useState<DeduplicatedAlert | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  const { data: stats } = useQuery({
    queryKey: ['ticket-stats'],
    queryFn: () => api.getTicketStats(),
    refetchInterval: 10000,
  })

  const { data: ticketsResp, isLoading } = useQuery({
    queryKey: ['tickets', statusFilter, priorityFilter, search],
    queryFn: () => api.getTickets({
      status: statusFilter === 'all' ? undefined : statusFilter,
      priority: priorityFilter === 'all' ? undefined : priorityFilter,
      q: search || undefined,
      limit: 200,
    }),
    refetchInterval: 10000,
  })
  const tickets = ticketsResp?.data ?? []

  // Critical alerts that don't yet have an open ticket → candidates for generation.
  const { data: critResp } = useQuery({
    queryKey: ['critical-alerts'],
    queryFn: () => api.getLatestAlerts({ severity: 'critical', limit: 100 }),
    refetchInterval: 10000,
  })
  const { data: openTicketsResp } = useQuery({
    queryKey: ['tickets', 'open-eventids'],
    queryFn: () => api.getTickets({ status: 'open', limit: 500 }),
    refetchInterval: 10000,
  })
  const ticketedEventIds = useMemo(
    () => new Set((openTicketsResp?.data ?? []).map(t => t.event_id).filter(Boolean) as string[]),
    [openTicketsResp]
  )
  const candidateAlerts = (critResp?.data ?? []).filter(a => !ticketedEventIds.has(String(a.id)))

  const assignees = useMemo(() => {
    const fromTickets = tickets.map(t => t.assigned_to).filter(Boolean) as string[]
    return [...new Set([...TEAM, ...fromTickets])].sort()
  }, [tickets])

  // ── Mutations ──────────────────────────────────────────────────────────────
  const assignMut = useMutation({
    mutationFn: ({ id, who }: { id: string; who: string | null }) =>
      api.assignTicket(id, who, CURRENT_USER),
    onSuccess: (_d, v) => { toast.success(v.who ? `Assigned to ${v.who}` : 'Unassigned'); invalidate() },
    onError: (e: any) => toast.error(e.message || 'Assign failed'),
  })
  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: TicketStatus }) =>
      api.updateTicket(id, { status, actor: CURRENT_USER }),
    onSuccess: () => { toast.success('Status updated'); invalidate() },
    onError: (e: any) => toast.error(e.message || 'Update failed'),
  })
  const priorityMut = useMutation({
    mutationFn: ({ id, priority }: { id: string; priority: TicketPriority }) =>
      api.updateTicket(id, { priority, actor: CURRENT_USER }),
    onSuccess: () => { toast.success('Priority updated'); invalidate() },
    onError: (e: any) => toast.error(e.message || 'Update failed'),
  })

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-4xl font-bold text-white flex items-center gap-3">
            <TicketIcon className="w-9 h-9 text-blue-400" /> Critical Tickets
          </h1>
          <p className="text-slate-400 mt-2 text-lg">
            Generate tickets from critical alerts and assign them to your team
          </p>
        </div>
        <Button onClick={() => setShowCreate(true)} className="shrink-0 bg-white text-slate-900 hover:bg-slate-100">
          <Plus className="w-4 h-4" /> New Ticket
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Open" value={stats?.open} accent="text-red-400" />
        <StatCard label="Unassigned" value={stats?.unassigned} accent="text-amber-400" />
        <StatCard label="P1 Open" value={stats?.p1_open} accent="text-orange-400" />
        <StatCard label="Resolved" value={stats?.resolved} accent="text-emerald-400" />
      </div>

      {/* Critical alerts awaiting tickets */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg">
        <div className="flex items-center gap-2 p-4 border-b border-white/10">
          <AlertTriangle className="w-5 h-5 text-red-400" />
          <h2 className="text-lg font-semibold text-white">Critical Alerts Awaiting Tickets</h2>
          <span className="ml-auto text-xs text-slate-500">{candidateAlerts.length} without an open ticket</span>
        </div>
        {candidateAlerts.length === 0 ? (
          <div className="p-6 text-center text-slate-400 text-sm">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 mx-auto mb-2" />
            Every critical alert already has an open ticket.
          </div>
        ) : (
          <div className="divide-y divide-white/5 max-h-72 overflow-y-auto">
            {candidateAlerts.map(a => (
              <div key={a.id} className="flex items-center gap-4 p-4 hover:bg-white/5">
                <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-red-500/20 text-red-400 border border-red-500/30 shrink-0">
                  CRITICAL
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-white truncate">{a.message}</p>
                  <p className="text-xs text-slate-500">
                    {a.server_name || a.agent_id} · {a.metric_type} · {ago(a.timestamp)}
                  </p>
                </div>
                <Button size="sm" onClick={() => setGenAlert(a)} className="shrink-0 bg-white text-slate-900 hover:bg-slate-100">
                  <Plus className="w-4 h-4" /> Generate Ticket
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400" />
          <span className="text-sm text-slate-400">Filter:</span>
        </div>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
          <option value="open">Open (active)</option>
          <option value="all">All statuses</option>
          {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </select>
        <select value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
          <option value="all">All priorities</option>
          {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search title / number…"
          className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white placeholder:text-slate-500 flex-1 min-w-[200px]" />
        <span className="text-xs text-slate-500 ml-auto">{tickets.length} tickets</span>
      </div>

      {/* Tickets table */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl shadow-lg overflow-x-auto">
        <table className="w-full min-w-[900px]">
          <thead className="bg-slate-900/50">
            <tr className="text-left text-slate-300">
              <th className="p-4 font-medium">Ticket</th>
              <th className="p-4 font-medium">Priority</th>
              <th className="p-4 font-medium">Status</th>
              <th className="p-4 font-medium">Title</th>
              <th className="p-4 font-medium">Assignee</th>
              <th className="p-4 font-medium">Created</th>
              <th className="p-4 font-medium"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {isLoading ? (
              <tr><td colSpan={7} className="p-8 text-center text-slate-400">
                <Loader2 className="w-5 h-5 animate-spin inline mr-2" /> Loading tickets…
              </td></tr>
            ) : tickets.length === 0 ? (
              <tr><td colSpan={7} className="p-10 text-center text-slate-400">No tickets match these filters.</td></tr>
            ) : tickets.map(t => (
              <tr key={t.id} className="hover:bg-white/5 group">
                <td className="p-4 font-mono text-sm text-slate-300 whitespace-nowrap">{t.ticket_number}</td>
                <td className="p-4">
                  <select value={t.priority}
                    onChange={e => priorityMut.mutate({ id: t.id, priority: e.target.value as TicketPriority })}
                    className={`px-2 py-1 rounded-full text-xs font-medium border bg-transparent ${priorityStyle[t.priority]}`}>
                    {PRIORITIES.map(p => <option key={p} value={p} className="bg-slate-800 text-white">{p}</option>)}
                  </select>
                </td>
                <td className="p-4">
                  <select value={t.status}
                    onChange={e => statusMut.mutate({ id: t.id, status: e.target.value as TicketStatus })}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium border bg-transparent capitalize ${statusStyle[t.status]}`}>
                    {STATUSES.map(s => <option key={s} value={s} className="bg-slate-800 text-white">{s.replace('_', ' ')}</option>)}
                  </select>
                </td>
                <td className="p-4 max-w-xs">
                  <p className="text-white truncate">{t.title}</p>
                  <p className="text-xs text-slate-500 truncate">{t.source_hostname || '—'} · {t.category}</p>
                </td>
                <td className="p-4">
                  <select value={t.assigned_to ?? ''}
                    onChange={e => assignMut.mutate({ id: t.id, who: e.target.value || null })}
                    className="px-2 py-1.5 text-sm rounded-lg bg-slate-800 border border-white/10 text-white max-w-[160px]">
                    <option value="">Unassigned</option>
                    {assignees.map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </td>
                <td className="p-4 text-sm text-slate-400 whitespace-nowrap">{fmt(t.created_at)}</td>
                <td className="p-4">
                  <button onClick={() => setDetailId(t.id)}
                    className="text-slate-400 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity">
                    <ChevronRight className="w-5 h-5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateDialog
          assignees={assignees}
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); invalidate() }}
        />
      )}
      {genAlert && (
        <GenerateDialog
          alert={genAlert}
          assignees={assignees}
          onClose={() => setGenAlert(null)}
          onCreated={() => { setGenAlert(null); invalidate(); qc.invalidateQueries({ queryKey: ['critical-alerts'] }) }}
        />
      )}
      {detailId && (
        <TicketDetail
          id={detailId}
          assignees={assignees}
          onClose={() => setDetailId(null)}
          onChanged={invalidate}
        />
      )}
    </div>
  )
}

function StatCard({ label, value, accent }: { label: string; value?: number; accent: string }) {
  return (
    <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className={`text-3xl font-bold mt-1 ${accent}`}>{value ?? '—'}</p>
    </div>
  )
}

// ── Manual create dialog ───────────────────────────────────────────────────
function CreateDialog({ assignees, onClose, onCreated }: {
  assignees: string[]; onClose: () => void; onCreated: () => void
}) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<TicketPriority>('P3')
  const [category, setCategory] = useState<TicketCategory>('other')
  const [sourceHostname, setSourceHostname] = useState('')
  const [assignedTo, setAssignedTo] = useState('')

  const mut = useMutation({
    mutationFn: () => api.createTicket({
      title: title.trim(),
      description: description.trim() || undefined,
      priority, category,
      source_hostname: sourceHostname.trim() || undefined,
      assigned_to: assignedTo || undefined,
      actor: CURRENT_USER,
    }),
    onSuccess: (t) => { toast.success(`Ticket ${t.ticket_number} created`); onCreated() },
    onError: (e: any) => toast.error(e.message || 'Failed to create ticket'),
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-slate-900 border-white/10 text-white">
        <DialogHeader><DialogTitle>New Ticket</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <Field label="Title">
            <input value={title} onChange={e => setTitle(e.target.value)} autoFocus
              placeholder="Short summary of the issue"
              className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white placeholder:text-slate-500" />
          </Field>
          <Field label="Description (optional)">
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3}
              placeholder="Details, impact, steps already taken…"
              className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white placeholder:text-slate-500 resize-y" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Priority">
              <select value={priority} onChange={e => setPriority(e.target.value as TicketPriority)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
                {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </Field>
            <Field label="Category">
              <select value={category} onChange={e => setCategory(e.target.value as TicketCategory)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white capitalize">
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Affected host / device (optional)">
            <input value={sourceHostname} onChange={e => setSourceHostname(e.target.value)}
              placeholder="e.g. core-sw-01"
              className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white placeholder:text-slate-500" />
          </Field>
          <Field label="Assign to (optional)">
            <select value={assignedTo} onChange={e => setAssignedTo(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
              <option value="">Leave unassigned</option>
              {assignees.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => mut.mutate()} disabled={mut.isPending || !title.trim()}>
            {mut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create Ticket
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Generate-from-alert dialog ─────────────────────────────────────────────
function GenerateDialog({ alert, assignees, onClose, onCreated }: {
  alert: DeduplicatedAlert; assignees: string[]; onClose: () => void; onCreated: () => void
}) {
  const [priority, setPriority] = useState<TicketPriority>('P1')
  const [category, setCategory] = useState<TicketCategory>('other')
  const [assignedTo, setAssignedTo] = useState('')
  const [title, setTitle] = useState(alert.message || '')

  const mut = useMutation({
    mutationFn: () => api.createTicketFromAlert({
      event_id: String(alert.id),
      priority, category,
      assigned_to: assignedTo || undefined,
      title: title || undefined,
      actor: CURRENT_USER,
    }),
    onSuccess: (t) => { toast.success(`Ticket ${t.ticket_number} created`); onCreated() },
    onError: (e: any) => toast.error(e.message || 'Failed to create ticket'),
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-slate-900 border-white/10 text-white">
        <DialogHeader><DialogTitle>Generate Ticket from Alert</DialogTitle></DialogHeader>
        <div className="rounded-lg bg-slate-800/60 border border-white/10 p-3 text-sm">
          <p className="text-white">{alert.message}</p>
          <p className="text-xs text-slate-500 mt-1">{alert.server_name || alert.agent_id} · {alert.metric_type}</p>
        </div>
        <div className="space-y-3">
          <Field label="Title">
            <input value={title} onChange={e => setTitle(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Priority">
              <select value={priority} onChange={e => setPriority(e.target.value as TicketPriority)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
                {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </Field>
            <Field label="Category">
              <select value={category} onChange={e => setCategory(e.target.value as TicketCategory)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white capitalize">
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Assign to (optional)">
            <select value={assignedTo} onChange={e => setAssignedTo(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
              <option value="">Leave unassigned</option>
              {assignees.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => mut.mutate()} disabled={mut.isPending}>
            {mut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create Ticket
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Ticket detail drawer ────────────────────────────────────────────────────
function TicketDetail({ id, assignees, onClose, onChanged }: {
  id: string; assignees: string[]; onClose: () => void; onChanged: () => void
}) {
  const qc = useQueryClient()
  const { data: ticket, isLoading } = useQuery({
    queryKey: ['ticket', id],
    queryFn: () => api.getTicket(id),
    refetchInterval: 8000,
  })
  const [comment, setComment] = useState('')
  const [resolution, setResolution] = useState('')

  const refresh = () => { qc.invalidateQueries({ queryKey: ['ticket', id] }); onChanged() }

  const patch = (data: any) => api.updateTicket(id, { ...data, actor: CURRENT_USER }).then(() => refresh())
  const commentMut = useMutation({
    mutationFn: () => api.addTicketComment(id, comment, CURRENT_USER),
    onSuccess: () => { setComment(''); refresh() },
    onError: (e: any) => toast.error(e.message || 'Failed to comment'),
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-slate-900 border-white/10 text-white max-w-2xl max-h-[85vh] overflow-y-auto">
        {isLoading || !ticket ? (
          <div className="p-8 text-center text-slate-400"><Loader2 className="w-5 h-5 animate-spin inline" /></div>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-3">
                <span className="font-mono text-blue-400">{ticket.ticket_number}</span>
                <span className={`px-2 py-0.5 rounded-full text-xs border ${priorityStyle[ticket.priority]}`}>{ticket.priority}</span>
              </DialogTitle>
            </DialogHeader>

            <p className="text-lg font-semibold text-white">{ticket.title}</p>
            {ticket.description && <p className="text-sm text-slate-400">{ticket.description}</p>}
            <p className="text-xs text-slate-500">
              {ticket.source_hostname || '—'} · {ticket.category} · opened {ago(ticket.created_at)}
            </p>

            {/* Controls */}
            <div className="grid grid-cols-2 gap-3 pt-2">
              <Field label="Status">
                <select value={ticket.status} onChange={e => patch({ status: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white capitalize">
                  {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                </select>
              </Field>
              <Field label="Assignee">
                <select value={ticket.assigned_to ?? ''} onChange={e => patch({ assigned_to: e.target.value || null })}
                  className="w-full px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white">
                  <option value="">Unassigned</option>
                  {assignees.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
              </Field>
            </div>

            {/* Resolution (when resolving) */}
            <Field label="Resolution / root cause">
              <div className="flex gap-2">
                <input value={resolution} onChange={e => setResolution(e.target.value)}
                  placeholder={ticket.resolution ?? 'How was it resolved?'}
                  className="flex-1 px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white" />
                <Button variant="outline" size="sm" disabled={!resolution}
                  onClick={() => patch({ resolution, status: 'resolved' })}>
                  <CheckCircle2 className="w-4 h-4" /> Resolve
                </Button>
              </div>
            </Field>

            {/* Activity */}
            <div className="pt-2">
              <p className="text-sm font-medium text-slate-300 mb-2 flex items-center gap-2">
                <Clock className="w-4 h-4" /> Activity
              </p>
              <div className="space-y-2 max-h-52 overflow-y-auto pr-1">
                {(ticket.activity ?? []).map((act: TicketActivity) => (
                  <div key={act.id} className="text-sm flex gap-2">
                    <span className="text-slate-500 whitespace-nowrap">{fmt(act.created_at)}</span>
                    <span className="text-slate-300">
                      <span className="text-slate-500">{act.actor || 'system'}</span>{' '}
                      {act.activity_type === 'status_change'
                        ? <>changed status {act.from_status} → <span className="text-white">{act.to_status}</span></>
                        : act.activity_type === 'comment'
                        ? <>commented: <span className="text-white">{act.message}</span></>
                        : act.message}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Comment box */}
            <div className="flex gap-2 pt-2">
              <input value={comment} onChange={e => setComment(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && comment) commentMut.mutate() }}
                placeholder="Add a comment…"
                className="flex-1 px-3 py-2 text-sm rounded-lg bg-slate-800 border border-white/10 text-white" />
              <Button size="sm" disabled={!comment || commentMut.isPending} onClick={() => commentMut.mutate()}>
                <MessageSquare className="w-4 h-4" /> Comment
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs text-slate-400">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}
