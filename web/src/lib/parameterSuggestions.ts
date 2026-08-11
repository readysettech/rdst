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

function parameterColumnName(sql: string, parameter: Parameter): string | null {
  const offset = parameterOffset(sql, parameter)
  if (offset < 0) return null
  const before = sql.slice(Math.max(0, offset - 160), offset)
  const after = sql.slice(offset + parameter.placeholder.length, offset + 160)
  const beforeMatch = before.match(
    /(["`[]?[A-Za-z_][\w$]*["`\]]?(?:\s*\.\s*["`[]?[A-Za-z_][\w$]*["`\]]?)?)\s*(?:=|<>|!=|<=|>=|<|>|LIKE|ILIKE|IN\s*\()\s*$/i
  )
  if (beforeMatch) return cleanIdentifier(beforeMatch[1])

  const afterMatch = after.match(
    /^\s*(?:=|<>|!=|<=|>=|<|>|LIKE|ILIKE)\s*(["`[]?[A-Za-z_][\w$]*["`\]]?(?:\s*\.\s*["`[]?[A-Za-z_][\w$]*["`\]]?)?)/i
  )
  if (afterMatch) return cleanIdentifier(afterMatch[1])

  if (parameter.type === 'named') {
    return parameter.placeholder.replace(/^[:@]/, '').toLowerCase()
  }
  return null
}

function referencedTables(sql: string): Set<string> {
  return new Set(
    Array.from(
      sql.matchAll(/\b(?:FROM|JOIN|UPDATE|INTO)\s+(["`[]?[\w$.]+["`\]]?)/gi),
      (match) => cleanIdentifier(match[1])
    )
  )
}

function findColumn(
  sql: string,
  columnName: string,
  schema: SchemaDetails
): { table: string; column: SchemaTableColumn } | null {
  const tables = referencedTables(sql)
  const matches = schema.tables.flatMap((table) =>
    table.columns
      .filter((column) => column.name.toLowerCase() === columnName)
      .map((column) => ({ table: table.name, column }))
  )
  if (matches.length === 1) return matches[0]
  const scoped = matches.filter((match) =>
    tables.has(cleanIdentifier(match.table))
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

function queryShapeSuggestion(
  sql: string,
  parameter: Parameter
): ParameterValueSuggestion | null {
  const offset = parameterOffset(sql, parameter)
  if (offset < 0) return null
  const before = sql.slice(Math.max(0, offset - 24), offset)
  const clause = before.match(/\b(LIMIT|OFFSET)\s*$/i)?.[1]
  if (!clause) return null
  return {
    value: clause.toUpperCase() === 'LIMIT' ? '100' : '0',
    provenance: `Query shape · ${clause.toUpperCase()}`,
  }
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
    const columnName = parameterColumnName(sql, parameter)
    if (!columnName) continue
    const match = findColumn(sql, columnName, schema)
    if (!match) continue
    const suggestion = suggestionForColumn(match.table, match.column, schema)
    if (suggestion) suggestions[key] = suggestion
  }
  return suggestions
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
