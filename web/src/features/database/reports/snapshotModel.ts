// The daily snapshot as the Report tab reads it (DESIGN_V5 section 5). Pure helpers:
// day titles, the day list, number deltas with their color, status and grade looks,
// chip colors that match the day's chart, and the Database page link for a query on
// that day.
import {
  formatClock,
  formatCompact,
  formatMsWithUnit,
  formatQps,
  formatRatio,
} from '@/features/monitor-kit/format'
import type {
  DaySnapshot,
  ReportSummary,
  SnapshotBriefItem,
  SnapshotChart,
  SnapshotMetric,
  SnapshotNumber,
  SnapshotPeriod,
  SnapshotQuery,
  SnapshotSpike,
  SnapshotStatus,
  SnapshotVitals,
} from '@/lib/monitor/types'

/** Same order as the chart kit's band colors (--mk-band-1..8). */
export const CHIP_COLORS = [
  'var(--mk-band-1)',
  'var(--mk-band-2)',
  'var(--mk-band-3)',
  'var(--mk-band-4)',
  'var(--mk-band-5)',
  'var(--mk-band-6)',
  'var(--mk-band-7)',
  'var(--mk-band-8)',
] as const
export const OTHER_CHIP_COLOR = 'var(--mk-band-other)'

const DAY = 86400

/** A local calendar date from 'YYYY-MM-DD'. */
export function parseDay(day: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!match) return null
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
}

/** 'Monday, September 28'; the year only when it isn't this year. */
export function dayTitle(day: string, now = Date.now() / 1000): string {
  const date = parseDay(day)
  if (!date) return day
  const thisYear = new Date(now * 1000).getFullYear() === date.getFullYear()
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    ...(thisYear ? {} : { year: 'numeric' }),
  })
}

