// Honest time ranges for monitor charts (brief section 4.3). Pure functions only.
//
// Usage:
//   const options = availableRanges(health.history)      // enabled + reason per range
//   const range = clampRange(urlRange, health.history)   // largest enabled when disabled
//   const [start, end] = domainFor(range, history, now)  // never pads before first_ts
//   const ticks = ticksFor([start, end])                 // 4-6 labeled x ticks
//   chartTitle('Throughput', range, history, now)        // 'Throughput, last 7 days (...)'
// Timestamps are Unix seconds.
import type { HistorySpan, MonitorRange, Resolution } from '@/lib/monitor/types'
import { MONITOR_RANGES } from '@/lib/monitor/types'
import { formatDayDate, formatDuration } from '../format'

/**
 * Ranges the Database page offers. `5m` and `15m` read the `1h` series (1-minute
 * buckets) and clip the domain, so they need nothing extra from the backend.
 */
export type LiveRange = '5m' | '15m' | MonitorRange
export const LIVE_RANGES: readonly LiveRange[] = [
  '5m',
  '15m',
  ...MONITOR_RANGES,
]

/** The backend range that serves a page range. */
export function fetchRange(range: LiveRange): MonitorRange {
  return range === '5m' || range === '15m' ? '1h' : range
}

const MINUTE = 60
const HOUR = 3600
const DAY = 86400

export const RANGE_SECONDS: Record<Exclude<LiveRange, 'all'>, number> = {
  '5m': 5 * MINUTE,
  '15m': 15 * MINUTE,
  '1h': HOUR,
  '24h': DAY,
  '7d': 7 * DAY,
  '30d': 30 * DAY,
  '90d': 90 * DAY,
}

export const RANGE_SHORT_LABEL: Record<LiveRange, string> = {
  '5m': '5m',
  '15m': '15m',
  '1h': '1h',
  '24h': '24h',
  '7d': '7d',
  '30d': '30d',
  '90d': '90d',
  all: 'All',
}

const RANGE_PHRASE: Record<Exclude<LiveRange, 'all'>, string> = {
  '5m': 'last 5 minutes',
  '15m': 'last 15 minutes',
  '1h': 'last hour',
  '24h': 'last 24 hours',
  '7d': 'last 7 days',
  '30d': 'last 30 days',
  '90d': 'last 90 days',
}

/** 'prior 24 h' style label for comparison footers. */
export const PRIOR_LABEL: Record<Exclude<LiveRange, 'all'>, string> = {
  '5m': 'prior 5 min',
  '15m': 'prior 15 min',
  '1h': 'prior hour',
  '24h': 'prior 24 h',
  '7d': 'prior 7 days',
  '30d': 'prior 30 days',
  '90d': 'prior 90 days',
}

export interface RangeOption {
  range: LiveRange
  label: string
  enabled: boolean
  /** Tooltip for a disabled range. */
  reason: string | null
}

const span = (history: HistorySpan | null | undefined) =>
  history?.span_seconds ?? 0

function previousRange(
  range: LiveRange,
  ranges: readonly LiveRange[]
): LiveRange | null {
  const index = ranges.indexOf(range)
  return index > 0 ? ranges[index - 1] : null
}

/**
 * A range is enabled when the stored history exceeds the next smaller range; 1h is
 * always enabled. 'All' is enabled once history exceeds 24 hours and carries the
 * span in its label ('All - 3 days').
 */
/** '2 days recorded. Needs more than 7 days.' */
function recordedReason(seconds: number, needed: number): string {
  const recorded =
    seconds > 0
      ? `${formatDuration(Math.max(MINUTE, seconds))} recorded`
      : 'Nothing recorded yet'
  return `${recorded}. Needs more than ${formatDuration(needed)}.`
}

export function availableRanges(
  history: HistorySpan | null | undefined,
  ranges: readonly LiveRange[] = MONITOR_RANGES
): RangeOption[] {
  const seconds = span(history)
  return ranges.map((range, position) => {
    if (position === 0 || range === '1h')
      return {
        range,
        label: RANGE_SHORT_LABEL[range],
        enabled: true,
        reason: null,
      }
    if (range === 'all') {
      const enabled = seconds > DAY
      return {
        range,
        label: enabled ? `All - ${formatDuration(seconds)}` : 'All',
        enabled,
        reason: enabled ? null : recordedReason(seconds, DAY),
      }
    }
    const previous = previousRange(range, ranges) as Exclude<LiveRange, 'all'>
    const needed = RANGE_SECONDS[previous]
    const enabled = seconds > needed
    return {
      range,
      label: RANGE_SHORT_LABEL[range],
      enabled,
      reason: enabled ? null : recordedReason(seconds, needed),
    }
  })
}

export function isRangeEnabled(
  range: LiveRange,
  history: HistorySpan | null | undefined,
  ranges: readonly LiveRange[] = LIVE_RANGES
): boolean {
  return (
    availableRanges(history, ranges).find((option) => option.range === range)
      ?.enabled ?? false
  )
}

/** The requested range when enabled, else the largest enabled range below it. */
export function clampRange<R extends LiveRange = MonitorRange>(
  range: R,
  history: HistorySpan | null | undefined,
  ranges: readonly R[] = MONITOR_RANGES as unknown as readonly R[]
): R {
  const options = availableRanges(history, ranges)
  const index = ranges.indexOf(range)
  for (let i = index; i >= 0; i--)
    if (options[i].enabled) return options[i].range as R
  return ranges[0]
}

