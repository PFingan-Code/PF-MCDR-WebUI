import { AlertTriangle } from 'lucide-react'
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal } from './Modal'

type MetricUnavailableNoticeProps = {
  metric: 'tps' | 'mspt'
  reason?: string | null
}

export const MetricUnavailableNotice: React.FC<MetricUnavailableNoticeProps> = ({ metric, reason }) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (!reason) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={t('page.status.metric_unavailable.open')}
        aria-label={t('page.status.metric_unavailable.open')}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-amber-500 transition-colors hover:bg-amber-100 hover:text-amber-700 dark:hover:bg-amber-900/30 dark:hover:text-amber-300"
      >
        <AlertTriangle className="h-3.5 w-3.5" />
      </button>
      <Modal
        isOpen={open}
        onClose={() => setOpen(false)}
        title={t('page.status.metric_unavailable.title', { metric: metric.toUpperCase() })}
        closeLabel={t('common.close')}
      >
        <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
          {t(`page.status.metric_unavailable.reasons.${reason}`, {
            defaultValue: t('page.status.metric_unavailable.reasons.commands_unsupported'),
          })}
        </p>
      </Modal>
    </>
  )
}
