import type { IndexPlannerResult, IndexTesting } from './api'

function normalizeSql(sql: string): string {
  return sql.toLowerCase().replace(/;\s*$/, '').split(/\s+/).join(' ')
}

function sameColumns(a: string[] | null | undefined, b: string[] | null | undefined): boolean {
  if (!a || !b || a.length !== b.length) return false
  return a.every((col, i) => col.toLowerCase() === b[i].toLowerCase())
}

/**
 * Find the planner (hypopg) verdict for an index recommendation, matching by
 * CREATE INDEX text first and by table + column list second.
 */
export function findPlannerResult(
  testing: IndexTesting | null | undefined,
  recommendation: { sql: string; table?: string | null; columns?: string[] | null },
): IndexPlannerResult | undefined {
  if (!testing?.tested || !testing.results) return undefined
  const wanted = normalizeSql(recommendation.sql)
  const bySql = testing.results.find((r) => normalizeSql(r.index_sql) === wanted)
  if (bySql) return bySql
  return testing.results.find(
    (r) =>
      (r.table ?? '').toLowerCase() === (recommendation.table ?? '').toLowerCase() &&
      sameColumns(r.columns, recommendation.columns),
  )
}

export function plannerVerificationOff(testing: IndexTesting | null | undefined): boolean {
  return Boolean(
    testing &&
      !testing.tested &&
      (testing.skipped_reason === 'hypopg_not_installed' ||
        testing.skipped_reason === 'hypopg_not_available'),
  )
}
