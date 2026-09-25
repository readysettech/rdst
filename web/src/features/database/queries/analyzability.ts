// Can RDST analyze this query, and if not, why (DESIGN_V4 section 3)? The backend's
// `analyzable` wins when it is served; otherwise the analyzer's own skip reason, then
// the statement's verb. The Analyze action is disabled with this reason, never a
// silent no-op.
import type { MonitorQuery } from '@/lib/monitor/types'

export type AnalyzabilityCode =
  | 'ok'
  | 'write'
  | 'ddl'
  | 'utility'
  | 'unsafe_function'
  | 'no_text'
  | 'maintenance'
  | 'rdst_internal'
  | 'skipped'

export interface Analyzability {
  ok: boolean
  code: AnalyzabilityCode
  /** One sentence for the row and the disabled Analyze button. */
  reason: string | null
}

const WRITE = /^(insert|update|delete|merge|upsert|replace|copy|call|do)$/i
const DDL =
  /^(create|drop|alter|truncate|grant|revoke|comment|rename|reindex|cluster|vacuum|analyze|refresh)$/i
const UTILITY =
  /^(begin|commit|rollback|savepoint|release|set|reset|show|lock|listen|notify|unlisten|discard|prepare|execute|deallocate|fetch|move|close|declare|start|end|abort|checkpoint|load|use)$/i
const UNSAFE_FUNCTIONS =
  /\b(pg_sleep|sleep|benchmark|pg_terminate_backend|pg_cancel_backend|dblink)\s*\(/i
const READ_START = /^\s*(\(|(select|with|table|values)\b)/i

const stripLeadingComments = (sql: string) =>
  sql.replace(/^\s*(\/\*[\s\S]*?\*\/\s*|--[^\n]*\n\s*)*/, '').trim()

const verbOf = (body: string) => body.split(/[\s(;]+/)[0]?.toUpperCase() ?? ''

/** Why RDST won't analyze `sql`, from the text alone. */
export function sqlAnalyzability(sql: string): Analyzability {
  if (!sql.trim())
    return {
      ok: false,
      code: 'no_text',
      reason:
        "The database didn't keep this statement's text, so there is nothing to analyze.",
    }
  if (/\/\*\s*rdst:/i.test(sql))
    return {
      ok: false,
      code: 'rdst_internal',
      reason: "This is one of RDST's own reads.",
    }
  const body = stripLeadingComments(sql)
  const verb = verbOf(body)
  const fn = body.match(UNSAFE_FUNCTIONS)?.[1]
  if (fn && READ_START.test(body))
    return {
      ok: false,
      code: 'unsafe_function',
      reason: `It calls ${fn}, so RDST doesn't run it.`,
    }
  if (READ_START.test(body)) return { ok: true, code: 'ok', reason: null }
  if (WRITE.test(verb))
    return {
      ok: false,
      code: 'write',
      reason: `${verb} changes data, so RDST doesn't run it.`,
    }
  if (DDL.test(verb))
    return {
      ok: false,
      code: 'ddl',
      reason: `${verb} changes the schema, so RDST doesn't run it.`,
    }
  if (UTILITY.test(verb))
    return {
      ok: false,
      code: 'utility',
      reason: `${verb} is a session command with no plan to analyze.`,
    }
  return {
    ok: false,
    code: 'write',
    reason: `RDST only analyzes reads. This statement runs ${verb || 'something else'}.`,
  }
}

export function analyzability(query: MonitorQuery): Analyzability {
  if (typeof query.analyzable === 'boolean')
    return {
      ok: query.analyzable,
      code:
        (query.analyzable_code as AnalyzabilityCode | undefined) ??
        (query.analyzable ? 'ok' : 'skipped'),
      reason: query.analyzable
        ? null
        : (query.not_analyzable_reason ?? "RDST won't analyze this query."),
    }
  if (query.text_status === 'unavailable' || !query.text.trim())
    return sqlAnalyzability('')
  const fromText = sqlAnalyzability(query.text)
  if (!fromText.ok) return fromText
  if (query.last_analysis?.state === 'skipped')
    return {
      ok: false,
      code: 'skipped',
      reason: query.last_analysis.reason ?? "RDST won't analyze this query.",
    }
  return fromText
}

const REASON_FALLBACK = "RDST won't analyze this query."

/**
 * The line shown in a query's detail when it can't be analyzed. Writes and schema changes
 * name their statement type ("RDST doesn't analyze UPDATE statements because they change
 * data."); other reasons pass through.
 */
export function notAnalyzedLine(
  query: Pick<MonitorQuery, 'text'>,
  gate: Analyzability
): string {
  if (gate.ok) return ''
  const verb = verbOf(stripLeadingComments(query.text))
  if (verb && gate.code === 'write' && WRITE.test(verb))
    return `RDST doesn't analyze ${verb} statements because they change data.`
  if (verb && gate.code === 'ddl' && DDL.test(verb))
    return `RDST doesn't analyze ${verb} statements because they change the schema.`
  return gate.reason ?? REASON_FALLBACK
}

/**
 * Why a row has no score, for its placeholder tile: "No score: RDST doesn't run UPDATE
 * statements", "No score yet: queued, about 6 min".
 */
export function noScoreLine(
  query: Pick<MonitorQuery, 'text'> & Partial<Pick<MonitorQuery, 'qps'>>,
  gate: Analyzability,
  waiting: string | null = null
): string {
  if (waiting)
    return `No score yet: ${waiting.charAt(0).toLowerCase()}${waiting.slice(1)}`
  // Automatic analysis picks from the queries that are running; an idle one waits for a click.
  if (gate.ok && query.qps === 0)
    return "No score: it hasn't run in this period. Click Analyze now to analyze."
  if (gate.ok) return 'No score yet. Click Analyze now to analyze.'
  const verb = verbOf(stripLeadingComments(query.text))
  if (
    verb &&
    ((gate.code === 'write' && WRITE.test(verb)) ||
      (gate.code === 'ddl' && DDL.test(verb)))
  )
    return `No score: RDST doesn't run ${verb} statements`
  return `No score: ${gate.reason ?? REASON_FALLBACK}`
}
