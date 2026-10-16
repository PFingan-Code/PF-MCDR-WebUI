import { motion } from 'framer-motion'
import { Copy, Loader2, ShieldCheck, ShieldOff } from 'lucide-react'
import QRCode from 'qrcode'
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import api, { getApiErrorMessage, unwrapData } from '../utils/api'

// 两步验证绑定属于主服本地账号，始终请求本地
const LOCAL = { headers: { 'X-Target-Server': 'local' } }

const inputClass =
  'w-full px-4 py-2.5 rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/60 transition-all outline-none'

type Status = { supported: boolean; enabled: boolean }
type Setup = { secret: string; qr: string }

const TwoFactorCard: React.FC<{ className?: string }> = ({ className }) => {
  const { t } = useTranslation()
  const [status, setStatus] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null)

  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [setup, setSetup] = useState<Setup | null>(null)
  const [askPassword, setAskPassword] = useState(false)

  const load = useCallback(async () => {
    try {
      const resp = await api.get('/auth/totp', LOCAL)
      setStatus(unwrapData<Status>(resp, { supported: false, enabled: false }))
    } catch {
      setStatus({ supported: false, enabled: false })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const reset = () => {
    setPassword('')
    setCode('')
    setSetup(null)
    setAskPassword(false)
  }

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage(null)
    try {
      await fn()
    } catch (error) {
      setMessage({ text: getApiErrorMessage(error, t('page.settings.totp.failed')), ok: false })
    } finally {
      setBusy(false)
    }
  }

  const beginSetup = () =>
    run(async () => {
      const resp = await api.post('/auth/totp/setup', { password }, LOCAL)
      const data = unwrapData<{ secret: string; otpauth_uri: string }>(resp, { secret: '', otpauth_uri: '' })
      const qr = await QRCode.toDataURL(data.otpauth_uri, { width: 220, margin: 1 })
      setPassword('')
      setAskPassword(false)
      setSetup({ secret: data.secret, qr })
    })

  const confirmSetup = () =>
    run(async () => {
      await api.post('/auth/totp/enable', { code }, LOCAL)
      reset()
      setMessage({ text: t('page.settings.totp.enabled_ok'), ok: true })
      await load()
    })

  const disable = () =>
    run(async () => {
      await api.post('/auth/totp/disable', { password, code }, LOCAL)
      reset()
      setMessage({ text: t('page.settings.totp.disabled_ok'), ok: true })
      await load()
    })

  const codeInput = (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="one-time-code"
      maxLength={6}
      value={code}
      onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
      placeholder={t('page.settings.totp.code_placeholder')}
      className={`${inputClass} font-mono tracking-widest`}
    />
  )

  const passwordInput = (
    <input
      type="password"
      autoComplete="current-password"
      value={password}
      onChange={(e) => setPassword(e.target.value)}
      placeholder={t('page.settings.totp.password_placeholder')}
      className={inputClass}
    />
  )

  const primaryBtn =
    'flex items-center justify-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all shadow-lg shadow-blue-500/20'

  return (
    <motion.div
      className={`bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4 ${className ?? ''}`}
    >
      <div className="flex items-center gap-3 text-blue-600 dark:text-blue-400">
        <ShieldCheck className="w-6 h-6" />
        <h2 className="text-xl font-bold text-slate-900 dark:text-white">{t('page.settings.totp.title')}</h2>
        {status?.enabled && (
          <span className="ml-auto text-xs font-bold px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
            {t('page.settings.totp.status_on')}
          </span>
        )}
      </div>
      <p className="text-sm text-slate-500 dark:text-slate-400">{t('page.settings.totp.desc')}</p>

      {status === null ? (
        <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
      ) : !status.supported ? (
        <p className="text-sm text-amber-600 dark:text-amber-400">{t('page.settings.totp.unsupported')}</p>
      ) : status.enabled ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">{t('page.settings.totp.disable_hint')}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {passwordInput}
            {codeInput}
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => void disable()}
              disabled={busy || !password || code.length !== 6}
              className="flex items-center gap-2 px-5 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white font-semibold rounded-2xl transition-all"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldOff className="w-4 h-4" />}
              {t('page.settings.totp.disable')}
            </button>
          </div>
        </div>
      ) : setup ? (
        <div className="space-y-4">
          <ol className="list-decimal list-inside text-sm text-slate-600 dark:text-slate-300 space-y-1">
            <li>{t('page.settings.totp.step_scan')}</li>
            <li>{t('page.settings.totp.step_code')}</li>
          </ol>
          <div className="flex flex-col sm:flex-row gap-4 items-center">
            <img
              src={setup.qr}
              alt="TOTP QR"
              className="w-44 h-44 rounded-2xl border border-slate-200 dark:border-slate-700 bg-white p-2"
            />
            <div className="space-y-2 w-full min-w-0">
              <p className="text-xs text-slate-500">{t('page.settings.totp.manual_key')}</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 min-w-0 break-all text-sm font-mono px-3 py-2 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200">
                  {setup.secret}
                </code>
                <button
                  type="button"
                  title={t('page.settings.totp.copy')}
                  onClick={() => void navigator.clipboard?.writeText(setup.secret)}
                  className="p-2 rounded-xl text-slate-500 hover:text-blue-600 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  <Copy className="w-4 h-4" />
                </button>
              </div>
              {codeInput}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={reset}
              className="px-5 py-2.5 rounded-2xl font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              {t('common.cancel')}
            </button>
            <button
              type="button"
              onClick={() => void confirmSetup()}
              disabled={busy || code.length !== 6}
              className={primaryBtn}
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
              {t('page.settings.totp.confirm')}
            </button>
          </div>
        </div>
      ) : askPassword ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">{t('page.settings.totp.password_hint')}</p>
          {passwordInput}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={reset}
              className="px-5 py-2.5 rounded-2xl font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              {t('common.cancel')}
            </button>
            <button type="button" onClick={() => void beginSetup()} disabled={busy || !password} className={primaryBtn}>
              {busy && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('page.settings.totp.next')}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex justify-end">
          <button type="button" onClick={() => setAskPassword(true)} className={primaryBtn}>
            <ShieldCheck className="w-4 h-4" />
            {t('page.settings.totp.enable')}
          </button>
        </div>
      )}

      {message && (
        <p className={`text-sm font-medium ${message.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
          {message.text}
        </p>
      )}
    </motion.div>
  )
}

export default TwoFactorCard
