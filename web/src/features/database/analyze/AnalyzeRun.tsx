// Analyze database as an experience (DESIGN_V4 section 4): a full-width pipeline of
// five stations over real progress from the run's event stream. Picked queries fly
// in as cards; each card flips from "analyzing" to its verdict as results land. Health
// checks tick in, then the summary types itself in when the backend streams it, or
// waits honestly when it doesn't. The finished report replaces this view in place.

import { cn } from '@rs/tailwind-base'
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Text } from '@rs/ui-new/text'
import { formatElapsed, formatShare } from '@/features/monitor-kit/format'
import type { ReportRun } from '../reports/useReportRun'
import {
  currentStation,
  overallProgress,
  type Station,
  stations,
} from './pipelineModel'
import {
  type AnalysisRow,
  activityLabel,
  elapsedSeconds,
  isTerminalRow,
  type RunView,
  runCounts,
} from './runModel'
import { useTicker } from './useTicker'

const EASE_OUT = [0.16, 1, 0.3, 1] as const

const PHASE_LABEL: Record<string, string> = {
  queued: 'Waiting for an analyzer',
  sample: 'Sampling values',
  plan: 'Reading the plan',
  run: 'Running with sampled values',
  schema: 'Reading the schema',
  advise: 'Writing advice',
  verify: 'Testing fixes',
}

function StationNode({ station, index }: { station: Station; index: number }) {
  const reduced = useReducedMotion()
  const { state } = station
  return (
    <span
      className={cn(
        'relative z-10 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border-2 bg-surface-layout-1 text-label-small tabular-nums transition-colors duration-normal ease-out',
        state === 'done' &&
          'border-content-positive-plain bg-surface-positive-soft text-content-positive-soft',
        state === 'running' &&
          'border-content-rising-soft text-content-rising-soft',
        state === 'failed' &&
          'border-content-negative-plain text-content-negative-soft',
        (state === 'queued' || state === 'skipped') &&
          'border-border-layout-2 text-content-layout-3'
      )}
    >
      {state === 'running' && !reduced && (
        <svg
          aria-hidden="true"
          viewBox="0 0 40 40"
          className="absolute -inset-1 h-11 w-11 animate-spin [animation-duration:1.4s]"
        >
          <circle
            cx="20"
            cy="20"
            r="18"
            fill="none"
            stroke="var(--color-content-rising-soft)"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray="28 90"
          />
        </svg>
      )}
      <AnimatePresence mode="wait" initial={false}>
        <m.span
          key={state}
          initial={reduced ? { opacity: 0 } : { scale: 0.4, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.28, ease: EASE_OUT }}
          className="inline-flex"
        >
          {state === 'done' ? (
            <Icon name="tick" label="Done" className="h-4 w-4" />
          ) : state === 'failed' ? (
            <Icon name="close" label="Failed" className="h-4 w-4" />
          ) : state === 'skipped' ? (
            <span role="img" aria-label="Skipped">
              -
            </span>
          ) : (
            <span aria-hidden="true">{index + 1}</span>
          )}
        </m.span>
      </AnimatePresence>
    </span>
  )
}

