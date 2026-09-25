// Shared number and time formatting for the monitor pages (Database, Inbox, Report).
//
// Usage:
//   formatQps(34.71)            -> '34.7'        (unit rendered separately: 'qps')
//   formatMs(0.132)             -> '0.13'        (unit: 'ms'; see formatMsWithUnit)
//   formatDbTime(93_000)        -> '1.6 min'
//   formatRatio(0.99812)        -> '99.8'        (unit: '%')
//   formatCompact(21_400)       -> '21.4k'
//   formatRelative(ts, now)     -> '12 s ago' | '3 h ago' | 'Mon 2:14 PM' | 'Sep 25'
//   formatChange(1.2, 1.0)      -> { text: '+20%', direction: 'up', ratio: 0.2 }
// Timestamps are Unix seconds everywhere in this kit.

const MINUTE = 60
const HOUR = 3600
const DAY = 86400

function trimmed(value: number, digits: number): string {
  return value.toLocaleString('en-US', {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  })
}

/** Queries per second, value only. Small rates keep two significant digits. */
export function formatQps(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  if (value === 0) return '0'
  const abs = Math.abs(value)
  if (abs >= 10_000) return formatCompact(value)
  if (abs >= 100) return trimmed(value, 0)
  if (abs >= 10) return trimmed(value, 1)
  if (abs >= 1) return trimmed(value, 2)
  if (abs >= 0.01) return trimmed(value, 3)
  return '<0.01'
}

/** Milliseconds, value only: at most two decimals, '<0.01' below that. */
export function formatMs(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  if (value === 0) return '0'
  const abs = Math.abs(value)
  if (abs >= 100) return trimmed(value, 0)
  if (abs >= 10) return trimmed(value, 1)
  if (abs >= 0.01) return trimmed(value, 2)
  return '<0.01'
}

/** A latency with its best unit: '0.13 ms', '312 ms', '4.2 s'. */
export function formatMsWithUnit(value: number | null | undefined): {
  value: string
  unit: string
} {
  if (value == null || !Number.isFinite(value)) return { value: '-', unit: '' }
  if (Math.abs(value) >= 10_000)
    return { value: trimmed(value / 1000, 1), unit: 's' }
  return { value: formatMs(value), unit: 'ms' }
}

/** Accumulated database time from milliseconds: '840 ms', '12 s', '1.6 min', '3.2 h'. */
export function formatDbTime(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '-'
  const seconds = ms / 1000
  if (seconds < 1) return `${trimmed(ms, 0)} ms`
  if (seconds < 60) return `${trimmed(seconds, seconds < 10 ? 1 : 0)} s`
  if (seconds < HOUR) return `${trimmed(seconds / MINUTE, 1)} min`
  if (seconds < DAY) return `${trimmed(seconds / HOUR, 1)} h`
  return `${trimmed(seconds / DAY, 1)} d`
}

/** A 0..1 ratio as a percent value without the sign: 0.99812 -> '99.8'. */
export function formatRatio(ratio: number | null | undefined, digits = 1) {
  if (ratio == null || !Number.isFinite(ratio)) return '-'
  const percent = ratio * 100
  if (percent >= 99.95 && percent < 100) return trimmed(percent, 2)
  return trimmed(percent, digits)
}

/** Share of a whole: 0.336 -> '33.6%'. */
export function formatShare(share: number | null | undefined): string {
  if (share == null || !Number.isFinite(share)) return '-'
  const percent = share * 100
  if (percent > 0 && percent < 0.1) return '<0.1%'
  return `${trimmed(percent, percent < 10 ? 1 : 0)}%`
}

