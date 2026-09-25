// The query classification (the Jev quick assessment) for a monitored query: the monitor row's `assessment` first
// (the backend batches it for the page), then the Query Library row as a fallback.
import type { JevAssessmentSummary } from '@/lib/api'
import type { MonitorQuery } from '@/lib/monitor/types'

export interface JevRead {
  assessment: JevAssessmentSummary | null
  /** What an incomplete assessment waits for, when the backend says. */
  waitingReason: string | null
}

export function jevRead(
  query: MonitorQuery,
  fallback?: JevAssessmentSummary | null
): JevRead {
  const served = query.assessment
  if (served)
    return {
      assessment: {
        status: served.status,
        band: served.band ?? undefined,
        priority_score: served.priority_score,
        confidence: served.confidence,
        findings: served.findings.map((finding) => ({
          ...finding,
          confidence: finding.confidence ?? undefined,
        })) as JevAssessmentSummary['findings'],
        model: served.model ?? undefined,
        rubric_version: served.rubric_version ?? undefined,
        assessed_at: served.assessed_at ?? undefined,
        error_code: served.error_code ?? undefined,
        schema_coverage: served.schema_coverage ?? undefined,
        schema_collected_at: served.schema_collected_at ?? undefined,
      },
      waitingReason:
        served.status === 'complete' ? null : (served.waiting_reason ?? null),
    }
  return { assessment: fallback ?? null, waitingReason: null }
}

/** 'Fix suggested' only when the stored analysis recommends a change with SQL. */
export function fixReady(query: MonitorQuery): boolean {
  return query.last_analysis?.summary?.fix_ready === true
}

/** The classification findings, as the second group of the Status filter. */
export const CLASSIFICATION_FILTERS = [
  'index_coverage',
  'join_growth',
  'broad_work',
  'repeated_work',
  'access_expression_risk',
  'none',
  'unassessed',
] as const
export type ClassificationFilter = (typeof CLASSIFICATION_FILTERS)[number]

export const CLASSIFICATION_LABELS: Record<ClassificationFilter, string> = {
  index_coverage: 'Missing index',
  join_growth: 'Join growth',
  broad_work: 'Broad scan',
  repeated_work: 'Repeated work',
  access_expression_risk: 'Index bypassed',
  none: 'No finding',
  unassessed: 'Not classified yet',
}

export function isClassificationFilter(
  value: unknown
): value is ClassificationFilter {
  return CLASSIFICATION_FILTERS.includes(value as ClassificationFilter)
}

export function matchesClassification(
  assessment: JevAssessmentSummary | null | undefined,
  filter: ClassificationFilter
): boolean {
  const complete = assessment?.status === 'complete'
  if (filter === 'unassessed') return !complete
  if (!complete) return false
  const findings = assessment?.findings ?? []
  if (filter === 'none') return findings.length === 0
  return findings.some((finding) => finding.id === filter)
}

/**
 * What the classification read, in plain words: "Based on 3 tables, 2 indexes and 1
 * foreign key". The backend stores "relevant tables (3 tables, 2 indexes)" or "partial
 * (...)" when the schema was cut short. Null when there's nothing worth saying.
 */
export function schemaBasis(
  coverage: string | null | undefined
): string | null {
  const match = coverage?.trim().match(/^([^()]*)\((.*)\)\s*$/)
  const counts = match?.[2]
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
  if (!match || !counts?.length) return null
  const list =
    counts.length === 1
      ? counts[0]
      : `${counts.slice(0, -1).join(', ')} and ${counts[counts.length - 1]}`
  return match[1].trim().toLowerCase() === 'partial'
    ? `Based on part of the schema: ${list}`
    : `Based on ${list}`
}
