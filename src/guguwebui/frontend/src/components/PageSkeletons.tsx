import React from 'react'

import { ChartSkeleton, MiniStatSkeleton, ModRowSkeleton, Skeleton, TableRowSkeleton } from './Skeleton'

/**
 * 页面级骨架：路由 chunk 懒加载期间使用，形态与真实页面一致，
 * 避免出现整屏“加载中…”文字造成的布局跳动。
 */

/** 服务器状态页骨架：页头 + 概览小卡 + 统计表 + 折线图 */
export const ServerStatusPageSkeleton: React.FC = () => (
  <div className="space-y-6">
    <div className="flex items-center gap-3">
      <Skeleton className="h-14 w-14 rounded-2xl shrink-0" />
      <div className="space-y-2">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-4 w-64 max-w-full" />
      </div>
    </div>

    <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-7 w-28 rounded-full" />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <MiniStatSkeleton key={i} />
        ))}
      </div>
    </div>

    <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <Skeleton className="h-5 w-40" />
        <div className="flex flex-wrap gap-1">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-6 w-12 rounded-full" />
          ))}
        </div>
      </div>
      <table className="min-w-full text-sm">
        <tbody>
          {Array.from({ length: 5 }).map((_, i) => (
            <TableRowSkeleton key={i} cols={5} />
          ))}
        </tbody>
      </table>
      <ChartSkeleton />
    </div>
  </div>
)

/** 模组管理页骨架：页头 + 标签页 + 筛选栏 + 模组列表 */
export const ModsPageSkeleton: React.FC = () => (
  <div className="space-y-5 pb-10">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-2">
        <Skeleton className="h-8 w-44" />
        <Skeleton className="h-4 w-72 max-w-full" />
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-9 w-28 rounded-lg" />
        <Skeleton className="h-9 w-9 rounded-lg" />
      </div>
    </div>

    <div className="flex gap-2 border-b border-slate-200 dark:border-slate-800 pb-2">
      <Skeleton className="h-5 w-16" />
      <Skeleton className="h-5 w-20" />
    </div>

    <div className="flex flex-wrap gap-2 items-center">
      <Skeleton className="h-9 flex-1 min-w-[14rem] max-w-md rounded-lg" />
      <Skeleton className="h-9 w-36 rounded-lg" />
      <Skeleton className="h-9 w-36 rounded-lg" />
    </div>

    <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
      {Array.from({ length: 6 }).map((_, i) => (
        <ModRowSkeleton key={i} />
      ))}
    </div>
  </div>
)
