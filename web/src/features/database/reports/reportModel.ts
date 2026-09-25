// Pure helpers for the report page and the Reports band: query-reference parsing,
// fallbacks for older report bodies, and report-time formatting.
import {
  formatClock,
  formatDate,
  formatWeekdayClock,
} from '@/features/monitor-kit/format'
import type {
  DatabaseReport,
  HealthCheckStatus,
  ReportBody,
  ReportFixFirst,
  ReportHealthCheck,
  ReportSummary,
} from '@/lib/monitor/types'

export type TextPart =
  | { kind: 'text'; text: string }
  | { kind: 'query'; key: string }

const TOKEN = /\{\{q:([0-9a-f]{6,64})\}\}|\b([0-9a-f]{12})\b/g

/**
 * Splits narrative text into plain runs and query references. `{{q:key}}` tokens
 * always become references; a bare 12-hex word only when the report knows it (refs),
 * so numbers and identifiers that merely look like hashes stay text.
 */
export function parseReportText(
  text: string,
  knownKeys: ReadonlySet<string> = new Set()
): TextPart[] {
  const parts: TextPart[] = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    const key = match[1] ?? match[2]
    if (!match[1] && !knownKeys.has(key)) continue
    const start = match.index ?? 0
    if (start > last)
      parts.push({ kind: 'text', text: text.slice(last, start) })
    parts.push({ kind: 'query', key })
    last = start + match[0].length
  }
  if (last < text.length) parts.push({ kind: 'text', text: text.slice(last) })
  return mergeText(parts)
}

function mergeText(parts: TextPart[]): TextPart[] {
  const merged: TextPart[] = []
  for (const part of parts) {
    const previous = merged[merged.length - 1]
    if (part.kind === 'text' && previous?.kind === 'text')
      previous.text += part.text
    else merged.push({ ...part })
  }
  return merged
}

/** Plain-text rendering of a narrative string (for aria labels and titles). */
export function plainReportText(
  text: string,
  refs: DatabaseReport['refs'] = {}
): string {
  return parseReportText(text, new Set(Object.keys(refs)))
    .map((part) =>
      part.kind === 'text'
        ? part.text
        : (refs[part.key]?.display_name ?? 'a query')
    )
    .join('')
}

