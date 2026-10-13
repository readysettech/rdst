// What the Database page says, as pure functions over the monitor contract: the bar's
// state word, the four health numbers for an hour or a day, the chart's event markers,
// and the query list's chips, deltas, order, filters and captions.

import { targetMonitorLine } from '@/components/TargetMonitorStatus'
import {
  BAND_COLORS,
  type ChartMarkLine,
  type LiveRange,
  OTHER_COLOR,
} from '@/features/monitor-kit/charts'
import { formatClock, formatDate } from '@/features/monitor-kit/format'
import type { MonitorPulseState } from '@/features/monitor-kit/useMonitorPulse'
import {
  type SwitcherState,
  switcherState,
} from '@/features/shared/filters/DatabaseFilter'
import type { ActionsPage } from '@/lib/monitor/actions'
import type {
  AnalyzeJob,
  HomeDatabaseState,
  InboxItem,
  MonitorHealth,
  MonitorQuery,
  MonitorSettings,
  QueryStatus,
  ReportSummary,
} from '@/lib/monitor/types'
import type { ListFilters, ListStatus } from './useDatabaseUrl'

export const DASH = '\u2014'
const MIN = 60
const DAY = 86400

export const RANGE_LABEL: Record<LiveRange, string> = {
  '5m': 'Last 5 minutes',
  '15m': 'Last 15 minutes',
  '1h': 'Last hour',
  '24h': 'Last 24 hours',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
  '90d': 'Last 90 days',
  all: 'All time',
}

const ENGINE_LABEL: Record<MonitorHealth['engine'], string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

export function engineLabel(health: MonitorHealth | undefined): string | null {
  if (!health) return null
  const version = health.engine_version ? ` ${health.engine_version}` : ''
  return `${ENGINE_LABEL[health.engine]}${version}`
}

/** "9:55" inside a day, "12:00 AM" across one. */
export function markerClock(ts: number, spanSeconds: number): string {
  if (spanSeconds >= DAY) return formatClock(ts)
  const date = new Date(ts * 1000)
  return `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** "9:56:30": the last reading, to the second. */
export function readingClock(ts: number): string {
  const date = new Date(ts * 1000)
  return `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}

export interface BarState extends SwitcherState {
  /** When the current state began, for "Can't reach, since 9:41 AM". */
  since: number | null
}

/**
 * The one state word in the bar: Watching, AI checks paused, Can't reach, Not monitored.
 * `apiState` is the database's state from the API, which knows when AI checks are paused
 * while the first reading is still on its way.
 */
export function barState(
  settings: MonitorSettings | undefined,
  pulse: MonitorPulseState,
  health: MonitorHealth | undefined,
  apiState?: HomeDatabaseState | null
): BarState {
  const line = targetMonitorLine(
    settings,
    pulse.state === 'unknown' ? undefined : pulse.state,
    {
      now: pulse.now,
      firstReading: pulse.firstReading,
      delayReason: pulse.delayReason,
      connectionReason: pulse.connectionProblem?.reason,
    }
  )
  const state: SwitcherState =
    line?.monitored && line.tone !== 'negative' && apiState === 'ai_paused'
      ? { word: 'AI checks paused', tone: 'warning' }
      : (switcherState(line) ?? { word: 'Watching', tone: 'live' })
  const outage = health?.outages?.find((entry) => entry.end === null)
  return {
    ...state,
    since: state.tone === 'negative' ? (outage?.start ?? null) : null,
  }
}

// Health numbers

export type HealthMetricId = 'qps' | 'connections' | 'hit' | 'rollback'

export interface HealthMetric {
  id: HealthMetricId
  label: string
  value: string
  unit?: string
  detail?: string
  reason?: string
  tone: 'neutral' | 'warning' | 'negative'
}

interface HealthOptions {
  /** The numbers read for the day (the 24 h range) instead of now. */
  day: boolean
  /** Why cache hit is out of range, from the latest report. */
  cacheReason: string | null
  /** The first minutes: what has no reading yet says it is collecting. */
  firstRun: boolean
}

function connectionsPeak(
  health: MonitorHealth
): { value: number; ts: number } | null {
  let best: { value: number; ts: number } | null = null
  for (const point of health.series) {
    const value = point.active_conns
    if (value != null && (!best || value > best.value))
      best = { value, ts: point.ts }
  }
  return best
}

