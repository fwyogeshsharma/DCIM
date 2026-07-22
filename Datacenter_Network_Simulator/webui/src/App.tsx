import { useEffect, useState } from 'react'
import MainPage from './pages/MainPage'
import LiveMetricsPage from './pages/LiveMetricsPage'
import FloorPlanPage from './pages/FloorPlanPage'
import { useStore } from './store/useStore'
import { api, getToken, AUTH_EXPIRED_EVENT } from './api/client'
import LoginScreen from './components/LoginScreen'

export default function App() {
  const { startPolling, connectSSE, activeView } = useStore()
  // 'checking' until we know whether the stored token is still valid.
  const [auth, setAuth] = useState<'checking' | 'in' | 'out'>(
    getToken() ? 'checking' : 'out',
  )

  // Validate a stored token once on load.
  useEffect(() => {
    if (auth !== 'checking') return
    api.checkAuth()
      .then(() => setAuth('in'))
      .catch(() => setAuth('out'))  // 401 handler already cleared the token
  }, [auth])

  // A 401 from any later request bounces us back to login.
  useEffect(() => {
    const onExpired = () => setAuth('out')
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  // Start polling + SSE only once authenticated. Tear both down when auth flips
  // away (e.g. token expiry → login) so a re-login doesn't stack a second
  // interval + second SSE stream on top of the old ones.
  useEffect(() => {
    if (auth !== 'in') return
    const stopPolling = startPolling()
    const stopSSE = connectSSE()
    return () => { stopPolling(); stopSSE() }
  }, [auth])

  if (auth === 'checking') {
    return (
      <div style={{
        position: 'fixed', inset: 0, display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: '#0e0f11', color: 'var(--text-muted, #8b8d8c)', fontSize: 12,
      }}>
        Loading…
      </div>
    )
  }

  if (auth === 'out') {
    return <LoginScreen onSuccess={() => setAuth('in')} />
  }

  if (activeView === 'metrics') return <LiveMetricsPage />
  if (activeView === 'floorplan') return <FloorPlanPage />
  return <MainPage />
}
