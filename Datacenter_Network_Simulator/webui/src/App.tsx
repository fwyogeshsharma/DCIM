import { useEffect } from 'react'
import MainPage from './pages/MainPage'
import { useStore } from './store/useStore'

export default function App() {
  const { startPolling, connectSSE } = useStore()

  useEffect(() => {
    startPolling()
    connectSSE()
  }, [])

  return <MainPage />
}
