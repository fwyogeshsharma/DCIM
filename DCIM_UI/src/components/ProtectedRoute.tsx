import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../stores/useAuthStore'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const currentUser = useAuthStore((s) => s.currentUser)
  const token = useAuthStore((s) => s.token)
  // A persisted currentUser without a usable token (stale storage after a
  // redeploy / DB reset) must NOT count as logged in — otherwise every page
  // fires requests with no bearer token and 401s. Require both.
  if (!currentUser || !token) return <Navigate to="/login" replace />
  return <>{children}</>
}