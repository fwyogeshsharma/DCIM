import { Outlet } from 'react-router-dom'
import { Lock } from 'lucide-react'
import Sidebar from './Sidebar'
import Header from './Header'
import { useAuthStore } from '@/stores/useAuthStore'

export default function AppLayout() {
  const status = useAuthStore((s) => s.currentUser?.status)

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950">
      <Sidebar />
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header />
        {status === 'pending' && (
          <div className="flex items-center gap-2 bg-amber-500/10 border-b border-amber-500/20 px-6 py-2 text-sm text-amber-300">
            <Lock className="w-4 h-4 flex-shrink-0" />
            <span>
              Your account is awaiting approval — you have <strong>read-only</strong> access
              until an administrator approves your role.
            </span>
          </div>
        )}
        <main className="flex-1 overflow-y-auto bg-slate-950 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