/** 'Mon, Sep 28' for the day list. */
export function dayShort(day: string): string {
  const date = parseDay(day)
  if (!date) return day
  return date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

function startOfToday(now: number): number {
  const date = new Date(now * 1000)
  date.setHours(0, 0, 0, 0)
  return date.getTime() / 1000
}

/** 'Today', 'Yesterday' or '' for older days. */
export function relativeDay(day: string, now = Date.now() / 1000): string {
  const date = parseDay(day)
  if (!date) return ''
  const start = date.getTime() / 1000
  const today = startOfToday(now)
  if (Math.abs(start - today) < 3600 * 2) return 'Today'
  if (Math.abs(start - (today - DAY)) < 3600 * 2) return 'Yesterday'
  return ''
}

/**
 * The line under the title, only when the snapshot isn't one whole day: today so far (a
 * run by hand), or an older report's rolling 24 hours. A whole day needs only its title.
 */
export function periodLine(
  period: SnapshotPeriod,
  writtenAt: number,
  now = Date.now() / 1000
): string | null {
  if (period.rolling)
    return `The 24 hours up to ${formatClock(period.end)}, written before daily snapshots`
  if (!period.partial) return null
  const asOf = formatClock(Math.min(period.end, writtenAt))
  return relativeDay(period.day, now) === 'Today'
    ? `Today so far, as of ${asOf}`
    : `So far, as of ${asOf}`
}

/**
 * What the day's numbers compare with: the previous local day's own readings. Null when
 * there's no earlier recorded day (the first day, or a report whose numbers carry no prior).
 */
export function comparisonLine(
  snapshot: Pick<DaySnapshot, 'period' | 'numbers'>
): string | null {
  const { period, numbers } = snapshot
  if (period.prior_start == null) return null
  if (!numbers.some((number) => number.prior != null)) return null
  const day = new Date(period.prior_start * 1000).toLocaleDateString('en-US', {
    weekday: 'long',
  })
  return period.prior_observed_from
    ? `Against ${day}, recorded from ${formatClock(period.prior_observed_from)}`
    : `Against ${day}`
}

export const BRIEF_LIMIT = 3

/** The day in brief: its top priorities, each with its explanation when it has one. */
export function briefItems(
  snapshot: Pick<DaySnapshot, 'brief' | 'brief_items'>
): SnapshotBriefItem[] {
  const items: SnapshotBriefItem[] = snapshot.brief_items?.length
    ? snapshot.brief_items
    : snapshot.brief.map((text) => ({ topic: null, text }))
  return items.slice(0, BRIEF_LIMIT)
}

// In-page navigation (FEEDBACK_ROUND4 R3): the report's sections in page order.

export type ReportSectionId =
  | 'snapshot-health'
  | 'snapshot-numbers'
  | 'snapshot-brief'
  | 'snapshot-actions'
  | 'snapshot-chart'
  | 'snapshot-spikes'
  | 'snapshot-events'
  | 'snapshot-look-at'
  | 'snapshot-all'

export interface ReportSection {
  id: ReportSectionId
  label: string
  count?: number
}

/** The sections this snapshot shows, in page order, with a count where one helps. */
export function reportSections(snapshot: DaySnapshot): ReportSection[] {
  const sections: (ReportSection | null)[] = [
    { id: 'snapshot-health', label: 'Health' },
    { id: 'snapshot-numbers', label: 'Numbers' },
    briefItems(snapshot).length
      ? { id: 'snapshot-brief', label: 'Brief' }
      : null,
    {
      id: 'snapshot-actions',
      label: 'Actions',
      count: snapshot.actions?.length,
    },
    snapshot.chart ? { id: 'snapshot-chart', label: 'Chart' } : null,
    snapshot.chart
      ? {
          id: 'snapshot-spikes',
          label: 'Spikes',
          count: snapshot.spikes?.length,
        }
      : null,
    {
      id: 'snapshot-events',
      label: 'What happened',
      count: snapshot.events.length,
    },
    {
      id: 'snapshot-look-at',
      label: 'Queries',
      count: snapshot.look_at.length,
    },
    snapshot.queries.length
      ? {
          id: 'snapshot-all',
          label: 'All queries',
          count: snapshot.queries.length,
        }
      : null,
  ]
  return sections.filter(
    (section): section is ReportSection => section !== null
  )
}

/**
 * The section being read: the last one whose top has passed the reading line, the first
 * before any has, and the last once the page can't scroll further.
 */
export function activeSection<T extends string>(
  tops: { id: T; top: number }[],
  line: number,
  atBottom: boolean
): T | null {
  if (!tops.length) return null
  if (atBottom) return tops[tops.length - 1].id
  let active = tops[0].id
  for (const { id, top } of tops) if (top <= line) active = id
  return active
}

export interface DayEntry {
  day: string
  id: number
  sample: boolean
  partial: boolean
  line: string
}

/** A short line for the day list: '4 events, 1 still open. 3 queries to look at'. */
export function dayLine(summary: ReportSummary): string {
  const parts: string[] = []
  if (summary.events != null) {
    if (summary.events === 0) parts.push('Nothing unusual')
    else {
      const open = summary.open_events ?? 0
      parts.push(
        `${summary.events} ${summary.events === 1 ? 'event' : 'events'}${open ? `, ${open} still open` : ''}`
      )
    }
  }
  if (summary.look_at)
    parts.push(
      `${summary.look_at} ${summary.look_at === 1 ? 'query' : 'queries'} to look at`
    )
  if (!parts.length && summary.counts.fix_first)
    parts.push(
      `${summary.counts.fix_first} ${summary.counts.fix_first === 1 ? 'query' : 'queries'} to look at`
    )
  return parts.join(' · ')
}

/**
 * One entry per day, newest first. A day's real report beats a sample; a full-day
 * (nightly) snapshot beats a partial one; otherwise the newest run wins.
 */
export function dayList(reports: ReportSummary[] | undefined): DayEntry[] {
  const best = new Map<string, ReportSummary>()
  const rank = (report: ReportSummary) =>
    (report.sample ? 0 : 4) + (report.period_partial ? 0 : 2)
  for (const report of reports ?? []) {
    if (!Number.isFinite(report.ts)) continue
    const day =
      report.day || new Date(report.ts * 1000).toISOString().slice(0, 10)
    const current = best.get(day)
    if (
      !current ||
      rank(report) > rank(current) ||
      (rank(report) === rank(current) && report.ts > current.ts)
    )
      best.set(day, report)
  }
  return [...best.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0))
    .map(([day, report]) => ({
      day,
      id: report.id,
      sample: Boolean(report.sample),
      partial: Boolean(report.period_partial),
      line: dayLine(report),
    }))
}

