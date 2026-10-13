// Benchmarking the queries that have a fix: the rows the result pane lists, the change
// each fix makes and the one verdict line, from the benchmark-fixes result.
import type {
  BenchmarkFixesResult,
  BenchmarkFixRow,
  MonitorQuery,
} from '@/lib/monitor/types'
import { rowStatus, swatchColor } from '../databaseModel'

export interface FixBenchmarkRow {
  key: string
  name: string
  color: string
  sql: string | null
  before_ms: number | null
  after_ms: number | null
  /** True while the after value is the planner's estimate, not a timed run. */
  estimated: boolean
  /** Why the change couldn't be measured, when the result says. */
  reason?: string | null
}

/** A result row with the reason an unmeasured change carries, once the API sends it. */
type ResultRow = BenchmarkFixRow & { reason?: string | null }

/** The queries a benchmark covers: every row with a suggested fix. */
export function queriesWithFixes(rows: MonitorQuery[]): MonitorQuery[] {
  return rows.filter(
    (row) => rowStatus(row) === 'fix_suggested' && row.suggested_fix !== null
  )
}

/**
 * One row per query: the result's before and after, else the live mean and nothing. A
 * before the result couldn't measure stays unknown, and so does a mean with no calls.
 */
export function benchmarkRows(
  queries: MonitorQuery[],
  result: BenchmarkFixesResult | undefined
): FixBenchmarkRow[] {
  const measured = new Map<string, ResultRow>(
    result?.rows.map((row) => [row.key, row]) ?? []
  )
  return queries.map((query) => {
    const facts = measured.get(query.key)
    return {
      key: query.key,
      name: query.display_name,
      color: swatchColor(query.color_index),
      sql: query.suggested_fix?.sql ?? null,
      before_ms: facts
        ? facts.before_ms
        : query.mean_ms > 0
          ? query.mean_ms
          : null,
      after_ms: facts?.after_ms ?? null,
      estimated: facts?.estimated ?? true,
      reason: facts?.reason ?? null,
    }
  })
}

/** -96 for 69.7 ms before and 3.1 ms after; null until both are measured. */
export function changePercent(
  before: number | null,
  after: number | null
): number | null {
  if (before == null || after == null || before <= 0) return null
  return Math.round(((after - before) / before) * 100)
}

export function formatChange(percent: number | null): string | null {
  if (percent == null) return null
  return `${percent > 0 ? '+' : ''}${percent}%`
}

export interface BenchmarkVerdict {
  lead: string
  rest: string | null
}

/**
 * "All 9 faster with their fixes." and the share of the database's time they cover. A
 * change with no after number is never counted as slower: it has no estimate.
 */
export function benchmarkVerdict(
  rows: FixBenchmarkRow[],
  target: string,
  share: number | null = null
): BenchmarkVerdict {
  const measured = rows.filter(
    (row) => row.before_ms != null && row.after_ms != null
  )
  const faster = measured.filter(
    (row) => (row.after_ms as number) < (row.before_ms as number)
  )
  const unmeasured = rows.length - measured.length
  const reason = rows.find((row) => row.reason)?.reason ?? null
  if (rows.length && !measured.length)
    return {
      lead:
        rows.length === 1
          ? 'No estimate for this change.'
          : 'No estimate for these changes.',
      rest: reason,
    }
  if (
    rows.length &&
    measured.length === rows.length &&
    faster.length === rows.length
  ) {
    const fall = share == null ? null : Math.round(share * 100)
    return {
      lead: `All ${rows.length} faster with their fixes.`,
      rest: fall ? `Total time on ${target} would fall ${fall}%.` : null,
    }
  }
  return {
    lead: `${faster.length} of ${rows.length} faster with their fixes.`,
    rest: unmeasured
      ? [
          `No estimate for ${unmeasured === 1 ? '1 change' : `${unmeasured} changes`}.`,
          reason,
        ]
          .filter(Boolean)
          .join(' ')
      : null,
  }
}

/** Every fix's SQL, one after the other, for the clipboard. */
export function allFixesSql(rows: FixBenchmarkRow[]): string {
  return rows
    .map((row) => row.sql)
    .filter((sql): sql is string => Boolean(sql))
    .join('\n\n')
}