function recordedMinutes(health: MonitorHealth): number {
  return Math.max(1, Math.round(health.history.span_seconds / MIN))
}

export function healthMetrics(
  health: MonitorHealth,
  { day, cacheReason, firstRun }: HealthOptions
): HealthMetric[] {
  const { now, period } = health
  const qpsPeak =
    period.qps_peak != null && period.qps_peak_at != null
      ? { value: period.qps_peak, ts: period.qps_peak_at }
      : null
  const connPeak = connectionsPeak(health)
  const conns = health.series
    .map((point) => point.active_conns)
    .filter((value) => value != null)
  const steady = conns.length > 1 && conns.every((value) => value === conns[0])
  const hit = day ? period.cache_hit_ratio_min : now.cache_hit_ratio
  const hitLow = period.cache_hit_ratio_min
  const hitWarn = hitLow != null && hitLow < 0.99
  const rollbacks = day ? period.xact_rollback : now.rollbacks_per_min
  return [
    {
      id: 'qps',
      label: 'Queries per second',
      value: (day ? period.qps : now.qps).toFixed(1),
      detail: firstRun
        ? `${recordedMinutes(health)} min so far`
        : qpsPeak
          ? day
            ? `average, peak ${qpsPeak.value.toFixed(1)} at ${markerClock(qpsPeak.ts, 0)}`
            : `peak ${qpsPeak.value.toFixed(1)}`
          : undefined,
      tone: 'neutral',
    },
    {
      id: 'connections',
      label: 'Connections',
      value: now.active_conns == null ? DASH : String(now.active_conns),
      unit: now.max_conns ? `of ${now.max_conns}` : undefined,
      detail: steady
        ? 'steady'
        : connPeak
          ? day
            ? `peak at ${markerClock(connPeak.ts, 0)}`
            : `peak ${connPeak.value}`
          : undefined,
      tone: 'neutral',
    },
    {
      id: 'hit',
      label: day ? 'Lowest cache hit' : 'Cache hit',
      value: hit == null ? DASH : (hit * 100).toFixed(1),
      unit: hit == null ? undefined : '%',
      detail: hit == null && firstRun ? 'collecting' : undefined,
      reason: hitWarn ? (cacheReason ?? undefined) : undefined,
      tone: hitWarn ? 'warning' : 'neutral',
    },
    {
      id: 'rollback',
      label: 'Rollbacks and deadlocks',
      value:
        rollbacks == null
          ? DASH
          : Math.round(rollbacks).toLocaleString('en-US'),
      unit: rollbacks == null ? undefined : day ? 'in 24 h' : 'a minute',
      detail:
        rollbacks == null && firstRun
          ? 'collecting'
          : `${period.deadlocks} deadlocks`,
      tone: period.deadlocks > 0 ? 'negative' : 'neutral',
    },
  ]
}

// Chart markers

interface MarkerInput {
  health: MonitorHealth | undefined
  /** Open inbox items on this database. */
  items: InboxItem[] | undefined
  reports: ReportSummary[] | undefined
  domain: [number, number]
}

/** Events in the window: a stuck query, a report, the moment the database went away. */
export function chartMarkers({
  health,
  items,
  reports,
  domain,
}: MarkerInput): ChartMarkLine[] {
  const [start, end] = domain
  const span = end - start
  const inside = (ts: number) => ts >= start && ts <= end
  const out: ChartMarkLine[] = []
  for (const marker of health?.markers ?? [])
    if (inside(marker.ts)) out.push({ x: marker.ts, label: marker.label })
  for (const item of items ?? [])
    if (
      item.kind === 'stuck_query' &&
      item.state !== 'resolved' &&
      item.state !== 'dismissed'
    )
      if (inside(item.created_at))
        out.push({
          x: item.created_at,
          label: `Stuck query ${markerClock(item.created_at, span)}`,
        })
  for (const report of reports ?? [])
    if (inside(report.ts) && span >= DAY)
      out.push({
        x: report.ts,
        label: `Report ${markerClock(report.ts, span)}`,
      })
  for (const outage of health?.outages ?? [])
    if (inside(outage.start))
      out.push({
        x: outage.start,
        label: `Can't reach ${markerClock(outage.start, span)}`,
      })
  return out.sort((a, b) => a.x - b.x)
}

