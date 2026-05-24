import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'
import { API_BASE } from '../config'

export interface UserInfo {
  username: string
  email: string
  nickname: string
  role: 'student' | 'teacher'
  created_at: string
  last_login?: string
  is_active?: boolean
  login_count?: number
}

interface AuthState {
  user: UserInfo | null
  loading: boolean
  login: (username: string, password: string, rememberMe: boolean) => Promise<{ message: string }>
  register: (username: string, password: string, email: string, nickname?: string, role?: string) => Promise<{ message: string }>
  logout: () => Promise<void>
  fetchMe: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [loading, setLoading] = useState(false)

  const fetchMe = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/me`, { credentials: 'include' })
      if (res.ok) {
        const data: UserInfo = await res.json()
        setUser(data)
      }
    } catch {
      setUser(null)
    }
  }, [])

  const login = useCallback(async (username: string, password: string, rememberMe: boolean) => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password, remember_me: rememberMe }),
      })
      const data = await res.json()
      console.log(data)
      if (!res.ok) throw new Error(data.detail || data.message || '登录失败')
      await fetchMe()
      setLoading(false)
      return data as { message: string }
    } catch (err) {
      setLoading(false)
      throw err
    }
  }, [fetchMe])

  const register = useCallback(async (username: string, password: string, email: string, nickname?: string, role?: string) => {
    setLoading(true)
    try {
      const body: Record<string, string> = { username, password, email }
      if (nickname) body.nickname = nickname
      if (role) body.role = role
      const res = await fetch(`${API_BASE}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.message || '注册失败')
      await fetchMe()
      setLoading(false)
      return data as { message: string }
    } catch (err) {
      setLoading(false)
      throw err
    }
  }, [fetchMe])

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/logout`, { method: 'POST', credentials: 'include' })
    } catch { /* ignore */ }
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, fetchMe }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
