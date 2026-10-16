import React, { createContext, useContext, useEffect, useState } from 'react'
import api, { getBasePath, unwrapData } from '../utils/api'
import { type LoginResult, parseLoginError } from '../utils/loginResult'

interface AuthContextType {
  isAuthenticated: boolean | null
  username: string | null
  nickname: string | null
  isAdmin: boolean | null
  isSuperAdmin: boolean | null
  loading: boolean
  login: (account: string, password: string, remember?: boolean) => Promise<LoginResult>
  loginWithTempCode: (tempCode: string) => Promise<LoginResult>
  completeTotpLogin: (ticket: string, code: string) => Promise<LoginResult>
  logout: () => Promise<void>
  checkLoginStatus: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)
  const [username, setUsername] = useState<string | null>(null)
  const [nickname, setNickname] = useState<string | null>(null)
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null)
  const [isSuperAdmin, setIsSuperAdmin] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    checkLoginStatus()
  }, [])

  const checkLoginStatus = async () => {
    try {
      const response = await api.get('/auth/me')
      const data = unwrapData<{
        username?: string | null
        nickname?: string | null
        is_admin?: boolean
        is_super_admin?: boolean
      }>(response, {})
      if (response.data.status === 'success') {
        setIsAuthenticated(true)
        setUsername(data.username ?? null)
        setNickname(data.nickname || null)
        setIsAdmin(Boolean(data.is_admin))
        setIsSuperAdmin(Boolean(data.is_super_admin))
      } else {
        setIsAuthenticated(false)
        setUsername(null)
        setNickname(null)
        setIsAdmin(null)
        setIsSuperAdmin(null)
      }
    } catch (error) {
      setIsAuthenticated(false)
      setUsername(null)
      setNickname(null)
      setIsAdmin(null)
      setIsSuperAdmin(null)
    } finally {
      setLoading(false)
    }
  }

  const login = async (account: string, password: string, remember: boolean = false) => {
    try {
      const formData = new FormData()
      formData.append('account', account)
      formData.append('password', password)
      formData.append('remember', remember.toString())

      const response = await api.post('/login', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      if (response.data.status === 'success') {
        setIsAuthenticated(true)
        setUsername(account)
        setNickname(response.data.nickname || null)
        setIsAdmin(Boolean(response.data.is_admin))
        setIsSuperAdmin(Boolean(response.data.is_super_admin))
        return { success: true }
      } else {
        return { success: false, message: response.data.message }
      }
    } catch (error: unknown) {
      return parseLoginError(error)
    }
  }

  const loginWithTempCode = async (tempCode: string) => {
    try {
      const formData = new FormData()
      formData.append('temp_code', tempCode)

      const response = await api.post('/login', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      if (response.data.status === 'success') {
        setIsAuthenticated(true)
        setUsername(response.data.username || 'tempuser')
        setNickname(response.data.nickname || null)
        setIsAdmin(Boolean(response.data.is_admin))
        setIsSuperAdmin(Boolean(response.data.is_super_admin))
        return { success: true }
      } else {
        return { success: false, message: response.data.message }
      }
    } catch (error: unknown) {
      return parseLoginError(error)
    }
  }

  const completeTotpLogin = async (ticket: string, code: string): Promise<LoginResult> => {
    try {
      const formData = new FormData()
      formData.append('ticket', ticket)
      formData.append('code', code.replace(/\s+/g, ''))
      const response = await api.post('/login/totp', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      if (response.data.status === 'success') {
        await checkLoginStatus()
        return { success: true }
      }
      return { success: false, message: response.data.message }
    } catch (error: unknown) {
      return parseLoginError(error)
    }
  }

  const logout = async () => {
    try {
      // 通过后端 API 主动注销，支持独立模式与挂载模式
      await api.post('/logout')
    } catch (error) {
      console.error('Logout error:', error)
    } finally {
      // 无论后端是否成功，前端都先清理本地登录状态并跳转登录页
      setIsAuthenticated(false)
      setUsername(null)
      setNickname(null)
      setIsAdmin(null)
      setIsSuperAdmin(null)
      // 清除所有可能的 cookie (前端尝试)
      const cookieNames = ['token', 'session'];
      const domains = [window.location.hostname, ''];
      const paths = [getBasePath() || '/', '/', '/guguwebui', '/guguwebui/'];

      cookieNames.forEach(name => {
        paths.forEach(path => {
          domains.forEach(domain => {
            document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=${path};${domain ? ` domain=${domain};` : ''}`;
          });
        });
      });

      window.location.href = getBasePath() + '/login'
    }
  }

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        username,
        nickname,
        isAdmin,
        isSuperAdmin,
        loading,
        login,
        loginWithTempCode,
        completeTotpLogin,
        logout,
        checkLoginStatus,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuthContext = () => {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuthContext must be used within an AuthProvider')
  }
  return context
}
