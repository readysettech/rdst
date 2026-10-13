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

const DOLLAR_TAG = /\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$/y

/**
 * How many statements `sql` holds. A semicolon separates statements only where the
 * database would read it as SQL: not inside a comment, a quoted string or identifier, or a
 * PostgreSQL dollar-quoted body. Empty statements (a trailing `;`) don't count.
 */
export function statementCount(sql: string): number {
  let count = 0
  let open = false
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]
    if (ch === ';') {
      if (open) count++
      open = false
      continue
    }
    if (ch === '-' && sql[i + 1] === '-') {
      const end = sql.indexOf('\n', i)
      i = end < 0 ? sql.length : end
      continue
    }
    if (ch === '/' && sql[i + 1] === '*') {
      // PostgreSQL nests block comments.
      let depth = 1
      for (i += 2; i < sql.length && depth > 0; i++) {
        if (sql[i] === '/' && sql[i + 1] === '*') {
          depth++
          i++
        } else if (sql[i] === '*' && sql[i + 1] === '/') {
          depth--
          i++
        }
      }
      i--
      continue
    }
    if (/\s/.test(ch)) continue
    open = true
    if (ch === "'" || ch === '"' || ch === '`') {
      // A doubled quote escapes itself; MySQL strings and PostgreSQL E'' strings also take
      // a backslash. Reading a backslash as an escape errs toward one statement.
      for (i++; i < sql.length; i++) {
        if (sql[i] === '\\' && ch !== '`') i++
        else if (sql[i] === ch) {
          if (sql[i + 1] === ch) i++
          else break
        }
      }
      continue
    }
    // `$tag$` opens a dollar quote unless it continues an identifier such as `a$b$`.
    if (ch === '$' && !/[\w$]/.test(sql[i - 1] ?? '')) {
      DOLLAR_TAG.lastIndex = i
      const tag = DOLLAR_TAG.exec(sql)?.[0]
      if (tag) {
        const close = sql.indexOf(tag, i + tag.length)
        i = close < 0 ? sql.length : close + tag.length - 1
      }
    }
  }
  return count + (open ? 1 : 0)
}

export type PasteCheck =
  | { ok: true; needsValues: boolean }
  | { ok: false; reason: string | null }

/** Can this SQL be analyzed, and does it need parameter values first? */
export function checkPaste(input: string): PasteCheck {
  const sql = pastedSql(input)
  if (!sql) return { ok: false, reason: null }
  if (statementCount(sql) > 1)
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