function Stations({ list }: { list: Station[] }) {
  return (
    <ol
      aria-label="Analysis steps"
      className="grid grid-cols-1 gap-3 tablet:grid-cols-5 tablet:gap-0"
    >
      {list.map((station, index) => {
        const next = list[index + 1]
        const lit = station.state === 'done' || station.state === 'skipped'
        return (
          <li
            key={station.id}
            data-station={station.id}
            data-state={station.state}
            className="relative flex items-center gap-3 tablet:flex-col tablet:items-center tablet:gap-2 tablet:text-center"
          >
            {next && (
              <span
                aria-hidden="true"
                className="absolute top-[18px] left-[calc(50%+22px)] hidden h-0.5 w-[calc(100%-44px)] overflow-hidden rounded-pill bg-border-layout-1 tablet:block"
              >
                <span
                  className={cn(
                    'block h-full rounded-pill transition-[width] duration-slower ease-out',
                    lit
                      ? 'w-full bg-content-positive-plain'
                      : station.state === 'running'
                        ? 'mk-flow w-full text-content-rising-soft opacity-60'
                        : 'w-0'
                  )}
                />
              </span>
            )}
            <StationNode station={station} index={index} />
            <span className="min-w-0 max-w-full space-y-0.5 tablet:w-full tablet:px-2">
              <span
                className={cn(
                  'block text-label-medium',
                  station.state === 'queued'
                    ? 'text-content-layout-3'
                    : 'text-content-layout-1'
                )}
              >
                {station.label}
              </span>
              <span className="block min-h-4 truncate text-caption tabular-nums text-content-layout-3">
                {station.detail}
              </span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

const FINDING = /recommend|needed|missing|seq scan|full scan|slow|unused/i

function resultTone(
  row: AnalysisRow
): 'positive' | 'warning' | 'negative' | 'neutral' {
  if (row.state === 'done')
    return FINDING.test(`${row.headline ?? ''} ${row.resultLine ?? ''}`)
      ? 'warning'
      : 'positive'
  if (row.state === 'provisional') return 'warning'
  if (row.state === 'failed') return 'negative'
  return 'neutral'
}

const TONE_TEXT = {
  positive: 'text-content-positive-soft',
  warning: 'text-content-warning-soft',
  negative: 'text-content-negative-soft',
  neutral: 'text-content-layout-2',
} as const

function QueryCard({ row, rank }: { row: AnalysisRow; rank: number }) {
  const reduced = useReducedMotion()
  const finished = isTerminalRow(row.state)
  const tone = resultTone(row)
  const face = finished ? 'result' : row.state
  const verdict =
    row.headline ??
    row.resultLine ??
    (row.state === 'done'
      ? 'Analyzed'
      : row.state === 'provisional'
        ? 'Provisional result'
        : row.state === 'skipped'
          ? 'Not analyzed'
          : "Didn't finish")
  return (
    <m.li
      layout={reduced ? false : 'position'}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 18, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        duration: 0.4,
        ease: EASE_OUT,
        delay: reduced ? 0 : Math.min(rank, 12) * 0.04,
      }}
      className="[perspective:900px]"
      data-query-card={row.key}
      data-state={row.state}
    >
      <div
        className={cn(
          'relative flex h-full min-h-[118px] flex-col gap-2 overflow-hidden rounded-panel border bg-surface-layout-1 p-3.5 transition-[border-color,box-shadow] duration-normal ease-out',
          row.state === 'running'
            ? 'mk-shimmer border-border-rising-soft shadow-elevation-1'
            : finished
              ? 'border-border-layout-1'
              : 'border-border-layout-1 opacity-70'
        )}
      >
        <div className="flex items-center gap-2">
          <span className="text-label-small tabular-nums text-content-layout-3">
            #{rank}
          </span>
          <span className="min-w-0 flex-1 truncate text-label-medium font-semibold text-content-layout-1">
            {row.displayName}
          </span>
          <span className="shrink-0 text-label-small tabular-nums text-content-layout-2">
            {formatShare(row.dbTimeShare)}
          </span>
        </div>
        <div
          className="h-1 overflow-hidden rounded-pill bg-surface-layout-2"
          aria-hidden="true"
        >
          <div
            className="h-full rounded-pill bg-content-layout-2 transition-[width] duration-slow ease-out"
            style={{
              width: `${Math.max(2, Math.min(100, row.dbTimeShare * 100))}%`,
            }}
          />
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <m.div
            key={face}
            initial={reduced ? { opacity: 0 } : { rotateX: -90, opacity: 0 }}
            animate={{ rotateX: 0, opacity: 1 }}
            exit={reduced ? { opacity: 0 } : { rotateX: 90, opacity: 0 }}
            transition={{ duration: 0.3, ease: EASE_OUT }}
            className="flex min-h-10 origin-center items-start gap-2 [backface-visibility:hidden]"
          >
            {finished ? (
              <>
                <span
                  className={cn('mt-0.5 inline-flex shrink-0', TONE_TEXT[tone])}
                >
                  <Icon
                    name={
                      row.state === 'done' && tone === 'positive'
                        ? 'tick'
                        : row.state === 'done'
                          ? 'info'
                          : row.state === 'skipped'
                            ? 'lock-sync'
                            : 'alert'
                    }
                    label=""
                    aria-hidden="true"
                    className="h-4 w-4"
                  />
                </span>
                <span className="min-w-0 max-w-full space-y-0.5 tablet:w-full tablet:px-2">
                  <span
                    className={cn('block text-body-small', TONE_TEXT[tone])}
                  >
                    {verdict.charAt(0).toUpperCase() + verdict.slice(1)}
                  </span>
                  {row.reason && row.state !== 'done' && (
                    <span className="line-clamp-2 block text-caption text-content-layout-3">
                      {row.reason}
                    </span>
                  )}
                  {row.durationS != null && row.state === 'done' && (
                    <span className="block text-caption tabular-nums text-content-layout-3">
                      {row.durationS < 1
                        ? 'under a second'
                        : `${Math.round(row.durationS)} s`}
                    </span>
                  )}
                </span>
              </>
            ) : row.state === 'running' ? (
              <span className="flex items-center gap-2 text-body-small text-content-rising-soft">
                <span className="inline-flex h-2 w-2 animate-pulse rounded-full bg-content-rising-soft" />
                {PHASE_LABEL[row.phase ?? ''] ?? 'Analyzing'}
              </span>
            ) : (
              <span className="text-body-small text-content-layout-3">
                Waiting its turn
              </span>
            )}
          </m.div>
        </AnimatePresence>
      </div>
    </m.li>
  )
}

function HealthPills({ view }: { view: RunView }) {
  if (!view.healthChecks.length) return null
  return (
    <ul aria-label="Health checks" className="flex flex-wrap gap-2">
      {view.healthChecks.map((check) => {
        const done = check.state === 'done'
        return (
          <m.li
            key={check.check}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.25, ease: EASE_OUT }}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-label-small transition-colors duration-normal',
              done
                ? 'border-border-positive-soft text-content-layout-1'
                : check.state === 'failed'
                  ? 'border-border-negative-soft text-content-negative-soft'
                  : 'border-border-layout-1 text-content-layout-3'
            )}
            data-state={check.state}
          >
            {done ? (
              <span className="inline-flex text-content-positive-plain">
                <Icon
                  name="tick"
                  label=""
                  aria-hidden="true"
                  className="h-3 w-3"
                />
              </span>
            ) : check.state === 'running' ? (
              <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-content-rising-soft" />
            ) : null}
            {check.label}
          </m.li>
        )
      })}
    </ul>
  )
}