export function parseRange(value: unknown): MonitorRange | undefined {
  return MONITOR_RANGES.includes(value as MonitorRange)
    ? (value as MonitorRange)
    : undefined
}

export function parseLiveRange(value: unknown): LiveRange | undefined {
  return LIVE_RANGES.includes(value as LiveRange)
    ? (value as LiveRange)
    : undefined
}

/** `[max(now - range, first_ts), now]`; the chart never pads empty time on the left. */
export function domainFor(
  range: LiveRange,
  history: HistorySpan | null | undefined,
  now: number
): [number, number] {
  const first = history?.first_ts ?? null
  const seconds = range === 'all' ? span(history) : RANGE_SECONDS[range]
  const start = now - Math.max(seconds, MINUTE)
  const clamped = first != null ? Math.max(start, first) : start
  return [Math.min(clamped, now - MINUTE), now]
}

/** Bucket size for a range (brief 4.3). 'All' picks the finest res with <= 1,000 points. */
export function resolutionFor(range: LiveRange, spanSeconds = 0): Resolution {
  if (range === '5m' || range === '15m' || range === '1h') return 60
  if (range === '24h') return 60
  if (range === '7d') return 300
  if (range === '30d' || range === '90d') return 3600
  for (const res of [60, 300, 3600] as const)
    if (spanSeconds / res <= 1000) return res
  return 86400
}

/** Is the prior equal period fully covered by stored history? */
export function priorCovered(
  range: LiveRange,
  history: HistorySpan | null | undefined
): boolean {
  if (range === 'all') return false
  return span(history) >= 2 * RANGE_SECONDS[range]
}

/** 'last 24 hours', or 'last 3 days' for All, or the shorter real span. */
export function rangePhrase(
  range: LiveRange,
  history: HistorySpan | null | undefined
): string {
  const seconds = span(history)
  if (range === 'all') return `last ${formatDuration(seconds)}`
  if (seconds > 0 && seconds < RANGE_SECONDS[range])
    return `last ${formatDuration(seconds)}`
  return RANGE_PHRASE[range]
}

/** 'Throughput, last 7 days (history starts Thu Sep 25)' when the range exceeds history. */
export function chartTitle(
  metric: string,
  range: LiveRange,
  history: HistorySpan | null | undefined
): string {
  const seconds = span(history)
  const exceeds =
    range !== 'all' && seconds > 0 && seconds < RANGE_SECONDS[range]
  const phrase = exceeds
    ? RANGE_PHRASE[range as Exclude<LiveRange, 'all'>]
    : rangePhrase(range, history)
  if (exceeds && seconds < DAY)
    return `${metric}, last ${formatDuration(Math.max(MINUTE, seconds))}`
  const starts =
    exceeds && history?.first_ts
      ? ` (history starts ${formatDayDate(history.first_ts)})`
      : ''
  return `${metric}, ${phrase}${starts}`
}

/** '3 days of history, since Thu Sep 25'; under an hour: 'Recording for 14 minutes'. */
export function historyLine(
  history: HistorySpan | null | undefined
): string | null {
  if (!history?.first_ts) return null
  const seconds = span(history)
  if (seconds < HOUR)
    return `Recording for ${formatDuration(Math.max(MINUTE, seconds))}`
  return `${formatDuration(seconds)} of history, since ${formatDayDate(history.first_ts)}`
}

// X axis ticks

export interface AxisTick {
  x: number
  label: string
}

const TICK_STEPS = [
  MINUTE,
  2 * MINUTE,
  5 * MINUTE,
  10 * MINUTE,
  15 * MINUTE,
  30 * MINUTE,
  HOUR,
  2 * HOUR,
  3 * HOUR,
  6 * HOUR,
  12 * HOUR,
  DAY,
  2 * DAY,
  7 * DAY,
  14 * DAY,
  30 * DAY,
]

/** Local midnight-aligned tick grid so '6 PM' and 'Thu' land on round times. */
function alignedStart(start: number, step: number): number {
  const offset = new Date(start * 1000).getTimezoneOffset() * MINUTE
  const local = start - offset
  return Math.ceil(local / step) * step + offset
}

export function tickLabel(
  x: number,
  step: number,
  spanSeconds: number
): string {
  const date = new Date(x * 1000)
  if (step < DAY) {
    const minutes = date.getMinutes()
    return date.toLocaleTimeString('en-US', {
      hour: 'numeric',
      ...(minutes ? { minute: '2-digit' } : {}),
    })
  }
  if (spanSeconds <= 8 * DAY)
    return date.toLocaleDateString('en-US', { weekday: 'short' })
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** 4-6 labeled ticks inside the domain (fewer only when the domain is tiny). */
export function ticksFor(
  [start, end]: [number, number],
  maxTicks = 6
): AxisTick[] {
  const spanSeconds = Math.max(1, end - start)
  const step =
    TICK_STEPS.find((candidate) => spanSeconds / candidate <= maxTicks) ??
    TICK_STEPS[TICK_STEPS.length - 1]
  const ticks: AxisTick[] = []
  for (let x = alignedStart(start, step); x <= end; x += step)
    ticks.push({ x, label: tickLabel(x, step, spanSeconds) })
  return ticks
}

/** Hover label for a sample: clock under a day, weekday + clock within a week, else date. */
export function hoverLabel(x: number, spanSeconds: number, res = 60): string {
  const date = new Date(x * 1000)
  const clock = date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
  if (res >= DAY)
    return date.toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    })
  if (spanSeconds <= DAY)
    return `${date.toLocaleDateString('en-US', { weekday: 'short' })} ${clock}`
  return `${date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })}, ${clock}`
}
