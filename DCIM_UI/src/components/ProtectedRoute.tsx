import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../stores/useAuthStore'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const currentUser = useAuthStore((s) => s.currentUser)
  if (!currentUser) return <Navigate to="/login" replace />
  return <>{children}</>
}