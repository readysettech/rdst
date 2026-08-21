import type { SchemaDetails, SchemaTableColumn } from '../types/schema'
import type { Parameter } from './sqlParameters'

export interface ParameterValueSuggestion {
  value: string
  provenance: string
}

export function parameterValueKey(parameter: Parameter): string {
  return parameter.placeholder === '?'
    ? `?${parameter.index}`
    : parameter.placeholder
}

function parameterOffset(sql: string, parameter: Parameter): number {
  if (parameter.placeholder !== '?') return sql.indexOf(parameter.placeholder)
  let offset = -1
  for (let index = 0; index < parameter.index; index += 1) {
    offset = sql.indexOf('?', offset + 1)
    if (offset < 0) return -1
  }
  return offset
}

function cleanIdentifier(value: string): string {
  return (
    value
      .replace(/["`[\]]/g, '')
      .split('.')
      .pop()
      ?.toLowerCase() ?? ''
  )
}

interface ColumnReference {
  column: string
  qualifier: string | null
}

function splitQualified(raw: string): ColumnReference {
  const parts = raw
    .replace(/["`[\]]/g, '')
    .split('.')
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean)
  const column = parts.pop() ?? ''
  return { column, qualifier: parts.pop() ?? null }
}

const QUALIFIED_IDENTIFIER =
  '["`[]?[A-Za-z_][\\w$]*["`\\]]?(?:\\s*\\.\\s*["`[]?[A-Za-z_][\\w$]*["`\\]]?)?'

const COMPARISON_BEFORE_RE = new RegExp(
  `(${QUALIFIED_IDENTIFIER})\\s*(?:=|<>|!=|<=|>=|<|>|LIKE|ILIKE|IN\\s*\\()\\s*$`,
  'i'
)
const COMPARISON_AFTER_RE = new RegExp(
  `^\\s*(?:=|<>|!=|<=|>=|<|>|LIKE|ILIKE)\\s*(${QUALIFIED_IDENTIFIER})`,
  'i'
)
// COALESCE(col, $n) and friends type the parameter against their first
// argument the same way a direct comparison does.
const COLUMN_FUNCTION_BEFORE_RE = new RegExp(
  `\\b(?:COALESCE|IFNULL|NULLIF)\\s*\\(\\s*(${QUALIFIED_IDENTIFIER})\\s*,\\s*$`,
  'i'
)
// A simple CASE (`CASE col WHEN $1 THEN ... WHEN $2 THEN ...`) compares each
// WHEN branch against the CASE subject the same way a direct comparison does.
const CASE_WHEN_BEFORE_RE = /\bWHEN\s*$/i

/**
 * Find the subject of the nearest simple CASE enclosing `before` (a searched
 * CASE, or one already closed by its own END, does not count). Scans
 * backward through CASE/END tokens tracking nesting depth so a parameter
 * inside a later WHEN branch still resolves to the original subject.
 */
function simpleCaseSubject(before: string): string | null {
  const tokens = Array.from(before.matchAll(/\b(CASE|END)\b/gi))
  let depth = 0
  let caseIndex = -1
  for (let i = tokens.length - 1; i >= 0; i--) {
    const token = tokens[i][0].toUpperCase()
    if (token === 'END') {
      depth++
      continue
    }
    if (depth === 0) {
      caseIndex = tokens[i].index
      break
    }
    depth--
  }
  if (caseIndex < 0) return null
  const afterCase = before.slice(caseIndex + 4)
  const subjectMatch = afterCase.match(
    new RegExp(`^\\s*(${QUALIFIED_IDENTIFIER})\\s+WHEN\\b`, 'i')
  )
  return subjectMatch ? subjectMatch[1] : null
}

function parameterColumnReference(
  sql: string,
  parameter: Parameter
): ColumnReference | null {
  const offset = parameterOffset(sql, parameter)
  if (offset < 0) return null
  const before = sql.slice(Math.max(0, offset - 160), offset)
  const after = sql.slice(offset + parameter.placeholder.length, offset + 160)
  const beforeMatch = before.match(COMPARISON_BEFORE_RE)
  if (beforeMatch) return splitQualified(beforeMatch[1])

  const functionMatch = before.match(COLUMN_FUNCTION_BEFORE_RE)
  if (functionMatch) return splitQualified(functionMatch[1])

  const afterMatch = after.match(COMPARISON_AFTER_RE)
  if (afterMatch) return splitQualified(afterMatch[1])

  if (CASE_WHEN_BEFORE_RE.test(before)) {
    const subject = simpleCaseSubject(before)
    if (subject) return splitQualified(subject)
  }

  if (parameter.type === 'named') {
    return {
      column: parameter.placeholder.replace(/^[:@]/, '').toLowerCase(),
      qualifier: null,
    }
  }
  return null
}

const NON_ALIAS_KEYWORDS = new Set([
  'as',
  'on',
  'using',
  'where',
  'join',
  'inner',
  'left',
  'right',
  'full',
  'cross',
  'natural',
  'outer',
  'lateral',
  'group',
  'order',
  'limit',
  'offset',
  'having',
  'union',
  'intersect',
  'except',
  'set',
  'values',
  'tablesample',
])

/**
 * Map each `WITH name AS (...)` CTE to the first table its body reads from,
 * best-effort so a parameter compared against a CTE column can still resolve
 * to real schema evidence through a simple (non-nested) CTE.
 */
function cteTableAliases(sql: string): Map<string, string> {
  const aliases = new Map<string, string>()
  const matches = sql.matchAll(/\bWITH\s+(?:RECURSIVE\s+)?([\w$]+)\s+AS\s*\(/gi)
  for (const match of matches) {
    const name = cleanIdentifier(match[1])
    if (!name || match.index == null) continue
    const bodyStart = match.index + match[0].length
    let depth = 1
    let end = bodyStart
    while (end < sql.length && depth > 0) {
      if (sql[end] === '(') depth++
      else if (sql[end] === ')') depth--
      end++
    }
    const body = sql.slice(bodyStart, end - 1)
    const tableMatch = body.match(/\b(?:FROM|JOIN)\s+(["`[]?[\w$.]+["`\]]?)/i)
    if (tableMatch) aliases.set(name, cleanIdentifier(tableMatch[1]))
  }
  return aliases
}

/** Map every table reference and its alias (`FROM users u`) to the table name. */
function tableAliases(sql: string): Map<string, string> {
  const aliases = new Map<string, string>()
  const matches = sql.matchAll(
    /\b(?:FROM|JOIN|UPDATE|INTO)\s+(["`[]?[\w$.]+["`\]]?)(?:\s+(?:AS\s+)?(["`[]?[A-Za-z_][\w$]*["`\]]?))?/gi
  )
  for (const match of matches) {
    const table = cleanIdentifier(match[1])
    if (!table) continue
    aliases.set(table, table)
    const alias = match[2] ? cleanIdentifier(match[2]) : ''
    if (alias && !NON_ALIAS_KEYWORDS.has(alias)) aliases.set(alias, table)
  }
  const cteAliases = cteTableAliases(sql)
  if (cteAliases.size > 0) {
    for (const [key, table] of aliases) {
      const resolved = cteAliases.get(table)
      if (resolved) aliases.set(key, resolved)
    }
  }
  return aliases
}

function findColumn(
  sql: string,
  reference: ColumnReference,
  schema: SchemaDetails
): { table: string; column: SchemaTableColumn } | null {
  const aliases = tableAliases(sql)
  if (reference.qualifier) {
    const tableName = aliases.get(reference.qualifier) ?? reference.qualifier
    const table = schema.tables.find(
      (candidate) => cleanIdentifier(candidate.name) === tableName
    )
    const column = table?.columns.find(
      (candidate) => candidate.name.toLowerCase() === reference.column
    )
    return table && column ? { table: table.name, column } : null
  }
  const matches = schema.tables.flatMap((table) =>
    table.columns
      .filter((column) => column.name.toLowerCase() === reference.column)
      .map((column) => ({ table: table.name, column }))
  )
  if (matches.length === 1) return matches[0]
  const referenced = new Set(aliases.values())
  const scoped = matches.filter((match) =>
    referenced.has(cleanIdentifier(match.table))
  )
  return scoped.length === 1 ? scoped[0] : null
}

function customEnumValues(schema: SchemaDetails, dataType: string) {
  const normalized = cleanIdentifier(dataType)
  return schema.custom_types.find(
    (type) => cleanIdentifier(type.name) === normalized
  )?.enum_values
}

function suggestionForColumn(
  table: string,
  column: SchemaTableColumn,
  schema: SchemaDetails
): ParameterValueSuggestion | null {
  if (column.is_pii) return null
  const enumValue =
    Object.keys(column.enum_values ?? {})[0] ??
    customEnumValues(schema, column.data_type ?? '')?.[0]
  if (enumValue) {
    return {
      value: enumValue,
      provenance: `Schema enum · ${table}.${column.name}`,
    }
  }

  const dataType = (column.data_type ?? '').toLowerCase()
  if (/\b(bool|boolean)\b/.test(dataType)) {
    return {
      value: 'TRUE',
      provenance: `Schema type · ${table}.${column.name}`,
    }
  }
  if (
    /\b(tinyint|smallint|integer|bigint|int\d*|decimal|numeric|real|double|float)\b/.test(
      dataType
    )
  ) {
    return {
      value: '1',
      provenance: `Schema type · ${table}.${column.name}`,
    }
  }
  return null
}

/** Is `offset` inside an unclosed `LIKE ( ... )` concatenation, e.g. `col LIKE ('%' || $1 || '%')`? */
function withinLikeConcat(sql: string, offset: number): boolean {
  const window = sql.slice(Math.max(0, offset - 200), offset)
  const likeMatch = window.match(/\bI?LIKE\s*\(/i)
  if (!likeMatch || likeMatch.index == null) return false
  const span = window.slice(likeMatch.index)
  const opens = (span.match(/\(/g) ?? []).length
  const closes = (span.match(/\)/g) ?? []).length
  return opens > closes && /\|\|/.test(span)
}

function queryShapeSuggestion(
  sql: string,
  parameter: Parameter
): ParameterValueSuggestion | null {
  const offset = parameterOffset(sql, parameter)
  if (offset < 0) return null
  const before = sql.slice(Math.max(0, offset - 40), offset)
  const after = sql.slice(offset + parameter.placeholder.length, offset + 40)
  const clause = before.match(/\b(LIMIT|OFFSET)\s*$/i)?.[1]
  if (clause) {
    return {
      value: clause.toUpperCase() === 'LIMIT' ? '100' : '0',
      provenance: `Query shape · ${clause.toUpperCase()}`,
    }
  }
  // The percentile fraction is structurally bounded to [0, 1]; the median is
  // the canonical representative value.
  if (/\bPERCENTILE_(?:CONT|DISC)\s*\(\s*$/i.test(before)) {
    return { value: '0.5', provenance: 'Query shape · percentile' }
  }
  // An aggregate compared directly (typically in HAVING) is a count-style
  // threshold; one is the smallest value that still filters anything.
  const aggregate = before.match(
    /\b(COUNT|SUM|AVG|MIN|MAX)\s*\([^()]*\)\s*(?:=|<>|!=|<=|>=|<|>)\s*$/i
  )?.[1]
  if (aggregate) {
    return {
      value: '1',
      provenance: `Query shape · ${aggregate.toUpperCase()} comparison`,
    }
  }
  // DATE_TRUNC's first argument is a unit string, not a value comparison.
  if (/\bDATE_TRUNC\s*\(\s*$/i.test(before)) {
    return { value: 'day', provenance: 'Query shape · DATE_TRUNC unit' }
  }
  // ROUND's second argument is a decimal-place count.
  if (/\bROUND\s*\(\s*[^,()]*,\s*$/i.test(before)) {
    return { value: '2', provenance: 'Query shape · ROUND precision' }
  }
  // A window frame bound (`ROWS BETWEEN $n PRECEDING`) is a row count.
  if (
    /\bROWS?\s+BETWEEN\s*$/i.test(before) &&
    /^\s*(?:PRECEDING|FOLLOWING)\b/i.test(after)
  ) {
    return { value: '5', provenance: 'Query shape · window frame' }
  }
  // A parameter concatenated into a LIKE pattern (`LIKE ('%' || $1 || '%')`)
  // is a search substring; the wildcard itself is the only shape-safe guess.
  if (withinLikeConcat(sql, offset)) {
    return { value: '%', provenance: 'Query shape · LIKE pattern' }
  }
  return null
}

export function buildParameterSuggestions(
  sql: string,
  parameters: Parameter[],
  schema: SchemaDetails | null
): Record<string, ParameterValueSuggestion> {
  const suggestions: Record<string, ParameterValueSuggestion> = {}
  for (const parameter of parameters) {
    const key = parameterValueKey(parameter)
    const shapeSuggestion = queryShapeSuggestion(sql, parameter)
    if (shapeSuggestion) {
      suggestions[key] = shapeSuggestion
      continue
    }
    if (!schema) continue
    const reference = parameterColumnReference(sql, parameter)
    if (!reference) continue
    const match = findColumn(sql, reference, schema)
    if (!match) continue
    const suggestion = suggestionForColumn(match.table, match.column, schema)
    if (suggestion) suggestions[key] = suggestion
  }
  return suggestions
}

/**
 * Summarize a Suggest-values pass truthfully: how many of the missing
 * parameters in this context were filled, how many still need a value, and
 * whether schema evidence was available at all.
 */
export function suggestionSummaryMessage(options: {
  filled: number
  missingBefore: number
  queryCount?: number
  schemaAvailable: boolean
}): string {
  const { filled, missingBefore, queryCount, schemaAvailable } = options
  const parameters = (count: number) =>
    count === 1 ? 'parameter' : 'parameters'
  const needs = (count: number) => (count === 1 ? 'needs' : 'need')
  if (filled === 0) {
    const evidence = schemaAvailable
      ? 'No safe suggestions were found.'
      : 'No schema evidence is available for this database.'
    return `${evidence} ${missingBefore} ${parameters(missingBefore)} ${needs(missingBefore)} a value you provide.`
  }
  const scope =
    queryCount && queryCount > 1 ? ` across ${queryCount} queries` : ''
  const remaining = missingBefore - filled
  const remainingPart =
    remaining > 0
      ? ` ${remaining} ${needs(remaining)} a value you provide.`
      : ''
  const schemaNote = schemaAvailable
    ? ''
    : ' Schema evidence is unavailable, so only query-shape suggestions were applied.'
  return `Filled ${filled} of ${missingBefore} missing ${parameters(missingBefore)}${scope}.${remainingPart}${schemaNote} Review suggested values before running.`
}

export async function fetchParameterSchema(
  target: string
): Promise<SchemaDetails | null> {
  const response = await fetch(
    `/api/semantic-layer?target=${encodeURIComponent(target)}`
  )
  if (response.status === 404) return null
  if (!response.ok) throw new Error('Schema suggestions are unavailable.')
  const data = (await response.json()) as SchemaDetails
  return Array.isArray(data.tables) ? data : null
}
