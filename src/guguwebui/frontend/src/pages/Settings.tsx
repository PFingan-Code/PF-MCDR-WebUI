import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertCircle,
  Bot,
  CheckCircle,
  Database,
  Eye,
  EyeOff,
  Info,
  Loader2,
  Lock,
  MessageSquare,
  Network,
  Plug,
  Plus,
  Save,
  Shield,
  Trash2,
  User,
  X
} from 'lucide-react'
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NiceSelect } from '../components/NiceSelect'
import { ConfirmModal } from '../components/ConfirmModal'
import { SettingsCardSkeleton, Skeleton } from '../components/Skeleton'
import TwoFactorCard from '../components/TwoFactorCard'
import { useAuth } from '../hooks/useAuth'
import api, { getTargetServerId, isCancel, unwrapData } from '../utils/api'

interface Repository {
  name: string
  url: string
}

interface PanelSlave {
  id: string
  name: string
  base_url: string
  token: string
  enabled: boolean
  verify_tls?: boolean
}

interface PanelAllowedToken {
  token: string
  enabled: boolean
  name?: string
  created_at?: string
}

interface PanelMasterConfig {
  allowed_tokens: PanelAllowedToken[]
  allowed_master_ips?: string[]
}

interface WebConfig {
  host: string
  port: number
  super_admin_account: string | number
  disable_admin_login_web: boolean
  enable_temp_login_password: boolean
  qq_qr_login_enabled?: boolean
  trusted_proxies?: string[]
  allowed_origins?: string[]
  ai_api_key_configured: boolean
  ai_model: string
  ai_api_url: string
  mcdr_plugins_url: string
  pf_plugin_catalogue_url?: string
  repositories: Repository[]
  ssl_enabled: boolean
  ssl_certfile: string
  ssl_keyfile: string
  ssl_keyfile_password?: string
  public_chat_enabled: boolean
  public_chat_to_game_enabled: boolean
  chat_verification_expire_minutes: number
  chat_session_expire_hours: number
  chat_message_count: number
  force_standalone: boolean
  icp_records: { icp: string; url: string }[]
  // 多服合并配置改为单独接口读取（避免在子服视图下误改本地多服配置）
}

