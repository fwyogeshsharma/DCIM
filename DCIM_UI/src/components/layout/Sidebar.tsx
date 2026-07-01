import { Link, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  Server,
  AlertTriangle,
  Network,
  // BrainCircuit,      // AI Analytics — hidden from sidebar
  // MessageSquareText, // NL Query — hidden from sidebar
  Settings,
  ChevronLeft,
  ChevronRight,
  ServerCog,
  Zap as ZapIcon,
  Radio,
  Flame,
  Ticket,
  Package,
  ShieldCheck
} from 'lucide-react'
import { useUIStore } from '@/stores/useUIStore'
import { useAuthStore } from '@/stores/useAuthStore'
import { Button } from '@/components/ui/button'

// `feature` maps each item to a key in rbac.yml; items the user has no access to
// are hidden. `approverOnly` items (Approvals) show only for root/admin.
const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard, feature: 'dashboard' },
  { name: 'Devices', href: '/agents', icon: Server, feature: 'devices' },
  { name: 'Alerts', href: '/alerts', icon: AlertTriangle, feature: 'alerts' },
  { name: 'Tickets', href: '/tickets', icon: Ticket, feature: 'tickets' },
  { name: 'Power Mgmt', href: '/reports', icon: ZapIcon, feature: 'reports' },
  { name: 'Network Ops', href: '/network-ops', icon: Radio, feature: 'network_ops' },
  { name: 'Fire & Safety', href: '/fire-safety', icon: Flame, feature: 'fire_safety' },
  { name: 'Inventory', href: '/inventory', icon: Package, feature: 'inventory' },
  { name: 'Topology', href: '/topology', icon: Network, feature: 'topology' },
  // { name: 'AI Analytics', href: '/ai-analytics', icon: BrainCircuit, feature: 'ai_ml' },
  // { name: 'NL Query', href: '/nl-query', icon: MessageSquareText, feature: 'ai_ml' },
  { name: 'Servers', href: '/servers', icon: ServerCog, feature: 'servers' },
  { name: 'Approvals', href: '/approvals', icon: ShieldCheck, feature: 'user_admin', approverOnly: true },
  { name: 'Settings', href: '/settings', icon: Settings, feature: 'settings' },
]

export default function Sidebar() {
  const location = useLocation()
  const { sidebarOpen, setSidebarOpen } = useUIStore()
  const can = useAuthStore((s) => s.can)
  const isApprover = useAuthStore((s) => s.isApprover())

  const visibleNav = navigation.filter((item) =>
    item.approverOnly ? isApprover : can(item.feature, 'read'),
  )

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
            <Link
              to="/"
              className="flex items-center gap-2 cursor-pointer hover:opacity-80 transition-opacity"
              aria-label="Go to home"
            >
              <Server className="w-6 h-6 text-blue-500" />
              <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">FWDCIM</h1>
            </Link>
          )}
          {!sidebarOpen && (
            <button
              onClick={() => setSidebarOpen(true)}
              className="w-full flex justify-center cursor-pointer hover:opacity-80 transition-opacity"
              aria-label="Expand sidebar"
            >
              <Server className="w-6 h-6 text-blue-500" />
            </button>
          )}
          {sidebarOpen && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="h-8 w-8 hover:bg-white/10"
              aria-label="Collapse sidebar"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-3">
          {visibleNav.map((item) => {
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
              FWDCIM Enterprise
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
