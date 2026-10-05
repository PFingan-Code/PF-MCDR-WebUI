import React, { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import { ModsPageSkeleton, ServerStatusPageSkeleton } from './components/PageSkeletons'
import { useAuth } from './hooks/useAuth'

// 路由级懒加载，将各页面拆成独立 chunk，减小主包体积
const Login = lazy(() => import('./pages/Login'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ServerStatus = lazy(() => import('./pages/ServerStatus'))
const MCDRConfig = lazy(() => import('./pages/MCDRConfig'))
const MCConfig = lazy(() => import('./pages/MCConfig'))
const LocalPlugins = lazy(() => import('./pages/LocalPlugins'))
const OnlinePlugins = lazy(() => import('./pages/OnlinePlugins'))
const Terminal = lazy(() => import('./pages/Terminal'))
const Settings = lazy(() => import('./pages/Settings'))
const About = lazy(() => import('./pages/About'))
const Chat = lazy(() => import('./pages/Chat'))
const PlayerChat = lazy(() => import('./pages/PlayerChat'))
const PluginPage = lazy(() => import('./pages/PluginPage'))
const NotFound = lazy(() => import('./pages/NotFound'))
const OperationLogs = lazy(() => import('./pages/OperationLogs'))
const Players = lazy(() => import('./pages/Players'))
const Mods = lazy(() => import('./pages/Mods'))

// 独立页面路径（不需要认证）
const PUBLIC_PATHS = ['/login', '/player-chat']

function AppContent() {
  const { isAuthenticated, loading } = useAuth()
  const location = useLocation()
  const { t } = useTranslation()

  // 如果是独立页面，直接渲染，不等待认证检查
  const isPublicPath = PUBLIC_PATHS.includes(location.pathname)

  // 如果鉴权状态还在加载中，且不是独立页面，先渲染一个简单的加载界面
  if (!isPublicPath && (loading || isAuthenticated === null)) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="text-gray-600 dark:text-gray-300 text-sm">{t('common.checking_login')}</div>
      </div>
    )
  }

  const fallback = (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="text-gray-600 dark:text-gray-300 text-sm">{t('common.notice_loading')}</div>
    </div>
  )

  // 侧边栏内页的懒加载兜底：已提供页面骨架的路由用骨架，其余沿用居中文字
  const layoutFallback =
    location.pathname === '/status' ? (
      <ServerStatusPageSkeleton />
    ) : location.pathname === '/mods' ? (
      <ModsPageSkeleton />
    ) : (
      fallback
    )

  return (
    <Suspense fallback={fallback}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/player-chat" element={<PlayerChat />} />
        <Route
          path="/*"
          element={
            isAuthenticated ? (
              <Layout>
                <Suspense fallback={layoutFallback}>
                  <Routes>
                    <Route path="/" element={<Navigate to="/index" replace />} />
                    <Route path="/index" element={<Dashboard />} />
                    <Route path="/status" element={<ServerStatus />} />
                    <Route path="/mcdr" element={<MCDRConfig />} />
                    <Route path="/mc" element={<MCConfig />} />
                    <Route path="/plugins" element={<LocalPlugins />} />
                    <Route path="/online-plugins" element={<OnlinePlugins />} />
                    <Route path="/terminal" element={<Terminal />} />
                    <Route path="/chat" element={<Chat />} />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="/about" element={<About />} />
                    <Route path="/operation-logs" element={<OperationLogs />} />
                    <Route path="/players" element={<Players />} />
                    <Route path="/mods" element={<Mods />} />
                    <Route path="/plugin-page/:pluginId" element={<PluginPage />} />
                    <Route path="*" element={<NotFound />} />
                  </Routes>
                </Suspense>
              </Layout>
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
      </Routes>
    </Suspense>
  )
}

import { getBasePath } from './utils/api'

function App() {
  const { i18n } = useTranslation()

  // 同步语言设置到 HTML
  React.useEffect(() => {
    document.documentElement.lang = i18n.language
  }, [i18n.language])

  return (
    <BrowserRouter basename={getBasePath()}>
      <AppContent />
    </BrowserRouter>
  )
}

export default App
