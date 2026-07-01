import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import { useEffect } from 'react'
import { useUIStore } from './stores/useUIStore'
import { useAlertNotifications } from './hooks/useAlertNotifications'
import AppLayout from './components/layout/AppLayout'
import ProtectedRoute from './components/ProtectedRoute'
import RequireFeature from './components/RequireFeature'
import Approvals from './pages/Approvals'
import SignIn from './pages/SignIn'
import SignUp from './pages/SignUp'
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
import Reports from './pages/Reports'
import NetworkOps from './pages/NetworkOps'
import FireSafety from './pages/FireSafety'
import Tickets from './pages/Tickets'
import Inventory from './pages/Inventory'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
})

function AlertWatcher() {
  useAlertNotifications()
  return null
}

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
      <AlertWatcher />
      <BrowserRouter>
        <div className="min-h-screen bg-background text-foreground">
          <Routes>
            <Route path="/" element={<LandingOptimized />} />
            <Route path="/login" element={<SignIn />} />
            <Route path="/register" element={<SignUp />} />
            <Route path="/app" element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
              <Route index element={<Navigate to="/app/dashboard" replace />} />
              <Route path="dashboard" element={<RequireFeature feature="dashboard"><Dashboard /></RequireFeature>} />
              <Route path="agents" element={<RequireFeature feature="devices"><Agents /></RequireFeature>} />
              <Route path="agents/:agentId" element={<RequireFeature feature="devices"><AgentDetail /></RequireFeature>} />
              <Route path="agents/:agentId/analytics" element={<RequireFeature feature="devices"><AgentAnalytics /></RequireFeature>} />
              <Route path="alerts" element={<RequireFeature feature="alerts"><Alerts /></RequireFeature>} />
              <Route path="tickets" element={<RequireFeature feature="tickets"><Tickets /></RequireFeature>} />
              <Route path="topology" element={<RequireFeature feature="topology"><Topology /></RequireFeature>} />
              <Route path="topology-3d" element={<RequireFeature feature="topology"><Topology3D /></RequireFeature>} />
              <Route path="topology-editor" element={<RequireFeature feature="topology" level="write"><TopologyEditor /></RequireFeature>} />
              <Route path="ai-analytics" element={<RequireFeature feature="ai_ml"><AIAnalytics /></RequireFeature>} />
              <Route path="nl-query" element={<RequireFeature feature="ai_ml"><NaturalLanguageQuery /></RequireFeature>} />
              <Route path="servers" element={<RequireFeature feature="servers"><ServerManagement /></RequireFeature>} />
              <Route path="settings" element={<RequireFeature feature="settings"><Settings /></RequireFeature>} />
              <Route path="reports" element={<RequireFeature feature="reports"><Reports /></RequireFeature>} />
              <Route path="network-ops" element={<RequireFeature feature="network_ops"><NetworkOps /></RequireFeature>} />
              <Route path="fire-safety" element={<RequireFeature feature="fire_safety"><FireSafety /></RequireFeature>} />
              <Route path="inventory" element={<RequireFeature feature="inventory"><Inventory /></RequireFeature>} />
              <Route path="approvals" element={<RequireFeature feature="user_admin" level="write"><Approvals /></RequireFeature>} />

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
