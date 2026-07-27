/**
 * Derivations over a saved `AuditReport`: report identity/tags, the unified
 * query-row merge, and index-recommendation targeting.
 */

import type {
  AuditReport,
  ReadysetComparison,
  ReadysetComparisonQuery,
  WorkloadAnalysis,
  WorkloadIndexRecommendation,
  WorkloadQuery,
} from '../types/audit'
import type { QueryReportTab } from './auditReportLocation'

export const INTERNAL_REPORT_TAG = /^(aws-account|role):/i

export function visibleReportTags(tags: string[] | null | undefined): string[] {
  return (tags ?? []).filter((tag) => !INTERNAL_REPORT_TAG.test(tag))
}

export function reportRole(
  tags: string[] | null | undefined
): string | undefined {
  const roleTag = (tags ?? []).find((tag) => /^role:/i.test(tag))
  const role = roleTag
    ?.slice(roleTag.indexOf(':') + 1)
    .trim()
    .toLowerCase()
  return role === 'writer' || role === 'reader' ? role : undefined
}

export function reportAnchorPrefix(report: AuditReport): string {
  const identity = report.target_name || report.host || 'database-target'
  const slug = identity
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
  return `report-${slug || 'database-target'}`
}

// LLM analysis arrays are declared string[] but the model sometimes returns
// structured objects (e.g. {rank, category, description, impact,
// recommendation}). Rendering an object as a React child throws (React #31),
// so flatten anything non-string into readable text.
export function analysisItemText(item: unknown): string {
  if (typeof item === 'string') return item
  if (item == null) return ''
  if (typeof item === 'object') {
    const record = item as Record<string, unknown>
    const main = [record.description, record.recommendation]
      .filter((part): part is string => typeof part === 'string' && part !== '')
      .join(' — ')
    if (main) {
      const category =
        typeof record.category === 'string' ? `${record.category}: ` : ''
      return `${category}${main}`
    }
    return Object.values(record)
      .filter((part) => typeof part === 'string')
      .join(' — ')
  }
  return String(item)
}

export function optimizationPriorityParts(item: unknown): {
  text: string
  type?: string
  effort?: string
  impact?: string
} {
  if (typeof item === 'string') {
    const match = item.match(
      /^(.*?)\s*[-–—]\s*([a-z][\w-]*)\s*[-–—]\s*(low|medium|high)\s*[-–—]\s*(low|medium|high)\s*$/i
    )
    if (match) {
      return {
        text: match[1].trim(),
        type: match[2].toLowerCase(),
        effort: match[3].toLowerCase(),
        impact: match[4].toLowerCase(),
      }
    }
    return { text: item }
  }
  if (item && typeof item === 'object') {
    const record = item as Record<string, unknown>
    const text = [
      record.description,
      record.action,
      record.recommendation,
      record.details,
    ]
      .filter((part): part is string => typeof part === 'string' && !!part)
      .filter((part, index, all) => all.indexOf(part) === index)
      .join(' — ')
    return {
      text: text || analysisItemText(item),
      type:
        typeof record.type === 'string'
          ? record.type
          : typeof record.category === 'string'
            ? record.category
            : undefined,
      effort: typeof record.effort === 'string' ? record.effort : undefined,
      impact: typeof record.impact === 'string' ? record.impact : undefined,
    }
  }
  return { text: analysisItemText(item) }
}

export function linkedIndexRecommendations(
  analysis: WorkloadAnalysis | null | undefined
): WorkloadIndexRecommendation[] {
  const recommendations = analysis?.index_recommendations ?? []
  const bottlenecks = analysis?.top_bottlenecks ?? []
  return recommendations.map((recommendation) => {
    if (
      recommendation.affected_queries?.length ||
      recommendation.query_hashes?.length ||
      recommendation.queries?.length
    ) {
      return recommendation
    }
    const recTerms = [recommendation.table, ...(recommendation.columns ?? [])]
      .filter((term): term is string => !!term)
      .map((term) => term.toLowerCase())
    const related = bottlenecks.filter((item) => {
      if (!item || typeof item !== 'object') return false
      const record = item as Record<string, unknown>
      if (!Array.isArray(record.affected_queries)) return false
      const category = String(record.category ?? '').toLowerCase()
      const text = [record.description, record.recommendation, record.details]
        .filter((part): part is string => typeof part === 'string')
        .join(' ')
        .toLowerCase()
      return (
        recTerms.some((term) => text.includes(term)) ||
        (recommendations.length === 1 &&
          ['missing_index', 'full_scan', 'index'].includes(category))
      )
    })
    const affectedQueries = related.flatMap((item) => {
      const values = (item as Record<string, unknown>).affected_queries
      return Array.isArray(values)
        ? values.filter((value): value is string => typeof value === 'string')
        : []
    })
    return affectedQueries.length > 0
      ? { ...recommendation, affected_queries: [...new Set(affectedQueries)] }
      : recommendation
  })
}

