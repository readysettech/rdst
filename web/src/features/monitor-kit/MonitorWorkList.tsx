// Monitoring's real work, app-wide: deep analyses running and queued across every database,
// reports being written, and what finished today. One list, shown from the sidebar's
// Analyses chip and, filtered to one database, from the Database page header. Every entry
// opens its query on that database.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useState } from 'react'
import { scoreBand } from '@/features/database/queries/ScorePill'
import { useTarget } from '@/hooks/useTarget'
import { useMonitorWork } from '@/lib/monitor/hooks'
import './charts/monitorKit.css'
import type { MonitorWork, MonitorWorkAnalysis } from '@/lib/monitor/types'

const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })

/** Running plus queued analyses and reports: the chip's count. Finished work never counts. */
export function workCount(work: MonitorWork | undefined, target?: string) {
  if (!work) return 0
  const mine = <T extends { target: string }>(rows: T[]) =>
    target ? rows.filter((row) => row.target === target) : rows
  return (
    mine(work.running).length +
    mine(work.queued).length +
    mine(work.reports).length
  )
}

/** "1 running, 4 queued", or "Nothing queued". */
export function analysisSummary(
  work: MonitorWork | undefined,
  target?: string
): string {
  const mine = <T extends { target: string }>(rows: T[] = []) =>
    target ? rows.filter((row) => row.target === target) : rows
  const running = mine(work?.running).length
  const queued = mine(work?.queued).length
  const parts = [
    running ? `${running} running` : null,
    queued ? `${queued} queued` : null,
  ].filter(Boolean)
  return parts.length ? parts.join(', ') : 'Nothing queued'
}

function useOpenWork(onOpened?: () => void) {
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const { target: current, setTarget } = useTarget()
  const switchTo = (target: string) => {
    if (target !== current) setTarget(target)
    onOpened?.()
  }
  return {
    // A finished analysis opens on its result; a waiting one is brought into view and
    // highlighted in the list. Either way the list keeps its filters and sort.
    query: (target: string, key: string, finished: boolean) => {
      switchTo(target)
      const onDatabase = pathname === '/database'
      void navigate({
        to: '/database',
        search: ((previous: Record<string, unknown>) => ({
          ...(onDatabase ? previous : {}),
          ...(finished
            ? { query: key, tab: 'analysis', focus: undefined }
            : { focus: key }),
        })) as never,
      })
    },
    report: (target: string) => {
      switchTo(target)
      void navigate({ to: '/database', search: { view: 'report' } })
    },
  }
}

