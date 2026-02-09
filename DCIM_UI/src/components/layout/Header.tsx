import { Bell } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAlerts } from '@/hooks/useAlerts'
import { cn } from '@/lib/utils'

export default function Header() {
  const { data: alerts } = useAlerts({ resolved: false })

  const unresolvedAlerts = alerts?.filter((a) => !a.resolved) || []
  const criticalAlerts = unresolvedAlerts.filter((a) => a.severity === 'CRITICAL')

  return (
    <header className="h-16 border-b border-white/10 bg-slate-900/50 backdrop-blur-xl">
      <div className="flex h-full items-center justify-between px-6">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-semibold text-white">Data Center Infrastructure Management</h2>
        </div>

        <div className="flex items-center gap-3">
          {/* Alerts Badge */}
          <Button
            variant="ghost"
            size="icon"
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

          {/* User Profile Placeholder */}
          <div className="flex items-center gap-2 ml-2">
            <div className="h-9 w-9 rounded-full bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center shadow-lg">
              <span className="text-sm font-bold text-white">A</span>
            </div>
            <span className="text-sm font-medium text-slate-200 hidden md:block">Admin</span>
          </div>
        </div>
      </div>
    </header>
  )
}
