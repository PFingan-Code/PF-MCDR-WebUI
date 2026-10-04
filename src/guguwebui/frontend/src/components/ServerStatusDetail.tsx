import { Activity, Cpu, Database, Gauge, HardDrive, MemoryStick, Network } from 'lucide-react'
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { STATUS_RANGES } from '../constants'
import { useTheme } from '../hooks/useTheme'
import api, { isCancel, unwrapData } from '../utils/api'
import {
  formatBytes,
  formatLoad,
  formatMspt,
  formatPercent,
  formatSpeed,
  formatTps,
  formatUptime,
  percentAxisTick,
  speedAxisTick,
} from '../utils/format'
import StatusChart, { ChartPoint, ChartSeriesDef } from './StatusChart'
import { ChartSkeleton, MiniStatSkeleton, TableRowSkeleton } from './Skeleton'
import { MetricUnavailableNotice } from './MetricUnavailableNotice'

type RangeKey = (typeof STATUS_RANGES)[number]
const RANGES: RangeKey[] = [...STATUS_RANGES]

/** 时间轴带日期格式的范围 */
const LONG_RANGES: RangeKey[] = ['12h', '1d', '3d', '7d']

interface CpuInfo {
  system: number | null
  minecraft: number | null
}
interface MemInfo {
  total: number | null
  available: number | null
  used: number | null
  percent: number | null
  minecraft: number | null
  swap_total: number | null
  swap_used: number | null
  swap_percent: number | null
}
interface DiskInfo {
  path: string | null
  total: number | null
  used: number | null
  percent: number | null
}
interface LoadInfo {
  load1: number | null
  load5: number | null
  load15: number | null
}
interface NetInfo {
  rx: number | null
  tx: number | null
}

export interface Overview {
  ts: number
  online: boolean
  uptime: number | null
  tps: number | null
  mspt: number | null
  tps_reason?: string | null
  mspt_reason?: string | null
  cpu: CpuInfo
  memory: MemInfo
  disk: DiskInfo
  load: LoadInfo
  network: NetInfo
}

interface StatCell {
  avg: number | null
  min: number | null
  max: number | null
}

interface TableStats {
  tps: StatCell
  mspt: StatCell
  cpu: { system: StatCell; minecraft: StatCell }
  memory: { system: StatCell; minecraft: StatCell }
  swap: StatCell
  disk: StatCell
  load: { load1: StatCell; load5: StatCell; load15: StatCell }
  network: { rx: StatCell; tx: StatCell }
}

interface HistoryMeta {
  cpu: ChartPoint[]
  memory: ChartPoint[]
  network: ChartPoint[]
}

const COLORS = {
  system: '#3b82f6',
  minecraft: '#f59e0b',
  rx: '#10b981',
  tx: '#8b5cf6',
}

type Formatter = (v: number | null | undefined) => string

const withSub = (main: string, sub: string | null): string => (sub ? `${main} ${sub}` : main)

