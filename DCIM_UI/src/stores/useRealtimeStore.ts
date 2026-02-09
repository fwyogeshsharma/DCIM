import { create } from 'zustand'
import type { Agent, Metric, Alert } from '@/lib/types'

interface RealtimeState {
  agents: Map<string, Agent>
  recentMetrics: Metric[]
  recentAlerts: Alert[]
  updateAgent: (agent: Agent) => void
  addMetric: (metric: Metric) => void
  addAlert: (alert: Alert) => void
  clearMetrics: () => void
  clearAlerts: () => void
}

export const useRealtimeStore = create<RealtimeState>((set) => ({
  agents: new Map(),
  recentMetrics: [],
  recentAlerts: [],
  updateAgent: (agent) =>
    set((state) => {
      const newAgents = new Map(state.agents)
      newAgents.set(agent.agent_id, agent)
      return { agents: newAgents }
    }),
  addMetric: (metric) =>
    set((state) => ({
      recentMetrics: [metric, ...state.recentMetrics].slice(0, 100), // Keep last 100
    })),
  addAlert: (alert) =>
    set((state) => ({
      recentAlerts: [alert, ...state.recentAlerts].slice(0, 50), // Keep last 50
    })),
  clearMetrics: () => set({ recentMetrics: [] }),
  clearAlerts: () => set({ recentAlerts: [] }),
}))
