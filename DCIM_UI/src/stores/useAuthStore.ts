import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api, type AuthUser } from '@/lib/api'

interface AuthState {
  currentUser: AuthUser | null
  login: (username: string, password: string) => Promise<{ success: boolean; error?: string }>
  register: (
    username: string,
    email: string,
    password: string,
  ) => Promise<{ success: boolean; error?: string }>
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      currentUser: null,

      // Authenticate against the database (users table) via the aggregator API.
      login: async (username, password) => {
        try {
          const user = await api.login(username, password)
          set({ currentUser: user })
          return { success: true }
        } catch (error: any) {
          return { success: false, error: error?.message || 'Invalid username or password' }
        }
      },

      // Persist the new account to the database so it can be reused to log in
      // again later, from any browser or device.
      register: async (username, email, password) => {
        try {
          const user = await api.register(username, email, password)
          set({ currentUser: user })
          return { success: true }
        } catch (error: any) {
          return { success: false, error: error?.message || 'Registration failed' }
        }
      },

      logout: () => set({ currentUser: null }),
    }),
    {
      name: 'dcim-auth',
      // Only keep the current session locally; the account itself lives in the DB.
      partialize: (state) => ({ currentUser: state.currentUser }),
    },
  ),
)
