import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface StoredUser {
  username: string
  email: string
  password: string
}

interface AuthState {
  currentUser: { username: string; email: string } | null
  users: StoredUser[]
  login: (username: string, password: string) => boolean
  register: (username: string, email: string, password: string) => { success: boolean; error?: string }
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      currentUser: null,
      users: [],

      login: (username, password) => {
        const user = get().users.find(
          (u) => u.username === username && u.password === password
        )
        if (user) {
          set({ currentUser: { username: user.username, email: user.email } })
          return true
        }
        return false
      },

      register: (username, email, password) => {
        const existing = get().users.find(
          (u) => u.username === username || u.email === email
        )
        if (existing) {
          return { success: false, error: 'Username or email already taken' }
        }
        set((state) => ({
          users: [...state.users, { username, email, password }],
          currentUser: { username, email },
        }))
        return { success: true }
      },

      logout: () => set({ currentUser: null }),
    }),
    { name: 'dcim-auth' }
  )
)