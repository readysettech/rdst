import { collapseWhitespace } from './collapseWhitespace'

type QueryIdentity = {
  tag?: string | null
  sql: string
  original_sql?: string | null
  question?: string | null
  hash: string
}

function normalizeSearch(value: string) {
  return collapseWhitespace(value).trim().toLowerCase()
}

export function deriveQueryName(sql: string): string {
  const normalized = collapseWhitespace(sql).trim()
  if (!normalized) return 'Untitled query'

  const verbMatch = normalized.match(
    /^(select|insert|update|delete|with|create|alter|drop|truncate)\b/i
  )
  const verb = verbMatch ? verbMatch[1].toLowerCase() : ''
  const aggregateMatch = normalized.match(/\b(count|sum|avg|min|max)\s*\(/i)
  const aggregate = aggregateMatch ? aggregateMatch[1].toUpperCase() : ''
  const tableMatch =
    normalized.match(/\bfrom\s+(?:["'`]|\[)?([\w.]+)/i) ||
    normalized.match(/\binto\s+(?:["'`]|\[)?([\w.]+)/i) ||
    normalized.match(/^update\s+(?:["'`]|\[)?([\w.]+)/i)
  const table = tableMatch
    ? (tableMatch[1].split('.').pop() ?? tableMatch[1])
    : ''
  const verbTitle = verb ? verb.charAt(0).toUpperCase() + verb.slice(1) : ''

  if (aggregate && table) return `${aggregate} on ${table}`
  if (verbTitle && table) return `${verbTitle} · ${table}`
  if (table) return table
  if (verbTitle) return verbTitle
  return normalized.length > 40 ? `${normalized.slice(0, 40)}…` : normalized
}

export function queryDisplayName(entry: QueryIdentity): string {
  return entry.tag?.trim() || deriveQueryName(entry.original_sql || entry.sql)
}

export function filterQueriesBySearch<Entry extends QueryIdentity>(
  entries: Entry[],
  rawSearch: string
): Entry[] {
  const search = normalizeSearch(rawSearch)
  if (!search) return entries

  const uniquePrefixMatches =
    search.length >= 4
      ? entries.filter((entry) => entry.hash.toLowerCase().startsWith(search))
          .length
      : 0

  return entries.filter((entry) => {
    const hash = entry.hash.toLowerCase()
    if (hash === search) return true
    if (
      search.length >= 4 &&
      uniquePrefixMatches === 1 &&
      hash.startsWith(search)
    ) {
      return true
    }

    return [
      entry.tag,
      queryDisplayName(entry),
      entry.question,
      entry.sql,
      entry.original_sql,
    ]
      .filter((value): value is string => Boolean(value))
      .some((value) => normalizeSearch(value).includes(search))
  })
}