/** The first sentence, for headlines. Keeps "0.5 ms" and "orders.total" intact. */
export function firstSentence(text: string): string {
  const trimmed = text.trim()
  const match = trimmed.match(/^.+?[.!?](?=\s+[A-Z{]|$)/s)
  return (match ? match[0] : trimmed).trim()
}

/** Short imperative title for a fix; falls back to the first clause of the action. */
export function fixTitle(fix: ReportFixFirst): string {
  if (fix.title?.trim()) return fix.title.trim()
  const action = (fix.action ?? '').trim()
  if (!action) return 'Review this query'
  if (/^(create|drop|alter|vacuum|analyze|reindex)\b/i.test(action))
    return 'Apply the recommended change'
  const clause = action.split(/[.;:]|,\s|\s-\s/)[0].trim()
  const words = clause.split(/\s+/)
  return words.length > 9 ? `${words.slice(0, 8).join(' ')}...` : clause
}

/** SQL-looking actions render as code, prose actions as text. */
export function isSqlAction(action: string): boolean {
  return /^\s*(create|drop|alter|vacuum|analyze|reindex|set|cluster)\b/i.test(
    action
  )
}

const LEGACY_STATUS: Record<string, HealthCheckStatus> = {
  ok: 'ok',
  healthy: 'ok',
  good: 'ok',
  warn: 'warn',
  warning: 'warn',
  bad: 'bad',
  critical: 'bad',
  error: 'bad',
  info: 'info',
  collected: 'info',
}

const CHECK_LABELS: Record<string, string> = {
  cache_hit_ratio: 'Cache hit ratio',
  deadlocks: 'Deadlocks',
  connections: 'Connections',
  vacuum: 'Vacuum and bloat',
  bloat: 'Vacuum and bloat',
  index_health: 'Index health',
  config: 'Configuration',
  rollbacks: 'Rollbacks',
}

function humanCheck(check: string): string {
  if (CHECK_LABELS[check]) return CHECK_LABELS[check]
  const words = check.replace(/[_-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Health rows with a human label and a known status, whatever the backend sent. */
export function normalizeHealth(
  checks: ReportHealthCheck[] | undefined
): ReportHealthCheck[] {
  return (checks ?? []).map((check) => ({
    check: check.check,
    label: check.label?.trim() || humanCheck(check.check),
    value: check.value ?? '',
    status: LEGACY_STATUS[(check.status ?? 'info').toLowerCase()] ?? 'info',
    note: check.note ?? '',
  }))
}

export interface ReportCounts {
  fixFirst: number
  oneFix: number
  healthWarn: number
  healthOk: number
}

export function bodyCounts(body: ReportBody | null): ReportCounts {
  const health = normalizeHealth(body?.health)
  return {
    fixFirst: body?.fix_first?.length ?? 0,
    oneFix: body?.one_fix_several_queries?.length ?? 0,
    healthWarn: health.filter((c) => c.status === 'warn' || c.status === 'bad')
      .length,
    healthOk: health.filter((c) => c.status === 'ok').length,
  }
}

export function summaryCounts(summary: ReportSummary): ReportCounts {
  return {
    fixFirst: summary.counts.fix_first,
    oneFix: summary.counts.one_fix,
    healthWarn: summary.counts.health_warn,
    healthOk: summary.counts.health_ok,
  }
}

/** One verdict line: "3 things to fix first . 1 fix helps several queries . health good". */
export function verdictParts(counts: ReportCounts): string[] {
  const parts = [
    counts.fixFirst === 0
      ? 'Nothing urgent to fix'
      : `${counts.fixFirst} ${counts.fixFirst === 1 ? 'thing' : 'things'} to fix first`,
  ]
  if (counts.oneFix > 0)
    parts.push(
      `${counts.oneFix} ${counts.oneFix === 1 ? 'fix helps' : 'fixes help'} several queries`
    )
  parts.push(
    counts.healthWarn === 0
      ? 'health good'
      : `${counts.healthWarn} health ${counts.healthWarn === 1 ? 'warning' : 'warnings'}`
  )
  return parts
}

const DAY = 86400

function startOfDay(ts: number, days = 0) {
  const date = new Date(ts * 1000)
  date.setHours(0, 0, 0, 0)
  date.setDate(date.getDate() + days)
  return date.getTime() / 1000
}

/** Report time: "Today 8:46 AM", "Yesterday 12:00 PM", "Mon 12:00 PM", "Sep 12". */
export function formatReportTime(ts: number, now = Date.now() / 1000): string {
  const today = startOfDay(now)
  if (ts >= today) return `Today ${formatClock(ts)}`
  if (ts >= startOfDay(now, -1)) return `Yesterday ${formatClock(ts)}`
  if (ts >= startOfDay(now, -6)) return formatWeekdayClock(ts)
  return formatDate(ts, now - ts > 300 * DAY)
}

/** Compact variant for tight lists: "8:46 AM", "Yesterday", "Mon", "Sep 12". */
export function formatReportDay(ts: number, now = Date.now() / 1000): string {
  const today = startOfDay(now)
  if (ts >= today) return `Today ${formatClock(ts)}`
  if (ts >= startOfDay(now, -1)) return 'Yesterday'
  if (ts >= startOfDay(now, -6))
    return new Date(ts * 1000).toLocaleDateString('en-US', { weekday: 'short' })
  return formatDate(ts)
}

export function triggerLabel(trigger: ReportSummary['trigger']): string {
  return trigger === 'nightly' ? 'Nightly' : 'You ran it'
}