// Query rows

export interface StatusChip {
  label: string
  variant: 'neutral' | 'positive' | 'negative'
  modifier: 'solid' | 'outline' | 'ghost'
  /** running: a breathing dot while the analysis runs. queued: a hollow dot until it starts. */
  activity?: 'running' | 'queued'
}

export const STATUS_CHIP: Record<QueryStatus, StatusChip | null> = {
  fix_suggested: {
    label: 'Fix suggested',
    variant: 'neutral',
    modifier: 'solid',
  },
  healthy: { label: 'Healthy', variant: 'positive', modifier: 'ghost' },
  analyzing: {
    label: 'Analyzing',
    variant: 'neutral',
    modifier: 'outline',
    activity: 'running',
  },
  stuck: { label: 'Stuck', variant: 'negative', modifier: 'ghost' },
  not_analyzed: null,
}

export const STATUS_FILTERS: { value: ListStatus; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'fix', label: 'Fix suggested' },
  { value: 'healthy', label: 'Healthy' },
  { value: 'analyzing', label: 'Analyzing' },
  { value: 'stuck', label: 'Stuck' },
]

const STATUS_OF: Record<Exclude<ListStatus, 'all'>, QueryStatus> = {
  fix: 'fix_suggested',
  analyzing: 'analyzing',
  healthy: 'healthy',
  stuck: 'stuck',
}

export function rowStatus(query: MonitorQuery): QueryStatus {
  return query.status ?? 'not_analyzed'
}

/** The row's analysis waits for a Readyset sign-in. */
export function waitsForSignIn(query: MonitorQuery): boolean {
  return query.last_analysis?.held_reason === 'sign_in'
}

/** Some analysis on this database waits for a sign-in: the first pass or a row's latest. */
export function analysesWaitForSignIn(
  checkup: ActionsPage['checkup'] | undefined,
  rows: MonitorQuery[] | undefined
): boolean {
  return checkup?.held === 'sign_in' || Boolean(rows?.some(waitsForSignIn))
}

/** The analysis is under way: running, or queued to start on its own. */
export function analysisUnderWay(
  query: MonitorQuery,
  monitored = true
): boolean {
  const job = query.last_analysis
  if (job?.state === 'running') return true
  if (!monitored || job?.queue_state === 'held') return false
  return rowStatus(query) === 'analyzing' || job?.state === 'queued'
}

export const SIGN_IN_CHIP: StatusChip = {
  label: 'Waiting for sign-in',
  variant: 'neutral',
  modifier: 'outline',
}

export const QUEUED_CHIP: StatusChip = {
  label: 'Queued',
  variant: 'neutral',
  modifier: 'ghost',
  activity: 'queued',
}

export const FAILED_CHIP: StatusChip = {
  label: 'Failed',
  variant: 'negative',
  modifier: 'ghost',
}

export const ON_HOLD_CHIP: StatusChip = {
  label: 'On hold',
  variant: 'neutral',
  modifier: 'outline',
}

/**
 * A row's chip. A queued analysis reads "Queued" until it starts. A held analysis never
 * reads "Analyzing": it waits for a sign-in, or reads "On hold" while it waits on something
 * else (AI paused, automatic analysis off). An unanalyzed row whose last analysis didn't
 * finish reads "Failed"; a retry on its way doesn't.
 */
export function rowChip(
  query: MonitorQuery,
  monitored = true
): StatusChip | null {
  const status = rowStatus(query)
  const job = query.last_analysis
  if (status === 'analyzing' || (status === 'not_analyzed' && job)) {
    if (job?.state === 'failed' && job.error_kind !== 'retrying')
      return FAILED_CHIP
    if (analysisUnderWay(query, monitored))
      return job?.state === 'queued' ? QUEUED_CHIP : STATUS_CHIP.analyzing
    if (!monitored || job?.queue_state !== 'held') return null
    return waitsForSignIn(query) ? SIGN_IN_CHIP : ON_HOLD_CHIP
  }
  return STATUS_CHIP[status]
}

/** Each query's newest analysis job, from the job list the page polls. */
export function newestJobs(
  jobs: AnalyzeJob[] | undefined
): Map<string, AnalyzeJob> {
  const newest = new Map<string, AnalyzeJob>()
  for (const job of jobs ?? []) {
    const current = newest.get(job.query_key)
    if (!current || job.id > current.id) newest.set(job.query_key, job)
  }
  return newest
}

