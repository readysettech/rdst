/**
 * Shared formatting utilities for RDST web UI.
 */

import { formatDuration as formatDurationShared } from '@rs/ui-new/format'

/**
 * Format an ISO timestamp as a relative time string (e.g. "5m ago", "2d ago").
 * Callers that re-render on a clock tick pass `nowMs` so the result is derived
 * from reactive state rather than a hidden read of the current time.
 */
export function formatTimestamp(isoString: string, nowMs = Date.now()): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  const diffMs = nowMs - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

/**
 * Format a duration in milliseconds as a human-readable string. Delegates to
 * the shared unit-explicit formatter with two-decimal seconds. Durations of a
 * minute or more render as `Nm Ns` (the shared contract), which reads better
 * than a large seconds count in the slow-query registry's max-duration column.
 */
export function formatDuration(ms?: number): string {
  return formatDurationShared({ ms: ms ?? 0 }, { decimals: 2 })
}

/** Clock-style seconds readout: `Xm Ys`, or `Ys` under a minute. */
export function formatSecondsClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(safe / 60)
  const remainder = safe % 60
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`
}

/** Short seconds readout: drops a zero remainder, so `5m` rather than `5m 0s`. */
export function formatSecondsShort(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return rem ? `${mins}m ${rem}s` : `${mins}m`
}

/**
 * Canonical sub-second-aware ms formatter for latency/duration readouts:
 * `<1ms` under a millisecond, `X.Xms` under a second, `X.XXs` above.
 * Distinct from `formatDuration` (which collapses zero/sub-1ms to `-`) so the
 * `<1ms` cache/audit/benchmark readouts stay byte-identical. `undefined`/`null`
 * → `-`. Single source for the three former local copies. [PS5 dedup item 1]
 */
export function formatMs(ms: number | undefined | null): string {
  if (ms === undefined || ms === null) return '-'
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(1)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

/**
 * Build a card's one muted meta line: drop empty/false/nullish segments and
 * join the rest with ` · `. Single source for the hand-rolled `· `-joins across
 * the query cards. [PS5 dedup item 2]
 */
export function formatMeta(
  segments: Array<string | false | null | undefined>
): string {
  return segments.filter(Boolean).join(' · ')
}

/** First 8 chars of a query hash — the display-length used on every card. */
export function shortHash(h: string): string {
  return h.slice(0, 8)
}
