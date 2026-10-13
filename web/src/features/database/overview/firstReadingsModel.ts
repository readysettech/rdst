// The Database page's first run: a few plain steps for the minutes after monitoring is
// turned on (finding queries, the first deep analyses, the first report), each with an
// honest state and real progress. Everything derives from the monitor API (settings,
// health history, collector pulses, the query list and the analysis queue); nothing is
// estimated beyond the collector interval and the stored levers. AI steps follow what can
// run now, so a signed-out or AI-off database never reads as analyzing.
import {
  type AiBlocker,
  type AiCanRun,
  aiCanRun,
  nextReportAt,
} from '@/features/monitor-kit/aiMonitoring'
import type {
  AnalyzeQueueEntry,
  AnalyzeQueueSummary,
  HistorySpan,
  MonitorPulse,
  MonitorQuery,
  MonitorSettings,
} from '@/lib/monitor/types'

/** Minutes of readings the load chart's first full view covers. */
export const CHART_READY_MINUTES = 5
const CHART_READY_S = CHART_READY_MINUTES * 60
/** The first-run steps show for this long after monitoring is turned on. */
export const FIRST_RUN_S = 3600
const DEFAULT_INTERVAL_S = 60

/** Settings as served; the turn-on time is optional in the typed contract. */
export type FirstRunSettings = Pick<
  MonitorSettings,
  'enabled' | 'attention_every_min' | 'nightly_report' | 'nightly_hour'
> & {
  deep_analysis?: boolean
  enabled_at?: number | null
  first_hour_started_at?: number | null
  ai_monitoring?: MonitorSettings['ai_monitoring']
  ai_reason?: MonitorSettings['ai_reason']
  ai_since?: MonitorSettings['ai_since']
}

/** Monitoring is on and the chart has less than its first five minutes of history. */
export function isFirstRun(
  enabled: boolean,
  history: HistorySpan | null | undefined
): boolean {
  if (!enabled || !history) return false
  if (!history.first_ts) return true
  return history.span_seconds < CHART_READY_S
}

/** The steps show for the first hour after turning monitoring on (or, without a turn-on
 * time on record, until the chart has its first five minutes). Only a true first run shows
 * them: a database with readings from before the turn-on already has a known workload. */
export function showFirstRun(
  settings: FirstRunSettings | undefined,
  history: HistorySpan | null | undefined,
  now: number
): boolean {
  if (!settings?.enabled) return false
  if (settings.first_hour_started_at !== undefined) {
    return (
      settings.first_hour_started_at != null &&
      now - settings.first_hour_started_at < FIRST_RUN_S
    )
  }
  if (settings.enabled_at != null) {
    const first = history?.first_ts
    if (first && first < settings.enabled_at - 60) return false
    return now - settings.enabled_at < FIRST_RUN_S
  }
  return isFirstRun(true, history)
}

/**
 * The chart's window while it has under five minutes of history: from the first reading
 * through the next few minutes, so one or two points sit at the left with room to grow.
 */
export function firstRunDomain(
  history: HistorySpan | null | undefined,
  now: number
): [number, number] | null {
  const first = history?.first_ts
  if (first == null || history == null) return null
  if (history.span_seconds >= CHART_READY_S) return null
  return [first, Math.max(first + CHART_READY_S, now + 60)]
}

export type StepState = 'done' | 'active' | 'pending' | 'blocked' | 'off'

export interface FirstRunStep {
  id: 'queries' | 'deep' | 'report'
  label: string
  state: StepState
  /** What's happening, a few words ("2 of 3 done"). Never a bare zero with a check. */
  meta: string | null
  /** A second line: what comes next ("Top 5 at 9:01 AM, then 10"). */
  sub?: string | null
  /** Real progress for an active step, 0 to 1. */
  progress?: number | null
  /** Unix seconds it happened (done) or is expected (pending). */
  at: number | null
}

export interface FirstRunInput {
  now: number
  settings: FirstRunSettings | undefined
  history: HistorySpan | null | undefined
  /** Recent pulses of every kind, any order. */
  pulses: MonitorPulse[]
  nextReadingAt?: number | null
  /** Every row of the query list (writes included), when loaded. */
  queries:
    | (Pick<MonitorQuery, 'key' | 'assessment' | 'analyzable'> & {
        qps?: number
      })[]
    | null
    | undefined
  queue: AnalyzeQueueSummary | null | undefined
  /** What AI work can run now; read from the settings when absent. */
  can?: AiCanRun
}

