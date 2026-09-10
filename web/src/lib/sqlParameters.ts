export interface Parameter {
  placeholder: string
  index: number
  type: 'positional' | 'named'
}

/** One placeholder token and the span it occupies in the source SQL. */
interface PlaceholderMatch {
  token: string
  start: number
  end: number
}

const DOLLAR_QUOTE_OPEN = /^\$(?:[a-zA-Z_][a-zA-Z0-9_]*)?\$/
const DOLLAR_SLOT = /^\$\d+/
const NAMED_SLOT = /^[:@][a-zA-Z_][a-zA-Z0-9_]*/

/**
 * Every placeholder token in `sql`, in textual order, reading only the parts a
 * database engine would read as SQL: quoted strings, quoted identifiers,
 * dollar-quoted bodies and comments are skipped, so a value's own text can
 * never be mistaken for a slot. `alice@example.com` substituted into a query
 * is `'alice@example.com'` -- inside a literal, and therefore not a parameter.
 */
function scanPlaceholders(sql: string): PlaceholderMatch[] {
  const found: PlaceholderMatch[] = []
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]

    if (ch === "'" || ch === '"' || ch === '`') {
      // A doubled quote inside the run escapes itself rather than closing it.
      for (i++; i < sql.length; i++) {
        if (sql[i] !== ch) continue
        if (sql[i + 1] === ch) i++
        else break
      }
      continue
    }
    if (ch === '-' && sql[i + 1] === '-') {
      const end = sql.indexOf('\n', i)
      i = end < 0 ? sql.length : end
      continue
    }
    if (ch === '/' && sql[i + 1] === '*') {
      // PostgreSQL nests block comments, so track the depth rather than
      // stopping at the first close.
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
    if (ch === '$') {
      const opening = DOLLAR_QUOTE_OPEN.exec(sql.slice(i))
      if (opening) {
        const close = sql.indexOf(opening[0], i + opening[0].length)
        i = close < 0 ? sql.length : close + opening[0].length - 1
        continue
      }
      const slot = DOLLAR_SLOT.exec(sql.slice(i))
      if (slot) {
        found.push({ token: slot[0], start: i, end: i + slot[0].length })
        i += slot[0].length - 1
      }
      continue
    }
    if (ch === '?') {
      found.push({ token: '?', start: i, end: i + 1 })
      continue
    }
    if (
      (ch === ':' || ch === '@') &&
      sql[i - 1] !== ':' &&
      sql[i + 1] !== ':'
    ) {
      const slot = NAMED_SLOT.exec(sql.slice(i))
      if (slot) {
        found.push({ token: slot[0], start: i, end: i + slot[0].length })
        i += slot[0].length - 1
      }
    }
  }
  return found
}

/**
 * Detect parameters in a SQL query.
 * Supports: $1, $2 (PostgreSQL), ? (MySQL), :name, @name (named)
 */
export function detectParameters(sql: string): Parameter[] {
  const params: Parameter[] = []
  const seen = new Set<string>()
  const matches = scanPlaceholders(sql)

  // PostgreSQL positional: $1, $2, etc.
  for (const match of matches) {
    if (!match.token.startsWith('$') || seen.has(match.token)) continue
    seen.add(match.token)
    params.push({
      placeholder: match.token,
      index: Number.parseInt(match.token.slice(1), 10),
      type: 'positional',
    })
  }

  // MySQL positional: ? (numbered by occurrence)
  let questionIndex = 1
  for (const match of matches) {
    if (match.token !== '?') continue
    params.push({ placeholder: '?', index: questionIndex, type: 'positional' })
    questionIndex++
  }

  // Named parameters: :name or @name (but not ::type casts)
  for (const match of matches) {
    if (!/^[:@]/.test(match.token) || seen.has(match.token)) continue
    seen.add(match.token)
    params.push({
      placeholder: match.token,
      index: params.length + 1,
      type: 'named',
    })
  }

  return params.sort((a, b) => a.index - b.index)
}

export function hasParameters(sql: string): boolean {
  return detectParameters(sql).length > 0
}

function quoteAsText(value: string): string {
  return `'${value.replace(/'/g, "''")}'`
}

/**
 * Render one entered value as a SQL literal. A bare number, NULL, TRUE and
 * FALSE are used as typed; everything else becomes a quoted string. Digits
 * with a leading zero (`00042`, a zip code or an account number) are text: as
 * a number literal they would silently lose the zeros. Double quotes around a
 * value are the user quoting a string, not naming a column, so they become a
 * string literal rather than a PostgreSQL identifier.
 */
export function formatValue(value: string): string {
  const trimmed = value.trim()

  if (/^-?(0|[1-9]\d*)(\.\d+)?$/.test(trimmed)) {
    return trimmed
  }

  if (['NULL', 'TRUE', 'FALSE'].includes(trimmed.toUpperCase())) {
    return trimmed.toUpperCase()
  }

  if (trimmed.length >= 2) {
    if (trimmed.startsWith("'") && trimmed.endsWith("'")) return trimmed
    if (trimmed.startsWith('"') && trimmed.endsWith('"'))
      return quoteAsText(trimmed.slice(1, -1))
  }

  return quoteAsText(trimmed)
}

/**
 * Substitute detected placeholders with their values in a single left-to-right
 * pass over the same literal-aware scan `detectParameters` uses, so a
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
  let cursor = 0

  for (const match of scanPlaceholders(sql)) {
    result += sql.slice(cursor, match.start)
    cursor = match.end
    if (match.token === '?') {
      questionIndex++
      result += byPlaceholder.has('?')
        ? formatValue(values[`?${questionIndex}`] || '')
        : match.token
      continue
    }
    result += byPlaceholder.has(match.token)
      ? formatValue(values[match.token] || '')
      : match.token
  }

  return result + sql.slice(cursor)
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
 * after substitution, ignoring any that falls inside a string literal (a
 * substituted value's own text may legitimately contain "$1"). A non-empty
 * result means at least one slot failed to resolve and the SQL is not safe
 * to run.
 */
export function findResidualPlaceholders(sql: string): string[] {
  return Array.from(new Set(scanPlaceholders(sql).map((m) => m.token)))
}

export function hasResidualPlaceholders(sql: string): boolean {
  return findResidualPlaceholders(sql).length > 0
}
