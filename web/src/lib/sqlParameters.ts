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

/**
 * Substitute detected placeholders with their values in a single left-to-right
 * pass, mirroring the quote-aware scan in `findResidualPlaceholders` so a
 * placeholder-shaped substring inside a string literal is never touched. `$N`
 * and `:name`/`@name` are matched by their longest run of digits/identifier
 * characters, so `$1` never fires on the first two characters of `$10` (and
 * likewise for `:p1` vs `:p10`) -- a naive per-placeholder regex replace
 * would mangle the longer placeholder.
 */
export function substituteParameters(
  sql: string,
  params: Parameter[],
  values: Record<string, string>
): string {
  const byPlaceholder = new Map(params.map((p) => [p.placeholder, p]))
  let result = ''
  let questionIndex = 0
  let inString = false

  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]

    if (ch === "'") {
      if (inString && sql[i + 1] === "'") {
        result += "''"
        i++
        continue
      }
      inString = !inString
      result += ch
      continue
    }
    if (inString) {
      result += ch
      continue
    }

    if (ch === '?' && byPlaceholder.has('?')) {
      questionIndex++
      result += formatValue(values[`?${questionIndex}`] || '')
      continue
    }

    if (ch === '$' && /\d/.test(sql[i + 1] ?? '')) {
      const match = sql.slice(i).match(/^\$\d+/)
      if (match) {
        const param = byPlaceholder.get(match[0])
        result += param ? formatValue(values[match[0]] || '') : match[0]
        i += match[0].length - 1
        continue
      }
    }

    if (
      (ch === ':' || ch === '@') &&
      sql[i - 1] !== ':' &&
      sql[i + 1] !== ':'
    ) {
      const match = sql.slice(i).match(/^[:@][a-zA-Z_][a-zA-Z0-9_]*/)
      if (match) {
        const param = byPlaceholder.get(match[0])
        result += param ? formatValue(values[match[0]] || '') : match[0]
        i += match[0].length - 1
        continue
      }
    }

    result += ch
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

/**
 * Substitute captured parameter values into a query, best-effort: every
 * placeholder with a stored value is filled; any placeholder without one is
 * left as-is so a downstream parameter dialog can still collect it. Returns the
 * SQL unchanged when it has no parameters. Used when handing a saved query to
 * Analyze so the values that ran it are not lost.
 */
export function fillCapturedParams(
  sql: string,
  storedParams: Record<string, unknown> | undefined
): string {
  const params = detectParameters(sql)
  if (params.length === 0) return sql
  const values: Record<string, string> = {}
  const toFill = params.filter((p) => {
    const value = resolveInitialValue(p, storedParams)
    if (value === '') return false
    values[p.placeholder] = value
    return true
  })
  if (toFill.length === 0) return sql
  return substituteParameters(sql, toFill, values)
}

/**
 * The backend's SQLGlot-normalized key for a parameter, the inverse of
 * `resolveInitialValue`'s key derivation: named parameters use their bare
 * name, `?` and `$N` placeholders normalize to `pN`.
 */
export function toBackendParamKey(param: Parameter): string {
  if (param.type === 'named') return param.placeholder.replace(/^[:@]/, '')
  if (param.placeholder.startsWith('$'))
    return param.placeholder.replace('$', 'p')
  return `p${param.index}`
}

/**
 * Convert placeholder-keyed dialog values (e.g. `{"$1": "42"}`) into the
 * backend's normalized keys (e.g. `{"p1": "42"}`) for persisting to the query
 * registry. Blank values are dropped rather than persisted as empty strings.
 */
export function toBackendParams(
  parameters: Parameter[],
  values: Record<string, string>
): Record<string, string> {
  const result: Record<string, string> = {}
  for (const parameter of parameters) {
    const key =
      parameter.placeholder === '?'
        ? `?${parameter.index}`
        : parameter.placeholder
    const value = values[key]
    if (value?.trim()) result[toBackendParamKey(parameter)] = value
  }
  return result
}

/**
 * Placeholder syntax ($N, :name, @name, bare ?) still present in a query
 * after substitution, ignoring any that fall inside a single-quoted string
 * literal (a substituted value's own text may legitimately contain "$1").
 * A non-empty result means at least one slot failed to resolve and the SQL
 * is not safe to run.
 */
export function findResidualPlaceholders(sql: string): string[] {
  const found = new Set<string>()
  let inString = false
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]
    if (ch === "'") {
      if (inString && sql[i + 1] === "'") {
        i++
        continue
      }
      inString = !inString
      continue
    }
    if (inString) continue
    if (ch === '$' && /\d/.test(sql[i + 1] ?? '')) {
      const match = sql.slice(i).match(/^\$\d+/)
      if (match) {
        found.add(match[0])
        i += match[0].length - 1
      }
      continue
    }
    if (ch === '?') {
      found.add('?')
      continue
    }
    if (
      (ch === ':' || ch === '@') &&
      sql[i - 1] !== ':' &&
      sql[i + 1] !== ':'
    ) {
      const match = sql.slice(i).match(/^[:@][a-zA-Z_][a-zA-Z0-9_]*/)
      if (match) {
        found.add(match[0])
        i += match[0].length - 1
      }
    }
  }
  return Array.from(found)
}

export function hasResidualPlaceholders(sql: string): boolean {
  return findResidualPlaceholders(sql).length > 0
}
