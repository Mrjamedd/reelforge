import { create } from 'zustand'
import { authApi } from '../lib/api'

interface AuthState {
  token: string | null
  user: { email: string; id: string } | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('rf_token'),
  user: null,
  loading: false,

  login: async (email, password) => {
    set({ loading: true })
    try {
      const res = await authApi.login(email, password)
      const token = res.data.access_token
      localStorage.setItem('rf_token', token)
      const me = await authApi.me()
      set({ token, user: me.data, loading: false })
    } catch {
      set({ loading: false })
      throw new Error('Invalid credentials')
    }
  },

  logout: () => {
    localStorage.removeItem('rf_token')
    set({ token: null, user: null })
  },

  checkAuth: async () => {
    const token = localStorage.getItem('rf_token')
    if (!token) return
    try {
      const me = await authApi.me()
      set({ token, user: me.data })
    } catch {
      localStorage.removeItem('rf_token')
      set({ token: null, user: null })
    }
  },
}))