/** The day before and after the shown one in the list (lists are newest first). */
export function neighbors(days: DayEntry[], id: number | null) {
  const index = days.findIndex((entry) => entry.id === id)
  if (index < 0) return { older: null, newer: null }
  return {
    older: days[index + 1] ?? null,
    newer: index > 0 ? days[index - 1] : null,
  }
}

export type DeltaTone = 'good' | 'warn' | 'bad' | 'neutral'

export interface NumberView {
  label: string
  value: string
  unit: string
  prior: string | null
  /** '+18%', '2.1x', 'same' */
  delta: string | null
  direction: 'up' | 'down' | 'flat' | null
  tone: DeltaTone
}

function withUnit({ value, unit }: { value: string; unit: string }): string {
  if (!unit) return value
  return unit === '%' || unit === '/s' ? `${value}${unit}` : `${value} ${unit}`
}

function valueText(number: SnapshotNumber, value: number | null) {
  if (value == null) return { value: '-', unit: '' }
  switch (number.unit) {
    case 'qps':
      return { value: formatQps(value), unit: '/s' }
    case 'ms':
      return formatMsWithUnit(value)
    case 'ratio':
      return { value: `${formatRatio(value)}%`, unit: '' }
    default:
      return { value: formatCompact(value), unit: '' }
  }
}

/** A day number with its previous-day value, the change, and the color it earns. */
export function numberView(number: SnapshotNumber): NumberView {
  const current = valueText(number, number.value)
  const before = number.prior != null ? valueText(number, number.prior) : null
  let delta: string | null = null
  let direction: NumberView['direction'] = null
  let tone: DeltaTone = 'neutral'
  const { value, prior } = number
  if (value != null && prior != null) {
    if (number.unit === 'ratio') {
      const points = (value - prior) * 100
      direction = Math.abs(points) < 0.1 ? 'flat' : points > 0 ? 'up' : 'down'
      delta =
        direction === 'flat'
          ? 'same'
          : `${points > 0 ? '+' : ''}${points.toFixed(1)} pts`
      if (direction !== 'flat' && number.better)
        tone =
          direction === number.better
            ? 'good'
            : Math.abs(points) >= 5
              ? 'bad'
              : 'warn'
    } else if (prior === 0 && value === 0) {
      direction = 'flat'
      delta = 'same'
    } else if (prior === 0) {
      direction = 'up'
      delta = 'new'
      tone = number.better === 'down' ? 'warn' : 'neutral'
    } else {
      const change = value / prior - 1
      direction = Math.abs(change) < 0.05 ? 'flat' : change > 0 ? 'up' : 'down'
      const ratio = value / prior
      delta =
        direction === 'flat'
          ? 'same'
          : ratio >= 2
            ? `${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}x`
            : `${change > 0 ? '+' : ''}${Math.round(change * 100)}%`
      if (direction !== 'flat' && number.better)
        tone =
          direction === number.better
            ? 'good'
            : Math.abs(change) >= 0.5
              ? 'bad'
              : 'warn'
    }
  }
  return {
    label: number.label,
    value: current.value,
    unit: current.unit,
    prior: before ? withUnit(before) : null,
    delta,
    direction,
    tone,
  }
}

export interface StatusLook {
  label: string
  tone: 'negative' | 'warning' | 'positive' | 'neutral'
}

