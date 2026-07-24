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
        background: '#060b16', color: 'var(--text-muted, #8aa0bd)', fontSize: 12,
      }}>
        Loading…
      </div>
    )
  }

  if (auth === 'out') {
    return <LoginScreen onSuccess={() => setAuth('in')} />
  }

  const page = activeView === 'metrics' ? <LiveMetricsPage />
             : activeView === 'floorplan' ? <FloorPlanPage />
             : <MainPage />
  return <>{page}<ChillerTripBanner /></>
}

// Persistent warning while any chiller is latched out on a high head-pressure trip.
// A latched trip does NOT self-heal — cooling stays degraded until reset — so it must
// be annunciated site-wide, not just on the chiller's own right-click menu.
function ChillerTripBanner() {
  const { chillerTrips, resetChillerTrip } = useStore()
  if (!chillerTrips.length) return null
  // A trip is only a cooling LOSS where no standby could cover it. Otherwise the
  // N+1 standby took over — cooling holds, but redundancy is reduced until reset.
  const degraded = chillerTrips.some(c => c.degraded)
  const n = chillerTrips.length
  return (
    <div style={{
      position: 'fixed', top: 0, left: '50%', transform: 'translateX(-50%)',
      zIndex: 10000, marginTop: 8, maxWidth: '92vw',
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      background: degraded ? '#7a1d1d' : '#7a5a12',
      border: `1px solid ${degraded ? '#f85149' : '#e3b341'}`, borderRadius: 6,
      padding: '8px 14px', color: degraded ? '#ffdede' : '#ffeec2', fontSize: 12,
      boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
    }}>
      <span style={{ fontSize: 15 }}>⚠</span>
      <b>{n} chiller{n > 1 ? 's' : ''} tripped on high head pressure</b>
      <span style={{ opacity: 0.85 }}>{degraded
        ? '— cooling capacity reduced, manual reset required'
        : '— standby carrying load; reset to restore N+1 redundancy'}</span>
      {chillerTrips.map(c => (
        <button key={c.device} onClick={() => resetChillerTrip(c.device)}
          style={{
            background: '#f85149', border: 'none', borderRadius: 4, color: '#fff',
            padding: '3px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer',
          }}>
          Reset {c.name}
        </button>
      ))}
    </div>
  )
}
