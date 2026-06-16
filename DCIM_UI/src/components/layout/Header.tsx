import { useState, useRef, useEffect } from 'react'
import { Bell, LogOut, ChevronDown, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAlerts } from '@/hooks/useAlerts'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/useAuthStore'
import { useNavigate } from 'react-router-dom'
import { formatDistanceToNow } from 'date-fns'

const SEVERITY_DOT: Record<string, string> = {
  CRITICAL: 'bg-red-500',
  WARNING: 'bg-yellow-500',
  INFO: 'bg-blue-500',
}

export default function Header() {
  const { data: alerts } = useAlerts({ resolved: false })
  const currentUser = useAuthStore((s) => s.currentUser)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const [notifOpen, setNotifOpen] = useState(false)
  const [userOpen, setUserOpen] = useState(false)
  const notifRef = useRef<HTMLDivElement>(null)
  const userRef = useRef<HTMLDivElement>(null)

  // Close either dropdown when clicking outside of it.
  useEffect(() => {
    function onPointerDown(e: MouseEvent) {
      const target = e.target as Node
      if (notifRef.current && !notifRef.current.contains(target)) setNotifOpen(false)
      if (userRef.current && !userRef.current.contains(target)) setUserOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [])

  const unresolvedAlerts = (alerts?.filter((a) => !a.resolved) || [])
    .slice()
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
  const criticalAlerts = unresolvedAlerts.filter((a) => a.severity === 'CRITICAL')
  const recentAlerts = unresolvedAlerts.slice(0, 6)

  function handleLogout() {
    setUserOpen(false)
    logout()
    navigate('/login', { replace: true })
  }

  function goToAlerts() {
    setNotifOpen(false)
    navigate('/app/alerts')
  }

  function relativeTime(ts: string) {
    try {
      return formatDistanceToNow(new Date(ts), { addSuffix: true })
    } catch {
      return ''
    }
  }

  const initials = currentUser?.username?.slice(0, 1).toUpperCase() ?? 'U'

  return (
    <header className="h-16 border-b border-white/10 bg-slate-900/50 backdrop-blur-xl">
      <div className="flex h-full items-center justify-between px-6">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-semibold text-white">Data Center Infrastructure Management</h2>
        </div>

        <div className="flex items-center gap-3">
          {/* Notifications */}
          <div className="relative" ref={notifRef}>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => { setNotifOpen((o) => !o); setUserOpen(false) }}
              className="relative hover:bg-white/10 text-slate-300 hover:text-white"
              aria-label={`${unresolvedAlerts.length} unresolved alerts`}
            >
              <Bell className="h-5 w-5" />
              {unresolvedAlerts.length > 0 && (
                <span
                  className={cn(
                    'absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold text-white',
                    criticalAlerts.length > 0 ? 'bg-red-500' : 'bg-yellow-500'
                  )}
                >
                  {unresolvedAlerts.length > 9 ? '9+' : unresolvedAlerts.length}
                </span>
              )}
            </Button>

            {notifOpen && (
              <div className="absolute right-0 mt-2 w-80 rounded-xl border border-white/10 bg-slate-900 shadow-2xl shadow-black/50 z-50 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
                  <span className="text-sm font-semibold text-white">Notifications</span>
                  {unresolvedAlerts.length > 0 && (
                    <span className="text-xs text-slate-400">
                      {unresolvedAlerts.length} unresolved
                      {criticalAlerts.length > 0 && (
                        <span className="ml-1 text-red-400">· {criticalAlerts.length} critical</span>
                      )}
                    </span>
                  )}
                </div>

                <div className="max-h-80 overflow-y-auto">
                  {recentAlerts.length === 0 ? (
                    <div className="flex flex-col items-center justify-center gap-2 py-8 text-slate-500">
                      <CheckCircle2 className="h-7 w-7 text-green-500/70" />
                      <p className="text-sm">No unresolved alerts</p>
                    </div>
                  ) : (
                    recentAlerts.map((alert) => (
                      <button
                        key={alert.id}
                        onClick={goToAlerts}
                        className="w-full text-left px-4 py-3 border-b border-white/5 hover:bg-white/5 transition-colors flex gap-3"
                      >
                        <span className={cn('mt-1.5 h-2 w-2 rounded-full shrink-0', SEVERITY_DOT[alert.severity] ?? 'bg-slate-500')} />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-white truncate">{alert.message || alert.metric_type}</p>
                          <p className="text-xs text-slate-500 truncate">
                            {alert.server_name || alert.agent_id}
                          </p>
                          <p className="text-[11px] text-slate-600 mt-0.5">{relativeTime(alert.timestamp)}</p>
                        </div>
                      </button>
                    ))
                  )}
                </div>

                <button
                  onClick={goToAlerts}
                  className="w-full px-4 py-2.5 text-sm text-blue-400 hover:text-blue-300 hover:bg-white/5 transition-colors flex items-center justify-center gap-1.5 border-t border-white/10"
                >
                  <AlertTriangle className="h-3.5 w-3.5" />
                  View all alerts
                </button>
              </div>
            )}
          </div>

          {/* User menu */}
          <div className="relative ml-2" ref={userRef}>
            <button
              onClick={() => { setUserOpen((o) => !o); setNotifOpen(false) }}
              className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-white/10 transition-colors"
              aria-label="User menu"
            >
              <div className="h-9 w-9 rounded-full bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center shadow-lg">
                <span className="text-sm font-bold text-white">{initials}</span>
              </div>
              <span className="text-sm font-medium text-slate-200 hidden md:block">
                {currentUser?.username ?? 'User'}
              </span>
              <ChevronDown className={cn('h-4 w-4 text-slate-400 transition-transform', userOpen && 'rotate-180')} />
            </button>

            {userOpen && (
              <div className="absolute right-0 mt-2 w-56 rounded-xl border border-white/10 bg-slate-900 shadow-2xl shadow-black/50 z-50 overflow-hidden">
                <div className="px-4 py-3 border-b border-white/10">
                  <p className="text-sm font-semibold text-white truncate">{currentUser?.username ?? 'User'}</p>
                  {currentUser?.email && (
                    <p className="text-xs text-slate-400 truncate">{currentUser.email}</p>
                  )}
                </div>
                <button
                  onClick={handleLogout}
                  className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-slate-300 hover:bg-white/5 hover:text-white transition-colors"
                >
                  <LogOut className="h-4 w-4" />
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
