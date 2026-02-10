import { Link, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  Server,
  AlertTriangle,
  Network,
  BrainCircuit,
  MessageSquareText,
  Settings,
  ChevronLeft,
  ChevronRight,
  Servers
} from 'lucide-react'
import { useUIStore } from '@/stores/useUIStore'
import { Button } from '@/components/ui/button'

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Agents', href: '/agents', icon: Server },
  { name: 'Alerts', href: '/alerts', icon: AlertTriangle },
  { name: 'Topology', href: '/topology', icon: Network },
  { name: 'AI Analytics', href: '/ai-analytics', icon: BrainCircuit },
  { name: 'NL Query', href: '/nl-query', icon: MessageSquareText },
  { name: 'Servers', href: '/servers', icon: Servers },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export default function Sidebar() {
  const location = useLocation()
  const { sidebarOpen, setSidebarOpen } = useUIStore()

  return (
    <aside
      className={cn(
        'bg-slate-900/50 backdrop-blur-xl border-r border-white/10 transition-all duration-300',
        sidebarOpen ? 'w-64' : 'w-16'
      )}
    >
      <div className="flex h-full flex-col">
        {/* Logo */}
        <div className="flex h-16 items-center justify-between px-4 border-b border-white/10">
          {sidebarOpen && (
            <div className="flex items-center gap-2">
              <Server className="w-6 h-6 text-blue-500" />
              <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">DCIM</h1>
            </div>
          )}
          {!sidebarOpen && (
            <Server className="w-6 h-6 text-blue-500 mx-auto" />
          )}
          {sidebarOpen && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="h-8 w-8 hover:bg-white/10"
              aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
          )}
          {!sidebarOpen && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="h-8 w-8 hover:bg-white/10 hidden"
              aria-label="Expand sidebar"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-3">
          {navigation.map((item) => {
            const isActive = location.pathname === item.href || location.pathname.startsWith('/app' + item.href)
            const Icon = item.icon

            return (
              <Link
                key={item.name}
                to={'/app' + item.href}
                className={cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                  isActive
                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                    : 'text-slate-300 hover:bg-white/5 hover:text-white cursor-pointer'
                )}
                aria-label={item.name}
              >
                <Icon className="h-5 w-5 flex-shrink-0" aria-hidden="true" />
                {sidebarOpen && <span>{item.name}</span>}
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        {sidebarOpen && (
          <div className="border-t border-white/10 p-4">
            <p className="text-xs text-slate-400">
              DCIM Enterprise
            </p>
            <p className="text-xs text-slate-500 mt-1">
              v1.0.0
            </p>
          </div>
        )}
      </div>
    </aside>
  )
}
