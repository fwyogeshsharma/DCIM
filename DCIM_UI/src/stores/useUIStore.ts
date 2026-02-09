import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UIState {
  theme: 'light' | 'dark'
  sidebarOpen: boolean
  timeRange: '5m' | '1h' | '6h' | '24h' | '7d' | '30d' | 'custom'
  toggleTheme: () => void
  setSidebarOpen: (open: boolean) => void
  setTimeRange: (range: UIState['timeRange']) => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      theme: 'dark',
      sidebarOpen: true,
      timeRange: '24h',
      toggleTheme: () =>
        set((state) => ({
          theme: state.theme === 'light' ? 'dark' : 'light',
        })),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      setTimeRange: (range) => set({ timeRange: range }),
    }),
    {
      name: 'dcim-ui-settings',
    }
  )
)
