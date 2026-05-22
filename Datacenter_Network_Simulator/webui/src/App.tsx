import { useEffect } from 'react'
import MainPage from './pages/MainPage'
import LiveMetricsPage from './pages/LiveMetricsPage'
import { useStore } from './store/useStore'

export default function App() {
  const { startPolling, connectSSE, activeView } = useStore()

  useEffect(() => {
    startPolling()
    connectSSE()
  }, [])

  return activeView === 'metrics' ? <LiveMetricsPage /> : <MainPage />
}
