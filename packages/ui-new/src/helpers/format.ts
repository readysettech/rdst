import { format, formatDistanceToNow, isValid, parseISO } from 'date-fns'

type InputDate = string | number | Date

const normalizeEpochToMs = (value: number): number => {
  const abs = Math.trunc(Math.abs(value))
  if (abs === 0) return 0
  const len = String(abs).length
  if (len <= 10) return value * 1000 // seconds -> ms
  if (len <= 13) return value // already ms
  if (len <= 16) return Math.floor(value / 1000) // microseconds -> ms
  return Math.floor(value / 1_000_000) // nanoseconds -> ms (best effort)
}

const coerceToDate = (input: InputDate): Date => {
  if (input instanceof Date) return input
  if (typeof input === 'number' && !Number.isNaN(input)) {
    return new Date(normalizeEpochToMs(input))
  }
  if (typeof input === 'string') {
    let s = input.trim()
    if (!s) return new Date(Number.NaN)
    const lower = s.toLowerCase()
    if (lower === 'nan' || lower === 'unknown' || lower === '--')
      return new Date(Number.NaN)
    if (/^\d+$/.test(s)) {
      const num = Number(s)
      return new Date(normalizeEpochToMs(num))
    }
    // Normalize common non-ISO formats Safari rejects
    // 1) "YYYY-MM-DD HH:mm:ss" -> "YYYY-MM-DDTHH:mm:ss"
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(s)) {
      s = s.replace(' ', 'T')
    }
    // 2) Handle timezone names at the end like " UTC" or " GMT"
    //    Replace with ISO Z (UTC)
    s = s.replace(/\s?(UTC|GMT)$/i, 'Z')
    // 3) Normalize numeric timezone without colon: +0000 -> +00:00
    s = s.replace(/([+-]\d{2})(\d{2})$/, '$1:$2')
    // If no timezone info, assume UTC to avoid Safari local parsing pitfalls
    const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(s)
    const normalized = hasTz ? s : `${s}Z`
    return parseISO(normalized)
  }
  return new Date(Number.NaN)
}

export const formatDateToRelativeTime = (value: InputDate): string => {
  const date = coerceToDate(value)
  if (!isValid(date)) return '--'
  return formatDistanceToNow(date, { addSuffix: true })
}

export const formatDate = (
  value: InputDate,
  formatStr = 'MMMM dd, yyyy, HH:mm'
): string => {
  const date = coerceToDate(value)
  if (!isValid(date)) return '--'
  return format(date, formatStr)
}

export const formatMoney = (amount: number) =>
  amount.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
  })

export interface FormatDurationOptions {
  /**
   * Decimal places shown for the sub-minute seconds display (the `N.Ns`
   * band below). Defaults to `1`. Has no effect on the sub-second `ms` band
   * or the `Nm Ns` band, which are always integer.
   */
  decimals?: number
}

/**
 * Format a duration as a compact, human-readable string. The unit is
 * explicit in the input — `{ seconds }` or `{ ms }` — so callers can't
 * silently pass the wrong unit (the bug this consolidates away: RDST had
 * three `formatDuration` copies, one keyed on milliseconds and two on
 * seconds, with no way to tell from a call site which was expected).
 *
 * Display contract, by magnitude:
 *   - `<= 0` or non-finite -> `-`
 *   - `0 < duration < 1ms` -> `<1ms`
 *   - `1ms <= duration < 1000ms` -> `X.Xms` (one decimal)
 *   - `1s <= duration < 60s` -> `X.Xs` (`opts.decimals`, default one)
 *   - `duration >= 60s` -> `Nm Ns` (seconds omitted entirely when zero,
 *     e.g. `5m` not `5m 0s`)
 */
export function formatDuration(
  input: { seconds: number } | { ms: number },
  opts: FormatDurationOptions = {}
): string {
  // Discrimination is `'ms' in input`: if a widened value somehow carries
  // both keys, `ms` wins.
  const ms = 'ms' in input ? input.ms : input.seconds * 1000
  if (!Number.isFinite(ms) || ms <= 0) return '-'
  if (ms < 1) return '<1ms'

  // Each band renders first and only claims the value if rounding kept it
  // inside the band — otherwise it falls through (999.96ms is 1.0s, not
  // 1000.0ms; 59.96s is 1m, not 60.0s).
  if (ms < 1000) {
    const rendered = ms.toFixed(1)
    if (Number(rendered) < 1000) return `${rendered}ms`
  }

  const seconds = ms / 1000
  if (seconds < 60) {
    const rendered = seconds.toFixed(opts.decimals ?? 1)
    if (Number(rendered) < 60) return `${rendered}s`
  }

  const totalSeconds = Math.round(seconds)
  const mins = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  return secs ? `${mins}m ${secs}s` : `${mins}m`
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB'] as const

/**
 * Format a byte count as a compact, human-readable string (`B`/`KB`/`MB`/
 * `GB`, no larger — this covers RDST's data-volume readouts). Precision
 * scales down as the number gets bigger so the string stays short: two
 * decimals under 10, one decimal under 100, none at or above 100 (and for
 * whole bytes).
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '-'
  if (bytes === 0) return '0 B'

  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < BYTE_UNITS.length - 1) {
    value /= 1024
    unitIndex++
  }

  // Round, then settle: rounding can cross a precision or unit boundary
  // (9.996 must read 10.0 not 10.00; 1023.6 B must read 1.00 KB not
  // 1024 B), so re-bucket until the rounded value agrees with the band
  // that formatted it. Converges in at most two passes.
  for (;;) {
    const decimals =
      unitIndex === 0 ? 0 : value < 10 ? 2 : value < 100 ? 1 : 0
    const rendered = Number(value.toFixed(decimals))
    if (rendered >= 1024 && unitIndex < BYTE_UNITS.length - 1) {
      value = rendered / 1024
      unitIndex++
      continue
    }
    if (rendered !== value) {
      value = rendered
      continue
    }
    return `${value.toFixed(decimals)} ${BYTE_UNITS[unitIndex]}`
  }
}
