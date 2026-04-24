import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import { useEffect } from 'react'
import { useUIStore } from './stores/useUIStore'
import AppLayout from './components/layout/AppLayout'
import LandingOptimized from './pages/LandingOptimized'
import Dashboard from './pages/Dashboard'
import Agents from './pages/Agents'
import AgentDetail from './pages/AgentDetail'
import AgentAnalytics from './pages/AgentAnalytics'
import Alerts from './pages/Alerts'
import Topology from './pages/Topology'
import Topology3D from './pages/Topology3D'
import TopologyEditor from './pages/TopologyEditor'
import AIAnalytics from './pages/AIAnalytics'
import NaturalLanguageQuery from './pages/NaturalLanguageQuery'
import Settings from './pages/Settings'
import ServerManagement from './pages/ServerManagement'
import Traps from './pages/Traps'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
})

function App() {
  const theme = useUIStore((state) => state.theme)

  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }, [theme])

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-background text-foreground">
          <Routes>
            <Route path="/" element={<LandingOptimized />} />
            <Route path="/app" element={<AppLayout />}>
              <Route index element={<Navigate to="/app/dashboard" replace />} />
              <Route path="dashboard" element={<Dashboard />} />
              <Route path="agents" element={<Agents />} />
              <Route path="agents/:agentId" element={<AgentDetail />} />
              <Route path="agents/:agentId/analytics" element={<AgentAnalytics />} />
              <Route path="alerts" element={<Alerts />} />
              <Route path="topology" element={<Topology />} />
              <Route path="topology-3d" element={<Topology3D />} />
              <Route path="topology-editor" element={<TopologyEditor />} />
              <Route path="ai-analytics" element={<AIAnalytics />} />
              <Route path="nl-query" element={<NaturalLanguageQuery />} />
              <Route path="traps" element={<Traps />} />
              <Route path="servers" element={<ServerManagement />} />
              <Route path="settings" element={<Settings />} />
            </Route>
          </Routes>
        </div>
      </BrowserRouter>
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 3000,
          style: {
            background: 'hsl(var(--background))',
            color: 'hsl(var(--foreground))',
            border: '1px solid hsl(var(--border))',
          },
        }}
      />
    </QueryClientProvider>
  )
}

export default App