const ServerStatusDetail: React.FC = () => {
  const { t } = useTranslation()
  const { isDark } = useTheme()
  const [range, setRange] = useState<RangeKey>('1h')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [history, setHistory] = useState<HistoryMeta>({ cpu: [], memory: [], network: [] })
  const [tableStats, setTableStats] = useState<TableStats | null>(null)
  const [historyLoading, setHistoryLoading] = useState(true)

  const xLong = LONG_RANGES.includes(range)

  useEffect(() => {
    const ac = new AbortController()
    let cancelled = false

    const fetchOverview = async () => {
      try {
        const resp = await api.get('/monitor/overview', { signal: ac.signal })
        const data = unwrapData<Overview | null>(resp, null)
        if (!cancelled && data && typeof data.online === 'boolean') {
          setOverview(data)
        }
      } catch (e: unknown) {
        const err = e as { name?: string; code?: string }
        if (isCancel(e) || err.name === 'AbortError' || err.code === 'ERR_CANCELED') return
        console.error('Failed to fetch monitor overview:', e)
      }
    }

    const fetchHistory = async () => {
      try {
        const [cpuResp, memResp, netResp] = await Promise.all([
          api.get('/monitor/history', { params: { metric: 'cpu', range }, signal: ac.signal }),
          api.get('/monitor/history', { params: { metric: 'memory', range }, signal: ac.signal }),
          api.get('/monitor/history', { params: { metric: 'network', range }, signal: ac.signal }),
        ])
        if (cancelled) return
        const cpu = unwrapData<{ points?: ChartPoint[] }>(cpuResp, {})
        const memory = unwrapData<{ points?: ChartPoint[] }>(memResp, {})
        const network = unwrapData<{ points?: ChartPoint[] }>(netResp, {})
        setHistory({
          cpu: cpu.points ?? [],
          memory: memory.points ?? [],
          network: network.points ?? [],
        })
      } catch (e: unknown) {
        const err = e as { name?: string; code?: string }
        if (isCancel(e) || err.name === 'AbortError' || err.code === 'ERR_CANCELED') return
        console.error('Failed to fetch monitor history:', e)
      } finally {
        if (!cancelled) setHistoryLoading(false)
      }
    }

    const fetchTable = async () => {
      try {
        const resp = await api.get('/monitor/table', { params: { range }, signal: ac.signal })
        const data = unwrapData<{ stats?: TableStats }>(resp, {})
        if (!cancelled && data.stats) {
          setTableStats(data.stats)
        }
      } catch (e: unknown) {
        const err = e as { name?: string; code?: string }
        if (isCancel(e) || err.name === 'AbortError' || err.code === 'ERR_CANCELED') return
        console.error('Failed to fetch monitor table:', e)
      }
    }

    setHistoryLoading(true)
    fetchOverview()
    fetchHistory()
    fetchTable()

    const overviewTimer = setInterval(fetchOverview, 5000)
    const historyTimer = setInterval(() => {
      fetchHistory()
      fetchTable()
    }, 30000)

    return () => {
      cancelled = true
      ac.abort()
      clearInterval(overviewTimer)
      clearInterval(historyTimer)
    }
  }, [range])

  const statOf = (cell: StatCell | undefined, fmt: Formatter) => ({
    avg: cell ? fmt(cell.avg) : '—',
    min: cell ? fmt(cell.min) : '—',
    max: cell ? fmt(cell.max) : '—',
  })

  /** 表格行 */
  interface TableRow {
    group: string
    items: { label: string; current: string; avg: string; min: string; max: string; notice?: React.ReactNode }[]
  }

  const rows: TableRow[] = (() => {
    if (!overview || !tableStats) return []
    const st = tableStats
    const tps = statOf(st.tps, formatTps)
    const mspt = statOf(st.mspt, formatMspt)
    const cpuSys = statOf(st.cpu.system, formatPercent)
    const cpuMc = statOf(st.cpu.minecraft, formatPercent)
    const memSys = statOf(st.memory.system, formatPercent)
    const memMc = statOf(st.memory.minecraft, formatBytes)
    const swap = statOf(st.swap, formatPercent)
    const disk = statOf(st.disk, formatPercent)
    const l1 = statOf(st.load.load1, formatLoad)
    const l5 = statOf(st.load.load5, formatLoad)
    const l15 = statOf(st.load.load15, formatLoad)
    const netRx = statOf(st.network.rx, formatSpeed)
    const netTx = statOf(st.network.tx, formatSpeed)

    const diskUsed = overview.disk.total && overview.disk.used
      ? `${formatBytes(overview.disk.used)} / ${formatBytes(overview.disk.total)}`
      : null

    return [
      {
        group: t('page.status.group_server'),
        items: [
          { label: 'TPS', current: formatTps(overview.tps), ...tps, notice: <MetricUnavailableNotice metric="tps" reason={overview.tps_reason} /> },
          { label: t('page.status.mspt'), current: formatMspt(overview.mspt), ...mspt, notice: <MetricUnavailableNotice metric="mspt" reason={overview.mspt_reason} /> },
        ],
      },
      {
        group: t('page.status.group_cpu'),
        items: [
          { label: t('page.status.whole_machine'), current: formatPercent(overview.cpu.system), ...cpuSys },
          { label: t('page.status.mc_process'), current: formatPercent(overview.cpu.minecraft), ...cpuMc },
        ],
      },
      {
        group: t('page.status.group_memory'),
        items: [
          { label: t('page.status.whole_machine'), current: formatPercent(overview.memory.percent), ...memSys },
          { label: t('page.status.mc_process'), current: formatBytes(overview.memory.minecraft), ...memMc },
          { label: t('page.status.swap'), current: formatPercent(overview.memory.swap_percent), ...swap },
        ],
      },
      {
        group: t('page.status.group_disk'),
        items: [
          { label: t('page.status.disk_usage'), current: withSub(formatPercent(overview.disk.percent), diskUsed), ...disk },
        ],
      },
      {
        group: t('page.status.group_load'),
        items: [
          { label: t('page.status.load_1m'), current: formatLoad(overview.load.load1), ...l1 },
          { label: t('page.status.load_5m'), current: formatLoad(overview.load.load5), ...l5 },
          { label: t('page.status.load_15m'), current: formatLoad(overview.load.load15), ...l15 },
        ],
      },
      {
        group: t('page.status.group_network'),
        items: [
          { label: t('page.status.network_rx'), current: formatSpeed(overview.network.rx), ...netRx },
          { label: t('page.status.network_tx'), current: formatSpeed(overview.network.tx), ...netTx },
        ],
      },
    ]
  })()

  /** 概览小格 */
  const overviewCells: { label: string; value: string; sub?: string; icon: React.ReactNode }[] = [
    {
      label: 'TPS',
      value: formatTps(overview?.tps),
      icon: <Activity className="w-4 h-4" />,
    },
    {
      label: t('page.status.mspt'),
      value: formatMspt(overview?.mspt),
      icon: <Gauge className="w-4 h-4" />,
    },
    {
      label: t('page.status.cpu'),
      value: formatPercent(overview?.cpu.system),
      sub: overview ? withSub(t('page.status.mc_process'), formatPercent(overview.cpu.minecraft)) : undefined,
      icon: <Cpu className="w-4 h-4" />,
    },
    {
      label: t('page.status.memory'),
      value: formatPercent(overview?.memory.percent),
      sub: overview ? withSub(t('page.status.mc_process'), formatBytes(overview.memory.minecraft)) : undefined,
      icon: <MemoryStick className="w-4 h-4" />,
    },
    {
      label: t('page.status.swap'),
      value: formatPercent(overview?.memory.swap_percent),
      icon: <MemoryStick className="w-4 h-4" />,
    },
    {
      label: t('page.status.disk'),
      value: formatPercent(overview?.disk.percent),
      sub: overview && overview.disk.total ? formatBytes(overview.disk.total) : undefined,
      icon: <HardDrive className="w-4 h-4" />,
    },
    {
      label: t('page.status.load'),
      value: formatLoad(overview?.load.load1),
      sub: overview ? `${formatLoad(overview.load.load5)} / ${formatLoad(overview.load.load15)}` : undefined,
      icon: <Database className="w-4 h-4" />,
    },
    {
      label: t('page.status.network'),
      value: formatSpeed(overview?.network.rx),
      sub: overview ? withSub('↑', formatSpeed(overview.network.tx)) : undefined,
      icon: <Network className="w-4 h-4" />,
    },
  ]

  const charts: {
    title: string
    data: ChartPoint[]
    series: ChartSeriesDef[]
    yDomain?: [number | string, number | string]
    yTick: (v: number) => string
    tooltip: (key: ChartSeriesDef['key'], v: number) => string
    names: Record<string, string>
  }[] = [
    {
      title: t('page.status.chart_cpu'),
      data: history.cpu,
      series: [
        { key: 'system', color: COLORS.system },
        { key: 'minecraft', color: COLORS.minecraft },
      ],
      yTick: percentAxisTick,
      tooltip: (_, v) => formatPercent(v),
      names: {
        system: t('page.status.whole_machine'),
        minecraft: t('page.status.mc_process'),
      },
    },
    {
      title: t('page.status.chart_memory'),
      data: history.memory,
      series: [
        { key: 'system', color: COLORS.system },
        { key: 'minecraft', color: COLORS.minecraft },
      ],
      yDomain: [0, 100],
      yTick: percentAxisTick,
      tooltip: (_, v) => formatPercent(v),
      names: {
        system: t('page.status.whole_machine'),
        minecraft: t('page.status.mc_process'),
      },
    },
    {
      title: t('page.status.chart_network'),
      data: history.network,
      series: [
        { key: 'rx', color: COLORS.rx },
        { key: 'tx', color: COLORS.tx },
      ],
      yTick: speedAxisTick,
      tooltip: (_, v) => formatSpeed(v),
      names: {
        rx: t('page.status.network_rx'),
        tx: t('page.status.network_tx'),
      },
    },
  ]

  return (
    <div className="space-y-4">
      {/* 概览 */}
      <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm p-5">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
          <h3 className="font-bold text-slate-900 dark:text-white">{t('page.status.overview')}</h3>
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border ${
              overview?.online
                ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border-green-100 dark:border-green-900/30'
                : 'bg-slate-50 dark:bg-slate-900/20 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-800'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                overview?.online ? 'bg-green-500 animate-pulse' : 'bg-slate-400'
              }`}
            />
            {overview?.online ? t('page.status.online') : t('page.status.offline')}
            {overview?.online && overview.uptime != null && ` · ${formatUptime(overview.uptime)}`}
          </span>
        </div>
        {!overview ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <MiniStatSkeleton key={i} />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">
            {overviewCells.map((cell) => (
              <div
                key={cell.label}
                className="rounded-2xl bg-slate-50 dark:bg-slate-800/60 p-3 flex flex-col gap-1 min-w-0"
              >
                <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
                  <span className="text-blue-500 dark:text-blue-400 shrink-0">{cell.icon}</span>
                  <span className="truncate">{cell.label}</span>
                </div>
                <p className="text-lg font-bold text-slate-900 dark:text-white tabular-nums truncate">
                  {cell.value}
                </p>
                {cell.sub && (
                  <p className="text-[11px] text-slate-500 dark:text-slate-400 truncate">{cell.sub}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 统计表 + 折线图 */}
      <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm p-5">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
          <h3 className="font-bold text-slate-900 dark:text-white">
            {t('page.status.stats_and_charts')}
          </h3>
          <div className="flex flex-wrap gap-1">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                className={`px-2.5 py-1 rounded-full text-xs font-semibold transition-colors ${
                  range === r
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {/* 统计表 */}
        <div className="overflow-x-auto mb-6">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800">
                <th className="px-3 py-2 whitespace-nowrap">{t('page.status.metric')}</th>
                <th className="px-3 py-2 whitespace-nowrap text-right">{t('page.status.current')}</th>
                <th className="px-3 py-2 whitespace-nowrap text-right">{t('page.status.avg')}</th>
                <th className="px-3 py-2 whitespace-nowrap text-right">{t('page.status.min')}</th>
                <th className="px-3 py-2 whitespace-nowrap text-right">{t('page.status.max')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                Array.from({ length: 7 }).map((_, i) => (
                  <TableRowSkeleton key={i} cols={5} />
                ))
              ) : (
                rows.map((row) => (
                  <React.Fragment key={row.group}>
                    <tr className="bg-slate-50/80 dark:bg-slate-800/40">
                      <td
                        colSpan={5}
                        className="px-3 py-1.5 text-xs font-bold text-slate-500 dark:text-slate-400"
                      >
                        {row.group}
                      </td>
                    </tr>
                    {row.items.map((item) => (
                      <tr
                        key={item.label}
                        className="border-b border-slate-100 dark:border-slate-800/60 last:border-0"
                      >
                        <td className="px-3 py-2 text-slate-800 dark:text-slate-200 whitespace-nowrap">
                          {item.label}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-900 dark:text-white font-semibold tabular-nums whitespace-nowrap">
                          {item.current}{item.notice}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600 dark:text-slate-400 tabular-nums whitespace-nowrap">
                          {item.avg}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600 dark:text-slate-400 tabular-nums whitespace-nowrap">
                          {item.min}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600 dark:text-slate-400 tabular-nums whitespace-nowrap">
                          {item.max}
                        </td>
                      </tr>
                    ))}
                  </React.Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* 折线图 */}
        {historyLoading && history.cpu.length === 0 ? (
          <div className="space-y-6">
            <ChartSkeleton />
            <ChartSkeleton />
            <ChartSkeleton />
          </div>
        ) : (
          <div className="space-y-6">
            {charts.map((chart) => (
              <div key={chart.title}>
                <div className="flex items-center justify-between flex-wrap gap-2 mb-1">
                  <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                    {chart.title}
                  </h4>
                  <div className="flex items-center gap-3">
                    {chart.series.map((s) => (
                      <span
                        key={s.key}
                        className="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400"
                      >
                        <span
                          className="w-2 h-2 rounded-full"
                          style={{ background: s.color }}
                        />
                        {chart.names[s.key]}
                      </span>
                    ))}
                  </div>
                </div>
                <StatusChart
                  data={chart.data}
                  series={chart.series}
                  names={chart.names}
                  dark={isDark}
                  yDomain={chart.yDomain}
                  yTickFormatter={chart.yTick}
                  tooltipValueFormatter={chart.tooltip}
                  xLong={xLong}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default ServerStatusDetail