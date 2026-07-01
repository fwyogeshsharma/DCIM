import { useEffect, useState } from 'react'
import { ShieldCheck, Check, X, UserCog } from 'lucide-react'
import toast from 'react-hot-toast'
import { api, type PendingUser, type RoleCatalogEntry } from '@/lib/api'

// Root/Admin screen: approve or reject users awaiting access, optionally
// overriding the role they requested. Until approved, users see their whole
// role in read-only mode (enforced server-side via rbac.yml `pending_access`).
export default function Approvals() {
  const [pending, setPending] = useState<PendingUser[]>([])
  const [catalog, setCatalog] = useState<RoleCatalogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  // Per-user role override selection (defaults to their requested role).
  const [selected, setSelected] = useState<Record<number, string[]>>({})

  async function load() {
    setLoading(true)
    try {
      const [p, c] = await Promise.all([api.getPendingUsers(), api.getRoleCatalog()])
      setPending(p)
      setCatalog(c)
      setSelected(
        Object.fromEntries(p.map((u) => [u.id, u.requested_role ? [u.requested_role] : u.roles])),
      )
    } catch (e: any) {
      toast.error(e?.message || 'Failed to load approvals')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function toggleRole(userId: number, role: string) {
    setSelected((prev) => {
      const cur = prev[userId] ?? []
      return {
        ...prev,
        [userId]: cur.includes(role) ? cur.filter((r) => r !== role) : [...cur, role],
      }
    })
  }

  async function review(user: PendingUser, action: 'approve' | 'reject') {
    setBusyId(user.id)
    try {
      const roles = action === 'approve' ? selected[user.id] : undefined
      await api.reviewUser(user.id, action, roles)
      toast.success(`${user.username} ${action === 'approve' ? 'approved' : 'rejected'}`)
      setPending((prev) => prev.filter((u) => u.id !== user.id))
    } catch (e: any) {
      toast.error(e?.message || `Failed to ${action}`)
    } finally {
      setBusyId(null)
    }
  }

  // Roles that are meaningful to assign (hide root unless already granting it).
  const assignable = catalog.filter((r) => r.key !== 'root')

  return (
    <div className="max-w-5xl">
      <div className="flex items-center gap-3 mb-6">
        <ShieldCheck className="w-6 h-6 text-blue-400" />
        <div>
          <h1 className="text-xl font-bold text-white">Role Approvals</h1>
          <p className="text-sm text-slate-400">
            Approve access requests and assign roles. Pending users have read-only access.
          </p>
        </div>
      </div>

      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : pending.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <UserCog className="w-10 h-10 text-slate-600 mb-3" />
          <p className="text-slate-300 font-medium">No pending requests</p>
          <p className="text-sm text-slate-500">New sign-ups will appear here for approval.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {pending.map((u) => (
            <div
              key={u.id}
              className="bg-slate-900/60 border border-white/10 rounded-xl p-5"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-white font-semibold">{u.username}</p>
                  <p className="text-sm text-slate-400">{u.email}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    Requested: <span className="text-blue-300">{u.requested_role || '—'}</span>
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    disabled={busyId === u.id || (selected[u.id] ?? []).length === 0}
                    onClick={() => review(u, 'approve')}
                    className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-3 py-2 transition-colors"
                  >
                    <Check className="w-4 h-4" /> Approve
                  </button>
                  <button
                    disabled={busyId === u.id}
                    onClick={() => review(u, 'reject')}
                    className="flex items-center gap-1.5 bg-slate-700 hover:bg-red-600 disabled:opacity-50 text-white text-sm font-medium rounded-lg px-3 py-2 transition-colors"
                  >
                    <X className="w-4 h-4" /> Reject
                  </button>
                </div>
              </div>

              <div className="mt-4">
                <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">
                  Roles to grant
                </p>
                <div className="flex flex-wrap gap-2">
                  {assignable.map((r) => {
                    const on = (selected[u.id] ?? []).includes(r.key)
                    return (
                      <button
                        key={r.key}
                        onClick={() => toggleRole(u.id, r.key)}
                        className={
                          'text-xs rounded-full px-3 py-1.5 border transition-colors ' +
                          (on
                            ? 'bg-blue-600 border-blue-500 text-white'
                            : 'bg-slate-800/60 border-white/10 text-slate-300 hover:border-blue-500/50')
                        }
                      >
                        {r.label}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