/** Counts: 940, 21.4k, 1.2M. */
export function formatCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${trimmed(value / 1_000_000, 1)}M`
  if (abs >= 10_000) return `${trimmed(value / 1000, abs >= 100_000 ? 0 : 1)}k`
  if (abs >= 1000) return `${trimmed(value / 1000, 1)}k`
  return trimmed(value, abs < 10 && abs % 1 !== 0 ? 1 : 0)
}

/** Bytes as MB with one decimal under 100 MB: '13.8 MB', '250 MB', '1.2 GB'. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '-'
  const mb = bytes / (1024 * 1024)
  if (mb >= 1024) return `${trimmed(mb / 1024, 1)} GB`
  if (mb >= 100) return `${trimmed(mb, 0)} MB`
  return `${trimmed(mb, 1)} MB`
}

export function formatClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function formatWeekdayClock(ts: number): string {
  const date = new Date(ts * 1000)
  return `${date.toLocaleDateString('en-US', { weekday: 'short' })} ${formatClock(ts)}`
}

export function formatDate(ts: number, withYear = false): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(withYear ? { year: 'numeric' } : {}),
  })
}

/** 'Thu Sep 25' */
export function formatDayDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Brief section 3 time rule: relative under 24 h ('12 s ago', '3 h ago'), then
 * weekday + clock within a week ('Mon 2:14 PM'), then a date.
 */
export function formatRelative(ts: number, now = Date.now() / 1000): string {
  const delta = Math.max(0, now - ts)
  if (delta < 5) return 'just now'
  if (delta < MINUTE) return `${Math.floor(delta)} s ago`
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)} min ago`
  if (delta < DAY) return `${Math.floor(delta / HOUR)} h ago`
  if (delta < 7 * DAY) return formatWeekdayClock(ts)
  return formatDate(ts, delta > 300 * DAY)
}

/** A duration in words: '42 s', '14 minutes', '3 hours', '3 days'. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, seconds)
  if (s < MINUTE) return `${Math.round(s)} s`
  if (s < HOUR) {
    const m = Math.round(s / MINUTE)
    return `${m} minute${m === 1 ? '' : 's'}`
  }
  if (s < 2 * DAY) {
    const h = Math.round(s / HOUR)
    return `${h} hour${h === 1 ? '' : 's'}`
  }
  const d = Math.floor(s / DAY)
  return `${d} day${d === 1 ? '' : 's'}`
}

/** Elapsed clock for running work: '0:42', '1:42', '1:02:03'. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / HOUR)
  const m = Math.floor((s % HOUR) / MINUTE)
  const sec = String(s % MINUTE).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

export type ChangeDirection = 'up' | 'down' | 'flat'

export interface FormattedChange {
  text: string
  direction: ChangeDirection
  ratio: number
}

/** Relative change from `before` to `after`. Under 1% reads as 'no change'. */
export function formatChange(
  after: number | null | undefined,
  before: number | null | undefined
): FormattedChange | null {
  if (after == null || before == null) return null
  if (!Number.isFinite(after) || !Number.isFinite(before)) return null
  if (before === 0) {
    if (after === 0) return { text: 'no change', direction: 'flat', ratio: 0 }
    return null
  }
  const ratio = (after - before) / Math.abs(before)
  if (Math.abs(ratio) < 0.01)
    return { text: 'no change', direction: 'flat', ratio }
  const percent = Math.abs(ratio) * 100
  const digits = percent >= 10 ? 0 : 1
  return {
    text: `${ratio > 0 ? '+' : '-'}${trimmed(percent, digits)}%`,
    direction: ratio > 0 ? 'up' : 'down',
    ratio,
  }
}

export function plural(count: number, noun: string, pluralNoun = `${noun}s`) {
  return `${count.toLocaleString('en-US')} ${count === 1 ? noun : pluralNoun}`
}

/**
 * A cache hit ratio as a percent value, one decimal, never rounded up to 100: '100'
 * only when every read hit. 0.99952 -> '99.9', 0.9934 -> '99.3'.
 */
export function formatHitRatio(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio)) return '-'
  if (ratio >= 1) return '100'
  // The epsilon keeps 0.999 at 99.9 despite binary rounding (998.999...).
  return (Math.floor(ratio * 1000 + 1e-6) / 10).toFixed(1)
}
