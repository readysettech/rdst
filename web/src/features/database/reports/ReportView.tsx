// The Report tab (DESIGN_V5 section 5): the daily snapshot. RDST writes one just after
// midnight for the day that ended. Every state stays honest: loading, none yet, failed, and
// past days from the day list.
import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { Skeleton } from '@rs/ui-new/skeleton'
import { useMemo } from 'react'
import { DatabaseGlyph } from '@/features/monitor-kit/DatabaseGlyph'
import { useReport, useReportHistory } from '@/lib/monitor/hooks'
import { DaySnapshot } from './DaySnapshot'
import { REPORT_HISTORY_LIMIT } from './reportNav'
import { dayList } from './snapshotModel'

function ReportSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-32 w-full rounded-card" />
      <Skeleton className="h-72 w-full rounded-card" />
      <Skeleton className="h-24 w-full rounded-card" />
    </div>
  )
}

export function ReportView({
  target,
  reportId,
  onShowLatest,
  onPick,
  onOpenActions,
}: {
  target: string
  reportId: number | 'latest'
  onShowLatest: () => void
  onPick: (id: number) => void
  onOpenActions?: () => void
}) {
  // Nightly reports only; a checkup feeds Actions.
  const report = useReport(target, reportId === 'latest' ? 'daily' : reportId)
  const history = useReportHistory(target, REPORT_HISTORY_LIMIT, true)
  const days = useMemo(() => dayList(history.data?.reports), [history.data])

  if (report.isPending) return <ReportSkeleton />

  if (
    report.data === null ||
    (report.data && !report.data.report_json && report.data.state === 'done')
  )
    return (
      <div className="rounded-card border border-border-layout-1 bg-surface-layout-1">
        <EmptyState
          media={<DatabaseGlyph size={96} tone="neutral" />}
          title={
            reportId === 'latest'
              ? 'Your first daily report arrives tonight'
              : 'This day is gone'
          }
          body={
            reportId === 'latest'
              ? `Every night just after midnight, RDST writes up ${target}'s day: its activity on a chart, what changed, and the queries that need a look. What to change right now is on Actions.`
              : 'RDST keeps a limited history. Open the latest day instead.'
          }
          action={
            reportId === 'latest'
              ? onOpenActions
                ? { label: 'Open Actions', onClick: onOpenActions }
                : undefined
              : { label: 'Open the latest day', onClick: onShowLatest }
          }
        />
      </div>
    )

  if (report.isError || !report.data)
    return (
      <ErrorState
        title="The report didn't load"
        message="RDST couldn't read this snapshot."
        onRetry={() => void report.refetch()}
      />
    )

  if (report.data.state === 'failed' || report.data.state === 'cancelled')
    return (
      <div className="rounded-card border border-border-layout-1 bg-surface-layout-1">
        <EmptyState
          icon="alert"
          title={
            report.data.state === 'failed'
              ? "This snapshot didn't finish"
              : 'This snapshot was cancelled'
          }
          body="It stopped before it was written. Nothing on your database was changed. Tonight's report runs as usual."
          action={
            onOpenActions
              ? { label: 'Open Actions', onClick: onOpenActions }
              : undefined
          }
        />
        {reportId !== 'latest' && (
          <div className="flex justify-center pb-6">
            <Button
              label="Open the latest day"
              modifier="ghost"
              onClick={onShowLatest}
            />
          </div>
        )}
      </div>
    )

  const snapshot = report.data.report_json?.snapshot
  if (!snapshot)
    return (
      <div className="space-y-3 rounded-card border border-border-layout-1 bg-surface-layout-1 p-6">
        <p className="text-headline-5 text-content-layout-1">An older report</p>
        <p className="max-w-3xl text-body-medium text-content-layout-2">
          {report.data.report_json?.summary?.replace(
            /\{\{q:[0-9a-f]+\}\}/g,
            'a query'
          ) || 'RDST kept only the summary of this report.'}
        </p>
        <Button
          label="Open the latest day"
          modifier="ghost"
          onClick={onShowLatest}
        />
      </div>
    )

  return (
    <DaySnapshot
      report={report.data}
      snapshot={snapshot}
      target={target}
      days={days}
      onPick={onPick}
    />
  )
}
