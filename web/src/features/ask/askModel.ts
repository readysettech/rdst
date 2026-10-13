// Pure helpers for the Ask page: the lead sentence built from the answer's ranking,
// the table's numeric columns, the Earlier list grouped by day, and the step the
// stream is on.
import type { AskHistoryItem } from '@/lib/api'
import type { AskResultEvent } from '@/lib/ask'

export const ROWS_SHOWN = 6

/** The LIMIT the backend adds to a query that has none. */
export const DEFAULT_ASK_LIMIT = 100

/** The LIMIT the backend added: the last one in the SQL that ran, else its default. */
export function addedLimit(sql: string): number {
  const limits = [...sql.matchAll(/\blimit\s+(\d+)/gi)]
  const last = limits.at(-1)
  return last ? Number(last[1]) : DEFAULT_ASK_LIMIT
}

export const STEPS = [
  'Reading the schema',
  'Writing the SQL',
  'Running it read-only',
] as const

const STEP_FOR_PHASE: Record<string, number> = {
  config: 0,
  schema: 0,
  filter: 0,
  clarify: 0,
  generate: 1,
  validate: 1,
  execute: 2,
}

/** The 0-based step the stream is on. */
export function stepForPhase(phase: string | undefined): number {
  return phase ? (STEP_FOR_PHASE[phase] ?? 0) : 0
}

/** A value that reads as a quantity, whether the driver typed it or not. */
export function isNumeric(cell: unknown): boolean {
  if (typeof cell === 'number') return Number.isFinite(cell)
  return (
    typeof cell === 'string' &&
    cell.trim() !== '' &&
    !Number.isNaN(Number(cell))
  )
}

/** Which columns hold numbers, so they align right in mono. */
export function numericColumns(result: AskResultEvent): boolean[] {
  return result.columns.map((_, index) => {
    const values = result.rows
      .map((row) => row[index])
      .filter((cell) => cell !== null && cell !== '')
    return values.length > 0 && values.every((cell) => isNumeric(cell))
  })
}

/** Last words that make a column's numbers labels (customer_id, zip, startYear). */
const LABEL_WORDS = new Set([
  'id',
  'key',
  'code',
  'zip',
  'zipcode',
  'postcode',
  'yr',
])

/** A column whose numbers are labels, printed without grouping and never a quantity. */
export function isLabelColumn(column: string): boolean {
  const words = column
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
  const last = words.at(-1) ?? ''
  return LABEL_WORDS.has(last) || last.endsWith('year')
}

export function cellText(cell: unknown, column?: string): string {
  if (cell === null || cell === undefined) return 'NULL'
  if (typeof cell === 'number' && !(column && isLabelColumn(column)))
    return Number.isInteger(cell) ? cell.toLocaleString('en-US') : String(cell)
  return String(cell)
}

function singular(word: string): string {
  return word.endsWith('s') && word.length > 3 ? word.slice(0, -1) : word
}

/** An ORDER BY key: a column name, possibly quoted or table-qualified, or a 1-based ordinal,
 * then its direction. */