function SummaryWriter({ view, nowMs }: { view: RunView; nowMs: number }) {
  const step = view.steps.narrative
  if (step.state === 'queued' && !view.narrative) return null
  const writing = step.state === 'running'
  const since =
    step.startedAtMs != null
      ? Math.max(0, Math.round((nowMs - step.startedAtMs) / 1000))
      : 0
  return (
    <div className="rounded-panel border border-border-rising-soft bg-surface-rising-soft/30 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="inline-flex text-content-rising-soft">
          <Icon
            name="sparkles"
            label=""
            aria-hidden="true"
            className="h-4 w-4"
          />
        </span>
        <Text as="span" level="label-medium" className="text-content-layout-1">
          {writing ? 'Writing the summary' : 'Summary written'}
        </Text>
        {writing && (
          <span className="ml-auto text-caption tabular-nums text-content-layout-3">
            {formatElapsed(since)}
          </span>
        )}
      </div>
      {view.narrative ? (
        <p
          className="text-body-medium text-content-layout-1"
          aria-live="polite"
        >
          {view.narrative}
          {writing && <span className="mk-caret text-content-rising-soft" />}
        </p>
      ) : writing ? (
        <p className="flex items-center gap-2 text-body-small text-content-layout-2">
          <span className="inline-flex gap-1" aria-hidden="true">
            {[0, 1, 2].map((dot) => (
              <span
                key={dot}
                className="h-1.5 w-1.5 animate-bounce rounded-full bg-content-rising-soft"
                style={{ animationDelay: `${dot * 120}ms` }}
              />
            ))}
          </span>
          RDST's AI is turning the findings into a short report. This usually
          takes under a minute.
        </p>
      ) : null}
    </div>
  )
}