export interface FirstRunModel {
  /** When the steps count from: the turn-on time, else the first reading. */
  startAt: number | null
  readings: number
  firstAt: number | null
  /** The latest successful reading. */
  lastAt: number | null
  interval: number
  nextAt: number | null
  /** The latest collector tick failed: its detail says why. */
  failure: string | null
  queriesFound: number | null
  /** Readings are coming in and the database hasn't run a query yet. */
  waitingForQueries: boolean
  /** Queries are known but no reading has measured their calls yet: the list would only
   * show them unranked, so the page waits for the reading that ranks them. */
  measuring: boolean
  steps: FirstRunStep[]
  /** Every step but the first report is done (or off): the panel collapses to one line. */
  complete: boolean
}

const QUERY_COUNT = /(\d+)\s+quer/i

function collects(pulses: MonitorPulse[]) {
  return pulses
    .filter((pulse) => pulse.kind === 'collect')
    .sort((a, b) => b.ts - a.ts)
}

/** "9:02:14 AM": first-run steps land seconds apart. */
export function clockSeconds(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** A step that can't run now: off, waiting on the user, or waiting for AI to start. */
function heldStep(
  blocker: AiBlocker
): Pick<FirstRunStep, 'state' | 'meta'> | null {
  switch (blocker) {
    case 'monitoring_off':
    case 'deep_off':
    case 'report_off':
      return { state: 'off', meta: 'Turned off' }
    case 'signed_out':
      return { state: 'blocked', meta: 'Needs sign-in' }
    case 'limit':
    case 'quota':
      return { state: 'blocked', meta: 'AI is off for this database' }
    case 'unreachable':
    case 'service_error':
    case 'not_started':
      return { state: 'pending', meta: 'Waiting for AI to start' }
    default:
      return null
  }
}

function deepStep(
  queue: AnalyzeQueueSummary | null | undefined,
  startAt: number | null,
  blocker: AiBlocker | null,
  queriesFound: boolean
): FirstRunStep {
  const base = {
    id: 'deep' as const,
    label: 'Analyzing the most expensive queries',
  }
  const held = blocker ? heldStep(blocker) : null
  if (held) return { ...base, ...held, at: null }
  if (!queue || startAt == null)
    return { ...base, state: 'pending', meta: null, at: null }
  const since = (job: AnalyzeQueueEntry) => job.enqueued_at >= startAt
  const waiting = [
    ...(queue.running ? [queue.running] : []),
    ...queue.next,
  ].filter(since)
  const ended = queue.recent.filter(since)
  const done = ended.filter((job) => job.state === 'done')
  const needsSignIn = ended.some((job) =>
    (job.error ?? '').toUpperCase().includes('LOGIN_REQUIRED')
  )
  if (!waiting.length && !ended.length)
    return queriesFound
      ? {
          ...base,
          state: 'active',
          meta: 'After the second reading',
          at: null,
        }
      : {
          ...base,
          state: 'pending',
          meta: 'Once queries show up',
          at: null,
        }
  const failed = ended.filter((job) => job.state === 'failed')
  const ramp = queue.first_hour
  const completed = ramp?.scheduled != null ? ramp.analyzed : done.length
  const failures = ramp?.failed ?? failed.length
  const total = ramp?.scheduled ?? waiting.length + ended.length
  if (failures)
    return {
      ...base,
      state: waiting.length ? 'active' : 'blocked',
      meta: `${completed} analyzed, ${failures} failed`,
      sub: needsSignIn
        ? 'Needs sign-in'
        : waiting.length
          ? null
          : 'Open the analysis list to see what failed',
      progress: total ? completed / total : 0,
      at: null,
    }
  if (!waiting.length) {
    if (!completed)
      return {
        ...base,
        state: 'pending',
        meta: 'Waiting for a query RDST can analyze',
        at: null,
      }
    const last = done.length
      ? Math.max(...done.map((job) => job.finished_at ?? 0))
      : 0
    return {
      ...base,
      state: 'done',
      meta: `${completed} analyzed`,
      at: last || null,
    }
  }
  return {
    ...base,
    state: 'active',
    meta: completed ? `${completed} analyzed` : 'Starting',
    progress: total ? completed / total : 0,
    at: null,
  }
}

export function firstRun(input: FirstRunInput): FirstRunModel {
  const { now, history, pulses, settings } = input
  const ticks = collects(pulses)
  const startAt =
    settings?.first_hour_started_at ??
    settings?.enabled_at ??
    history?.first_ts ??
    null
  const since = startAt ?? 0
  const okTicks = ticks.filter((pulse) => pulse.ok && pulse.ts >= since)
  const ramp = input.queue?.first_hour
  // The recent pulses can start after the first reading; the queue's facts say when it was.
  const firstAt =
    ramp?.first_reading_at ??
    (okTicks.length
      ? okTicks[okTicks.length - 1].ts
      : (history?.first_ts ?? null))
  const readings = Math.max(
    ramp?.readings ?? okTicks.length,
    firstAt != null ? 1 : 0
  )
  const interval =
    ticks.length >= 2
      ? Math.max(10, Math.min(600, ticks[0].ts - ticks[1].ts))
      : DEFAULT_INTERVAL_S
  const lastAt = ticks[0]?.ts ?? null
  // The first minutes read every few seconds, sooner than the served next reading.
  const candidates = [
    input.nextReadingAt,
    lastAt != null ? lastAt + interval : null,
  ].filter((value): value is number => value != null)
  const upcoming = candidates.filter((value) => value > now - 2)
  const nextAt = upcoming.length
    ? Math.min(...upcoming)
    : candidates.length
      ? Math.max(...candidates)
      : null
  const failure = ticks[0] && !ticks[0].ok ? ticks[0].detail : null

  const listed = input.queries ? input.queries.length : null
  const queriesFound = listed
  const foundTick = [...okTicks]
    .reverse()
    .find((pulse) => Number(pulse.detail.match(QUERY_COUNT)?.[1] ?? 0) > 0)
  const foundAt =
    ramp?.first_queries_at ?? foundTick?.ts ?? (listed ? firstAt : null)
  // Nothing has run when the latest reading saw no queries; a list fetched before the
  // first reading is empty for a moment and says nothing yet.
  const latestCount = okTicks[0]?.detail.match(QUERY_COUNT)?.[1]
  const waitingForQueries =
    firstAt != null &&
    listed === 0 &&
    (latestCount != null ? Number(latestCount) === 0 : readings >= 2)
  const measuring =
    !waitingForQueries &&
    listed != null &&
    listed > 0 &&
    (input.queries ?? []).every((query) => !((query.qps ?? 0) > 0))

  const can = input.can ?? aiCanRun({ settings, now })
  const deep = deepStep(input.queue, startAt, can.deep, Boolean(listed))
  const reportHeld = can.report ? heldStep(can.report) : null
  const steps: FirstRunStep[] = [
    listed
      ? {
          id: 'queries',
          label: 'Found your queries',
          state: 'done',
          meta: null,
          at: foundAt,
        }
      : {
          id: 'queries',
          label: 'Finding your queries',
          state: failure && firstAt == null ? 'blocked' : 'active',
          meta:
            failure && firstAt == null
              ? "The first reading didn't finish. RDST is trying again."
              : firstAt == null
                ? 'Reading the database'
                : 'Waiting for the database to run some',
          at: null,
        },
    deep,
    reportHeld
      ? { id: 'report', label: 'First report', ...reportHeld, at: null }
      : {
          id: 'report',
          label: 'First report',
          state: 'pending',
          meta: null,
          at: nextReportAt(settings?.nightly_hour ?? 0, now),
        },
  ]
  const complete =
    Boolean(listed) && (deep.state === 'done' || deep.state === 'off')

  return {
    startAt,
    readings,
    firstAt,
    lastAt: okTicks[0]?.ts ?? null,
    interval,
    nextAt,
    failure,
    queriesFound,
    waitingForQueries,
    measuring,
    steps,
    complete,
  }
}