export type UnifiedQueryRow = WorkloadQuery & {
  benchmark?: ReadysetComparisonQuery
}

export function queryKey(
  query: WorkloadQuery | ReadysetComparisonQuery
): string {
  const hash = query.query_hash?.trim().toLowerCase()
  if (hash) return `hash:${hash}`
  const sql = query.query_text?.trim().toLowerCase()
  return sql ? `sql:${sql}` : ''
}

export function hashesMatch(left: string, right: string): boolean {
  return (
    !!left &&
    !!right &&
    (left === right || left.startsWith(right) || right.startsWith(left))
  )
}

export function mergeQueryRows({
  primary,
  supplemental = [],
  comparison,
  includeUnmatchedBenchmarks = true,
}: {
  primary: WorkloadQuery[]
  supplemental?: WorkloadQuery[]
  comparison?: ReadysetComparison | null
  includeUnmatchedBenchmarks?: boolean
}): UnifiedQueryRow[] {
  const rows: UnifiedQueryRow[] = []

  const findRow = (query: WorkloadQuery | ReadysetComparisonQuery) => {
    const key = queryKey(query)
    if (key) {
      const exact = rows.find((row) => queryKey(row) === key)
      if (exact) return exact
    }
    const hash = query.query_hash?.trim().toLowerCase() || ''
    return rows.find((row) =>
      hashesMatch(row.query_hash?.trim().toLowerCase() || '', hash)
    )
  }

  for (const query of primary) rows.push({ ...query })
  for (const query of supplemental) {
    const existing = findRow(query)
    if (existing) {
      for (const [key, value] of Object.entries(query)) {
        const field = key as keyof WorkloadQuery
        if (existing[field] == null && value != null) {
          Object.assign(existing, { [field]: value })
        }
      }
    }
  }

  for (const benchmark of comparison?.queries || []) {
    const existing = findRow(benchmark)
    if (existing) {
      existing.benchmark = benchmark
      if (!existing.query_text && benchmark.query_text) {
        existing.query_text = benchmark.query_text
      }
    } else if (includeUnmatchedBenchmarks) {
      rows.push({
        query_hash: benchmark.query_hash,
        query_text: benchmark.query_text,
        benchmark,
      })
    }
  }

  return rows
}

export function queryRowAnchorId(sectionId: string, hash: string): string {
  const safeHash = hash
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-|-$/g, '')
  return `${sectionId}-query-${safeHash || 'unknown'}`
}

export function indexRecommendationTarget(rec: WorkloadIndexRecommendation): {
  table?: string
  columns: string[]
} {
  let table = rec.table?.trim()
  let columns = (rec.columns || []).filter(Boolean)
  if (rec.expression?.trim()) columns = [...columns, rec.expression.trim()]

  const reason = rec.reason || ''
  if (!table) {
    const qualified = reason.match(
      /\b(?:on|from|table)\s+["`]?([a-z_][\w$]*)["`]?\s*\.\s*["`]?([a-z_][\w$]*)["`]?/i
    )
    if (qualified) {
      table = qualified[1]
      if (columns.length === 0) columns = [qualified[2]]
    }
  }
  if (columns.length === 0) {
    const parenthesized = reason.match(
      /\b(?:index|on)\s+(?:["`]?[a-z_][\w$]*["`]?\s*)?\(([^)]+)\)/i
    )
    if (parenthesized) {
      columns = parenthesized[1]
        .split(',')
        .map((column) => column.trim())
        .filter(Boolean)
    }
  }
  if (columns.length === 0) {
    const namedColumns = reason.match(
      /\b(?:column|columns|field|fields)\s+([a-z_][\w$]*(?:\s*(?:,|and)\s*[a-z_][\w$]*)*)/i
    )
    if (namedColumns) {
      columns = namedColumns[1]
        .split(/\s*(?:,|and)\s*/i)
        .map((column) => column.trim())
        .filter(Boolean)
    }
  }
  if (columns.length === 0) {
    const idPhrase = reason.match(
      /\b(?:on|by)\s+(?:the\s+)?([a-z_][\w$]*(?:\s+id|_id))\b/i
    )
    if (idPhrase) {
      columns = [idPhrase[1].replace(/\s+/g, '_').toLowerCase()]
    }
  }
  return { table, columns }
}

export function queryTabForHash(
  report: AuditReport,
  hash: string
): QueryReportTab {
  const captured = report.workload?.queries || []
  const historical = report.top_queries || []
  const capturedMatch = captured.some((query) =>
    hashesMatch(query.query_hash?.toLowerCase() ?? '', hash.toLowerCase())
  )
  const historicalMatch = historical.some((query) =>
    hashesMatch(query.query_hash?.toLowerCase() ?? '', hash.toLowerCase())
  )
  return capturedMatch || !historicalMatch ? 'captured' : 'historical'
}
