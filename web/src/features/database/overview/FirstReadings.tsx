// The first run on the Database page: a short plain intro above the live chart and three
// steps (finding queries, the first deep analyses, tonight's report), each with an honest
// loader and real progress. Once its steps are done it collapses to one line; after the
// first hour it's gone. When the database hasn't run a query yet, a friendly card says RDST
// is connected and waiting, and how to send it some traffic. Under reduced motion
// everything is still.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { useReducedMotion } from '@rs/ui-new/motion'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  formatClock,
  formatElapsed,
  formatWeekdayClock,
} from '@/features/monitor-kit/format'
import { RollingNumber } from '@/features/monitor-kit/RollingNumber'
import type { MonitorPulseState } from '@/features/monitor-kit/useMonitorPulse'
import { getPulses } from '@/lib/monitor/api'
import { useAnalyzeJobs } from '@/lib/monitor/hooks'
import type {
  HistorySpan,
  MonitorPulse,
  MonitorQuery,
} from '@/lib/monitor/types'
import {
  type FirstRunModel,
  type FirstRunSettings,
  type FirstRunStep,
  firstRun,
  type StepState,
} from './firstReadingsModel'

/** Enough pulses to count every reading of a first run at the fastest cadence. */
const PULSE_LIMIT = 50

function useFirstRunPulses(target: string, beat: number) {
  const query = useQuery({
    queryKey: ['monitor', target, 'pulse', 'first-run'],
    queryFn: () => getPulses(target, PULSE_LIMIT),
    refetchInterval: 15_000,
  })
  const { refetch } = query
  useEffect(() => {
    if (beat) void refetch()
  }, [beat, refetch])
  return query.data?.pulses
}

function merge(a: MonitorPulse[], b: MonitorPulse[] | undefined) {
  const seen = new Set<string>()
  return [...a, ...(b ?? [])].filter((pulse) => {
    const id = `${pulse.kind}:${pulse.ts}`
    if (seen.has(id)) return false
    seen.add(id)
    return true
  })
}

/** When a pending step is expected: "at 9:05 AM", "Midnight tonight". */
function when(at: number, now: number): string {
  const ahead = at - now
  if (ahead <= 0) return 'Any moment'
  if (ahead < 3600) return `at ${formatClock(at)}`
  const day = (ts: number) => new Date(ts * 1000).toDateString()
  const date = new Date(at * 1000)
  if (date.getHours() === 0 && date.getMinutes() === 0 && ahead <= 86_400)
    return 'Tonight at midnight'
  if (day(at) === day(now)) return `Today ${formatClock(at)}`
  if (day(at) === day(now + 86_400)) return `Tomorrow ${formatClock(at)}`
  return formatWeekdayClock(at)
}

function stepMeta(step: FirstRunStep, now: number): string | null {
  if (step.meta || step.state === 'done') return step.meta
  return step.at != null ? when(step.at, now) : null
}

function StepMark({ state, reduced }: { state: StepState; reduced: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'relative flex h-5 w-5 shrink-0 items-center justify-center rounded-full border bg-surface-layout-1',
        state === 'done' &&
          'border-border-positive-soft bg-surface-positive-soft text-content-positive-plain',
        state === 'active' && 'border-border-layout-2',
        state === 'pending' && 'border-border-layout-2',
        state === 'blocked' &&
          'border-border-warning-soft bg-surface-warning-soft',
        state === 'off' && 'border-dashed border-border-layout-2'
      )}
    >
      {state === 'done' ? (
        <svg viewBox="0 0 20 20" className="h-3.5 w-3.5">
          <path
            d="M5.5 10.4l3 3L14.5 7"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.4}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : state === 'active' ? (
        <>
          {/* A loader that keeps turning while the step is genuinely at work. */}
          <span
            className={cn(
              'absolute -inset-px rounded-full border-2 border-transparent border-t-content-rising-plain',
              !reduced && 'animate-spin'
            )}
          />
          <span className="h-1.5 w-1.5 rounded-full bg-content-rising-plain" />
        </>
      ) : state === 'blocked' ? (
        <span className="h-1.5 w-1.5 rounded-full bg-content-warning-plain" />
      ) : null}
    </span>
  )
}

const STATE_WORD: Record<StepState, string> = {
  done: 'Done',
  active: 'In progress',
  pending: 'Coming up',
  blocked: 'Needs attention',
  off: 'Off',
}

