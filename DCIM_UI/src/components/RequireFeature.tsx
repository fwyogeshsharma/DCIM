import { Navigate } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { useAuthStore } from '../stores/useAuthStore'
import type { AccessLevel } from '@/lib/api'

// Gates a route by RBAC feature. Not logged in → /login. Logged in but lacking
// the required level → an in-place "no access" panel (redirecting could loop for
// roles that don't include the dashboard).
export default function RequireFeature({
  feature,
  level = 'read',
  children,
}: {
  feature: string
  level?: AccessLevel
  children: React.ReactNode
}) {
  const currentUser = useAuthStore((s) => s.currentUser)
  const can = useAuthStore((s) => s.can)

  if (!currentUser) return <Navigate to="/login" replace />

  if (!can(feature, level)) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center py-24">
        <ShieldAlert className="w-12 h-12 text-amber-400 mb-4" />
        <h2 className="text-lg font-semibold text-white">No access to this area</h2>
        <p className="text-sm text-slate-400 mt-1 max-w-sm">
          Your role doesn’t grant access to <span className="text-slate-200">{feature}</span>.
          {currentUser.status === 'pending'
            ? ' Your account is still awaiting approval.'
            : ' Contact an administrator if you need it.'}
        </p>
      </div>
    )
  }

  return <>{children}</>
}