export const STATUS_LOOK: Record<SnapshotStatus, StatusLook | null> = {
  needs_attention: { label: 'Needs attention', tone: 'negative' },
  watch: { label: 'Watch', tone: 'warning' },
  fix_ready: { label: 'Fix suggested', tone: 'positive' },
  healthy: null,
  not_analyzed: { label: 'Not analyzed yet', tone: 'neutral' },
}

export type GradeTone = 'positive' | 'info' | 'warning' | 'negative'

export function gradeTone(grade: string | null | undefined): GradeTone | null {
  if (!grade) return null
  if (grade === 'A') return 'positive'
  if (grade === 'B') return 'info'
  if (grade === 'C') return 'warning'
  return 'negative'
}

/** Each charted query's band color, so a name chip matches the day's chart. */
export function chipColors(snapshot: DaySnapshot): Map<string, string> {
  const colors = new Map<string, string>()
  for (const band of snapshot.chart?.bands ?? []) {
    if (band.kind === 'query' && band.color_index != null)
      colors.set(band.key, CHIP_COLORS[band.color_index % CHIP_COLORS.length])
  }
  return colors
}

const RANGES: [string, number][] = [
  ['24h', DAY],
  ['7d', 7 * DAY],
  ['30d', 30 * DAY],
  ['90d', 90 * DAY],
]

/**
 * The Database page for one query, zoomed to the snapshot's day: the smallest range
 * that still reaches the day's start, with the day as the zoom window.
 */
export function databaseHref({
  target,
  queryKey,
  period,
  now = Date.now() / 1000,
}: {
  target: string
  queryKey?: string | null
  period: Pick<SnapshotPeriod, 'start' | 'end'>
  now?: number
}): string {
  const end = Math.min(period.end, Math.floor(now))
  const range =
    RANGES.find(([, seconds]) => now - seconds <= period.start)?.[0] ?? 'all'
  const search = new URLSearchParams()
  search.set('target', target)
  if (queryKey) search.set('query', queryKey)
  search.set('range', range)
  if (end - period.start >= 120) search.set('zoom', `${period.start}-${end}`)
  return `/database?${search.toString()}`
}

/**
 * The event's state in words. RDST only watches, so an event that ended is worded as
 * observed: 'Back to normal at 8:07 PM', never 'Resolved'.
 */
export function eventState(
  event: {
    state: string
    resolved_at?: number | null
    kind: string
  },
  dayEnd?: number
): { label: string; tone: 'negative' | 'positive' | 'neutral' } {
  if (event.kind === 'index') return { label: 'Applied', tone: 'neutral' }
  // The line itself says it went back to normal, at its own time.
  if (event.kind === 'resolved') return { label: '', tone: 'positive' }
  if (event.state === 'open') return { label: 'Still open', tone: 'negative' }
  if (event.state === 'resolved') {
    const at = event.resolved_at
    // After the day ended, the time names its day.
    const when =
      at == null
        ? ''
        : dayEnd != null && at >= dayEnd
          ? ` ${new Date(at * 1000).toLocaleDateString('en-US', { weekday: 'short' })} at ${formatClock(at)}`
          : ` at ${formatClock(at)}`
    return { label: `Back to normal${when}`, tone: 'positive' }
  }
  if (event.state === 'dismissed')
    return { label: 'Dismissed', tone: 'neutral' }
  return { label: '', tone: 'neutral' }
}

/** A fix in words, without its column list: the definition belongs in a code block. */
export function fixLine(title: string): string {
  const cut = title.indexOf('(')
  const words = (cut > 0 ? title.slice(0, cut) : title).trim()
  return words.charAt(0).toLowerCase() + words.slice(1)
}

export type QuerySort = 'share' | 'calls' | 'mean_ms' | 'change' | 'name'

export function sortQueries<
  T extends {
    display_name: string
    share: number
    calls: number | null
    mean_ms: number | null
    latency_change: number | null
  },