function Steps({
  steps,
  now,
  reduced,
  onOpenQueue,
}: {
  steps: FirstRunStep[]
  now: number
  reduced: boolean
  onOpenQueue?: () => void
}) {
  return (
    <ol
      aria-label="First run steps"
      className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2"
    >
      {steps.map((step, index) => {
        const meta = stepMeta(step, now)
        const openable =
          step.id === 'deep' && onOpenQueue && step.state !== 'off'
        const tone =
          step.state === 'pending' || step.state === 'off'
            ? 'text-content-layout-2'
            : 'text-content-layout-1'
        return (
          <li
            key={step.id}
            data-step={step.id}
            data-state={step.state}
            title={step.sub ?? undefined}
            className="flex min-w-0 items-center gap-2"
          >
            <StepMark state={step.state} reduced={reduced} />
            <span className="sr-only">
              Step {index + 1}, {STATE_WORD[step.state]}:{' '}
            </span>
            {openable ? (
              <button
                type="button"
                onClick={onOpenQueue}
                title="See what's analyzing and what's next"
                className={cn(
                  'rounded-control text-left text-label-medium underline decoration-border-layout-2 decoration-dotted underline-offset-4 transition-colors hover:text-content-primary-soft hover:decoration-current focus-visible:shadow-focus focus-visible:outline-none',
                  tone
                )}
              >
                {step.label}
              </button>
            ) : (
              <Text as="span" level="label-medium" className={tone}>
                {step.label}
              </Text>
            )}
            {meta ? (
              <Text
                as="span"
                level="caption"
                className={cn(
                  'tabular-nums',
                  step.state === 'blocked'
                    ? 'text-content-warning-soft'
                    : 'text-content-layout-3'
                )}
              >
                {meta}
              </Text>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

export interface FirstRunProps {
  target: string
  settings: FirstRunSettings | undefined
  history: HistorySpan | null | undefined
  pulse: MonitorPulseState
  /** Every row of the query list, writes included; null while loading. */
  queries: MonitorQuery[] | null | undefined
  nextReadingAt?: number | null
}

/** The model the strip and the waiting card share. */
export function useFirstRun({
  target,
  settings,
  history,
  pulse,
  queries,
  nextReadingAt,
}: FirstRunProps): FirstRunModel {
  const extra = useFirstRunPulses(target, pulse.beat)
  const jobs = useAnalyzeJobs(target)
  const pulses = useMemo(
    () => merge(pulse.pulses, extra),
    [pulse.pulses, extra]
  )
  return firstRun({
    now: pulse.now,
    settings,
    history,
    pulses,
    nextReadingAt: pulse.nextAt ?? nextReadingAt,
    queries,
    queue: jobs.data?.queue,
  })
}

function readingsLine(model: FirstRunModel, now: number) {
  const untilNext =
    model.nextAt != null ? Math.max(0, model.nextAt - now) : null
  const next =
    untilNext == null
      ? null
      : untilNext < 1
        ? 'reading now'
        : `next in ${formatElapsed(untilNext)}`
  return { next }
}

export function FirstRunStrip({
  model,
  now,
  onHide,
  onOpenQueue,
}: {
  model: FirstRunModel
  now: number
  onHide?: () => void
  /** Opens the deep analysis list from its step. */
  onOpenQueue?: () => void
}) {
  const reduced = Boolean(useReducedMotion())
  // Collapsed to the step under way; Details shows all three.
  const [expanded, setExpanded] = useState(false)
  // The first step not finished yet, in order: what's happening now.
  const current =
    model.steps.find((step) => step.state !== 'done' && step.state !== 'off') ??
    model.steps[model.steps.length - 1]
  const report = model.steps.find((step) => step.id === 'report')
  const hide = onHide ? (
    <Button
      variant="primary"
      modifier="ghost"
      size="small"
      label="Hide"
      onClick={onHide}
      className="ml-auto"
    />
  ) : null
  if (model.complete) {
    // Its steps are done: one quiet line until the hour is up or the user hides it.
    const reportLine =
      report?.state === 'pending' && report.at != null
        ? ` The nightly report comes ${when(report.at, now).toLowerCase()}.`
        : ''
    return (
      <section
        data-testid="first-run"
        data-complete="true"
        aria-label="First run"
        className="flex items-center gap-3 rounded-card border border-border-layout-1 bg-surface-layout-1 px-4 py-2.5 tablet:px-5"
      >
        <StepMark state="done" reduced={reduced} />
        <Text
          as="p"
          level="body-small"
          className="min-w-0 text-content-layout-2"
        >
          <span className="text-content-layout-1">First run done.</span> RDST
          found your queries.{' '}
          {model.steps.find((step) => step.id === 'deep')?.state === 'off'
            ? 'Deep analysis is off.'
            : `${model.steps.find((step) => step.id === 'deep')?.meta ?? 'The first analyses finished'}.`}
          {reportLine}
        </Text>
        {hide}
      </section>
    )
  }
  return (
    <section
      data-testid="first-run"
      data-expanded={expanded || undefined}
      aria-label="First run"
      className="flex items-center gap-3 rounded-card border border-border-layout-1 bg-surface-layout-1 px-4 py-2.5 tablet:px-5"
    >
      <Steps
        steps={expanded ? model.steps : [current]}
        now={now}
        reduced={reduced}
        onOpenQueue={onOpenQueue}
      />
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((open) => !open)}
        className="inline-flex shrink-0 items-center gap-1 rounded-control px-1.5 py-0.5 text-caption text-content-layout-3 hover:text-content-layout-1 focus-visible:shadow-focus focus-visible:outline-none"
      >
        {expanded ? 'Less' : 'Details'}
        <Icon
          name="chevron-down"
          label=""
          aria-hidden="true"
          className={cn(
            'h-3.5 w-3.5 transition-transform',
            expanded && 'rotate-180'
          )}
        />
      </button>
      {hide}
    </section>
  )
}

/** Queries are known and the reading that ranks them is seconds away. */
export function MeasuringQueries() {
  const reduced = Boolean(useReducedMotion())
  return (
    <section
      data-testid="measuring-queries"
      aria-live="polite"
      className="flex items-center gap-3 rounded-card border border-border-layout-1 bg-surface-layout-1 px-5 py-6"
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-2 w-2 shrink-0 rounded-full bg-content-positive-plain',
          !reduced && 'mk-breathe'
        )}
      />
      <Text as="p" level="body-small" className="text-content-layout-2">
        RDST is gathering your queries.
      </Text>
    </section>
  )
}

/** The database is monitored and hasn't run a query yet. */
export function WaitingForQueries({
  target,
  model,
  now,
}: {
  target: string
  model: FirstRunModel
  now: number
}) {
  const reduced = Boolean(useReducedMotion())
  const { next } = readingsLine(model, now)
  const seconds = Math.max(10, Math.round(model.interval))
  return (
    <section
      data-testid="waiting-for-queries"
      aria-labelledby="waiting-for-queries-title"
      className="rounded-card border border-border-layout-1 bg-surface-layout-1 p-5 tablet:p-8"
    >
      <div className="grid gap-8 laptop:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] laptop:gap-12">
        <div className="space-y-4">
          <div className="space-y-2">
            <Text
              as="p"
              level="overline"
              className="flex items-center gap-2 text-content-positive-plain"
            >
              <span
                aria-hidden="true"
                className={cn(
                  'h-2 w-2 rounded-full bg-content-positive-plain',
                  !reduced && 'mk-breathe'
                )}
              />
              Connected
            </Text>
            <h2
              id="waiting-for-queries-title"
              className="text-headline-3 text-content-layout-1"
            >
              Waiting for queries
            </h2>
            <Text
              as="p"
              level="body-medium"
              className="max-w-xl text-content-layout-2"
            >
              RDST is reading {target}, and nothing has run on it yet. Point
              your app or your test suite at this database, and the queries show
              up here within {seconds} seconds, with their load on the chart.
            </Text>
          </div>
          <Text
            as="p"
            level="body-small"
            className="flex items-center gap-1 tabular-nums text-content-layout-3"
            aria-live="polite"
          >
            <RollingNumber value={String(model.readings)} />
            <span>
              {model.readings === 1 ? 'reading' : 'readings'} so far
              {next ? `, ${next}` : ''}
            </span>
          </Text>
        </div>
        <div className="space-y-3 border-border-layout-1 max-laptop:border-t max-laptop:pt-6 laptop:border-l laptop:pl-10">
          <Text as="h3" level="label-medium" className="text-content-layout-1">
            Send it some real traffic
          </Text>
          <ol className="list-decimal space-y-2 pl-5 text-body-small text-content-layout-2 marker:text-content-layout-3">
            <li>
              Start your app with this database as its connection, and click
              through the pages that matter.
            </li>
            <li>
              Or run your test suite or a load script against it. Real workloads
              give the most useful picture.
            </li>
            <li>
              Each new query lands in the list as monitoring reads your
              workload.
            </li>
          </ol>
        </div>
      </div>
    </section>
  )
}
