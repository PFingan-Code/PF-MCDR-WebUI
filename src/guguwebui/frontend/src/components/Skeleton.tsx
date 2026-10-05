import React from 'react'

/** 基础骨架块：灰底 + 呼吸动画 */
export const Skeleton: React.FC<{ className?: string; variant?: 'default' | 'terminal' }> = ({
  className = '',
  variant = 'default',
}) => (
  <div
    className={`animate-pulse rounded-xl ${
      variant === 'terminal' ? 'bg-slate-600/50' : 'bg-slate-200/80 dark:bg-slate-700/50'
    } ${className}`}
  />
)

/** 一行文字骨架 */
export const SkeletonLine: React.FC<{ className?: string }> = ({ className = '' }) => (
  <Skeleton className={`h-3 ${className}`} />
)

/** 插件卡片骨架：模拟插件卡片布局（本地插件 / 在线插件） */
export const PluginCardSkeleton: React.FC = () => (
  <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 overflow-hidden shadow-sm flex flex-col">
    <div className="p-6 flex-1 space-y-4">
      <div className="flex items-start gap-4">
        <Skeleton className="w-12 h-12 rounded-2xl" />
        <div className="flex-1 min-w-0 space-y-2">
          <div className="flex items-center gap-3">
            <Skeleton className="h-5 flex-1" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-4/5" />
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-6 w-20 rounded-lg" />
        <Skeleton className="h-6 w-16 rounded-lg" />
      </div>
    </div>
    <div className="px-6 py-4 bg-slate-50/50 dark:bg-slate-900/50 border-t border-slate-100 dark:border-slate-800">
      <Skeleton className="h-9 w-36 rounded-xl" />
    </div>
  </div>
)

/** 配置项小卡片骨架（MC 配置页） */
export const ConfigCardSkeleton: React.FC = () => (
  <div className="bg-white dark:bg-slate-900 p-5 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm">
    <div className="flex flex-col h-full space-y-3">
      <div className="space-y-2">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-4 w-3/4" />
      </div>
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-5/6" />
      <div className="mt-auto pt-4 border-t border-slate-100 dark:border-slate-800">
        <Skeleton className="h-9 w-full rounded-lg" />
      </div>
    </div>
  </div>
)

/** MCDR 配置分区骨架：标题行 + 若干配置项行 */
export const MCDRConfigSectionSkeleton: React.FC = () => (
  <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm p-6 space-y-6">
    <div className="flex items-center gap-2">
      <Skeleton className="w-6 h-6 rounded-lg" />
      <Skeleton className="h-5 w-32" />
    </div>
    <div className="space-y-6">
      {[0, 1, 2].map((i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-4 w-2/5" />
          <Skeleton className="h-3 w-3/5" />
          <Skeleton className="h-9 w-full rounded-lg" />
        </div>
      ))}
    </div>
  </div>
)

/** 设置页分区卡片骨架 */
export const SettingsCardSkeleton: React.FC<{ wide?: boolean }> = ({ wide = false }) => (
  <div
    className={`bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4 ${
      wide ? 'lg:col-span-2' : ''
    }`}
  >
    <div className="flex items-center gap-3">
      <Skeleton className="w-6 h-6 rounded-xl" />
      <Skeleton className="h-5 w-40" />
    </div>
    <Skeleton className="h-3 w-full" />
    <Skeleton className="h-3 w-3/4" />
    <div className="flex gap-3 pt-2">
      <Skeleton className="h-10 w-40 rounded-2xl" />
      <Skeleton className="h-10 w-32 rounded-2xl" />
    </div>
    <div className="space-y-3 pt-2">
      <Skeleton className="h-12 w-full rounded-2xl" />
      <Skeleton className="h-12 w-full rounded-2xl" />
    </div>
  </div>
)

/** 表格行骨架 */
export const TableRowSkeleton: React.FC<{ cols?: number; className?: string }> = ({
  cols = 5,
  className = '',
}) => (
  <tr className={`border-b border-slate-100 dark:border-slate-800/60 last:border-0 ${className}`}>
    {Array.from({ length: cols }).map((_, i) => (
      <td key={i} className="px-4 py-3">
        <Skeleton className={`h-4 ${i === cols - 1 ? 'w-16 ml-auto' : i % 2 === 0 ? 'w-24' : 'w-20'}`} />
      </td>
    ))}
  </tr>
)