function Entry({
  label,
  name,
  database,
  you,
  live = false,
  score,
  onOpen,
}: {
  label: string
  name: string
  database: string | null
  you: boolean
  live?: boolean
  score?: number | null
  onOpen: () => void
}) {
  return (
    <Pressable
      type="button"
      onClick={onOpen}
      data-testid="work-entry"
      className="flex w-full min-w-0 items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-fast hover:bg-surface-layout-2"
    >
      <span
        aria-hidden="true"
        className={cn(
          'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
          live
            ? 'bg-content-rising-plain mk-breathe'
            : score != null
              ? 'bg-content-positive-plain'
              : 'border border-content-layout-3'
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-label-small text-content-layout-1">
          {name}
        </span>
        <span className="block truncate text-caption text-content-layout-3">
          {[label, database, you ? 'you started it' : null]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </span>
      {score != null ? (
        <span
          className={cn(
            'shrink-0 rounded-control border px-1.5 text-label-small tabular-nums',
            score >= 70
              ? 'border-border-positive-soft text-content-positive-soft'
              : scoreBand(score) === 'fair'
                ? 'border-border-warning-soft text-content-warning-soft'
                : 'border-border-negative-soft text-content-negative-soft'
          )}
        >
          {Math.round(score)}
        </span>
      ) : null}
    </Pressable>
  )
}

function Group({
  title,
  summary,
  open,
  onToggle,
  live = false,
  children,
  testId,
}: {
  title: string
  summary: string
  open: boolean
  onToggle: () => void
  live?: boolean
  children: React.ReactNode
  testId: string
}) {
  return (
    <div data-testid={testId}>
      <Pressable
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors duration-fast hover:bg-surface-layout-2"
      >
        <Icon
          name="chevron-right"
          label=""
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-content-layout-3 transition-transform duration-fast',
            open && 'rotate-90'
          )}
        />
        <Text level="label-small" className="text-content-layout-1">
          {title}
        </Text>
        <Text
          level="caption"
          className={cn(
            'ml-auto truncate',
            live ? 'text-content-rising-soft' : 'text-content-layout-3'
          )}
        >
          {summary}
        </Text>
      </Pressable>
      {open ? <div className="space-y-0.5 pb-1 pl-3">{children}</div> : null}
    </div>
  )
}

/**
 * The list itself. `target` narrows it to one database (and drops the database labels);
 * `onOpened` closes the host when an entry opens its query.
 */
export function MonitorWorkList({
  target,
  onOpened,
  defaultOpen = false,
}: {
  target?: string
  onOpened?: () => void
  /** Start with the deep analysis group expanded. */
  defaultOpen?: boolean
}) {
  const work = useMonitorWork().data
  const opener = useOpenWork(onOpened)
  const open = opener.query
  const openReport = opener.report
  const [analysesOpen, setAnalysesOpen] = useState(defaultOpen)
  const [doneOpen, setDoneOpen] = useState(false)
  const mine = <T extends { target: string }>(rows: T[] = []) =>
    target ? rows.filter((row) => row.target === target) : rows
  const running = mine(work?.running)
  const queued = mine(work?.queued)
  const retrying = mine(work?.retrying)
  const reports = mine(work?.reports)
  const done = mine(work?.done_today)
  const doneCount = target ? done.length : (work?.done_today_count ?? 0)
  const dbLabel = (row: { target: string }) => (target ? null : row.target)
  const entry = (job: MonitorWorkAnalysis, label: string, live = false) => (
    <Entry
      key={job.id}
      label={label}
      name={job.display_name}
      database={dbLabel(job)}
      you={job.source === 'you'}
      live={live}
      score={job.state === 'done' ? (job.efficiency_score ?? null) : undefined}
      onOpen={() => open(job.target, job.query_key, job.state === 'done')}
    />
  )
  const now = Date.now() / 1000

  return (
    <div className="space-y-1 p-2" data-testid="monitor-work">
      <Group
        testId="work-analyses"
        title="Deep analysis"
        summary={analysisSummary(work, target)}
        live={running.length > 0}
        open={analysesOpen}
        onToggle={() => setAnalysesOpen((value) => !value)}
      >
        {running.map((job) =>
          entry(
            job,
            job.started_at
              ? `Analyzing now, ${Math.max(1, Math.round((now - job.started_at) / 60))} min`
              : 'Analyzing now',
            true
          )
        )}
        {queued.map((job) => entry(job, 'Queued'))}
        {retrying.map((row) => (
          <Entry
            key={`${row.target}:${row.query_key}`}
            label={`Will retry at ${clock(row.retry_at)}`}
            name={row.display_name}
            database={dbLabel(row)}
            you={false}
            onOpen={() => open(row.target, row.query_key, false)}
          />
        ))}
        {!running.length && !queued.length && !retrying.length ? (
          <Text
            as="p"
            level="caption"
            className="px-2 py-1.5 text-content-layout-3"
          >
            Nothing is waiting. New and busy queries join the line as RDST finds
            them.
          </Text>
        ) : null}
      </Group>
      {reports.map((report) => (
        <Pressable
          key={report.id}
          type="button"
          data-testid="work-report"
          onClick={() => openReport(report.target)}
          className="flex w-full min-w-0 items-start gap-2 rounded-lg px-2 py-2 text-left transition-colors duration-fast hover:bg-surface-layout-2"
        >
          <span
            aria-hidden="true"
            className="mt-1.5 ml-1 h-1.5 w-1.5 shrink-0 rounded-full bg-content-rising-plain mk-breathe"
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-label-small text-content-layout-1">
              {report.trigger === 'nightly' ? 'Nightly checkup' : 'Checkup'}
              {target ? '' : ` for ${report.target}`}: running
            </span>
            {report.message ? (
              <span className="block truncate text-caption text-content-layout-3">
                {report.message}
              </span>
            ) : null}
          </span>
        </Pressable>
      ))}
      {doneCount > 0 ? (
        <Group
          testId="work-done"
          title="Done today"
          summary={String(doneCount)}
          open={doneOpen}
          onToggle={() => setDoneOpen((value) => !value)}
        >
          {done.map((job) =>
            entry(
              job,
              job.finished_at
                ? `Finished ${clock(job.finished_at)}`
                : 'Finished'
            )
          )}
        </Group>
      ) : null}
    </div>
  )
}