const ORDER_KEY =
  /^\s*(?:(\d+)\b|(?:(?:"[^"]+"|`[^`]+`|[\w$]+)\s*\.\s*)*("[^"]+"|`[^`]+`|[\w$]+))\s*(asc\b|desc\b)?\s*(?:nulls\b|,|;|limit\b|offset\b|$)/i

export interface OrderedBy {
  /** The column sorted on first; null when the key is an expression the answer can't name. */
  column: number | null
  descending: boolean
}

/** What the query's outermost ORDER BY sorts on first; null when it has none. */
export function orderedBy(sql: string, columns: string[]): OrderedBy | null {
  let depth = 0
  let quote: string | null = null
  let key: string | null = null
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]
    if (quote) {
      if (ch === quote) quote = null
    } else if (ch === "'" || ch === '"' || ch === '`') quote = ch
    else if (ch === '(') depth++
    else if (ch === ')') depth--
    else if (depth === 0 && /^order\s+by\b/i.test(sql.slice(i, i + 16))) {
      if (i === 0 || !/[\w$]/.test(sql[i - 1]))
        key = sql.slice(i).replace(/^order\s+by/i, '')
    }
  }
  if (key === null) return null
  const match = key.match(ORDER_KEY)
  if (!match) return { column: null, descending: false }
  const descending = match[3]?.toLowerCase() === 'desc'
  if (match[1]) {
    const ordinal = Number(match[1]) - 1
    return {
      column: ordinal >= 0 && ordinal < columns.length ? ordinal : null,
      descending,
    }
  }
  const name = match[2].replace(/^["`]|["`]$/g, '').toLowerCase()
  const index = columns.findIndex((column) => column.toLowerCase() === name)
  return { column: index < 0 ? null : index, descending }
}

/**
 * The lead sentence. A single value reads as itself. A ranking names its top row by the first
 * text cell and says how far ahead of the runner-up it is, or which row is lowest when the
 * ranking ascends. The ranking is the SQL's ORDER BY on a quantity, in the order the rows
 * came back. An answer without one, sorted on anything else, or whose top value is shared,
 * states the row count, and that the rows stop at the LIMIT when they do.
 */
export function leadSentence(result: AskResultEvent): string | null {
  if (result.rows.length === 0) return null
  const numeric = numericColumns(result)
  const textIndex = numeric.findIndex((isNumber) => !isNumber)
  const quantities = numeric.flatMap((isNumber, index) =>
    isNumber && !isLabelColumn(result.columns[index]) ? [index] : []
  )
  const fallback = result.truncated
    ? `Showing the first ${rowsWord(result.row_count)}; the query matched more.`
    : `The answer has ${rowsWord(result.row_count)}.`
  if (result.rows.length === 1 && textIndex < 0 && quantities.length === 1) {
    const [index] = quantities
    const column = result.columns[index]
    return `The answer is ${cellText(Number(result.rows[0][index]), column)} ${column.toLowerCase()}.`
  }
  const order = orderedBy(result.sql, result.columns)
  if (textIndex < 0 || order?.column == null) return fallback
  const valueIndex = order.column
  if (!quantities.includes(valueIndex)) return fallback
  const [top, next] = result.rows.filter((row) => isNumeric(row[valueIndex]))
  if (!top) return fallback
  const title = String(top[textIndex] ?? '').trim()
  const value = Number(top[valueIndex])
  const second = next ? Number(next[valueIndex]) : null
  if (!title || second === value) return fallback
  const metric = result.columns[valueIndex].toLowerCase()
  const amount = `${value.toLocaleString('en-US')} ${metric}`
  if (!order.descending) return `"${title}" is lowest with ${amount}.`
  const noun = singular(result.columns[textIndex].toLowerCase())
  let sentence = `"${title}" leads with ${amount}`
  if (second !== null && second > 0 && value / second >= 2)
    sentence += `, more than twice the next ${noun}`
  return `${sentence}.`
}

/** "0.4 s". */
export function secondsWord(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`
}

/** "412 rows" or "1 row". */
export function rowsWord(count: number): string {
  return `${count.toLocaleString('en-US')} ${count === 1 ? 'row' : 'rows'}`
}

function startOfDay(ms: number): number {
  const date = new Date(ms)
  date.setHours(0, 0, 0, 0)
  return date.getTime()
}

/** "Today", "Yesterday", or "September 18". */
export function dayHeading(iso: string, nowMs: number): string {
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return 'Earlier'
  const days = Math.round((startOfDay(nowMs) - startOfDay(ts)) / 86_400_000)
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return new Date(ts).toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
  })
}

/** "9:12 AM" today, "Sep 18" on another day. */
export function whenWord(iso: string, nowMs: number): string {
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return ''
  const date = new Date(ts)
  return startOfDay(ts) === startOfDay(nowMs)
    ? date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export interface EarlierGroup {
  heading: string
  items: AskHistoryItem[]
}

/** The Earlier list, newest first, one group per day. */
export function groupByDay(
  items: AskHistoryItem[],
  nowMs: number
): EarlierGroup[] {
  const sorted = [...items].sort(
    (a, b) => Date.parse(b.last_used) - Date.parse(a.last_used)
  )
  const groups: EarlierGroup[] = []
  for (const item of sorted) {
    const heading = dayHeading(item.last_used, nowMs)
    const last = groups[groups.length - 1]
    if (last && last.heading === heading) last.items.push(item)
    else groups.push({ heading, items: [item] })
  }
  return groups
}

/** A sampled parameter value, as the analysis names it (`$1`, or a bare `1`). */
export interface SampleParam {
  name: string
  value: string
}

/** True while the SQL still has `$1`-style placeholders. */
export function hasPlaceholders(sql: string): boolean {
  return /\$\d+\b/.test(sql)
}

/** A value the analysis already wrote as a SQL literal: a number, a quoted string, NULL. */
const LITERAL = /^(-?\d+(\.\d+)?|'(?:[^']|'')*'|NULL|TRUE|FALSE)$/i

/**
 * Fills `$1`-style placeholders from the analysis's sample values: a value that is already a
 * SQL literal goes in as is, any other as a quoted string (the database casts it), and a
 * missing value as NULL. Placeholders without a sample stay.
 */
export function fillParams(
  sql: string,
  params: readonly SampleParam[] | undefined
): string {
  if (!params?.length) return sql
  const values = new Map(
    params.map((param) => [param.name.replace(/^\$/, ''), param.value])
  )
  return sql.replace(/\$(\d+)\b/g, (placeholder, index: string) => {
    const value = values.get(index)
    if (value === undefined) return placeholder
    if (value === 'None') return 'NULL'
    return LITERAL.test(value) ? value : `'${value.replace(/'/g, "''")}'`
  })
}
