export interface Parameter {
  placeholder: string
  index: number
  type: 'positional' | 'named'
}

/**
 * Detect parameters in a SQL query.
 * Supports: $1, $2 (PostgreSQL), ? (MySQL), :name, @name (named)
 */
export function detectParameters(sql: string): Parameter[] {
  const params: Parameter[] = []
  const seen = new Set<string>()

  // PostgreSQL positional: $1, $2, etc.
  const pgMatches = sql.matchAll(/\$(\d+)/g)
  for (const match of pgMatches) {
    const placeholder = match[0]
    if (!seen.has(placeholder)) {
      seen.add(placeholder)
      params.push({
        placeholder,
        index: Number.parseInt(match[1], 10),
        type: 'positional',
      })
    }
  }

  // MySQL positional: ? (numbered by occurrence)
  let questionIndex = 1
  const mysqlMatches = sql.matchAll(/\?/g)
  for (const _ of mysqlMatches) {
    params.push({
      placeholder: '?',
      index: questionIndex,
      type: 'positional',
    })
    questionIndex++
  }

  // Named parameters: :name or @name (but not ::type casts)
  const namedMatches = sql.matchAll(/(?<!:)[:@]([a-zA-Z_][a-zA-Z0-9_]*)/g)
  for (const match of namedMatches) {
    const placeholder = match[0]
    if (!seen.has(placeholder)) {
      seen.add(placeholder)
      params.push({
        placeholder,
        index: params.length + 1,
        type: 'named',
      })
    }
  }

  return params.sort((a, b) => a.index - b.index)
}

export function hasParameters(sql: string): boolean {
  return detectParameters(sql).length > 0
}

export function formatValue(value: string): string {
  const trimmed = value.trim()

  if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
    return trimmed
  }

  if (['NULL', 'TRUE', 'FALSE'].includes(trimmed.toUpperCase())) {
    return trimmed.toUpperCase()
  }

  if (
    (trimmed.startsWith("'") && trimmed.endsWith("'")) ||
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
  ) {
    return trimmed
  }

  return `'${trimmed.replace(/'/g, "''")}'`
}

export function substituteParameters(
  sql: string,
  params: Parameter[],
  values: Record<string, string>
): string {
  let result = sql

  // Handle MySQL ? parameters (replace in order)
  const questionParams = params.filter((p) => p.placeholder === '?')
  if (questionParams.length > 0) {
    const parts: string[] = []
    let lastIndex = 0
    let qIndex = 0

    for (let i = 0; i < result.length; i++) {
      if (result[i] === '?') {
        parts.push(result.slice(lastIndex, i))
        const value = values[`?${qIndex + 1}`] || ''
        parts.push(formatValue(value))
        lastIndex = i + 1
        qIndex++
      }
    }
    parts.push(result.slice(lastIndex))
    result = parts.join('')
  }

  // Handle PostgreSQL $N and named parameters
  for (const param of params) {
    if (param.placeholder !== '?') {
      const value = values[param.placeholder] || ''
      const escapedPlaceholder = param.placeholder.replace(
        /[.*+?^${}()|[\]\\]/g,
        '\\$&'
      )
      result = result.replace(
        new RegExp(escapedPlaceholder, 'g'),
        formatValue(value)
      )
    }
  }

  return result
}

/**
 * Resolve an initial value from backend's most_recent_params for a given parameter.
 * Backend stores params as {p1: value, p2: value, ...} (SQLGlot-normalized keys).
 */
export function resolveInitialValue(
  param: Parameter,
  storedParams: Record<string, unknown> | undefined
): string {
  if (!storedParams) return ''
  let backendKey: string
  if (param.type === 'named') {
    backendKey = param.placeholder.replace(/^[:@]/, '')
  } else if (param.placeholder.startsWith('$')) {
    backendKey = param.placeholder.replace('$', 'p')
  } else {
    backendKey = `p${param.index}`
  }
  const value = storedParams[backendKey]
  return value != null ? String(value) : ''
}
