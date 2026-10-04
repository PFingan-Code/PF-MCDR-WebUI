import { Activity, ChevronRight } from 'lucide-react'
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import api, { isCancel, unwrapData } from '../utils/api'
import { formatBytes, formatMspt, formatPercent, formatSpeed, formatTps } from '../utils/format'
import type { Overview } from './ServerStatusDetail'
import { MiniStatSkeleton } from './Skeleton'
import { MetricUnavailableNotice } from './MetricUnavailableNotice'

interface StatusOverviewCardProps {
  onOpenDetail: () => void
}

const MiniStat: React.FC<{ label: string; value: string; sub?: string; color?: string; notice?: React.ReactNode }> = ({
  label,
  value,
  sub,
  color,
  notice,
}) => (
  <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/60 p-3 min-w-0">
    <p className="text-[11px] font-medium text-slate-500 dark:text-slate-400 truncate">{label}</p>
    <p className={`text-lg font-bold tabular-nums truncate ${color ?? 'text-slate-900 dark:text-white'}`}>
      {value}
      {notice}
    </p>
    {sub && <p className="text-[11px] text-slate-500 dark:text-slate-400 truncate">{sub}</p>}
  </div>
)

const StatusOverviewCard: React.FC<StatusOverviewCardProps> = ({ onOpenDetail }) => {
  const { t } = useTranslation()
  const [overview, setOverview] = useState<Overview | null>(null)

  useEffect(() => {
    const ac = new AbortController()
    const fetchOverview = async () => {
      try {
        const resp = await api.get('/monitor/overview', { signal: ac.signal })
        const data = unwrapData<Overview | null>(resp, null)
        if (data && typeof data.online === 'boolean') {
          setOverview(data)
        }
      } catch (e: unknown) {
        const err = e as { name?: string; code?: string }
        if (isCancel(e) || err.name === 'AbortError' || err.code === 'ERR_CANCELED') return
        // 静默失败，下次轮询重试
      }
    }
    fetchOverview()
    const timer = setInterval(fetchOverview, 5000)
    return () => {
      ac.abort()
      clearInterval(timer)
    }
  }, [])

  return (
    <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm p-6">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-cyan-500 rounded-xl text-white shadow-lg shadow-cyan-500/25 shrink-0">
            <Activity className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white">
              {t('page.index.server_status')}
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t('page.index.server_status_desc')}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onOpenDetail}
          className="inline-flex items-center gap-1 px-3.5 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold transition-colors"
        >
          {t('page.status.view_detail')}
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">
        {!overview &&
          Array.from({ length: 8 }).map((_, i) => (
            <MiniStatSkeleton key={i} />
          ))}
        {overview && (
          <>
        <MiniStat
          label="TPS"
           notice={<MetricUnavailableNotice metric="tps" reason={overview?.tps_reason} />}
          value={formatTps(overview?.tps)}
          color={overview?.tps != null && overview.tps < 18 ? 'text-amber-600 dark:text-amber-400' : undefined}
        />
        <MiniStat
           label={t('page.status.mspt')}
           value={formatMspt(overview?.mspt)}
           notice={<MetricUnavailableNotice metric="mspt" reason={overview?.mspt_reason} />}
         />
        <MiniStat
          label={t('page.status.cpu')}
          value={formatPercent(overview?.cpu.system)}
          sub={
            overview
              ? `${t('page.status.mc_process')} ${formatPercent(overview.cpu.minecraft)}`
              : undefined
          }
        />
        <MiniStat
          label={t('page.status.memory')}
          value={formatPercent(overview?.memory.percent)}
          sub={overview ? formatBytes(overview.memory.minecraft) : undefined}
        />
        <MiniStat label={t('page.status.swap')} value={formatPercent(overview?.memory.swap_percent)} />
        <MiniStat label={t('page.status.disk')} value={formatPercent(overview?.disk.percent)} />
        <MiniStat label={t('page.status.load')} value={overview ? String(overview.load.load1?.toFixed(2) ?? '—') : '—'} />
        <MiniStat
          label={t('page.status.network')}
          value={formatSpeed(overview?.network.rx)}
          sub={overview ? `↑ ${formatSpeed(overview.network.tx)}` : undefined}
        />
          </>
        )}
      </div>
    </div>
  )
}

export default StatusOverviewCard