export function AnalyzeRun({
  target,
  run,
  onShowReport,
}: {
  target: string
  run: ReportRun
  /** Opens the finished report (the run view steps aside). */
  onShowReport: () => void
}) {
  const view = run.view
  const nowMs = useTicker(run.active, 1000)
  const reduced = useReducedMotion()
  if (!view) return null
  const list = stations(view)
  const counts = runCounts(view)
  const progress = overallProgress(view)
  const current = currentStation(view)
  const elapsed = elapsedSeconds(view, nowMs)
  const finished = !run.active
  const title =
    run.status === 'done'
      ? 'Report ready'
      : run.status === 'failed'
        ? "The analysis didn't finish"
        : run.status === 'cancelled'
          ? 'Analysis cancelled'
          : `Analyzing ${target}`

  return (
    <section
      aria-label="Analyze database"
      aria-busy={run.active}
      className="overflow-hidden rounded-card border border-border-layout-1 bg-surface-layout-1 shadow-elevation-1"
      data-testid="analyze-run"
      data-status={run.status}
    >
      <div
        className="h-1 w-full bg-surface-layout-2"
        role="progressbar"
        aria-label="Analysis progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
      >
        <div
          className={cn(
            'h-full transition-[width] duration-slower ease-out',
            run.status === 'failed'
              ? 'bg-content-negative-plain'
              : run.status === 'done'
                ? 'bg-content-positive-plain'
                : 'bg-content-rising-soft'
          )}
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>
      <div className="space-y-7 p-4 tablet:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className={cn(
                'relative inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border',
                run.status === 'done'
                  ? 'border-border-positive-soft bg-surface-positive-soft text-content-positive-soft'
                  : run.status === 'failed'
                    ? 'border-border-negative-soft text-content-negative-soft'
                    : 'border-border-rising-soft bg-surface-rising-soft text-content-rising-soft'
              )}
            >
              {run.active && !reduced && (
                <span className="absolute inset-0 animate-ping rounded-2xl border border-border-rising-soft [animation-duration:2s]" />
              )}
              <Icon
                name={
                  run.status === 'done'
                    ? 'tick'
                    : run.status === 'failed'
                      ? 'alert'
                      : 'sparkles'
                }
                label=""
                aria-hidden="true"
                className="h-5 w-5"
              />
            </span>
            <div className="min-w-0">
              <Text
                as="h2"
                level="headline-4"
                className="truncate text-content-layout-1"
              >
                {title}
              </Text>
              <Text
                as="p"
                level="body-small"
                className="text-content-layout-2"
                aria-live="polite"
              >
                {finished && run.status === 'done'
                  ? `${counts.done} of ${counts.total} analyses finished in ${formatElapsed(elapsed)}.${counts.failed ? ` ${counts.failed} failed.` : ''}${counts.skipped ? ` ${counts.skipped} skipped.` : ''}${counts.provisional ? ` ${counts.provisional} couldn't finish.` : ''}`
                  : activityLabel(view)}
              </Text>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-mono-medium tabular-nums text-content-layout-2">
              {formatElapsed(elapsed)}
            </span>
            <span className="text-label-medium tabular-nums text-content-layout-1">
              {Math.round(progress * 100)}%
            </span>
            {run.active ? (
              <Button
                label={run.status === 'cancelling' ? 'Cancelling' : 'Cancel'}
                modifier="outline"
                size="small"
                disabled={run.status !== 'running'}
                onClick={() => void run.cancel()}
              />
            ) : run.status === 'done' ? (
              <Button
                label="Read the report"
                size="small"
                onClick={onShowReport}
              />
            ) : (
              <Button
                label={run.status === 'failed' ? 'Try again' : 'Analyze again'}
                size="small"
                onClick={() => void run.start()}
              />
            )}
          </div>
        </div>

        <Stations list={list} />

        {view.budgetMessage && (
          <Alert
            variant="informative"
            modifier="outline"
            icon="info"
            iconPosition="left"
            label={view.budgetMessage}
          />
        )}

        {view.queries.length > 0 ? (
          <div className="space-y-3">
            <div className="flex items-baseline justify-between gap-2">
              <Text
                as="h3"
                level="overline"
                className="uppercase tracking-wider text-content-layout-3"
              >
                The queries that matter
              </Text>
              <span className="text-caption tabular-nums text-content-layout-3">
                {counts.done} of {counts.total} analyses finished
                {counts.skipped ? `, ${counts.skipped} skipped` : ''}
                {counts.failed ? `, ${counts.failed} failed` : ''}
              </span>
            </div>
            <ul className="grid grid-cols-1 gap-3 tablet:grid-cols-2 laptop:grid-cols-3">
              {view.queries.map((row, index) => (
                <QueryCard key={row.key} row={row} rank={index + 1} />
              ))}
            </ul>
          </div>
        ) : current === 'history' || current === 'select' ? (
          <ul
            aria-hidden="true"
            className="grid grid-cols-1 gap-3 tablet:grid-cols-2 laptop:grid-cols-3"
          >
            {[0, 1, 2].map((index) => (
              <li
                key={index}
                className="mk-shimmer h-[118px] rounded-panel border border-dashed border-border-layout-1"
              />
            ))}
          </ul>
        ) : null}

        <HealthPills view={view} />
        <SummaryWriter view={view} nowMs={nowMs} />

        {run.status === 'failed' && (
          <Alert
            variant="negative"
            modifier="outline"
            icon="alert"
            iconPosition="left"
            label="The run stopped before it wrote a report. Nothing on your database was changed."
          />
        )}
      </div>
    </section>
  )
}
