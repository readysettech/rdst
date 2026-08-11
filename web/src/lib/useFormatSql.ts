/**
 * Lazy-load sql-formatter and cache formatted results.
 * Handles named parameter placeholders (:p1, @name) that sql-formatter
 * can't parse by temporarily replacing them before formatting.
 */

import { useEffect, useState } from 'react'

const MAX_CACHE = 200
const formattedSqlCache = new Map<string, string>()

function detectDialect(sql: string): 'mysql' | 'postgresql' {
  return /`[a-zA-Z_]/.test(sql) ? 'mysql' : 'postgresql'
}

function sanitizeParams(sql: string): {
  sanitized: string
  restore: (s: string) => string
} {
  const extracted: string[] = []
  const sanitized = sql.replace(/(?<!:)[:@]([a-zA-Z_][a-zA-Z0-9_]*)/g, (m) => {
    extracted.push(m)
    return `__P${extracted.length - 1}__`
  })
  return {
    sanitized,
    restore: (s: string) =>
      s.replace(/__P(\d+)__/g, (_, i) => extracted[Number(i)]),
  }
}

// sql-formatter's expressionWidth only affects parenthesized expressions —
// column/item lists always break one per line. Re-join indented continuation
// lines while they fit, so clauses stay compact without hiding AND/OR branches
// that make a query easy to scan.
const DEFAULT_LINE_WIDTH = 120

function compactLines(
  text: string,
  lineWidth: number,
  multilineProjection: boolean
): string {
  const lines = text.split('\n')
  const out: string[] = []
  // A string literal spanning lines must keep its exact newlines.
  let insideString = false
  let insideProjection = false
  for (const line of lines) {
    const trimmed = line.trim()
    const prev = out[out.length - 1]
    if (!/^\s/.test(line)) {
      insideProjection =
        multilineProjection && /^(SELECT|RETURNING)\b/i.test(trimmed)
    }
    const canJoin =
      !insideString &&
      !insideProjection &&
      out.length > 0 &&
      trimmed.length > 0 &&
      /^\s/.test(line) &&
      !!prev?.trim() &&
      !prev.includes('--') &&
      !/^(AND|OR|XOR|JOIN)\b/i.test(trimmed) &&
      `${prev} ${trimmed}`.length <= lineWidth
    if (canJoin) out[out.length - 1] = `${prev} ${trimmed}`
    else out.push(line)
    if ((line.match(/'/g) ?? []).length % 2 === 1) insideString = !insideString
  }
  return out.join('\n')
}

interface FormatSqlOptions {
  /** Target width used when compacting formatter continuation lines. */
  lineWidth?: number
  /** Width used by sql-formatter for parenthesized expressions. */
  expressionWidth?: number
  /** Keep SELECT/RETURNING projection items on separate lines. */
  multilineProjection?: boolean
}

export function useFormatSql(
  sql: string | null,
  dialect?: 'postgresql' | 'mysql',
  {
    lineWidth = DEFAULT_LINE_WIDTH,
    expressionWidth = 400,
    multilineProjection = false,
  }: FormatSqlOptions = {}
): string | null {
  const language = dialect ?? (sql ? detectDialect(sql) : 'postgresql')
  const cacheKey = sql
    ? `${language}:${lineWidth}:${expressionWidth}:${multilineProjection}:${sql}`
    : null
  const cached = cacheKey ? formattedSqlCache.get(cacheKey) : undefined
  const [result, setResult] = useState<{
    key: string | null
    value: string | null
  }>(() => ({ key: cacheKey, value: cached ?? null }))

  useEffect(() => {
    if (!sql || !cacheKey) {
      setResult({ key: null, value: null })
      return
    }

    const cachedResult = formattedSqlCache.get(cacheKey)
    if (cachedResult) {
      setResult({ key: cacheKey, value: cachedResult })
      return
    }

    let cancelled = false
    import('sql-formatter').then(({ format }) => {
      if (cancelled) return
      try {
        const { sanitized, restore } = sanitizeParams(sql)
        const result = compactLines(
          restore(
            format(sanitized, {
              language,
              tabWidth: 2,
              keywordCase: 'upper',
              expressionWidth,
            })
          ),
          lineWidth,
          multilineProjection
        )
        if (formattedSqlCache.size >= MAX_CACHE) {
          formattedSqlCache.delete(formattedSqlCache.keys().next().value!)
        }
        formattedSqlCache.set(cacheKey, result)
        setResult({ key: cacheKey, value: result })
      } catch {
        setResult({ key: cacheKey, value: sql })
      }
    })
    return () => {
      cancelled = true
    }
  }, [sql, cacheKey, language, lineWidth, expressionWidth, multilineProjection])

  if (!cacheKey) return null
  if (cached) return cached
  return result.key === cacheKey ? result.value : null
}