>(rows: T[], sort: QuerySort): T[] {
  const value = (row: T): number => {
    switch (sort) {
      case 'calls':
        return row.calls ?? -1
      case 'mean_ms':
        return row.mean_ms ?? -1
      case 'change':
        return row.latency_change ?? Number.NEGATIVE_INFINITY
      default:
        return row.share
    }
  }
  const sorted = [...rows]
  if (sort === 'name')
    return sorted.sort((a, b) => a.display_name.localeCompare(b.display_name))
  return sorted.sort((a, b) => value(b) - value(a))
}

// Chart metrics (B3): the three views of the day's queries, and the database-wide
// readings a tile in the day in numbers can put on the chart.

export type ChartMetric = 'calls' | 'latency' | 'time'
export type VitalKey =
  | 'active_conns'
  | 'cache_hit_ratio'
  | 'xact_rollback'
  | 'deadlocks'
export type ChartView = ChartMetric | VitalKey

export const METRIC_TITLE: Record<ChartView, string> = {
  calls: 'Queries per second',
  latency: 'Latency',
  time: 'Time spent',
  active_conns: 'Active connections',
  cache_hit_ratio: 'Cache hit',
  xact_rollback: 'Rollbacks',
  deadlocks: 'Deadlocks',
}

export const METRIC_SUBTITLE: Record<ChartView, string> = {
  calls: 'Stacked by query',
  latency:
    'Average per call for each busy query; dashed is every query together',
  time: "Each query's share of database time",
  active_conns: 'Connections running a statement, database-wide',
  cache_hit_ratio: 'Reads served from memory, database-wide',
  xact_rollback: 'Transactions rolled back in each interval',
  deadlocks: 'Deadlocks in each interval',
}

/** The chart view a day-in-numbers tile opens. */
export const NUMBER_VIEW: Record<SnapshotMetric, ChartView> = {
  qps: 'calls',
  mean_ms: 'latency',
  active_conns_peak: 'active_conns',
  cache_hit_ratio_min: 'cache_hit_ratio',
  xact_rollback: 'xact_rollback',
  deadlocks: 'deadlocks',
}

/** Which query views the stored chart can draw; older snapshots only kept queries/s. */
export function availableMetrics(chart: SnapshotChart | null): ChartMetric[] {
  if (!chart) return []
  return chart.ms && chart.mean_ms ? ['calls', 'latency', 'time'] : ['calls']
}

export interface MetricBand {
  id: string
  values: (number | null)[]
  /** A dashed reference line (latency over every query). */
  reference?: boolean
}

/** Per band values for one view: queries/s, latency in ms, or % of database time. */
export function metricBands(
  chart: SnapshotChart,
  metric: ChartMetric
): MetricBand[] {
  if (metric === 'latency' && chart.mean_ms) {
    const bands = chart.bands
      .map((band, index) => ({
        id: band.key,
        values: chart.mean_ms?.[index] ?? [],
      }))
      .filter((band) => band.id !== 'other')
    return chart.overall_mean_ms
      ? [
          ...bands,
          { id: 'overall', values: chart.overall_mean_ms, reference: true },
        ]
      : bands
  }
  if (metric === 'time' && chart.ms) {
    const ms = chart.ms
    const totals = chart.xs.map((_, x) =>
      ms.reduce((sum, row) => sum + (row[x] ?? 0), 0)
    )
    return chart.bands.map((band, index) => ({
      id: band.key,
      values: (ms[index] ?? []).map((value, x) =>
        value == null ? null : totals[x] ? (value / totals[x]) * 100 : 0
      ),
    }))
  }
  return chart.bands.map((band, index) => ({
    id: band.key,
    values: chart.qps[index] ?? [],
  }))
}

