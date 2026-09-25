// Which queries RDST will benchmark, and old benchmark links on the Database page.
import type { MonitorQuery } from '@/lib/monitor/types'

/** A query can be benchmarked only when its text was captured. */
export function hasQueryText(query: MonitorQuery): boolean {
  return query.text_status !== 'unavailable' && query.text.trim().length > 0
}

const READ_ONLY_START = /^\s*(\(|(select|with|table|values)\b)/i

/** Why RDST won't run this SQL in a benchmark: only reads run. Null when it can. */
export function sqlBlockReason(sql: string): string | null {
  if (!sql.trim() || READ_ONLY_START.test(sql)) return null
  const verb = sql.trim().split(/\s+/)[0]?.toUpperCase()
  return `RDST only runs read-only queries. This statement runs ${verb}.`
}

/**
 * Why RDST won't run this query in a benchmark, or null when it can. The analyzer's
 * safety rules decide (a skipped analysis carries their reason, such as pg_sleep or
 * a write); a statement that isn't a read is refused before any analysis ran.
 */
export function benchmarkBlockReason(query: MonitorQuery): string | null {
  if (!hasQueryText(query)) return null
  if (query.last_analysis?.state === 'skipped')
    return query.last_analysis.reason ?? "RDST won't run this query."
  return sqlBlockReason(query.text)
}

const LEGACY_KEYS = ['bench', 'run', 'select'] as const

/**
 * The Database page once had a multi-query benchmark drawer driven by `bench`, `run`
 * and `select`. Links that still carry them drop them; a `select` of queries opens
 * the first one on its Benchmarks section. Null when there's nothing to rewrite.
 */
export function withoutLegacyBenchmarkParams<
  T extends Partial<Record<string, string>>,
>(search: T): T | null {
  if (!LEGACY_KEYS.some((key) => search[key] !== undefined)) return null
  const next: Partial<Record<string, string>> = { ...search }
  for (const key of LEGACY_KEYS) delete next[key]
  const first = search.select
    ?.split(',')
    .map((key) => key.trim())
    .find(Boolean)
  if (first && !next.query) {
    next.query = first
    next.tab = 'readyset'
  }
  return next as T
}