/** 模组列表行骨架（模组管理页 / 回收站）：图标 + 状态点 + 名称/详情两行 + 徽标 + 操作按钮 */
export const ModRowSkeleton: React.FC<{ actions?: number }> = ({ actions = 4 }) => (
  <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b last:border-b-0 border-slate-100 dark:border-slate-800">
    <Skeleton className="h-9 w-9 rounded-lg shrink-0" />
    <span className="h-2 w-2 rounded-full bg-slate-200 dark:bg-slate-700 shrink-0" />
    <div className="min-w-0 flex-1 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-4 w-40 max-w-full" />
        <Skeleton className="h-3 w-24" />
      </div>
      <Skeleton className="h-3 w-2/3 max-w-[22rem]" />
    </div>
    <Skeleton className="h-6 w-16 rounded-full" />
    <div className="flex items-center gap-1">
      {Array.from({ length: actions }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-8 rounded-lg" />
      ))}
    </div>
  </div>
)

/** 配置文件列表行骨架（插件配置弹窗） */
export const ConfigFileRowSkeleton: React.FC = () => (
  <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-800 rounded-2xl">
    <div className="flex items-center gap-3 flex-1 min-w-0">
      <Skeleton className="w-10 h-10 rounded-xl shrink-0" />
      <Skeleton className="h-4 w-2/5" />
    </div>
    <Skeleton className="h-6 w-16 rounded-lg" />
  </div>
)

/** 迷你统计卡片骨架（监控小格） */
export const MiniStatSkeleton: React.FC = () => (
  <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/60 p-3 space-y-2 min-w-0">
    <Skeleton className="h-3 w-12" />
    <Skeleton className="h-5 w-16" />
    <Skeleton className="h-3 w-10" />
  </div>
)

/** 聊天消息行骨架（variant='terminal' 用于深色聊天背景） */
export const MessageLineSkeleton: React.FC<{ variant?: 'default' | 'terminal' }> = ({ variant = 'default' }) => (
  <div className="flex items-center gap-2 py-1.5">
    <Skeleton variant={variant} className="h-3 w-16 shrink-0" />
    <Skeleton variant={variant} className="h-3 w-24 shrink-0" />
    <Skeleton variant={variant} className="h-3 flex-1 max-w-[55%]" />
  </div>
)

/** 终端日志行骨架（深色背景） */
export const LogLineSkeleton: React.FC = () => (
  <div className="flex items-center gap-3 py-1">
    <Skeleton variant="terminal" className="h-3 w-8 shrink-0" />
    <Skeleton variant="terminal" className="h-3 w-2/3" />
  </div>
)

/** 折线图占位骨架 */
export const ChartSkeleton: React.FC = () => (
  <div className="space-y-2">
    <div className="flex items-center justify-between">
      <Skeleton className="h-4 w-28" />
      <div className="flex gap-3">
        <Skeleton className="h-3 w-14" />
        <Skeleton className="h-3 w-14" />
      </div>
    </div>
    <Skeleton className="h-40 w-full rounded-2xl" />
  </div>
)

/** 通知条目骨架（仪表盘） */
export const NoticeRowSkeleton: React.FC = () => (
  <div className="flex items-center gap-3 p-3 rounded-2xl bg-slate-50 dark:bg-slate-800/60">
    <Skeleton className="w-10 h-10 rounded-full shrink-0" />
    <div className="flex-1 space-y-2 min-w-0">
      <Skeleton className="h-3 w-1/4" />
      <Skeleton className="h-3 w-3/4" />
    </div>
  </div>
)

/** 仪表盘统计卡片骨架（四小卡）：结构与真实卡片一致，含底部固定高度的操作区占位 */
export const DashboardStatSkeleton: React.FC<{ showBadge?: boolean }> = ({ showBadge = true }) => (
  <div className="bg-white dark:bg-slate-900 p-6 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm flex flex-col gap-4">
    <div className="flex items-center justify-between">
      <Skeleton className="w-12 h-12 rounded-2xl" />
      {showBadge && <Skeleton className="h-6 w-16 rounded-full" />}
    </div>
    <div className="flex-1 space-y-2">
      <Skeleton className="h-4 w-24" />
      <Skeleton className="h-7 w-32" />
    </div>
    <div className="h-8" />
  </div>
)
