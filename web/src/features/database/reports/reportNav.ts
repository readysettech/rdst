import { useNavigate } from '@tanstack/react-router'
import { useCallback } from 'react'
import type { ReportSummary } from '@/lib/monitor/types'

export const REPORT_HISTORY_LIMIT = 30

export type ReportTab =
  | 'fix_first'
  | 'one_fix'
  | 'health'
  | 'changed'
  | 'queries'

/** Opens a report on the Database page's Report tab (latest when no id). */
export function useOpenReport() {
  const navigate = useNavigate()
  return useCallback(
    (id: number | 'latest', _tab?: ReportTab) =>
      void navigate({
        to: '/database' as never,
        search: ((previous: Record<string, unknown>) => {
          const next: Record<string, unknown> = { ...previous, view: 'report' }
          if (id === 'latest') delete next.report
          else next.report = String(id)
          return next
        }) as never,
      }),
    [navigate]
  )
}

/** Done reports only, newest first, one row per run. Never internal counters. */
export function cleanHistory(reports: ReportSummary[] | undefined) {
  const seen = new Set<number>()
  return (reports ?? [])
    .filter((report) => {
      if (seen.has(report.id) || !Number.isFinite(report.ts)) return false
      seen.add(report.id)
      return true
    })
    .sort((a, b) => b.ts - a.ts)
}
