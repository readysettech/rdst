// "Analyze a query" on the Database page: what the pasted SQL needs before a deep
// analysis can run, and how a finished run is filed in the query list.
import type { CompleteEvent } from '@/lib/api'
import type { AddQueryInput } from '@/lib/monitor/types'
import { hasParameters } from '@/lib/sqlParameters'
import { sqlAnalyzability } from '../queries/analyzability'

/** One statement, without surrounding blank lines or a trailing semicolon. */
export function pastedSql(input: string): string {
  return input.trim().replace(/;\s*$/, '').trim()
}

export type PasteCheck =
  | { ok: true; needsValues: boolean }
  | { ok: false; reason: string | null }

/** Can this SQL be analyzed, and does it need parameter values first? */
export function checkPaste(input: string): PasteCheck {
  const sql = pastedSql(input)
  if (!sql) return { ok: false, reason: null }
  if (/;\s*\S/.test(sql))
    return { ok: false, reason: 'Paste one statement at a time.' }
  const gate = sqlAnalyzability(sql)
  if (!gate.ok) return { ok: false, reason: gate.reason }
  return { ok: true, needsValues: hasParameters(sql) }
}

/** The list entry for a finished run: keyed by the SQL as pasted, with its analysis. */
export function addQueryInput(
  input: string,
  results: CompleteEvent | undefined,
  planOnly: boolean
): AddQueryInput {
  const format = (
    results?.explain_results as { plan_format?: string } | null | undefined
  )?.plan_format
  const measured = format ? format === 'analyze' : !planOnly
  const stored = results?.query_hash && results.stored_analysis_id
  return {
    sql: pastedSql(input),
    query_hash: stored ? results.query_hash : null,
    analysis_id: stored ? results.stored_analysis_id : null,
    mode: stored ? (measured ? 'analyze' : 'explain') : null,
  }
}