export type RowActivity = 'running' | 'queued'

export const ACTIVITY_LABEL: Record<RowActivity, string> = {
  running: 'Analyzing\u2026',
  queued: 'Queued for analysis',
}

/**
 * A running or queued analysis on any row, scored or not. The polled job list is fresher
 * than the row, so its newest job for the query decides; without one the row's own last
 * analysis does. A held job waits on something else and shows its chip instead.
 */
export function rowActivity(
  query: MonitorQuery,
  job: AnalyzeJob | null | undefined,
  monitored = true
): RowActivity | null {
  if (job) {
    if (job.state === 'running') return 'running'
    return job.state === 'queued' && monitored && job.queue_state !== 'held'
      ? 'queued'
      : null
  }
  if (!analysisUnderWay(query, monitored)) return null
  return query.last_analysis?.state === 'queued' ? 'queued' : 'running'
}

/** "+12% vs yesterday" in the warning tone, "-8% vs yesterday", or "no day before yet". */
export function deltaLabel(delta: number | null | undefined): {
  text: string
  up: boolean
} {
  if (delta == null) return { text: 'no day before yet', up: false }
  const percent = Math.round(delta * 100)
  const sign = percent > 0 ? '+' : ''
  return { text: `${sign}${percent}% vs yesterday`, up: percent > 0 }
}

/** Plan scores under 40 read as poor and take the warning tone. */
export function scorePoor(score: number | null | undefined): boolean {
  return score != null && score < 40
}

export function swatchColor(index: number | null | undefined): string {
  return index == null ? OTHER_COLOR : (BAND_COLORS[index] ?? OTHER_COLOR)
}

export type RowSort = 'share' | 'latency'

export function sortRows(rows: MonitorQuery[], sort: RowSort): MonitorQuery[] {
  return [...rows].sort((a, b) =>
    sort === 'latency'
      ? b.mean_ms - a.mean_ms || a.key.localeCompare(b.key)
      : b.db_time_share - a.db_time_share || a.key.localeCompare(b.key)
  )
}

export function filterRows(rows: MonitorQuery[], filters: ListFilters) {
  const needle = filters.q.trim().toLowerCase()
  const status = filters.status === 'all' ? null : STATUS_OF[filters.status]
  return rows.filter(
    (row) =>
      (status === null || rowStatus(row) === status) &&
      (!filters.table || row.tables.includes(filters.table)) &&
      (!needle ||
        row.display_name.toLowerCase().includes(needle) ||
        row.text.toLowerCase().includes(needle))
  )
}

export function tablesOf(rows: MonitorQuery[]): string[] {
  return [...new Set(rows.flatMap((row) => row.tables))].sort()
}

export function fixCount(rows: MonitorQuery[]): number {
  return rows.filter((row) => rowStatus(row) === 'fix_suggested').length
}

/** "16, 9 with a fix", "16, sorted by latency", "3 seen so far", "3, from a scan on Oct 6". */
export function sectionMeta(
  rows: MonitorQuery[],
  options: { sort: RowSort; firstRun: boolean; scanAt: number | null }
): string {
  const count = rows.length
  if (options.scanAt != null)
    return `${count}, from a scan on ${formatDate(options.scanAt)}`
  if (options.firstRun) return `${count} seen so far`
  if (options.sort === 'latency') return `${count}, sorted by latency`
  return `${count}, ${fixCount(rows)} with a fix`
}

export const PAGE_SIZE = 10

export function pagerLabel(page: number, total: number): string {
  const first = page * PAGE_SIZE + 1
  const last = Math.min(total, (page + 1) * PAGE_SIZE)
  return `${first} to ${last} of ${total}`
}

/** A scan's calls over a day, from the rate the scan recorded. */
export function dailyCalls(query: MonitorQuery): string {
  return Math.round(query.qps * DAY).toLocaleString('en-US')
}

export function latencyText(ms: number): string {
  return ms >= 100 ? ms.toFixed(0) : ms >= 10 ? ms.toFixed(1) : ms.toFixed(2)
}

export function formatSharePercent(share: number): string {
  const percent = share * 100
  return percent >= 1 ? `${Math.round(percent)}%` : `${percent.toFixed(1)}%`
}
