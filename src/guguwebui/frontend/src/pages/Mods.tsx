import {
  AlertTriangle,
  ArrowLeft,
  Boxes,
  ChevronRight,
  FileCode2,
  FilePlus2,
  FileText,
  Info,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings,
  Trash2,
  Upload,
  Power,
} from 'lucide-react'
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CodeMirrorEditor } from '../components/CodeMirrorEditor'
import { ConfigForm, type GenericConfigObject } from '../components/ConfigForm'
import { Modal } from '../components/Modal'
import { ModIcon } from '../components/ModIcon'
import { NiceSelect } from '../components/NiceSelect'
import { ConfigFileRowSkeleton, ModRowSkeleton } from '../components/Skeleton'
import { useAuth } from '../hooks/useAuth'
import api, { unwrapData } from '../utils/api'
import { formatEpoch } from '../utils/format'

type Warning = { code: string; message: string }
type Dependency = { id: string; version?: string; mandatory?: boolean }
type Mod = {
  filename: string
  enabled: boolean
  id: string
  name: string
  version: string
  authors: string[]
  description: string
  loader: string
  environment: string
  dependencies: Dependency[]
  conflicts: Dependency[]
  size: number
  modified_at: number
  recognized: boolean
  parse_error?: string | null
  has_icon?: boolean
  config_count?: number
  file_conflict?: boolean
  warnings: Warning[]
}
type ConfigFile = { path: string; name: string; root: string; association?: string | null; structured: boolean; size: number }

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`
}

const errorMessage = (error: unknown) => {
  const response = (error as { response?: { data?: { message?: string; data?: { warnings?: Warning[] } } } }).response
  return { message: response?.data?.message || '', warnings: response?.data?.data?.warnings || [] }
}

const Mods: React.FC = () => {
  const { t, i18n } = useTranslation()
  const { isSuperAdmin } = useAuth()
  const [mods, setMods] = useState<Mod[]>([])
  const [trash, setTrash] = useState<Array<{ id: string; filename: string; enabled: boolean; deleted_at: number }>>([])
  const [loading, setLoading] = useState(true)
  const [trashLoading, setTrashLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<'all' | 'enabled' | 'disabled'>('all')
  const [loader, setLoader] = useState('all')
  const [view, setView] = useState<'mods' | 'trash'>('mods')
  const [detailMod, setDetailMod] = useState<Mod | null>(null)
  const [configMod, setConfigMod] = useState<Mod | null>(null)
  const [needsRestart, setNeedsRestart] = useState(false)
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadEnabled, setUploadEnabled] = useState(true)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadLimit, setUploadLimit] = useState<number | null>(null)
  const [uploadWarnings, setUploadWarnings] = useState<Warning[]>([])
  const [confirmUploadWarnings, setConfirmUploadWarnings] = useState(false)
  const [configFiles, setConfigFiles] = useState<ConfigFile[]>([])
  const [configAssociatedOnly, setConfigAssociatedOnly] = useState(true)
  const [selectedConfig, setSelectedConfig] = useState<ConfigFile | null>(null)
  const [configContent, setConfigContent] = useState('')
  const [configData, setConfigData] = useState<GenericConfigObject | null>(null)
  const [configMode, setConfigMode] = useState<'form' | 'code'>('code')
  const [configDirty, setConfigDirty] = useState(false)
  const [configLoading, setConfigLoading] = useState(false)

  const showNotice = useCallback((text: string, type: 'success' | 'error') => {
    setNotice({ text, type })
    window.setTimeout(() => setNotice(null), 5000)
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const response = await api.get('/mods')
      const data = unwrapData<{ mods?: Mod[]; server_running?: boolean }>(response, {})
      setMods(data.mods || [])
      if (data.server_running) setNeedsRestart((current) => current)
    } catch (error) {
      showNotice(errorMessage(error).message || t('page.mods.load_failed'), 'error')
    } finally {
      setLoading(false)
    }
  }, [showNotice, t])

  const refreshTrash = useCallback(async () => {
    setTrashLoading(true)
    try {
      const response = await api.get('/mods/trash')
      setTrash(unwrapData<{ items?: typeof trash }>(response, {}).items || [])
    } catch (error) {
      showNotice(errorMessage(error).message || t('page.mods.load_failed'), 'error')
    } finally {
      setTrashLoading(false)
    }
  }, [showNotice, t])

  useEffect(() => {
    void refresh()
    void refreshTrash()
    api.get('/mods/settings')
      .then((response) => setUploadLimit(unwrapData<{ upload_max_mib?: number }>(response, {}).upload_max_mib || null))
      .catch(() => undefined)
  }, [refresh, refreshTrash])

  const loaders = useMemo(() => ['all', ...Array.from(new Set(mods.map((mod) => mod.loader).filter(Boolean)))], [mods])
  const filteredMods = useMemo(() => mods.filter((mod) => {
    const haystack = `${mod.filename} ${mod.id} ${mod.name} ${mod.authors.join(' ')}`.toLowerCase()
    return (!query || haystack.includes(query.toLowerCase())) &&
      (status === 'all' || (status === 'enabled' ? mod.enabled : !mod.enabled)) &&
      (loader === 'all' || mod.loader === loader)
  }), [loader, mods, query, status])

  const operationStateOf = (response: { data: unknown }) =>
    unwrapData<{ needs_restart?: boolean; warnings?: Warning[] }>(response, {})

  const applyOperationState = (data: { needs_restart?: boolean; warnings?: Warning[] }) => {
    if (data.needs_restart) setNeedsRestart(true)
  }

  const closeConfigModal = () => {
    setConfigMod(null)
    setConfigFiles([])
    setSelectedConfig(null)
    setConfigContent('')
    setConfigData(null)
    setConfigDirty(false)
    setConfigLoading(false)
  }

  const backToConfigFileList = () => {
    setSelectedConfig(null)
    setConfigContent('')
    setConfigData(null)
    setConfigDirty(false)
  }

  const closeModPanels = () => {
    setDetailMod(null)
    closeConfigModal()
  }

  const switchView = (nextView: 'mods' | 'trash') => {
    if (view === nextView) return
    closeModPanels()
    setView(nextView)
  }

  const toggleMod = async (mod: Mod, acknowledge = false) => {
    try {
      const response = await api.put(`/mods/${encodeURIComponent(mod.filename)}/enabled`, {
        enabled: !mod.enabled,
        acknowledge_warnings: acknowledge,
      })
      applyOperationState(operationStateOf(response))
      showNotice(t('page.mods.toggle_success'), 'success')
      await refresh()
      if (detailMod?.filename === mod.filename) setDetailMod(null)
    } catch (error) {
      const result = errorMessage(error)
      if ((error as { response?: { status?: number } }).response?.status === 409 && result.warnings.length > 0 && window.confirm(`${result.message}\n\n${result.warnings.map((warning) => warning.message).join('\n')}\n\n${t('page.mods.confirm_warning')}`)) {
        await toggleMod(mod, true)
      } else showNotice(result.message || t('page.mods.operation_failed'), 'error')
    }
  }

  const trashMod = async (mod: Mod) => {
    if (!window.confirm(t('page.mods.delete_confirm'))) return
    try {
      const response = await api.post('/mods/trash', { filename: mod.filename })
      applyOperationState(operationStateOf(response))
      showNotice(t('page.mods.delete_success'), 'success')
      setDetailMod(null)
      await Promise.all([refresh(), refreshTrash()])
    } catch (error) { showNotice(errorMessage(error).message || t('page.mods.operation_failed'), 'error') }
  }

  const upload = async (acknowledge = confirmUploadWarnings) => {
    if (!uploadFile) return
    const form = new FormData()
    form.append('file', uploadFile)
    form.append('enabled', String(uploadEnabled))
    form.append('acknowledge_warnings', String(acknowledge))
    try {
      const response = await api.post('/mods/upload', form, {
        onUploadProgress: (event) => setUploadProgress(event.total ? Math.round((event.loaded / event.total) * 100) : 0),
      })
      applyOperationState(operationStateOf(response))
      setUploadOpen(false)
      setUploadFile(null)
      setUploadWarnings([])
      setConfirmUploadWarnings(false)
      showNotice(t('page.mods.upload_success'), 'success')
      await refresh()
    } catch (error) {
      const result = errorMessage(error)
      if ((error as { response?: { status?: number } }).response?.status === 409 && result.warnings.length > 0) {
        setUploadWarnings(result.warnings)
        setConfirmUploadWarnings(false)
      } else showNotice(result.message || t('page.mods.operation_failed'), 'error')
    } finally { setUploadProgress(0) }
  }

  const openConfigs = async (mod: Mod) => {
    setDetailMod(null)
    setConfigMod(mod)
    setConfigLoading(true)
    setConfigFiles([])
    setSelectedConfig(null)
    setConfigContent('')
    setConfigData(null)
    setConfigDirty(false)
    try {
      const response = await api.get('/mods/configs', { params: { mod_id: mod.id, associated_only: configAssociatedOnly } })
      setConfigFiles(unwrapData<{ files?: ConfigFile[] }>(response, {}).files || [])
    } catch (error) { showNotice(errorMessage(error).message || t('page.mods.config_load_failed'), 'error') }
    finally { setConfigLoading(false) }
  }

  const switchConfigScope = async (associatedOnly: boolean) => {
    if (!configMod) return
    setConfigAssociatedOnly(associatedOnly)
    setConfigLoading(true)
    try {
      const response = await api.get('/mods/configs', {
        params: { mod_id: configMod.id, associated_only: associatedOnly },
      })
      setConfigFiles(unwrapData<{ files?: ConfigFile[] }>(response, {}).files || [])
      backToConfigFileList()
    } catch (error) {
      showNotice(errorMessage(error).message || t('page.mods.config_load_failed'), 'error')
    } finally {
      setConfigLoading(false)
    }
  }

  const openConfigFile = async (file: ConfigFile, requestedMode?: 'form' | 'code') => {
    setConfigLoading(true)
    try {
      const response = await api.get('/mods/config', { params: { path: file.path } })
      const doc = unwrapData<{ content?: string; config_data?: GenericConfigObject }>(response, {})
      setSelectedConfig(file)
      setConfigContent(doc?.content || '')
      const data = doc?.config_data && typeof doc.config_data === 'object' ? doc.config_data as GenericConfigObject : null
      setConfigData(data)
      setConfigMode(requestedMode === 'form' && data ? 'form' : requestedMode || (data ? 'form' : 'code'))
      setConfigDirty(false)
    } catch (error) { showNotice(errorMessage(error).message || t('page.mods.config_load_failed'), 'error') }
    finally { setConfigLoading(false) }
  }

  const saveConfig = async () => {
    if (!selectedConfig) return
    let content: string | null = configContent
    let data: GenericConfigObject | null = null
    if (configMode === 'form' && configData) { content = null; data = configData }
    try {
      const response = await api.put('/mods/config', { path: selectedConfig.path, content, config_data: data })
      applyOperationState(operationStateOf(response))
      setConfigDirty(false)
      showNotice(t('page.mods.config_saved'), 'success')
    } catch (error) { showNotice(errorMessage(error).message || t('page.mods.config_save_failed'), 'error') }
  }

  const restoreTrash = async (id: string) => {
    try { const response = await api.post(`/mods/trash/${id}/restore`); applyOperationState(operationStateOf(response)); await Promise.all([refresh(), refreshTrash()]); showNotice(t('page.mods.restore_success'), 'success') }
    catch (error) { showNotice(errorMessage(error).message || t('page.mods.operation_failed'), 'error') }
  }

  const purgeTrash = async (id: string) => {
    if (!window.confirm(t('page.mods.purge_confirm'))) return
    try { await api.delete(`/mods/trash/${id}`, { params: { confirm: true } }); await refreshTrash(); showNotice(t('page.mods.purge_success'), 'success') }
    catch (error) { showNotice(errorMessage(error).message || t('page.mods.operation_failed'), 'error') }
  }

  return (
    <div className="space-y-5 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-2"><Boxes className="w-7 h-7 text-blue-600" />{t('page.mods.title')}</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">{t('page.mods.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => setUploadOpen(true)} className="inline-flex items-center gap-2 px-3 py-2 text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-lg" title={t('page.mods.upload')}><Upload className="w-4 h-4" />{t('page.mods.upload')}</button>
          <button type="button" onClick={() => { void refresh(); void refreshTrash() }} className="p-2 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800" title={t('common.refresh')}><RefreshCw className="w-4 h-4" /></button>
        </div>
      </div>

      {needsRestart && <div className="flex items-start gap-2 p-3 border border-amber-200 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-800 text-amber-800 dark:text-amber-200 rounded-lg text-sm"><AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" /><span>{t('page.mods.restart_tip')}</span></div>}
      {notice && <div className={`p-3 rounded-lg text-sm border ${notice.type === 'success' ? 'bg-emerald-50 border-emerald-200 text-emerald-700 dark:bg-emerald-900/20 dark:border-emerald-800 dark:text-emerald-300' : 'bg-rose-50 border-rose-200 text-rose-700 dark:bg-rose-900/20 dark:border-rose-800 dark:text-rose-300'}`}>{notice.text}</div>}

      <div className="flex gap-2 border-b border-slate-200 dark:border-slate-800">
        {(['mods', 'trash'] as const).map((item) => <button key={item} type="button" onClick={() => switchView(item)} className={`px-3 py-2 text-sm font-semibold border-b-2 ${view === item ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500'}`}>{item === 'mods' ? t('page.mods.mods_tab') : `${t('page.mods.trash_tab')} (${trash.length})`}</button>)}
      </div>

      {view === 'mods' ? <>
        <div className="flex flex-wrap gap-2 items-center">
          <label className="relative flex-1 min-w-[14rem] max-w-md"><Search className="absolute left-3 top-2.5 w-4 h-4 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('page.mods.search')} className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900" /></label>
          <div className="w-36"><NiceSelect value={status} onChange={(value) => setStatus(value as typeof status)} options={[{ value: 'all', label: t('page.mods.status_all') }, { value: 'enabled', label: t('page.mods.enabled') }, { value: 'disabled', label: t('page.mods.disabled') }]} /></div>
          <div className="w-36"><NiceSelect value={loader} onChange={setLoader} options={loaders.map((item) => ({ value: item, label: item === 'all' ? t('page.mods.loader_all') : item }))} /></div>
        </div>
        <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
          {loading ? Array.from({ length: 6 }).map((_, index) => <ModRowSkeleton key={index} />) : filteredMods.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">{t('page.mods.empty')}</div> : filteredMods.map((mod) => <div key={mod.filename} className="flex flex-wrap items-center gap-3 px-4 py-3 border-b last:border-b-0 border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40">
            <ModIcon mod={mod} />
            <div className={`w-2 h-2 rounded-full shrink-0 ${mod.enabled ? 'bg-emerald-500' : 'bg-slate-400'}`} />
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-semibold text-slate-900 dark:text-white truncate">{mod.name || mod.filename}</span><span className="font-mono text-xs text-slate-400 truncate">{mod.id}</span>{mod.loader !== 'unknown' && <span className="text-[11px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">{mod.loader}</span>}{mod.warnings.length > 0 && <span className="inline-flex items-center gap-1 text-[11px] text-amber-600"><AlertTriangle className="w-3 h-3" />{mod.warnings.length}</span>}</div><div className="text-xs text-slate-500 truncate">{mod.filename} · {mod.version || t('common.unknown')} · {formatBytes(mod.size)}</div></div>
            <span className={`text-xs px-2 py-1 rounded-full ${mod.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}`}>{mod.enabled ? t('page.mods.enabled') : t('page.mods.disabled')}</span>
            <div className="flex items-center gap-1"><button type="button" onClick={() => void toggleMod(mod)} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" title={mod.enabled ? t('page.mods.disable') : t('page.mods.enable')}><Power className="w-4 h-4" /></button><button type="button" onClick={() => void openConfigs(mod)} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" title={t('page.mods.config')}><FileCode2 className="w-4 h-4" /></button><button type="button" onClick={() => setDetailMod(mod)} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" title={t('page.mods.details')}><Info className="w-4 h-4" /></button><button type="button" onClick={() => void trashMod(mod)} className="p-2 rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20" title={t('common.delete')}><Trash2 className="w-4 h-4" /></button></div>
          </div>)}
        </div>
      </> : <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">{trashLoading && trash.length === 0 ? Array.from({ length: 5 }).map((_, index) => <ModRowSkeleton key={index} actions={2} />) : trash.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">{t('page.mods.trash_empty')}</div> : trash.map((item) => <div key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-3 border-b last:border-b-0 border-slate-100 dark:border-slate-800"><Trash2 className="w-4 h-4 text-slate-400" /><div className="flex-1 min-w-0"><div className="font-medium truncate text-slate-900 dark:text-white">{item.filename}</div>              <div className="text-xs text-slate-500">{formatEpoch(item.deleted_at)} · {item.enabled ? t('page.mods.enabled') : t('page.mods.disabled')}</div></div><button type="button" onClick={() => void restoreTrash(item.id)} className="p-2 rounded-lg text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20" title={t('page.mods.restore')}><RotateCcw className="w-4 h-4" /></button>{isSuperAdmin && <button type="button" onClick={() => void purgeTrash(item.id)} className="p-2 rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20" title={t('page.mods.purge')}><Trash2 className="w-4 h-4" /></button>}</div>)}</div>}

      {detailMod && <Modal isOpen={true} onClose={() => setDetailMod(null)} closeLabel={t('common.close')} title={detailMod.name || detailMod.filename}>
        <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
          <div className="flex items-start gap-3"><ModIcon mod={detailMod} size="large" /><div className="min-w-0"><p className="font-mono text-xs text-slate-500 break-all">{detailMod.id} · {detailMod.version || t('common.unknown')}</p><p className="mt-2 text-sm text-slate-600 dark:text-slate-300 whitespace-pre-wrap">{detailMod.description || t('page.mods.no_description')}</p></div></div>
          <div className="grid grid-cols-2 gap-3 text-sm"><div><span className="text-xs text-slate-400">{t('page.mods.loader')}</span><div>{detailMod.loader}</div></div><div><span className="text-xs text-slate-400">{t('page.mods.environment')}</span><div>{detailMod.environment}</div></div><div><span className="text-xs text-slate-400">{t('page.mods.authors')}</span><div>{detailMod.authors.join(', ') || t('common.unknown')}</div></div><div><span className="text-xs text-slate-400">{t('page.mods.file_info')}</span><div>{formatBytes(detailMod.size)} · {detailMod.config_count || 0} {t('page.mods.config_count')}</div></div></div>
          {detailMod.dependencies.length > 0 && <div className="text-sm"><strong>{t('page.mods.dependencies')}:</strong> {detailMod.dependencies.map((item) => `${item.id} ${item.version || '*'}`).join(', ')}</div>}
          {detailMod.conflicts.length > 0 && <div className="text-sm text-rose-600"><strong>{t('page.mods.conflicts')}:</strong> {detailMod.conflicts.map((item) => item.id).join(', ')}</div>}
          {detailMod.warnings.length > 0 && <div className="space-y-1">{detailMod.warnings.map((warning) => <div key={`${warning.code}-${warning.message}`} className="flex gap-1 text-sm text-amber-700 dark:text-amber-300"><AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />{warning.message}</div>)}</div>}
        </div>
      </Modal>}

      {configMod && <Modal isOpen={true} onClose={closeConfigModal} closeLabel={t('common.close')} title={`${t('page.mods.config')} - ${configMod.name || configMod.filename}`} fullWidth={Boolean(selectedConfig)}>
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex rounded-lg bg-slate-100 dark:bg-slate-800 p-0.5"><button type="button" onClick={() => void switchConfigScope(true)} className={`px-3 py-1.5 text-xs rounded-md ${configAssociatedOnly ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}>{t('page.mods.associated_configs')}</button><button type="button" onClick={() => void switchConfigScope(false)} className={`px-3 py-1.5 text-xs rounded-md ${!configAssociatedOnly ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}>{t('page.mods.all_configs')}</button></div><span className="text-xs text-slate-500">{configFiles.length}</span></div>
          {configLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, index) => <ConfigFileRowSkeleton key={index} />)}
            </div>
          ) : selectedConfig ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <button type="button" onClick={backToConfigFileList} className="flex items-center gap-2 text-slate-500 transition-colors hover:text-slate-800 dark:hover:text-slate-200"><ArrowLeft size={18} /><span>{t('plugins.config_modal.back_to_list')}</span></button>
                <div className="flex gap-2 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
                  <button type="button" onClick={() => void openConfigFile(selectedConfig, 'code')} className={`rounded-md px-3 py-1 text-xs font-semibold transition-all ${configMode === 'code' ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-700' : 'text-slate-500'}`}>{t('plugins.config_modal.code_view')}</button>
                  <button type="button" disabled={!configData} onClick={() => void openConfigFile(selectedConfig, 'form')} className={`rounded-md px-3 py-1 text-xs font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-50 ${configMode === 'form' ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-700' : 'text-slate-500'}`}>{t('plugins.config_modal.form_view')}</button>
                </div>
              </div>
              <div className="flex min-h-[400px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950">
                {configMode === 'form' && configData ? (
                  <div className="max-h-[500px] flex-1 overflow-y-auto p-6 custom-scrollbar"><ConfigForm data={configData} onChange={(data) => { setConfigData(data); setConfigDirty(true) }} lang={i18n.language} /></div>
                ) : <CodeMirrorEditor value={configContent} onChange={(content) => { setConfigContent(content); setConfigDirty(true) }} theme={i18n.language === 'zh-CN' ? 'dark' : 'light'} height="500px" />}
              </div>
              <div className="flex justify-end gap-3 pt-4">
                <button type="button" onClick={backToConfigFileList} className="rounded-xl bg-slate-100 px-6 py-2.5 font-bold text-slate-700 transition-colors hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">{t('common.cancel')}</button>
                <button type="button" onClick={() => void saveConfig()} disabled={!configDirty} className="flex items-center gap-2 rounded-xl bg-blue-600 px-8 py-2.5 font-bold text-white shadow-lg shadow-blue-600/20 transition-all hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"><Save size={18} />{t('common.save')}</button>
              </div>
            </div>
          ) : configFiles.length > 0 ? (
            <div className="space-y-3">
              <p className="mb-2 text-sm font-medium text-slate-500">{t('plugins.config_modal.available_configs')}</p>
              <div className="grid max-h-[400px] gap-3 overflow-y-auto pr-2 custom-scrollbar">
                {configFiles.map((file) => <div key={file.path} className="group flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50 p-4 transition-all dark:border-slate-800 dark:bg-slate-800">
                  <div className="flex min-w-0 items-center gap-3"><div className="rounded-xl bg-white p-2 shadow-sm dark:bg-slate-700"><Settings className="text-slate-400 transition-colors group-hover:text-blue-500" size={18} /></div><div className="min-w-0"><span className="block truncate text-sm font-semibold text-slate-700 dark:text-slate-200">{file.name}</span><span className="block truncate text-xs text-slate-400">{file.path}</span></div></div>
                  <div className="flex shrink-0 gap-1"><button type="button" disabled={!file.structured} onClick={() => void openConfigFile(file, 'form')} className="rounded-lg p-2 text-slate-400 transition-all hover:bg-blue-50 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-blue-900/20" title={t('plugins.config_modal.form_view')}><FileText size={18} /></button><button type="button" onClick={() => void openConfigFile(file, 'code')} className="rounded-lg p-2 text-slate-400 transition-all hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-blue-900/20" title={t('plugins.config_modal.code_view')}><ChevronRight size={18} /></button></div>
                </div>)}
              </div>
            </div>
          ) : <div className="flex flex-col items-center justify-center py-12 text-slate-500"><div className="mb-4 rounded-full bg-slate-100 p-4 dark:bg-slate-800"><Info size={32} className="opacity-20" /></div><p className="font-medium">{t('page.mods.no_configs')}</p></div>}
        </div>
      </Modal>}

      <Modal isOpen={uploadOpen} onClose={() => setUploadOpen(false)} closeLabel={t('common.close')} title={t('page.mods.upload')}><div className="space-y-4"><label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-slate-300 p-4 dark:border-slate-700"><FilePlus2 className="h-5 w-5 text-blue-600" /><span className="truncate text-sm">{uploadFile?.name || t('page.mods.choose_jar')}</span><input type="file" accept=".jar,application/java-archive" className="hidden" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} /></label><label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300"><input type="checkbox" checked={uploadEnabled} onChange={(event) => setUploadEnabled(event.target.checked)} />{t('page.mods.initial_enabled')}</label>{uploadLimit && <p className="text-xs text-slate-500">{t('page.mods.upload_limit', { limit: uploadLimit })}</p>}{uploadWarnings.length > 0 && <div className="space-y-1 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-900/20 dark:text-amber-200"><strong>{t('page.mods.compatibility_warning')}</strong>{uploadWarnings.map((warning) => <div key={`${warning.code}-${warning.message}`}>{warning.message}</div>)}<label className="mt-2 flex gap-2"><input type="checkbox" checked={confirmUploadWarnings} onChange={(event) => setConfirmUploadWarnings(event.target.checked)} />{t('page.mods.confirm_warning')}</label></div>}{uploadProgress > 0 && <div className="h-2 overflow-hidden rounded bg-slate-100 dark:bg-slate-800"><div className="h-full bg-blue-600 transition-all" style={{ width: `${uploadProgress}%` }} /></div>}<button type="button" disabled={!uploadFile || (uploadWarnings.length > 0 && !confirmUploadWarnings)} onClick={() => void upload(uploadWarnings.length > 0)} className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-3 py-2.5 font-bold text-white shadow-lg shadow-blue-600/20 disabled:opacity-50"><Upload className="h-4 w-4" />{t('page.mods.upload')}</button></div></Modal>
    </div>
  )
}

export default Mods
