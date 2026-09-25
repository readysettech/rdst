// Steady ordering for the query list while readings settle. Rows keep their places
// between readings and only re-rank when a new reading lands; then a row overtakes the
// one above it only when it leads by more than OVERTAKE_MARGIN, so two queries with
// nearly equal load don't swap back and forth. A real change (a query doubling) still
// moves at the next reading. Pure, so the rules are unit-tested.
import type { MonitorQuery } from '@/lib/monitor/types'
import type { QuerySort } from './querySelectors'

/** How far ahead (relative) a row must be to pass the row above it. */
export const OVERTAKE_MARGIN = 0.2

/** The number each steady sort ranks by, larger first. Other sorts take the fresh order. */
const METRIC: Partial<Record<QuerySort, (query: MonitorQuery) => number>> = {
  impact: (query) => query.impact_ms ?? query.db_time_ms,
  qps: (query) => query.qps,
  latency: (query) => query.mean_ms,
}

function overtakes(value: number, above: number, margin: number) {
  return value > above * (1 + margin) && value - above > 1e-9
}

/**
 * The list's order. `fresh` is the plain sorted order for the current numbers; `previous`
 * is the order shown so far (keys), or null to take `fresh` as is. With `boundary` false
 * (no new reading since `previous`), rows keep their places and new rows slot in where
 * `fresh` puts them.
 */
export function stableOrder(
  previous: readonly string[] | null,
  fresh: readonly MonitorQuery[],
  sort: QuerySort,
  boundary: boolean,
  margin = OVERTAKE_MARGIN,
  /** An open row keeps its place, whatever the reading. */
  pinnedKey: string | null = null
): MonitorQuery[] {
  const metric = METRIC[sort]
  if (!previous || !metric) return [...fresh]
  const byKey = new Map(fresh.map((query) => [query.key, query]))
  const freshIndex = new Map(fresh.map((query, index) => [query.key, index]))
  const kept = previous.filter((key) => byKey.has(key))
  const known = new Set(kept)
  // New rows go before the first kept row that fresh ranks after them.
  const rows: MonitorQuery[] = kept.map((key) => byKey.get(key) as MonitorQuery)
  for (const query of fresh) {
    if (known.has(query.key)) continue
    const at = rows.findIndex(
      (row) => (freshIndex.get(row.key) ?? 0) > (freshIndex.get(query.key) ?? 0)
    )
    if (at < 0) rows.push(query)
    else rows.splice(at, 0, query)
  }
  if (!boundary) return rows
  // One insertion pass: each row climbs past the rows it clearly leads. Nothing moves past
  // or out of the open row.
  for (let i = 1; i < rows.length; i += 1) {
    let j = i
    while (
      j > 0 &&
      rows[j].key !== pinnedKey &&
      rows[j - 1].key !== pinnedKey &&
      overtakes(metric(rows[j]), metric(rows[j - 1]), margin)
    ) {
      ;[rows[j - 1], rows[j]] = [rows[j], rows[j - 1]]
      j -= 1
    }
  }
  return rows
}
