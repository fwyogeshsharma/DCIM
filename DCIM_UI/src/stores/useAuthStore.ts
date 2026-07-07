import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api, type AuthUser, type AccessLevel } from '@/lib/api'

const LEVEL_ORDER: Record<AccessLevel, number> = { none: 0, read: 1, write: 2, manage: 3 }

interface AuthState {
  currentUser: AuthUser | null
  token: string | null
  login: (username: string, password: string) => Promise<{ success: boolean; error?: string }>
  register: (
    username: string,
    email: string,
    password: string,
    requestedRole?: string,
  ) => Promise<{ success: boolean; error?: string }>
  logout: () => void
  refresh: () => Promise<void>
  // Effective access level for a feature key (from rbac.yml, resolved server-side).
  can: (feature: string, level?: AccessLevel) => boolean
  isApprover: () => boolean
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      currentUser: null,
      token: null,

      login: async (username, password) => {
        try {
          const { token, user } = await api.login(username, password)
          api.setAuthToken(token)
          set({ currentUser: user, token })
          return { success: true }
        } catch (error: any) {
          return { success: false, error: error?.message || 'Invalid username or password' }
        }
      },

      register: async (username, email, password, requestedRole) => {
        try {
          const { token, user } = await api.register(username, email, password, requestedRole)
          api.setAuthToken(token)
          set({ currentUser: user, token })
          return { success: true }
        } catch (error: any) {
          return { success: false, error: error?.message || 'Registration failed' }
        }
      },

      logout: () => {
        api.logout().catch(() => {})
        api.setAuthToken(null)
        set({ currentUser: null, token: null })
      },

      // Re-fetch the current user so permission changes (e.g. an approval) take
      // effect without re-login.
      refresh: async () => {
        try {
          const user = await api.me()
          set({ currentUser: user })
        } catch {
          // token invalid/expired → force logout
          api.setAuthToken(null)
          set({ currentUser: null, token: null })
        }
      },

      can: (feature, level = 'read') => {
        const perms = get().currentUser?.permissions
        if (!perms) return false
        return LEVEL_ORDER[perms[feature] ?? 'none'] >= LEVEL_ORDER[level]
      },

      isApprover: () => {
        const roles = get().currentUser?.roles ?? []
        return roles.includes('root') || roles.includes('admin')
      },
    }),
    {
      name: 'dcim-auth',
      partialize: (state) => ({ currentUser: state.currentUser, token: state.token }),
      // Re-attach the token to the API client after the store rehydrates from
      // localStorage on a fresh page load.
      onRehydrateStorage: () => (state) => {
        if (state?.token) api.setAuthToken(state.token)
      },
    },
  ),
)

// Any 401 from the API (missing / expired / invalid session) tears down local
// auth state. ProtectedRoute then redirects to /login reactively — no full
// reload, no page left firing failed requests on its polling interval. Guarded
// so an already-logged-out 401 (e.g. a bad-credentials login attempt) is a no-op.
api.setUnauthorizedHandler(() => {
  const { currentUser, token } = useAuthStore.getState()
  if (currentUser || token) {
    api.setAuthToken(null)
    useAuthStore.setState({ currentUser: null, token: null })
  }
})