const Settings: React.FC = () => {
  const { t } = useTranslation()
  const { isSuperAdmin } = useAuth()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<string | null>(null)
  const [clearHistoryConfirm, setClearHistoryConfirm] = useState(false)
  const [config, setConfig] = useState<WebConfig | null>(null)
  const [aiApiKey, setAiApiKey] = useState('')
  const [showAiKey, setShowAiKey] = useState(false)
  const [validatingAiKey, setValidatingAiKey] = useState(false)
  const [pimStatus, setPimStatus] = useState<'installed' | 'not_installed' | 'installing' | 'loading'>('loading')
  const [newRepo, setNewRepo] = useState<Repository>({ name: '', url: '' })
  const [newIcp, setNewIcp] = useState({ icp: '', url: '' })
  const [newSlave, setNewSlave] = useState<PanelSlave>({
    id: '',
    name: '',
    base_url: '',
    token: '',
    enabled: true,
    verify_tls: true
  })
  const [newAllowedToken, setNewAllowedToken] = useState<PanelAllowedToken>({
    token: '',
    enabled: true,
    name: ''
  })
  const [panelMergeConfig, setPanelMergeConfig] = useState<{
    panel_role: 'master' | 'slave'
    panel_slaves: PanelSlave[]
    panel_master: PanelMasterConfig
  } | null>(null)
  const [panelMergeLoading, setPanelMergeLoading] = useState(false)
  const [panelMergeDirty, setPanelMergeDirty] = useState(false)
  const targetServerId = getTargetServerId()
  const [panelMergeMode, setPanelMergeMode] = useState<'quick' | 'config'>(() => {
    try {
      const v = localStorage.getItem('panel_merge_mode')
      return v === 'config' ? 'config' : 'quick'
    } catch {
      return 'quick'
    }
  })
  const [pairingEnabledUntil, setPairingEnabledUntil] = useState<number | null>(null)
  const [pairingPending, setPairingPending] = useState<Array<{ request_id: string; ip: string; master_name?: string; created_at?: string }>>([])
  const [connectId, setConnectId] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)
  const connectStartedAtRef = React.useRef<number | null>(null)

  const [notification, setNotification] = useState<{ message: string; type: 'success' | 'error' } | null>(null)
  const [modUploadMaxMiB, setModUploadMaxMiB] = useState<number | null>(null)
  const [modSettingsLoading, setModSettingsLoading] = useState(false)
  const [modSettingsSaving, setModSettingsSaving] = useState(false)

  const getErrorMeta = (err: unknown): { name?: string; code?: string; message?: string } => {
    if (!err || typeof err !== 'object') return {}
    const rec = err as Record<string, unknown>
    return {
      name: typeof rec.name === 'string' ? rec.name : undefined,
      code: typeof rec.code === 'string' ? rec.code : undefined,
      message: typeof rec.message === 'string' ? rec.message : undefined,
    }
  }

  const showNotification = useCallback((message: string, type: 'success' | 'error') => {
    setNotification({ message, type })
    setTimeout(() => setNotification(null), 5000)
  }, [])

  const fetchConfig = useCallback(async (signal?: AbortSignal) => {
    try {
      const resp = await api.get('/web-config', { signal })
      setConfig(unwrapData<WebConfig | null>(resp, null))
    } catch (error: unknown) {
      // 忽略取消的请求错误
      const meta = getErrorMeta(error)
      if (isCancel(error) || meta.name === 'AbortError' || meta.code === 'ERR_CANCELED') {
        return
      }
      console.error('Failed to fetch config:', error)
      showNotification(t('page.settings.msg.get_config_failed'), 'error')
    } finally {
      setLoading(false)
    }
  }, [t, showNotification])

  const fetchPanelMergeConfig = useCallback(async (signal?: AbortSignal) => {
    setPanelMergeLoading(true)
    try {
      const resp = await api.get('/panel_merge_config', {
        signal,
        headers: { 'X-Target-Server': 'local' }
      })
      const d = unwrapData<{
        panel_role?: 'master' | 'slave'
        panel_slaves?: PanelSlave[]
        panel_master?: PanelMasterConfig
      } | null>(resp, null)
      if (d) {
        setPanelMergeConfig({
          panel_role: (d.panel_role || 'master') as 'master' | 'slave',
          panel_slaves: Array.isArray(d.panel_slaves) ? d.panel_slaves : [],
          panel_master: d.panel_master || { allowed_tokens: [], allowed_master_ips: [] }
        })
        setPanelMergeDirty(false)
      }
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      if (isCancel(error) || meta.name === 'AbortError' || meta.code === 'ERR_CANCELED') return
    } finally {
      setPanelMergeLoading(false)
    }
  }, [])

  const persistPanelMergeConfig = useCallback(
    async (
      next: { panel_role: 'master' | 'slave'; panel_slaves: PanelSlave[]; panel_master: PanelMasterConfig },
      opts: { silent?: boolean } = {}
    ) => {
      setPanelMergeLoading(true)
      try {
        const resp = await api.put('/panel_merge_config', next, {
          headers: { 'X-Target-Server': 'local' }
        })
        const d = resp.data as { status?: string; message?: string } | undefined
        if (d?.status === 'success') {
          setPanelMergeConfig(next)
          setPanelMergeDirty(false)
          // 通知侧边栏服务器列表刷新
          try {
            window.dispatchEvent(new Event('gugu:serversChanged'))
          } catch {
            // ignore
          }
          if (!opts.silent) showNotification(d.message || t('common.save_success'), 'success')
        } else {
          if (!opts.silent) showNotification(t('page.settings.msg.save_failed_prefix') + (d?.message || ''), 'error')
        }
      } catch (error: unknown) {
        if (!opts.silent) {
          const meta = getErrorMeta(error)
          showNotification(t('page.settings.msg.save_error_prefix') + (meta.message || ''), 'error')
        }
      } finally {
        setPanelMergeLoading(false)
      }
    },
    [showNotification, t]
  )

  const fetchPairingPending = useCallback(async (signal?: AbortSignal) => {
    try {
      const resp = await api.get('/pairing/pending', {
        signal,
        headers: { 'X-Target-Server': 'local' }
      })
      const d = unwrapData<{ pending?: Array<{ request_id: string; ip: string; master_name?: string }> } | null>(resp, null)
      if (d && Array.isArray(d.pending)) {
        setPairingPending(d.pending)
      }
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      if (isCancel(error) || meta.name === 'AbortError' || meta.code === 'ERR_CANCELED') return
    }
  }, [])

  const fetchPimStatus = useCallback(async (signal?: AbortSignal) => {
    try {
      const resp = await api.get('/pim/status', { signal })
      const data = unwrapData<{ pim_status?: string; message?: string } | null>(resp, null)
      if (data && data.pim_status) {
        setPimStatus(data.pim_status as 'installed' | 'not_installed' | 'installing' | 'loading')
      } else {
        setPimStatus('not_installed')
        console.error('Failed to fetch PIM status:', data?.message)
      }
    } catch (error: unknown) {
      // 忽略取消的请求错误
      const meta = getErrorMeta(error)
      if (isCancel(error) || meta.name === 'AbortError' || meta.code === 'ERR_CANCELED') {
        return
      }
      console.error('Failed to fetch PIM status:', error)
      setPimStatus('not_installed')
    }
  }, [])

  const fetchModSettings = useCallback(async (signal?: AbortSignal) => {
    if (!isSuperAdmin) return
    setModSettingsLoading(true)
    try {
      const resp = await api.get('/mods/settings', { signal })
      const d = unwrapData<{ upload_max_mib?: number } | null>(resp, null)
      const value = Number(d?.upload_max_mib)
      if (Number.isInteger(value) && value >= 1 && value <= 4096) setModUploadMaxMiB(value)
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      if (isCancel(error) || meta.name === 'AbortError' || meta.code === 'ERR_CANCELED') return
      console.error('Failed to fetch mod settings:', error)
    } finally {
      setModSettingsLoading(false)
    }
  }, [isSuperAdmin])

  useEffect(() => {
    // 创建 AbortController 用于取消请求
    const abortController = new AbortController()
    const signal = abortController.signal

    fetchConfig(signal)
    fetchPimStatus(signal)
    fetchPanelMergeConfig(signal)
    fetchPairingPending(signal)
    fetchModSettings(signal)

    return () => {
      // 取消所有进行中的请求
      abortController.abort()
    }
  }, [fetchConfig, fetchPimStatus, fetchPanelMergeConfig, fetchPairingPending, fetchModSettings])

  // 主服：连接请求状态自动轮询（5分钟超时）
  useEffect(() => {
    if (!connectId) return

    if (!connectStartedAtRef.current) connectStartedAtRef.current = Date.now()

    const timer = window.setInterval(async () => {
      const startedAt = connectStartedAtRef.current || Date.now()
      if (Date.now() - startedAt > 5 * 60 * 1000) {
        window.clearInterval(timer)
        connectStartedAtRef.current = null
        setConnectId(null)
        showNotification(t('page.settings.multi_server.connect_timeout'), 'error')
        return
      }

      try {
        const resp = await api.get('/pairing/connect_status', {
          params: { connect_id: connectId },
          headers: { 'X-Target-Server': 'local' }
        })
        const st = unwrapData<{ phase?: string } | null>(resp, null)
        if (st?.phase === 'accepted') {
          window.clearInterval(timer)
          connectStartedAtRef.current = null
          showNotification(t('page.settings.multi_server.connected'), 'success')
          setConnectId(null)
          setNewSlave({ id: '', name: '', base_url: '', token: '', enabled: true, verify_tls: true })
          fetchPanelMergeConfig()
          // 通知侧边栏服务器列表刷新（新增第一个子服时需要立刻显示）
          try {
            window.dispatchEvent(new Event('gugu:serversChanged'))
          } catch {
            // ignore
          }
          return
        }
        if (st?.phase === 'denied') {
          window.clearInterval(timer)
          connectStartedAtRef.current = null
          showNotification(t('page.settings.multi_server.denied'), 'error')
          setConnectId(null)
        }
      } catch {
        // 网络波动忽略，继续轮询
      }
    }, 2000)

    return () => {
      window.clearInterval(timer)
    }
  }, [connectId, fetchPanelMergeConfig, showNotification, t])

  // 子服：开启接受连接期间，自动轮询 pending（5分钟超时由后端控制窗口，这里仅做 UI 刷新）
  useEffect(() => {
    if ((panelMergeConfig?.panel_role || 'master') !== 'slave') return
    if (!pairingEnabledUntil && pairingPending.length === 0) return

    const timer = window.setInterval(() => {
      fetchPairingPending()
    }, 2000)

    return () => {
      window.clearInterval(timer)
    }
  }, [fetchPairingPending, pairingEnabledUntil, pairingPending.length, panelMergeConfig?.panel_role])

  // 子服：一旦收到连接请求（pending 出现），立即隐藏“有效期至”
  useEffect(() => {
    if ((panelMergeConfig?.panel_role || 'master') !== 'slave') return
    if (pairingPending.length > 0 && pairingEnabledUntil) {
      setPairingEnabledUntil(null)
    }
  }, [pairingPending.length, pairingEnabledUntil, panelMergeConfig?.panel_role])

  // 子服：pairingEnabledUntil 到期后自动清理显示
  useEffect(() => {
    if (pairingEnabledUntil == null) return
    const expiresAtMs = pairingEnabledUntil * 1000
    if (!Number.isFinite(expiresAtMs)) return

    const timer = window.setTimeout(() => {
      setPairingEnabledUntil(null)
    }, Math.max(0, expiresAtMs - Date.now()))

    return () => window.clearTimeout(timer)
  }, [pairingEnabledUntil])

  const handleSave = async (action: string, data: Record<string, unknown>) => {
    setSaving(action)
    try {
      const { data: resp } = await api.put('/web-config', data)
      if (resp.status === 'success') {
        showNotification(resp.message || t('common.save_success'), 'success')
        fetchConfig()
      } else {
        showNotification(t('page.settings.msg.save_failed_prefix') + resp.message, 'error')
      }
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      console.error('Save failed:', error)
      showNotification(t('page.settings.msg.save_error_prefix') + (meta.message || ''), 'error')
    } finally {
      setSaving(null)
    }
  }

  const toggleSetting = async (
    key: 'disable_admin_login_web' | 'enable_temp_login_password' | 'qq_qr_login_enabled'
  ) => {
    if (!config) return
    setSaving(key)
    try {
      const next = key === 'qq_qr_login_enabled' ? config[key] === false : !config[key]
      const { data: resp } = await api.put('/web-config', { [key]: next })
      if (resp.status === 'success') {
        showNotification(
          next ? t('page.settings.msg.toggle_enabled') : t('page.settings.msg.toggle_disabled'),
          'success'
        )
        fetchConfig()
      } else {
        showNotification(t('page.settings.msg.toggle_failed_prefix') + resp.message, 'error')
      }
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      showNotification(t('page.settings.msg.toggle_error_prefix') + (meta.message || ''), 'error')
    } finally {
      setSaving(null)
    }
  }

  const validateAiKey = async () => {
    if (!aiApiKey) {
      showNotification(t('page.settings.msg.api_key_required'), 'error')
      return
    }
    setValidatingAiKey(true)
    try {
      const { data } = await api.post('/deepseek', {
        query: t('page.settings.ai.validate_query'),
        system_prompt: t('page.settings.ai.validate_system_prompt'),
        api_key: aiApiKey,
        api_url: config?.ai_api_url,
        model: config?.ai_model
      })
      if (data.status === 'success') {
        showNotification(t('page.settings.msg.api_key_validate_success'), 'success')
      } else {
        showNotification(t('page.settings.msg.api_key_validate_failed_prefix') + (data.message || ''), 'error')
      }
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      showNotification(t('page.settings.msg.api_key_validate_error_prefix') + (meta.message || ''), 'error')
    } finally {
      setValidatingAiKey(false)
    }
  }

  const addRepository = () => {
    if (!newRepo.name || !newRepo.url) {
      showNotification(t('page.settings.msg.repo_name_url_required'), 'error')
      return
    }
    if (config) {
      const updatedRepos = [...config.repositories, newRepo]
      setConfig({ ...config, repositories: updatedRepos })
      setNewRepo({ name: '', url: '' })
      showNotification(t('page.settings.msg.repo_added_tip'), 'success')
    }
  }

  const deleteRepository = (index: number) => {
    if (config) {
      const updatedRepos = config.repositories.filter((_, i) => i !== index)
      setConfig({ ...config, repositories: updatedRepos })
      showNotification(t('page.settings.msg.repo_deleted_tip'), 'success')
    }
  }

  const addIcpRecord = () => {
    if (!newIcp.icp || !newIcp.url) {
      showNotification(t('page.settings.msg.icp_required'), 'error')
      return
    }
    if (config) {
      if (config.icp_records.length >= 2) {
        showNotification(t('page.settings.msg.icp_limit'), 'error')
        return
      }
      const updatedIcp = [...config.icp_records, newIcp]
      setConfig({ ...config, icp_records: updatedIcp })
      setNewIcp({ icp: '', url: '' })
      showNotification(t('page.settings.msg.icp_added_tip'), 'success')
    }
  }

  const deleteIcpRecord = (index: number) => {
    if (config) {
      const updatedIcp = config.icp_records.filter((_, i) => i !== index)
      setConfig({ ...config, icp_records: updatedIcp })
      showNotification(t('page.settings.msg.icp_deleted_tip'), 'success')
    }
  }

  const installPim = async () => {
    setPimStatus('installing')
    try {
      const resp = await api.post('/pim/bootstrap')
      const body = resp.data as { message?: string } | undefined
      showNotification(t('page.settings.msg.pim_install_success') + (body?.message ? `: ${body.message}` : ''), 'success')
      setPimStatus('installed')
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      showNotification(t('page.settings.msg.pim_install_error_prefix') + (meta.message || ''), 'error')
      setPimStatus('not_installed')
    }
  }

  const saveModSettings = async () => {
    if (modUploadMaxMiB === null || !Number.isInteger(modUploadMaxMiB) || modUploadMaxMiB < 1 || modUploadMaxMiB > 4096) {
      showNotification(t('page.settings.mod_upload.invalid'), 'error')
      return
    }
    setModSettingsSaving(true)
    try {
      const resp = await api.put('/mods/settings', { upload_max_mib: modUploadMaxMiB })
      const d = unwrapData<{ upload_max_mib?: number } | null>(resp, null)
      if (d && typeof d.upload_max_mib === 'number') {
        setModUploadMaxMiB(d.upload_max_mib)
        showNotification(t('page.settings.mod_upload.saved'), 'success')
      } else {
        showNotification((resp.data as { message?: string } | undefined)?.message || t('page.settings.mod_upload.save_failed'), 'error')
      }
    } catch (error: unknown) {
      const meta = getErrorMeta(error)
      showNotification(meta.message || t('page.settings.mod_upload.save_failed'), 'error')
    } finally {
      setModSettingsSaving(false)
    }
  }

  if (loading || !config) {
    return (
      <div className="space-y-8 pb-12">
        {/* Header skeleton */}
        <div className="space-y-2">
          <Skeleton className="h-9 w-56" />
          <Skeleton className="h-4 w-80" />
        </div>

        {/* Settings cards skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <SettingsCardSkeleton wide />
          <SettingsCardSkeleton />
          <SettingsCardSkeleton />
          <SettingsCardSkeleton />
          <SettingsCardSkeleton />
        </div>
      </div>
    )
  }

  const containerVariants = {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: { staggerChildren: 0.1 }
    }
  }

  const itemVariants = {
    hidden: { y: 20, opacity: 0 },
    visible: { y: 0, opacity: 1 }
  }

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-col gap-2"
      >
        <h1 className="text-3xl font-bold text-slate-900 dark:text-white">
          {t('page.settings.header')}
        </h1>
        <p className="text-slate-500 dark:text-slate-400">
          {t('page.settings.network.tip')}
        </p>
      </motion.div>

      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
        className="grid grid-cols-1 lg:grid-cols-2 gap-6"
      >
        {/* Multi-server panel merge */}
        <motion.div variants={itemVariants} className="lg:col-span-2 bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
          <div className="flex items-center gap-3 text-slate-700 dark:text-slate-200">
            <Network className="w-6 h-6" />
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              {t('page.settings.multi_server.title')}
            </h2>
          </div>
          <p className="text-sm text-slate-500">
            {t('page.settings.multi_server.tip')}
          </p>
          {targetServerId !== 'local' && (
            <div className="p-4 bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/20 rounded-2xl text-xs text-amber-700 dark:text-amber-300">
              {t('page.settings.multi_server.local_only_tip')}
            </div>
          )}

          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2 p-1 bg-slate-100 dark:bg-slate-800 rounded-2xl">
              <button
                onClick={() => {
                  setPanelMergeMode('quick')
                  try { localStorage.setItem('panel_merge_mode', 'quick') } catch { /* ignore */ }
                }}
                className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${panelMergeMode === 'quick'
                  ? 'bg-white dark:bg-slate-700 text-blue-600 dark:text-blue-400 shadow-sm'
                  : 'text-slate-500 dark:text-slate-400'
                  }`}
              >
                {t('page.settings.multi_server.mode_quick')}
              </button>
              <button
                onClick={() => {
                  setPanelMergeMode('config')
                  try { localStorage.setItem('panel_merge_mode', 'config') } catch { /* ignore */ }
                }}
                className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${panelMergeMode === 'config'
                  ? 'bg-white dark:bg-slate-700 text-blue-600 dark:text-blue-400 shadow-sm'
                  : 'text-slate-500 dark:text-slate-400'
                  }`}
              >
                {t('page.settings.multi_server.mode_config')}
              </button>
            </div>
            <select
              value={panelMergeConfig?.panel_role || 'master'}
              onChange={() => { }}
              className="hidden"
            />
            <div className="min-w-[160px]">
              <NiceSelect
                value={panelMergeConfig?.panel_role || 'master'}
                title={t('page.settings.multi_server.role')}
                onChange={(v) => {
                  const nextRole = (v as 'master' | 'slave') || 'master'
                  const next = {
                    panel_role: nextRole,
                    panel_slaves: panelMergeConfig?.panel_slaves || [],
                    panel_master: panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                  }
                  setPanelMergeConfig(next)
                  persistPanelMergeConfig(next, { silent: true })
                }}
                options={[
                  { value: 'master', label: t('page.settings.multi_server.role_master') },
                  { value: 'slave', label: t('page.settings.multi_server.role_slave') },
                ]}
              />
            </div>
          </div>

          {panelMergeMode === 'quick' ? (
            <div className="space-y-4">
              {(panelMergeConfig?.panel_role || 'master') === 'master' ? (
                <>
                  <p className="text-sm font-bold text-slate-900 dark:text-white">
                    {t('page.settings.multi_server.slaves')}
                  </p>
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      <input
                        value={newSlave.name}
                        onChange={(e) => setNewSlave({ ...newSlave, name: e.target.value })}
                        placeholder={t('page.settings.multi_server.slave.name')}
                        className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-sm outline-none"
                      />
                      <input
                        value={newSlave.base_url}
                        onChange={(e) => setNewSlave({ ...newSlave, base_url: e.target.value })}
                        placeholder={t('page.settings.multi_server.slave.base_url')}
                        className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-sm outline-none"
                      />
                      <button
                        disabled={connecting || !newSlave.name.trim() || !newSlave.base_url.trim()}
                        onClick={async () => {
                          setConnecting(true)
                          try {
                            const resp = await api.post(
                              '/pairing/connect_request',
                              { slave_name: newSlave.name.trim(), base_url: newSlave.base_url.trim() },
                              { headers: { 'X-Target-Server': 'local' } }
                            )
                            const p = unwrapData<{ phase?: string; connect_id?: string } | null>(resp, null)
                            if (p?.phase === 'pending' && p.connect_id) {
                              setConnectId(p.connect_id)
                              showNotification(t('page.settings.multi_server.connect_pending'), 'success')
                            } else {
                              showNotification((resp.data as { message?: string } | undefined)?.message || t('page.settings.msg.save_failed_prefix'), 'error')
                            }
                          } catch (error: unknown) {
                            const meta = getErrorMeta(error)
                            showNotification(meta.message || t('page.settings.msg.save_error_prefix'), 'error')
                          } finally {
                            setConnecting(false)
                          }
                        }}
                        className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-blue-500/20"
                      >
                        {connecting ? t('page.settings.multi_server.connecting') : t('page.settings.multi_server.connect')}
                      </button>
                    </div>

                    {connectId && (
                      <div className="p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl flex items-center justify-between">
                        <div className="text-xs text-slate-600 dark:text-slate-300">
                          {t('page.settings.multi_server.waiting_slave')}
                        </div>
                        <button
                          className="px-4 py-2 text-xs font-bold bg-slate-900 dark:bg-white text-white dark:text-slate-900 rounded-xl"
                          onClick={async () => {
                            try {
                              const resp = await api.get('/pairing/connect_status', {
                                params: { connect_id: connectId },
                                headers: { 'X-Target-Server': 'local' }
                              })
                              const st = unwrapData<{ phase?: string } | null>(resp, null)
                              if (st?.phase === 'accepted') {
                                showNotification(t('page.settings.multi_server.connected'), 'success')
                                setConnectId(null)
                                setNewSlave({ id: '', name: '', base_url: '', token: '', enabled: true, verify_tls: true })
                                fetchPanelMergeConfig()
                                // 通知侧边栏服务器列表刷新（新增第一个子服时需要立刻显示）
                                try {
                                  window.dispatchEvent(new Event('gugu:serversChanged'))
                                } catch {
                                  // ignore
                                }
                              } else if (st?.phase === 'denied') {
                                showNotification(t('page.settings.multi_server.denied'), 'error')
                                setConnectId(null)
                              }
                            } catch {
                              // ignore
                            }
                          }}
                        >
                          {t('common.refresh')}
                        </button>
                      </div>
                    )}

                    <div className="space-y-2">
                      {(panelMergeConfig?.panel_slaves || []).map((s) => (
                        <div key={s.id} className="flex items-center justify-between p-3 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
                          <div className="min-w-0">
                            <div className="font-bold text-slate-900 dark:text-white text-sm truncate">{s.name || s.id}</div>
                            <div className="text-xs text-slate-500 truncate">{s.base_url}</div>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => {
                                const nextSlaves = (panelMergeConfig?.panel_slaves || []).map(x => x.id === s.id ? { ...x, enabled: !x.enabled } : x)
                                const next = {
                                  panel_role: panelMergeConfig?.panel_role || 'master',
                                  panel_slaves: nextSlaves,
                                  panel_master: panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                                }
                                setPanelMergeConfig(next)
                                persistPanelMergeConfig(next, { silent: true })
                              }}
                              className={`w-10 h-6 rounded-full p-1 transition-colors ${s.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                            >
                              <motion.div animate={{ x: s.enabled ? 16 : 0 }} className="w-4 h-4 bg-white rounded-full shadow-sm" />
                            </button>
                            <button
                              onClick={() => {
                                const nextSlaves = (panelMergeConfig?.panel_slaves || []).filter(x => x.id !== s.id)
                                const next = {
                                  panel_role: panelMergeConfig?.panel_role || 'master',
                                  panel_slaves: nextSlaves,
                                  panel_master: panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                                }
                                setPanelMergeConfig(next)
                                persistPanelMergeConfig(next, { silent: true })
                              }}
                              className="p-2 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-all"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </div>
                      ))}
                      <div className="text-xs text-slate-500">
                        {t('page.settings.multi_server.quick_edit_tip')}
                      </div>
                      <div className="text-xs text-slate-500">
                        {t('page.settings.multi_server.auto_saved')}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-sm font-bold text-slate-900 dark:text-white">
                    {t('page.settings.multi_server.valid_tokens')}
                  </p>
                  <div className="flex flex-wrap gap-3 items-center">
                    <button
                      onClick={async () => {
                        try {
                          if (pairingEnabledUntil) {
                            await api.post('/pairing/disable', {}, { headers: { 'X-Target-Server': 'local' } })
                            setPairingEnabledUntil(null)
                            showNotification(t('page.settings.multi_server.accept_disabled'), 'success')
                            return
                          }
                          const resp = await api.post('/pairing/enable', {}, { headers: { 'X-Target-Server': 'local' } })
                          const p = unwrapData<{ expires_at?: number } | null>(resp, null)
                          if (p && typeof p.expires_at === 'number') {
                            setPairingEnabledUntil(p.expires_at)
                            showNotification(t('page.settings.multi_server.accept_enabled'), 'success')
                          }
                        } catch {
                          // ignore
                        }
                      }}
                      className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-2xl shadow-lg shadow-blue-500/20"
                    >
                      {pairingEnabledUntil
                        ? t('page.settings.multi_server.disable_pairing')
                        : t('page.settings.multi_server.enable_pairing')}
                    </button>
                    {pairingEnabledUntil && pairingPending.length === 0 && (
                      <span className="text-xs text-slate-500">
                        {t('page.settings.multi_server.enabled_until')}{new Date(pairingEnabledUntil * 1000).toLocaleString()}
                      </span>
                    )}
                    <button
                      onClick={() => fetchPairingPending()}
                      className="px-4 py-2.5 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 rounded-2xl text-sm font-bold"
                    >
                      {t('common.refresh')}
                    </button>
                  </div>

                  {pairingPending.length > 0 && (
                    <div className="space-y-2">
                      <div className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                        {t('page.settings.multi_server.pending_requests')}
                      </div>
                      {pairingPending.map((p) => (
                        <div key={p.request_id} className="p-4 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex items-center justify-between">
                          <div className="text-sm">
                            <div className="font-bold text-slate-900 dark:text-white">
                              {t('page.settings.multi_server.request_from')} IP: {p.ip}
                            </div>
                            <div className="text-xs text-slate-500">
                              {p.master_name ? `${t('page.settings.multi_server.master_name')}: ${p.master_name}` : ''}
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <button
                              onClick={async () => {
                                await api.post('/pairing/deny', { request_id: p.request_id }, { headers: { 'X-Target-Server': 'local' } })
                                fetchPairingPending()
                              }}
                              className="px-4 py-2 bg-slate-100 dark:bg-slate-800 rounded-xl text-sm font-bold"
                            >
                              {t('common.cancel')}
                            </button>
                            <button
                              onClick={async () => {
                                await api.post('/pairing/accept', { request_id: p.request_id }, { headers: { 'X-Target-Server': 'local' } })
                                showNotification(t('page.settings.multi_server.accepted'), 'success')
                                fetchPairingPending()
                                fetchPanelMergeConfig()
                              }}
                              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-bold"
                            >
                              {t('common.confirm')}
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="space-y-2">
                    {(panelMergeConfig?.panel_master?.allowed_tokens || []).map((it) => (
                      <div key={it.token} className="flex items-center justify-between p-3 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
                        <div className="min-w-0">
                          <div className="font-bold text-slate-900 dark:text-white text-sm truncate">{it.name || 'master'}</div>
                          <div className="text-xs text-slate-500 font-mono truncate">{it.token}</div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => {
                              const nextAllowed = (panelMergeConfig?.panel_master?.allowed_tokens || []).map(x => x.token === it.token ? { ...x, enabled: !x.enabled } : x)
                              const next = {
                                panel_role: panelMergeConfig?.panel_role || 'slave',
                                panel_slaves: panelMergeConfig?.panel_slaves || [],
                                panel_master: { ...(panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: nextAllowed },
                              }
                              setPanelMergeConfig(next)
                              persistPanelMergeConfig(next, { silent: true })
                            }}
                            className={`w-10 h-6 rounded-full p-1 transition-colors ${it.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                          >
                            <motion.div animate={{ x: it.enabled ? 16 : 0 }} className="w-4 h-4 bg-white rounded-full shadow-sm" />
                          </button>
                          <button
                            onClick={() => {
                              const nextAllowed = (panelMergeConfig?.panel_master?.allowed_tokens || []).filter(x => x.token !== it.token)
                              const next = {
                                panel_role: panelMergeConfig?.panel_role || 'slave',
                                panel_slaves: panelMergeConfig?.panel_slaves || [],
                                panel_master: { ...(panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: nextAllowed },
                              }
                              setPanelMergeConfig(next)
                              persistPanelMergeConfig(next, { silent: true })
                            }}
                            className="p-2 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-all"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                    <div className="text-xs text-slate-500">
                      {t('page.settings.multi_server.quick_edit_tip2')}
                    </div>
                    <div className="text-xs text-slate-500">
                      {t('page.settings.multi_server.auto_saved')}
                    </div>
                  </div>
                </>
              )}
            </div>
          ) : ((panelMergeConfig?.panel_role || 'master') === 'master' ? (
            <div className="space-y-4">
              <p className="text-sm font-bold text-slate-900 dark:text-white">
                {t('page.settings.multi_server.slaves')}
              </p>
              <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800">
                <table className="w-full text-sm text-left">
                  <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 font-medium">
                    <tr>
                      <th className="px-4 py-3">{t('page.settings.multi_server.slave.id')}</th>
                      <th className="px-4 py-3">{t('page.settings.multi_server.slave.name')}</th>
                      <th className="px-4 py-3">{t('page.settings.multi_server.slave.base_url')}</th>
                      <th className="px-4 py-3">{t('page.settings.multi_server.slave.token')}</th>
                      <th className="px-4 py-3">{t('page.settings.multi_server.slave.enabled')}</th>
                      <th className="px-4 py-3 text-right">{t('page.settings.multi_server.actions')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {(panelMergeConfig?.panel_slaves || []).map((s, idx) => (
                      <tr key={`${s.id}-${idx}`}>
                        <td className="px-4 py-3">
                          <input
                            value={s.id}
                            onChange={(e) => {
                              const next = [...(panelMergeConfig?.panel_slaves || [])]
                              next[idx] = { ...next[idx], id: e.target.value }
                              setPanelMergeConfig((prev) => ({
                                panel_role: prev?.panel_role || 'master',
                                panel_slaves: next,
                                panel_master: prev?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                              }))
                              setPanelMergeDirty(true)
                            }}
                            className="w-28 px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            value={s.name}
                            onChange={(e) => {
                              const next = [...(panelMergeConfig?.panel_slaves || [])]
                              next[idx] = { ...next[idx], name: e.target.value }
                              setPanelMergeConfig((prev) => ({
                                panel_role: prev?.panel_role || 'master',
                                panel_slaves: next,
                                panel_master: prev?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                              }))
                              setPanelMergeDirty(true)
                            }}
                            className="w-32 px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            value={s.base_url}
                            onChange={(e) => {
                              const next = [...(panelMergeConfig?.panel_slaves || [])]
                              next[idx] = { ...next[idx], base_url: e.target.value }
                              setPanelMergeConfig((prev) => ({
                                panel_role: prev?.panel_role || 'master',
                                panel_slaves: next,
                                panel_master: prev?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                              }))
                              setPanelMergeDirty(true)
                            }}
                            placeholder="http://127.0.0.1:8001"
                            className="min-w-[220px] px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            value={s.token}
                            onChange={(e) => {
                              const next = [...(panelMergeConfig?.panel_slaves || [])]
                              next[idx] = { ...next[idx], token: e.target.value }
                              setPanelMergeConfig((prev) => ({
                                panel_role: prev?.panel_role || 'master',
                                panel_slaves: next,
                                panel_master: prev?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                              }))
                              setPanelMergeDirty(true)
                            }}
                            className="min-w-[240px] px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs font-mono"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <button
                            onClick={() => {
                              const next = [...(panelMergeConfig?.panel_slaves || [])]
                              next[idx] = { ...next[idx], enabled: !next[idx].enabled }
                              const merged = {
                                panel_role: panelMergeConfig?.panel_role || 'master',
                                panel_slaves: next,
                                panel_master: panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                              }
                              setPanelMergeConfig(merged)
                              persistPanelMergeConfig(merged, { silent: true })
                            }}
                            className={`w-10 h-6 rounded-full p-1 transition-colors ${s.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                          >
                            <motion.div animate={{ x: s.enabled ? 16 : 0 }} className="w-4 h-4 bg-white rounded-full shadow-sm" />
                          </button>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={() => {
                              const next = (panelMergeConfig?.panel_slaves || []).filter((_, i) => i !== idx)
                              const merged = {
                                panel_role: panelMergeConfig?.panel_role || 'master',
                                panel_slaves: next,
                                panel_master: panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                              }
                              setPanelMergeConfig(merged)
                              persistPanelMergeConfig(merged, { silent: true })
                            }}
                            className="p-2 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-all"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                    <tr className="bg-slate-50/30 dark:bg-slate-800/30">
                      <td className="px-4 py-3">
                        <input
                          value={newSlave.id}
                          onChange={(e) => setNewSlave({ ...newSlave, id: e.target.value })}
                          placeholder="s1"
                          className="w-28 px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          value={newSlave.name}
                          onChange={(e) => setNewSlave({ ...newSlave, name: e.target.value })}
                          placeholder="Server 1"
                          className="w-32 px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          value={newSlave.base_url}
                          onChange={(e) => setNewSlave({ ...newSlave, base_url: e.target.value })}
                          placeholder="http://127.0.0.1:8001"
                          className="min-w-[220px] px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          value={newSlave.token}
                          onChange={(e) => setNewSlave({ ...newSlave, token: e.target.value })}
                          placeholder="子服面板 token"
                          className="min-w-[240px] px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs font-mono"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => setNewSlave({ ...newSlave, enabled: !newSlave.enabled })}
                          className={`w-10 h-6 rounded-full p-1 transition-colors ${newSlave.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                        >
                          <motion.div animate={{ x: newSlave.enabled ? 16 : 0 }} className="w-4 h-4 bg-white rounded-full shadow-sm" />
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => {
                            if (!newSlave.id || !newSlave.base_url || !newSlave.token) {
                              showNotification(t('page.settings.multi_server.slave.required'), 'error')
                              return
                            }
                            const nextSlaves = [...(panelMergeConfig?.panel_slaves || []), newSlave]
                            const merged = {
                              panel_role: panelMergeConfig?.panel_role || 'master',
                              panel_slaves: nextSlaves,
                              panel_master: panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] },
                            }
                            setPanelMergeConfig(merged)
                            persistPanelMergeConfig(merged, { silent: true })
                            setNewSlave({ id: '', name: '', base_url: '', token: '', enabled: true, verify_tls: true })
                            showNotification(t('page.settings.multi_server.slave.added'), 'success')
                          }}
                          className="p-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 shadow-sm"
                        >
                          <Plus className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {panelMergeDirty && (
                <div className="flex justify-end">
                  <button
                    disabled={panelMergeLoading || !panelMergeConfig}
                    onClick={() => {
                      if (!panelMergeConfig) return
                      persistPanelMergeConfig(panelMergeConfig)
                    }}
                    className="flex items-center gap-2 px-6 py-2.5 bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:opacity-90 disabled:opacity-50 font-semibold rounded-2xl transition-all shadow-lg"
                  >
                    {panelMergeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    {t('page.settings.multi_server.save_changes')}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-sm font-bold text-slate-900 dark:text-white">
                {t('page.settings.multi_server.allowed_tokens')}
              </p>
              <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800">
                <table className="w-full text-sm text-left">
                  <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 font-medium">
                    <tr>
                      <th className="px-4 py-3">{t('page.settings.multi_server.token.name')}</th>
                      <th className="px-4 py-3">{t('page.settings.multi_server.token.token')}</th>
                      <th className="px-4 py-3">{t('page.settings.multi_server.token.enabled')}</th>
                      <th className="px-4 py-3 text-right">{t('page.settings.multi_server.actions')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {(panelMergeConfig?.panel_master?.allowed_tokens || []).map((it, idx) => (
                      <tr key={`${it.token}-${idx}`}>
                        <td className="px-4 py-3">
                          <input
                            value={it.name || ''}
                            onChange={(e) => {
                              const next = [...(panelMergeConfig?.panel_master?.allowed_tokens || [])]
                              next[idx] = { ...next[idx], name: e.target.value }
                              setPanelMergeConfig((prev) => ({
                                panel_role: prev?.panel_role || 'slave',
                                panel_slaves: prev?.panel_slaves || [],
                                panel_master: { ...(prev?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: next },
                              }))
                              setPanelMergeDirty(true)
                            }}
                            className="w-40 px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            value={it.token}
                            onChange={(e) => {
                              const next = [...(panelMergeConfig?.panel_master?.allowed_tokens || [])]
                              next[idx] = { ...next[idx], token: e.target.value }
                              setPanelMergeConfig((prev) => ({
                                panel_role: prev?.panel_role || 'slave',
                                panel_slaves: prev?.panel_slaves || [],
                                panel_master: { ...(prev?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: next },
                              }))
                              setPanelMergeDirty(true)
                            }}
                            className="min-w-[260px] px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs font-mono"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <button
                            onClick={() => {
                              const next = [...(panelMergeConfig?.panel_master?.allowed_tokens || [])]
                              next[idx] = { ...next[idx], enabled: !next[idx].enabled }
                              const merged = {
                                panel_role: panelMergeConfig?.panel_role || 'slave',
                                panel_slaves: panelMergeConfig?.panel_slaves || [],
                                panel_master: { ...(panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: next },
                              }
                              setPanelMergeConfig(merged)
                              persistPanelMergeConfig(merged, { silent: true })
                            }}
                            className={`w-10 h-6 rounded-full p-1 transition-colors ${it.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                          >
                            <motion.div animate={{ x: it.enabled ? 16 : 0 }} className="w-4 h-4 bg-white rounded-full shadow-sm" />
                          </button>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={() => {
                              const next = (panelMergeConfig?.panel_master?.allowed_tokens || []).filter((_, i) => i !== idx)
                              const merged = {
                                panel_role: panelMergeConfig?.panel_role || 'slave',
                                panel_slaves: panelMergeConfig?.panel_slaves || [],
                                panel_master: { ...(panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: next },
                              }
                              setPanelMergeConfig(merged)
                              persistPanelMergeConfig(merged, { silent: true })
                            }}
                            className="p-2 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-all"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                    <tr className="bg-slate-50/30 dark:bg-slate-800/30">
                      <td className="px-4 py-3">
                        <input
                          value={newAllowedToken.name || ''}
                          onChange={(e) => setNewAllowedToken({ ...newAllowedToken, name: e.target.value })}
                          placeholder="master"
                          className="w-40 px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          value={newAllowedToken.token}
                          onChange={(e) => setNewAllowedToken({ ...newAllowedToken, token: e.target.value })}
                          placeholder="面板 token"
                          className="min-w-[260px] px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs font-mono"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => setNewAllowedToken({ ...newAllowedToken, enabled: !newAllowedToken.enabled })}
                          className={`w-10 h-6 rounded-full p-1 transition-colors ${newAllowedToken.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                        >
                          <motion.div animate={{ x: newAllowedToken.enabled ? 16 : 0 }} className="w-4 h-4 bg-white rounded-full shadow-sm" />
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => {
                            if (!newAllowedToken.token) {
                              showNotification(t('page.settings.multi_server.token.required'), 'error')
                              return
                            }
                            const allowed = [...(panelMergeConfig?.panel_master?.allowed_tokens || []), newAllowedToken]
                            const merged = {
                              panel_role: panelMergeConfig?.panel_role || 'slave',
                              panel_slaves: panelMergeConfig?.panel_slaves || [],
                              panel_master: { ...(panelMergeConfig?.panel_master || { allowed_tokens: [], allowed_master_ips: [] }), allowed_tokens: allowed },
                            }
                            setPanelMergeConfig(merged)
                            persistPanelMergeConfig(merged, { silent: true })
                            setNewAllowedToken({ token: '', enabled: true, name: '' })
                            showNotification(t('page.settings.multi_server.token.added'), 'success')
                          }}
                          className="p-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 shadow-sm"
                        >
                          <Plus className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {panelMergeDirty && (
                <div className="flex justify-end">
                  <button
                    disabled={panelMergeLoading || !panelMergeConfig}
                    onClick={() => {
                      if (!panelMergeConfig) return
                      persistPanelMergeConfig(panelMergeConfig)
                    }}
                    className="flex items-center gap-2 px-6 py-2.5 bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:opacity-90 disabled:opacity-50 font-semibold rounded-2xl transition-all shadow-lg"
                  >
                    {panelMergeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    {t('page.settings.multi_server.save_changes')}
                  </button>
                </div>
              )}
            </div>
          ))}
        </motion.div>

        {/* Network & Account Section */}
        <div className="space-y-6">
          {/* Network Settings */}
          <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
            <div className="flex items-center gap-3 text-blue-500">
              <Network className="w-6 h-6" />
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                {t('page.settings.network.title')}
              </h2>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  {t('page.settings.network.host')}
                </label>
                <input
                  type="text"
                  value={config.host}
                  onChange={(e) => setConfig({ ...config, host: e.target.value })}
                  placeholder="127.0.0.1"
                  className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/60 transition-all outline-none"
                />
                <p className="text-xs text-slate-500">{t('page.settings.network.host_hint')}</p>
                <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">{t('page.settings.network.host_effect')}</p>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  {t('page.settings.network.port')}
                </label>
                <input
                  type="number"
                  value={config.port}
                  onChange={(e) => setConfig({ ...config, port: parseInt(e.target.value) || 0 })}
                  placeholder="8000"
                  className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/60 transition-all outline-none"
                />
                <p className="text-xs text-slate-500">{t('page.settings.network.port_hint')}</p>
                <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">{t('page.settings.network.port_effect')}</p>
              </div>
            </div>

            <div className="flex justify-end pt-2">
              <button
                onClick={() =>
                  handleSave('config', {
                    host: config.host,
                    port: String(config.port)
                  })
                }
                disabled={saving === 'config'}
                className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-blue-500/20"
              >
                {saving === 'config' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('page.settings.network.save')}
              </button>
            </div>
          </motion.div>

          {/* Account Settings */}
          <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
            <div className="flex items-center gap-3 text-purple-500">
              <User className="w-6 h-6" />
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                {t('page.settings.account.title')}
              </h2>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                {t('page.settings.account.super_admin')}
              </label>
              <input
                type="text"
                value={config.super_admin_account}
                onChange={(e) => setConfig({ ...config, super_admin_account: e.target.value })}
                className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-purple-500/60 transition-all outline-none"
              />
              <p className="text-xs text-slate-500">{t('page.settings.account.super_admin_hint')}</p>
              <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{t('page.settings.account.super_admin_effect')}</p>
            </div>

            <div className="flex justify-end pt-2">
              <button
                onClick={() =>
                  handleSave('config', {
                    super_account: String(config.super_admin_account)
                  })
                }
                disabled={saving === 'account'}
                className="flex items-center gap-2 px-6 py-2.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-purple-500/20"
              >
                {saving === 'account' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('page.settings.account.save')}
              </button>
            </div>
          </motion.div>

          {isSuperAdmin && (
            <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
              <div className="flex items-center gap-3 text-emerald-500">
                <Shield className="w-6 h-6" />
                <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                  {t('page.settings.mod_upload.title')}
                </h2>
              </div>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {t('page.settings.mod_upload.tip')}
              </p>
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  {t('page.settings.mod_upload.limit')}
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={4096}
                    step={1}
                    value={modUploadMaxMiB ?? ''}
                    disabled={modSettingsLoading}
                    onChange={(event) => setModUploadMaxMiB(event.target.value === '' ? null : Number(event.target.value))}
                    className="w-28 px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-emerald-500/60 transition-all outline-none"
                  />
                  <span className="text-sm text-slate-500">MiB</span>
                </div>
                <p className="text-xs text-slate-500">{t('page.settings.mod_upload.range')}</p>
              </div>
              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  onClick={() => void saveModSettings()}
                  disabled={modSettingsLoading || modSettingsSaving || modUploadMaxMiB === null}
                  className="flex items-center gap-2 px-6 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-emerald-500/20"
                >
                  {modSettingsSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {t('page.settings.mod_upload.save')}
                </button>
              </div>
            </motion.div>
          )}

          {/* Two-factor authentication (per account) */}
          <motion.div variants={itemVariants}>
            <TwoFactorCard />
          </motion.div>

          {/* Security Settings */}
          <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
            <div className="flex items-center gap-3 text-rose-500">
              <Lock className="w-6 h-6" />
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                {t('page.settings.security.title')}
              </h2>
            </div>

            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                <div>
                  <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.security.disable_admin_web')}</p>
                  <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">{t('page.settings.security.disable_admin_web_effect')}</p>
                </div>
                <button
                  onClick={() => toggleSetting('disable_admin_login_web')}
                  className={`w-12 h-6 rounded-full p-1 transition-colors ${config.disable_admin_login_web ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                >
                  <motion.div
                    animate={{ x: config.disable_admin_login_web ? 24 : 0 }}
                    className="w-4 h-4 bg-white rounded-full shadow-sm"
                  />
                </button>
              </div>

              <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                <div>
                  <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.security.enable_temp_code')}</p>
                  <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">{t('page.settings.security.enable_temp_code_effect')}</p>
                </div>
                <button
                  onClick={() => toggleSetting('enable_temp_login_password')}
                  className={`w-12 h-6 rounded-full p-1 transition-colors ${config.enable_temp_login_password ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                >
                  <motion.div
                    animate={{ x: config.enable_temp_login_password ? 24 : 0 }}
                    className="w-4 h-4 bg-white rounded-full shadow-sm"
                  />
                </button>
              </div>

              <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                <div>
                  <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.security.enable_qq_qr')}</p>
                  <p className="text-xs text-slate-500">{t('page.settings.security.enable_qq_qr_desc')}</p>
                  <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">{t('page.settings.security.enable_temp_code_effect')}</p>
                </div>
                <button
                  onClick={() => toggleSetting('qq_qr_login_enabled')}
                  className={`w-12 h-6 rounded-full p-1 transition-colors ${config.qq_qr_login_enabled !== false ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                >
                  <motion.div
                    animate={{ x: config.qq_qr_login_enabled !== false ? 24 : 0 }}
                    className="w-4 h-4 bg-white rounded-full shadow-sm"
                  />
                </button>
              </div>

              <div className="p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl space-y-2">
                <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.security.trusted_proxies')}</p>
                <p className="text-xs text-slate-500">{t('page.settings.security.trusted_proxies_desc')}</p>
                <textarea
                  rows={2}
                  value={(config.trusted_proxies ?? []).join('\n')}
                  onChange={(e) => setConfig({ ...config, trusted_proxies: e.target.value.split('\n') })}
                  placeholder={'127.0.0.1\n::1'}
                  className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white font-mono text-sm focus:ring-2 focus:ring-rose-500/60 transition-all outline-none"
                />
              </div>

              <div className="p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl space-y-2">
                <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.security.allowed_origins')}</p>
                <p className="text-xs text-slate-500">{t('page.settings.security.allowed_origins_desc')}</p>
                <textarea
                  rows={2}
                  value={(config.allowed_origins ?? []).join('\n')}
                  onChange={(e) => setConfig({ ...config, allowed_origins: e.target.value.split('\n') })}
                  placeholder="https://mc.example.com"
                  className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white font-mono text-sm focus:ring-2 focus:ring-rose-500/60 transition-all outline-none"
                />
              </div>

              <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                <div>
                  <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.security.force_standalone')}</p>
                  <p className="text-xs text-slate-500">{t('page.settings.security.force_standalone_desc')}</p>
                  <p className="text-xs text-amber-600 dark:text-amber-400 font-medium mt-1">{t('page.settings.security.force_standalone_effect')}</p>
                </div>
                <button
                  onClick={() => setConfig({ ...config, force_standalone: !config.force_standalone })}
                  className={`w-12 h-6 rounded-full p-1 transition-colors ${config.force_standalone ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
                >
                  <motion.div
                    animate={{ x: config.force_standalone ? 24 : 0 }}
                    className="w-4 h-4 bg-white rounded-full shadow-sm"
                  />
                </button>
              </div>
            </div>
            <div className="flex justify-end pt-2">
              <button
                onClick={() =>
                  handleSave('security', {
                    force_standalone: config.force_standalone,
                    trusted_proxies: (config.trusted_proxies ?? []).map((x) => x.trim()).filter(Boolean),
                    allowed_origins: (config.allowed_origins ?? []).map((x) => x.trim()).filter(Boolean),
                  })
                }
                disabled={saving === 'security'}
                className="flex items-center gap-2 px-6 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-rose-500/20"
              >
                {saving === 'security' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('page.settings.security.save')}
              </button>
            </div>
          </motion.div>
        </div>

        {/* SSL & AI Section */}
        <div className="space-y-6">
          {/* HTTPS Settings */}
          <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
            <div className="flex items-center gap-3 text-emerald-500">
              <Shield className="w-6 h-6" />
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                {t('page.settings.https.title')}
              </h2>
            </div>

            <div className="p-4 bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/20 rounded-2xl flex gap-3">
              <Info className="w-5 h-5 text-blue-500 shrink-0" />
              <div className="text-xs text-blue-700 dark:text-blue-300 space-y-1">
                <p className="font-bold">{t('page.settings.https.ssl_tip_title')}</p>
                <ul className="list-disc pl-4 space-y-1">
                  <li>{t('page.settings.https.ssl_tip_1')}</li>
                  <li>{t('page.settings.https.ssl_tip_4')}</li>
                </ul>
              </div>
            </div>

            <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
              <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.https.enable')}</p>
              <button
                onClick={() => setConfig({ ...config, ssl_enabled: !config.ssl_enabled })}
                className={`w-12 h-6 rounded-full p-1 transition-colors ${config.ssl_enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
              >
                <motion.div
                  animate={{ x: config.ssl_enabled ? 24 : 0 }}
                  className="w-4 h-4 bg-white rounded-full shadow-sm"
                />
              </button>
            </div>

            <AnimatePresence>
              {config.ssl_enabled && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="space-y-4 overflow-hidden"
                >
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.https.cert')}</label>
                    <input
                      type="text"
                      value={config.ssl_certfile}
                      onChange={(e) => setConfig({ ...config, ssl_certfile: e.target.value })}
                      placeholder="./ssl/cert.pem"
                      className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-emerald-500/60 transition-all outline-none"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.https.key')}</label>
                    <input
                      type="text"
                      value={config.ssl_keyfile}
                      onChange={(e) => setConfig({ ...config, ssl_keyfile: e.target.value })}
                      placeholder="./ssl/key.pem"
                      className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-emerald-500/60 transition-all outline-none"
                    />
                  </div>
                  <form
                    className="space-y-2"
                    onSubmit={(e) => {
                      e.preventDefault()
                    }}
                  >
                    {/* 隐藏用户名字段以满足浏览器对密码表单的可访问性要求 */}
                    <input
                      type="text"
                      autoComplete="username"
                      tabIndex={-1}
                      aria-hidden="true"
                      className="hidden"
                    />
                    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.https.key_pass')}</label>
                    <input
                      type="password"
                      value={config.ssl_keyfile_password || ''}
                      onChange={(e) => setConfig({ ...config, ssl_keyfile_password: e.target.value })}
                      placeholder={t('page.settings.https.placeholder_key_pass')}
                      autoComplete="new-password"
                      className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-emerald-500/60 transition-all outline-none"
                    />
                  </form>
                </motion.div>
              )}
            </AnimatePresence>

            <div className="flex justify-end pt-2">
              <button
                onClick={() => handleSave('config', {
                  ssl_enabled: config.ssl_enabled,
                  ssl_certfile: config.ssl_certfile,
                  ssl_keyfile: config.ssl_keyfile,
                  ssl_keyfile_password: config.ssl_keyfile_password
                })}
                disabled={saving === 'ssl'}
                className="flex items-center gap-2 px-6 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-emerald-500/20"
              >
                {saving === 'ssl' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('page.settings.https.save')}
              </button>
            </div>
          </motion.div>

          {/* AI Settings */}
          <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
            <div className="flex items-center gap-3 text-amber-500">
              <Bot className="w-6 h-6" />
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                {t('page.settings.ai.title')}
              </h2>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.ai.api_url')}</label>
                <input
                  type="text"
                  value={config.ai_api_url}
                  onChange={(e) => setConfig({ ...config, ai_api_url: e.target.value })}
                  placeholder="https://api.deepseek.com/chat/completions"
                  className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-amber-500/60 transition-all outline-none"
                />
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{t('page.settings.ai.api_url_effect')}</p>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.ai.model')}</label>
                <input
                  type="text"
                  value={config.ai_model}
                  onChange={(e) => setConfig({ ...config, ai_model: e.target.value })}
                  placeholder="deepseek-chat"
                  className="w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-amber-500/60 transition-all outline-none"
                />
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{t('page.settings.ai.model_effect')}</p>
              </div>
              <form
                className="space-y-2"
                onSubmit={(e) => {
                  e.preventDefault()
                }}
              >
                {/* 隐藏用户名字段以满足浏览器对密码表单的可访问性要求 */}
                <input
                  type="text"
                  autoComplete="username"
                  tabIndex={-1}
                  aria-hidden="true"
                  className="hidden"
                />
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.ai.api_key')}</label>
                <div className="relative">
                  <input
                    type={showAiKey ? 'text' : 'password'}
                    value={aiApiKey}
                    onChange={(e) => setAiApiKey(e.target.value)}
                    placeholder={config.ai_api_key_configured ? '••••••••••••••••' : t('page.settings.ai.placeholder_api_key')}
                    autoComplete="new-password"
                    className="w-full px-4 py-2.5 pr-12 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-amber-500/60 transition-all outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => setShowAiKey(!showAiKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                  >
                    {showAiKey ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                  </button>
                </div>
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{t('page.settings.ai.api_key_effect')}</p>
                <div className="flex justify-end gap-2 mt-2">
                  <button
                    type="button"
                    onClick={validateAiKey}
                    disabled={validatingAiKey || !aiApiKey}
                    className="flex items-center gap-2 px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 text-sm font-medium rounded-xl transition-all"
                  >
                    {validatingAiKey ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                    {t('page.settings.ai.validate')}
                  </button>
                </div>
              </form>
            </div>

            <div className="flex justify-end pt-2">
              <button
                onClick={() => handleSave('config', { ai_api_key: aiApiKey || undefined, ai_api_url: config.ai_api_url, ai_model: config.ai_model })}
                disabled={saving === 'ai'}
                className="flex items-center gap-2 px-6 py-2.5 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-amber-500/20"
              >
                {saving === 'ai' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('page.settings.ai.save')}
              </button>
            </div>
          </motion.div>
        </div>

        {/* Repositories Section */}
        <motion.div variants={itemVariants} className="lg:col-span-2 bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
          <div className="flex items-center gap-3 text-cyan-500">
            <Database className="w-6 h-6" />
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              {t('page.settings.repo.title')}
            </h2>
          </div>

          <p className="text-sm text-slate-500">{t('page.settings.repo.tip')}</p>
          <p className="text-xs text-amber-600 dark:text-amber-400 font-medium mt-1">{t('page.settings.repo.effect')}</p>

          <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800">
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 font-medium">
                <tr>
                  <th className="px-6 py-4">{t('page.settings.repo.name')}</th>
                  <th className="px-6 py-4">{t('page.settings.repo.url')}</th>
                  <th className="px-6 py-4 text-right">{t('page.settings.repo.actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {/* Official */}
                <tr className="bg-blue-50/30 dark:bg-blue-900/10">
                  <td className="px-6 py-4 font-bold text-slate-900 dark:text-white flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-blue-500" />
                    {t('page.settings.repo.official')}
                  </td>
                  <td className="px-6 py-4 text-slate-500 truncate max-w-xs">{config.mcdr_plugins_url}</td>
                  <td className="px-6 py-4 text-right">
                    <span className="text-xs font-medium text-slate-400 bg-slate-100 dark:bg-slate-800 px-2 py-1 rounded-full">
                      {t('page.settings.repo.default')}
                    </span>
                  </td>
                </tr>
                {/* Loose Repository (URL from get_web_config) */}
                <tr className="bg-blue-50/30 dark:bg-blue-900/10">
                  <td className="px-6 py-4 font-bold text-slate-900 dark:text-white flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-blue-500" />
                    {t('page.settings.repo.loose_repo')}
                  </td>
                  <td className="px-6 py-4 text-slate-500 truncate max-w-xs">{config.pf_plugin_catalogue_url ?? ''}</td>
                  <td className="px-6 py-4 text-right">
                    <span className="text-xs font-medium text-slate-400 bg-slate-100 dark:bg-slate-800 px-2 py-1 rounded-full">
                      {t('page.settings.repo.default')}
                    </span>
                  </td>
                </tr>
                {/* Custom Repos */}
                {config.repositories.map((repo, idx) => (
                  <tr key={idx}>
                    <td className="px-6 py-4 text-slate-900 dark:text-white font-medium">{repo.name}</td>
                    <td className="px-6 py-4 text-slate-500 truncate max-w-xs">{repo.url}</td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => deleteRepository(idx)}
                        className="p-2 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-all"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
                {/* Add New */}
                <tr className="bg-slate-50/30 dark:bg-slate-800/30">
                  <td className="px-6 py-3">
                    <input
                      value={newRepo.name}
                      onChange={(e) => setNewRepo({ ...newRepo, name: e.target.value })}
                      placeholder={t('page.settings.repo.placeholder_name')}
                      className="w-full px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                    />
                  </td>
                  <td className="px-6 py-3">
                    <input
                      value={newRepo.url}
                      onChange={(e) => setNewRepo({ ...newRepo, url: e.target.value })}
                      placeholder={t('page.settings.repo.placeholder_url')}
                      className="w-full px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                    />
                  </td>
                  <td className="px-6 py-3 text-right">
                    <button
                      onClick={addRepository}
                      className="p-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 shadow-sm"
                    >
                      <Plus className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="flex justify-end pt-2">
            <button
              onClick={() => handleSave('config', { repositories: config.repositories })}
              disabled={saving === 'repos'}
              className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-blue-500/20"
            >
              {saving === 'repos' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {t('page.settings.repo.save')}
            </button>
          </div>
        </motion.div>

        {/* ICP Records Section */}
        <motion.div variants={itemVariants} className="lg:col-span-2 bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
          <div className="flex items-center gap-3 text-slate-500">
            <Shield className="w-6 h-6" />
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              {t('page.settings.icp.title')}
            </h2>
          </div>

          <p className="text-sm text-slate-500">{t('page.settings.icp.tip')}</p>
          <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">{t('page.settings.icp.effect')}</p>

          <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800">
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 font-medium">
                <tr>
                  <th className="px-6 py-4">{t('page.settings.icp.text')}</th>
                  <th className="px-6 py-4">{t('page.settings.icp.url')}</th>
                  <th className="px-6 py-4 text-right">{t('page.settings.icp.actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {config.icp_records.map((record, idx) => (
                  <tr key={idx}>
                    <td className="px-6 py-4 text-slate-900 dark:text-white font-medium">{record.icp}</td>
                    <td className="px-6 py-4 text-slate-500 truncate max-w-xs">{record.url}</td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => deleteIcpRecord(idx)}
                        className="p-2 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-all"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
                {config.icp_records.length < 2 && (
                  <tr className="bg-slate-50/30 dark:bg-slate-800/30">
                    <td className="px-6 py-3">
                      <input
                        value={newIcp.icp}
                        onChange={(e) => setNewIcp({ ...newIcp, icp: e.target.value })}
                        placeholder={t('page.settings.icp.placeholder_text')}
                        className="w-full px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                      />
                    </td>
                    <td className="px-6 py-3">
                      <input
                        value={newIcp.url}
                        onChange={(e) => setNewIcp({ ...newIcp, url: e.target.value })}
                        placeholder={t('page.settings.icp.placeholder_url')}
                        className="w-full px-3 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-xs"
                      />
                    </td>
                    <td className="px-6 py-3 text-right">
                      <button
                        onClick={addIcpRecord}
                        className="p-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 shadow-sm"
                      >
                        <Plus className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex justify-end pt-2">
            <button
              onClick={() => handleSave('config', { icp_records: config.icp_records })}
              disabled={saving === 'icp'}
              className="flex items-center gap-2 px-6 py-2.5 bg-slate-600 hover:bg-slate-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-slate-500/20"
            >
              {saving === 'icp' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {t('page.settings.icp.save')}
            </button>
          </div>
        </motion.div>

        {/* Public Chat Section */}
        <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
          <div className="flex items-center gap-3 text-sky-500">
            <MessageSquare className="w-6 h-6" />
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              {t('page.settings.public_chat.title')}
            </h2>
          </div>

          <div className="p-4 bg-sky-50 dark:bg-sky-900/10 border border-sky-100 dark:border-sky-900/20 rounded-2xl flex gap-3">
            <Info className="w-5 h-5 text-sky-500 shrink-0" />
            <p className="text-xs text-sky-700 dark:text-sky-300">{t('page.settings.public_chat.tip')}</p>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
              <div>
                <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.public_chat.enable')}</p>
                <p className="text-xs text-slate-500">{t('page.settings.public_chat.enable_desc')}</p>
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">{t('page.settings.public_chat.enable_effect')}</p>
              </div>
              <button
                onClick={() => setConfig({ ...config, public_chat_enabled: !config.public_chat_enabled })}
                className={`w-12 h-6 rounded-full p-1 transition-colors ${config.public_chat_enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
              >
                <motion.div
                  animate={{ x: config.public_chat_enabled ? 24 : 0 }}
                  className="w-4 h-4 bg-white rounded-full shadow-sm"
                />
              </button>
            </div>

            <div className={`flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl transition-opacity ${!config.public_chat_enabled ? 'opacity-50' : 'opacity-100'}`}>
              <div>
                <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.public_chat.enable_to_game')}</p>
                <p className="text-xs text-slate-500">{t('page.settings.public_chat.enable_to_game_desc')}</p>
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">{t('page.settings.public_chat.enable_to_game_effect')}</p>
              </div>
              <button
                disabled={!config.public_chat_enabled}
                onClick={() => setConfig({ ...config, public_chat_to_game_enabled: !config.public_chat_to_game_enabled })}
                className={`w-12 h-6 rounded-full p-1 transition-colors ${config.public_chat_to_game_enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-slate-700'}`}
              >
                <motion.div
                  animate={{ x: config.public_chat_to_game_enabled ? 24 : 0 }}
                  className="w-4 h-4 bg-white rounded-full shadow-sm"
                />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.public_chat.verification_expire.title')}</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={config.chat_verification_expire_minutes}
                    onChange={(e) => setConfig({ ...config, chat_verification_expire_minutes: parseInt(e.target.value) || 0 })}
                    className="w-20 px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-sm outline-none"
                  />
                  <span className="text-xs text-slate-500">{t('page.settings.public_chat.verification_expire.unit_min')}</span>
                </div>
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{t('page.settings.public_chat.verification_expire.effect')}</p>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('page.settings.public_chat.session_expire.title')}</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={config.chat_session_expire_hours}
                    onChange={(e) => setConfig({ ...config, chat_session_expire_hours: parseInt(e.target.value) || 0 })}
                    className="w-20 px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-sm outline-none"
                  />
                  <span className="text-xs text-slate-500">{t('page.settings.public_chat.session_expire.unit_hour')}</span>
                </div>
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{t('page.settings.public_chat.session_expire.effect')}</p>
              </div>
            </div>

            <div className="p-4 bg-rose-50 dark:bg-rose-900/10 border border-rose-100 dark:border-rose-900/20 rounded-2xl">
              <p className="font-bold text-rose-900 dark:text-rose-300 text-sm mb-2">{t('page.settings.public_chat.history.title')}</p>
              <div className="flex items-center justify-between">
                <div className="text-xs text-rose-700 dark:text-rose-400">
                  {t('page.settings.public_chat.history.count_prefix')}{config.chat_message_count}{t('page.settings.public_chat.history.count_suffix')}
                </div>
                <button
                  onClick={() => setClearHistoryConfirm(true)}
                  className="flex items-center gap-2 px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold rounded-xl shadow-lg shadow-rose-500/20 transition-all"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  {t('page.settings.public_chat.history.clear')}
                </button>
              </div>
            </div>
            <ConfirmModal isOpen={clearHistoryConfirm} onCancel={() => setClearHistoryConfirm(false)} onConfirm={async () => { setClearHistoryConfirm(false); await api.delete('/chat/messages'); await fetchConfig() }} title={t('page.settings.public_chat.history.clear')} message={`${t('page.settings.public_chat.history.clear')}?`} confirmLabel={t('common.confirm')} cancelLabel={t('common.cancel')} danger />
          </div>

          <div className="flex justify-end pt-2">
            <button
              onClick={() => handleSave('config', {
                public_chat_enabled: config.public_chat_enabled,
                public_chat_to_game_enabled: config.public_chat_to_game_enabled,
                chat_verification_expire_minutes: config.chat_verification_expire_minutes,
                chat_session_expire_hours: config.chat_session_expire_hours,
                chat_message_count: config.chat_message_count
              })}
              disabled={saving === 'chat'}
              className="flex items-center gap-2 px-6 py-2.5 bg-sky-600 hover:bg-sky-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-sky-500/20"
            >
              {saving === 'chat' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {t('page.settings.public_chat.save')}
            </button>
          </div>
        </motion.div>

        {/* PIM Section */}
        <motion.div variants={itemVariants} className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
          <div className="flex items-center gap-3 text-indigo-500">
            <Plug className="w-6 h-6" />
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              {t('page.settings.pim.title')}
            </h2>
          </div>

          <div className="p-4 bg-indigo-50 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-900/20 rounded-2xl">
            <p className="text-xs text-indigo-700 dark:text-indigo-300 leading-relaxed">
              {t('page.settings.pim.desc')}
            </p>
          </div>

          <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
            <div className="flex items-center gap-2">
              <p className="font-bold text-slate-900 dark:text-white">{t('page.settings.pim.install_title')}</p>
              {pimStatus === 'installed' && <CheckCircle className="w-4 h-4 text-emerald-500" />}
            </div>

            <button
              disabled={pimStatus === 'installed' || pimStatus === 'installing'}
              onClick={installPim}
              className={`flex items-center gap-2 px-6 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-indigo-500/20`}
            >
              {pimStatus === 'installing' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {pimStatus === 'installed' ? t('common.installed') : t('common.install')}
            </button>
          </div>

          {pimStatus === 'installed' && (
            <div className="p-4 bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-100 dark:border-emerald-900/20 rounded-2xl flex gap-3">
              <CheckCircle className="w-5 h-5 text-emerald-500 shrink-0" />
              <p className="text-xs text-emerald-700 dark:text-emerald-300">{t('page.settings.pim.installed_tip')}</p>
            </div>
          )}
        </motion.div>
      </motion.div>

      {/* Notifications */}
      <AnimatePresence>
        {notification && (
          <motion.div
            initial={{ opacity: 0, y: 50, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.9 }}
            className={`fixed bottom-8 right-8 z-50 flex items-center gap-3 px-6 py-4 rounded-3xl shadow-2xl border ${notification.type === 'success'
              ? 'bg-emerald-500 border-emerald-400 text-white'
              : 'bg-rose-500 border-rose-400 text-white'
              }`}
          >
            {notification.type === 'success' ? <CheckCircle className="w-6 h-6" /> : <AlertCircle className="w-6 h-6" />}
            <span className="font-bold">{notification.message}</span>
            <button onClick={() => setNotification(null)} className="ml-4 p-1 hover:bg-white/20 rounded-full transition-colors">
              <X className="w-4 h-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default Settings
