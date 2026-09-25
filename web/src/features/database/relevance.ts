// The relevance gate (NOTIFICATION_MODEL.md): only queries in the top 10 by
// recorded database time, or at 2 % of it or more, earn attention on the Database
// page. Everything else stays reachable but quiet: collapsed in the list, and
// without inbox markers on the charts.
import type { ChartMarker, InboxItem, MonitorQuery } from '@/lib/monitor/types'

export const RELEVANCE_TOP_N = 10
export const RELEVANCE_MIN_SHARE = 0.02

/** Keys of the queries that pass the gate over the fetched range. */
export function relevantQueryKeys(queries: MonitorQuery[]): Set<string> {
  const ranked = [...queries].sort((a, b) => b.db_time_ms - a.db_time_ms)
  return new Set(
    ranked
      .filter(
        (query, index) =>
          query.db_time_ms > 0 &&
          (index < RELEVANCE_TOP_N ||
            query.db_time_share >= RELEVANCE_MIN_SHARE)
      )
      .map((query) => query.key)
  )
}

/** The newest bucket any query reported: the end of the list's window. */
export function windowEnd(queries: MonitorQuery[]): number {
  let end = 0
  for (const query of queries)
    for (const point of query.series) if (point.ts > end) end = point.ts
  return end
}

/**
 * A query that ran earlier in the range but has no calls in the last hour (or the
 * last two buckets, whichever is longer). A stuck query is still running.
 */
export function isStopped(query: MonitorQuery, end: number): boolean {
  if (query.state.includes('stuck') || !end || query.series.length < 2)
    return false
  const ts = query.series.map((point) => point.ts).sort((a, b) => a - b)
  const res = ts.length > 1 ? ts[ts.length - 1] - ts[ts.length - 2] : 60
  const quietFrom = end - Math.max(3600, 2 * res)
  let lastCall = 0
  for (const point of query.series)
    if ((point.qps ?? 0) > 0 && point.ts > lastCall) lastCall = point.ts
  return lastCall > 0 && lastCall <= quietFrom
}

/** Inbox types that are digest material or index attribution, never chart events. */
const QUIET_TYPES = new Set([
  'daily_summary',
  'one_fix_several_queries',
  'fix_available',
  'fix_confirmed',
  'fix_no_effect',
  'index_no_effect',
  'cache_candidate',
  'slow_creep',
  'query_improved',
  'analysis_completed',
])

/** Monitoring issues about the database itself; sign-in and quota issues are RDST's. */
const DATABASE_ISSUES = new Set(['lost_connection', 'password_missing'])

/** Types that matter only for a top-load query (>= 2 % of recorded time). */
const TOP_LOAD_TYPES = new Set(['query_stopped_running'])

/**
 * Whether an inbox item earns a marker: worth a look (not FYI), about the
 * database, and, when it names queries, about at least one that passes the gate.
 */
export function inboxItemRelevant(
  item: InboxItem,
  relevant: Set<string>,
  shares: Map<string, number>
): boolean {
  if (QUIET_TYPES.has(item.type)) return false
  if (item.type === 'monitoring_issue') return DATABASE_ISSUES.has(item.subject)
  if (item.severity_band === 'fyi') return false
  if (!item.query_keys.length) return true
  if (TOP_LOAD_TYPES.has(item.type))
    return item.query_keys.some(
      (key) => (shares.get(key) ?? 0) >= RELEVANCE_MIN_SHARE
    )
  return item.query_keys.some((key) => relevant.has(key))
}

/**
 * Keeps index changes, reports and analyses; keeps an inbox marker only when its
 * item passes `inboxItemRelevant`. Until the items load, only act-now severity
 * (>= 0.7) markers show, so the strip never floods while data arrives.
 */
export function relevantMarkers(
  markers: ChartMarker[],
  items: InboxItem[] | undefined,
  queries: MonitorQuery[] | undefined
): ChartMarker[] {
  const byId = new Map((items ?? []).map((item) => [item.id, item]))
  const relevant = relevantQueryKeys(queries ?? [])
  const shares = new Map(
    (queries ?? []).map((query) => [query.key, query.db_time_share])
  )
  return markers.filter((marker) => {
    if (marker.kind !== 'inbox') return true
    const item = marker.inbox_id != null ? byId.get(marker.inbox_id) : undefined
    if (!item) return !items && (marker.severity ?? 0) >= 0.7
    if (!queries) return item.severity_band === 'act_now'
    return inboxItemRelevant(item, relevant, shares)
  })
}