/** One database-wide reading as chart points. Cache hit reads as a percentage. */
export function vitalPoints(
  vitals: SnapshotVitals | null | undefined,
  key: VitalKey | 'qps' | 'mean_ms'
): { x: number; y: number | null }[] {
  if (!vitals) return []
  const values = vitals[key] ?? []
  return vitals.xs.map((x, index) => {
    const value = values[index]
    return {
      x,
      y: value == null ? null : key === 'cache_hit_ratio' ? value * 100 : value,
    }
  })
}

/** Where a sentence in the day in brief leads when clicked. */
export function briefTarget(topic: string | null | undefined): string | null {
  switch (topic) {
    case 'traffic':
    case 'latency':
    case 'spike':
      return 'snapshot-chart'
    case 'events':
      return 'snapshot-events'
    case 'queries':
      return 'snapshot-look-at'
    case 'health':
    case 'todo':
      return 'snapshot-actions'
    case 'index':
      return 'snapshot-events'
    default:
      return null
  }
}

/** A spike's size in words: '28 ms', against '5.2 ms', '5.4x'. */
export function spikeSize(spike: SnapshotSpike) {
  const format = (value: number) =>
    spike.metric === 'traffic'
      ? `${formatQps(value)}/s`
      : (() => {
          const ms = formatMsWithUnit(value)
          return `${ms.value} ${ms.unit}`
        })()
  return {
    peak: format(spike.peak),
    usual: format(spike.usual),
    ratio:
      spike.ratio == null
        ? null
        : `${spike.ratio >= 10 ? Math.round(spike.ratio) : spike.ratio.toFixed(1)}x`,
    format,
  }
}

/** 'From 4:45 PM to 5:00 PM', or 'At 5:35 PM' for one interval. */
export function spikeWhen(spike: SnapshotSpike): string {
  return spike.end - spike.start > 600
    ? `${formatClock(spike.start)} to ${formatClock(spike.end)}`
    : `At ${formatClock(spike.start)}`
}

// CSV (B7): every column the report holds for every query that ran that day.

const CSV_COLUMNS: [string, (row: SnapshotQuery) => unknown][] = [
  ['query', (row) => row.display_name],
  ['query_key', (row) => row.query_key],
  ['calls', (row) => row.calls],
  ['queries_per_second', (row) => row.qps],
  ['total_ms', (row) => row.total_ms],
  ['avg_latency_ms', (row) => row.mean_ms],
  ['share_of_database_time', (row) => row.share],
  ['calls_day_before', (row) => row.prior_calls],
  ['avg_latency_ms_day_before', (row) => row.prior_mean_ms],
  ['latency_change', (row) => row.latency_change],
  ['new_that_day', (row) => row.new],
  ['status', (row) => STATUS_LOOK[row.status]?.label ?? 'Healthy'],
  ['status_reason', (row) => row.status_reason],
  ['score', (row) => row.score],
  ['analysis_headline', (row) => row.headline],
  ['analysis_findings', (row) => (row.findings ?? []).join(' | ')],
  ['analysis_mode', (row) => row.analysis_mode],
  [
    'analyzed_at',
    (row) =>
      row.analyzed_at ? new Date(row.analyzed_at * 1000).toISOString() : null,
  ],
  ['plan_only', (row) => row.provisional],
  ['fix_kind', (row) => row.fix?.kind],
  ['fix', (row) => row.fix?.title],
  ['fix_sql', (row) => row.fix?.sql],
  ['sql', (row) => row.text],
]

function csvCell(value: unknown): string {
  if (value == null) return ''
  const text = typeof value === 'number' ? String(value) : String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function queriesCsv(rows: SnapshotQuery[]): string {
  const lines = [CSV_COLUMNS.map(([name]) => name).join(',')]
  for (const row of rows)
    lines.push(CSV_COLUMNS.map(([, read]) => csvCell(read(row))).join(','))
  return `${lines.join('\r\n')}\r\n`
}

export function csvFileName(target: string, day: string): string {
  return `${target.replace(/[^\w.-]+/g, '-')}-queries-${day}.csv`
}
