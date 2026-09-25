import { resolveRewriteTesting } from '../../../components/analysis/AnalysisSections'
import { isPlanOnly } from '../../../lib/analysisPayload'
import type {
  CompleteEvent,
  IndexPlannerResult,
  IndexRecommendation,
  IndexTesting,
  OptimizationOpportunity,
  ReadysetCacheability,
  RewriteSuggestion,
  RewriteTesting,
  TestedRewrite,
} from '../../../lib/api'
import { findPlannerResult } from '../../../lib/indexTesting'

export type ResultTone = 'positive' | 'informative' | 'warning' | 'negative'

export interface ResultPerformance {
  score?: number
  rating?: string
  concerns: string[]
}

export interface ReadysetVerdict {
  verified: boolean
  estimated: boolean
  cacheable: boolean
  alreadyCached: boolean
  tone: ResultTone
  title: string
  tag: string
  body?: string
  technicalDetail?: string
  issues: string[]
  warnings: string[]
}

export type ResultNextStep =
  | {
      kind: 'rewrite'
      evidence: 'Tested' | 'Suggested'
      tone: 'positive' | 'informative'
      title: string
      body: string
      supportingText: string
      sql: string
    }
  | {
      kind: 'opportunity'
      evidence: 'Suggested'
      tone: 'informative'
      title: string
      body: string
      supportingText: string
      sql?: string
    }
  | {
      kind: 'index'
      evidence: 'Suggested' | 'Planner-verified'
      tone: 'informative'
      title: string
      body: string
      supportingText: string
      sql: string
      caveats: string[]
      plannerVerdict?: IndexPlannerResult
    }
  | {
      kind: 'readyset'
      evidence: 'Verified'
      tone: 'positive'
      title: string
      body: string
      supportingText: string
    }
  | {
      kind: 'none'
      evidence: 'Measured' | 'Estimated'
      tone: 'informative'
      title: string
      body: string
      supportingText?: undefined
    }

export interface ResultsViewModel {
  performance?: ResultPerformance
  planOnly: boolean
  explainResults: CompleteEvent['explain_results']
  testing?: RewriteTesting
  cacheability?: ReadysetCacheability
  readysetVerdict?: ReadysetVerdict
  indexRecommendations: IndexRecommendation[]
  indexTesting?: IndexTesting
  additionalRecommendations: NonNullable<
    CompleteEvent['llm_analysis']
  >['optimization_opportunities']
  hasModelAnalysis: boolean
  nextStep: ResultNextStep
}

const CHECK_ERROR =
  /db error|connection|timeout|timed out|unreachable|refused|startup|unavailable|pending|failed|\berror\b/i

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function rowCountMatches(
  baseline: number | null | undefined,
  candidate: number | null | undefined
) {
  if (typeof baseline !== 'number' || typeof candidate !== 'number') return true
  return baseline === candidate
}

function findTestedImprovement(
  testing: RewriteTesting | undefined
): TestedRewrite | undefined {
  if (!testing?.tested) return undefined

  const candidates = [
    testing.best_rewrite,
    ...(testing.rewrite_results ?? []),
  ].filter((candidate): candidate is TestedRewrite => Boolean(candidate))

  return candidates.find((candidate) => {
    const improvement = candidate.improvement?.overall?.improvement_pct
    return (
      candidate.success &&
      typeof improvement === 'number' &&
      improvement > 0 &&
      rowCountMatches(
        testing.original_performance?.rows_returned,
        candidate.performance?.rows_returned
      )
    )
  })
}

export function getResultTone(rating?: string): ResultTone {
  switch (rating?.toLowerCase()) {
    case 'excellent':
      return 'positive'
    case 'good':
      return 'informative'
    case 'fair':
      return 'warning'
    case 'poor':
    case 'critical':
      return 'negative'
    default:
      return 'informative'
  }
}

export function getScoreTone(score?: number): ResultTone {
  if (typeof score !== 'number') return 'informative'
  if (score >= 80) return 'positive'
  if (score >= 70) return 'informative'
  if (score >= 50) return 'warning'
  return 'negative'
}

/** A stored analysis reduced to the rating and score a card can show. */
export interface AnalysisAssessment {
  overall_rating: string
  efficiency_score: number | null
}

/**
 * Compact "Good · 82/100" outcome with the tone that matches it. Null when the
 * stored record carries neither a rating nor a score, so a card shows nothing
 * rather than an empty placeholder.
 */
export function analysisOutcome(
  assessment: AnalysisAssessment | null | undefined
): { label: string; tone: ResultTone } | null {
  if (!assessment) return null
  const rating = assessment.overall_rating.trim()
  const ratingLabel = rating
    ? rating[0].toUpperCase() + rating.slice(1).toLowerCase()
    : ''
  const score =
    typeof assessment.efficiency_score === 'number' &&
    assessment.efficiency_score > 0
      ? Math.round(assessment.efficiency_score)
      : null
  const label =
    ratingLabel && score !== null
      ? `${ratingLabel} · ${score}/100`
      : ratingLabel || (score !== null ? `${score}/100` : '')
  if (!label) return null
  return {
    label,
    tone: score !== null ? getScoreTone(score) : getResultTone(rating),
  }
}

export function getRatingTitle(rating?: string) {
  switch (rating?.toLowerCase()) {
    case 'excellent':
      return 'Excellent performance'
    case 'good':
      return 'Good performance'
    case 'fair':
      return 'Fair performance'
    case 'poor':
    case 'critical':
      return 'Needs attention'
    default:
      return 'Performance assessed'
  }
}

export function getRatingDescription(rating?: string) {
  switch (rating?.toLowerCase()) {
    case 'excellent':
      return 'The measured plan is efficient and needs little attention.'
    case 'good':
      return 'The query performs well, with some room to reduce database work.'
    case 'fair':
      return 'The query works, but the plan shows meaningful room for improvement.'
    case 'poor':
    case 'critical':
      return 'The measured plan shows a bottleneck worth addressing before it grows.'
    default:
      return 'Measured execution data is available for review.'
  }
}

export function getReadysetVerdict(
  cacheability: ReadysetCacheability
): ReadysetVerdict {
  const alreadyCached = /already cached/i.test(cacheability.explanation ?? '')
  const estimated =
    cacheability.checked && cacheability.method === 'static_analysis'
  const unreliable =
    cacheability.cacheable === false &&
    (cacheability.confidence === 'low' ||
      cacheability.confidence === 'unknown' ||
      CHECK_ERROR.test(cacheability.explanation ?? '') ||
      (cacheability.issues ?? []).some((issue) => CHECK_ERROR.test(issue)))

  const verified =
    cacheability.checked &&
    cacheability.method !== 'static_analysis' &&
    cacheability.method !== 'readyset_unavailable' &&
    !unreliable
  const cacheable = verified && cacheability.cacheable === true
  const rawDetail = cacheability.detail ?? undefined
  const explanationLooksRaw =
    !verified &&
    Boolean(cacheability.explanation) &&
    CHECK_ERROR.test(cacheability.explanation ?? '')

  const body = alreadyCached
    ? 'This query is already cached and ready to manage in Queries.'
    : explanationLooksRaw && rawDetail === undefined
      ? "Readyset couldn't complete the cacheability check, so this query hasn't been verified."
      : (cacheability.explanation ?? undefined)
  const technicalDetail =
    rawDetail ??
    (explanationLooksRaw || alreadyCached
      ? (cacheability.explanation ?? undefined)
      : undefined)

  if (estimated) {
    const potentialBlockers = cacheability.cacheable !== true

    return {
      verified: false,
      estimated: true,
      cacheable: false,
      alreadyCached: false,
      tone: 'informative',
      title: potentialBlockers
        ? 'Potential Readyset blockers'
        : 'No obvious Readyset blockers',
      tag: 'Static check',
      body: potentialBlockers
        ? 'Static SQL screening found potential blockers. Verify this query with Readyset before making a compatibility decision.'
        : 'Static SQL screening found no obvious blockers. Verify this query with Readyset before treating it as cacheable.',
      technicalDetail: rawDetail ?? undefined,
      issues: cacheability.issues ?? [],
      warnings: cacheability.warnings ?? [],
    }
  }

  if (!verified) {
    return {
      verified: false,
      estimated: false,
      cacheable: false,
      alreadyCached: false,
      tone: 'warning',
      title: 'Cacheability not verified',
      tag: 'Not verified',
      body:
        body ??
        "Readyset couldn't complete the compatibility check for this query.",
      technicalDetail,
      issues: cacheability.issues ?? [],
      warnings: cacheability.warnings ?? [],
    }
  }

  if (cacheable) {
    return {
      verified: true,
      estimated: false,
      cacheable: true,
      alreadyCached,
      tone: 'positive',
      title: alreadyCached
        ? 'Cached by Readyset'
        : 'Readyset can cache this query',
      tag: alreadyCached ? 'Cached' : 'Cacheable',
      body:
        body ??
        'Readyset verified this query and found no compatibility blockers.',
      technicalDetail,
      issues: cacheability.issues ?? [],
      warnings: cacheability.warnings ?? [],
    }
  }

  return {
    verified: true,
    estimated: false,
    cacheable: false,
    alreadyCached: false,
    tone: 'negative',
    title: "Readyset can't cache this query yet",
    tag: 'Not cacheable',
    body:
      body ??
      cacheability.issues?.[0] ??
      'The query needs a compatibility change before Readyset can cache it.',
    technicalDetail,
    issues: cacheability.issues ?? [],
    warnings: cacheability.warnings ?? [],
  }
}

const OPPORTUNITY_TITLES: Record<string, string> = {
  configuration: 'Change a database setting',
  caching: 'Precompute or cache the result',
  schema: 'Change the schema',
  query_structure: 'Restructure the query',
}

function getNextStep({
  planOnly,
  testing,
  indexes,
  indexTesting,
  readyset,
  rewrites,
  opportunities,
}: {
  planOnly: boolean
  testing?: RewriteTesting
  indexes: IndexRecommendation[]
  indexTesting?: IndexTesting
  readyset?: ReadysetVerdict
  rewrites: RewriteSuggestion[]
  opportunities: OptimizationOpportunity[]
}): ResultNextStep {
  const rewrite = findTestedImprovement(testing)
  if (rewrite) {
    const improvement = rewrite.improvement.overall.improvement_pct
    const originalTime = testing?.original_performance?.execution_time_ms
    const rewriteTime = rewrite.performance.execution_time_ms
    const measuredComparison =
      typeof originalTime === 'number'
        ? `${originalTime.toFixed(2)} ms → ${rewriteTime.toFixed(2)} ms in this test`
        : `${improvement.toFixed(1)}% faster in this test`

    return {
      kind: 'rewrite',
      evidence: 'Tested',
      tone: 'positive',
      title: 'Use the fastest tested rewrite',
      body:
        rewrite.suggestion_metadata?.explanation ||
        'This rewrite produced the best measured result.',
      supportingText: `${improvement.toFixed(1)}% faster · ${measuredComparison}`,
      sql: rewrite.sql,
    }
  }

  // An index the planner actually picks up (hypopg) beats a guessed impact.
  const verified = indexes
    .map((candidate) => ({
      candidate,
      verdict: findPlannerResult(indexTesting, candidate),
    }))
    .filter((entry) => entry.verdict?.planner_used_index)
    .sort(
      (a, b) =>
        (b.verdict?.cost_reduction_pct ?? 0) -
        (a.verdict?.cost_reduction_pct ?? 0)
    )[0]
  const index =
    verified?.candidate ??
    indexes.find((candidate) => candidate.estimated_impact === 'high') ??
    indexes[0]
  if (index) {
    const plannerVerdict =
      verified?.verdict ?? findPlannerResult(indexTesting, index)
    return {
      kind: 'index',
      evidence: plannerVerdict?.planner_used_index
        ? 'Planner-verified'
        : 'Suggested',
      tone: 'informative',
      title: `Add an index on ${index.table}`,
      body: index.rationale,
      supportingText: `${index.estimated_impact.charAt(0).toUpperCase()}${index.estimated_impact.slice(1)} expected impact`,
      sql: index.sql,
      caveats: index.caveats ?? [],
      plannerVerdict,
    }
  }

  if (readyset?.alreadyCached) {
    return {
      kind: 'none',
      evidence: planOnly ? 'Estimated' : 'Measured',
      tone: 'informative',
      title: 'No cache change needed',
      body: 'This query is already cached. Review its status in Queries when you need to manage it.',
    }
  }

  if (readyset?.cacheable) {
    return {
      kind: 'readyset',
      evidence: 'Verified',
      tone: 'positive',
      title: 'Set up caching for this query',
      body: 'Readyset verified that it can cache this query.',
      supportingText: 'Continue in Queries with this query selected',
    }
  }

  // A rewrite the analysis suggested but nothing tested. Once tested without a gain,
  // the measurement wins and it isn't recommended.
  const suggested = testing?.tested
    ? undefined
    : rewrites.find((rewrite) => rewrite.rewritten_sql?.trim())
  if (suggested) {
    return {
      kind: 'rewrite',
      evidence: 'Suggested',
      tone: 'informative',
      title: 'Try the suggested rewrite',
      body:
        suggested.explanation ||
        "The analysis suggests this rewrite. It wasn't tested against the database.",
      supportingText: suggested.expected_improvement
        ? `Estimated: ${suggested.expected_improvement}`
        : 'Not tested yet',
      sql: suggested.rewritten_sql.trim(),
    }
  }

  const opportunity = opportunities.find(
    (candidate) => candidate.priority === 'high'
  )
  if (opportunity) {
    const example =
      typeof opportunity.example === 'string' ? opportunity.example.trim() : ''
    return {
      kind: 'opportunity',
      evidence: 'Suggested',
      tone: 'informative',
      title:
        OPPORTUNITY_TITLES[opportunity.type?.toLowerCase()] ??
        'Review the top recommendation',
      body: opportunity.description,
      supportingText: 'High priority · review before applying',
      ...(example ? { sql: example } : {}),
    }
  }

  return {
    kind: 'none',
    evidence: planOnly ? 'Estimated' : 'Measured',
    tone: 'informative',
    title: 'No clear change recommended',
    body: planOnly
      ? "The analysis didn't find a high-confidence next step. Review the estimated plan before making a change."
      : "The analysis didn't find a high-confidence next step. Review the measured plan before making a change.",
  }
}

export function selectResultsViewModel(
  results: CompleteEvent,
  streamedTesting?: RewriteTesting,
  streamedCacheability?: ReadysetCacheability
): ResultsViewModel {
  const { llm_analysis: modelAnalysis, formatted } = results
  const performanceCandidate =
    modelAnalysis?.performance_assessment ?? formatted?.analysis_summary
  const score = finiteNumber(performanceCandidate?.efficiency_score)
  const rating = performanceCandidate?.overall_rating
  const performanceIsUnknown =
    (!rating || rating.toLowerCase() === 'unknown') &&
    (score === undefined || score <= 0)
  const performance = performanceIsUnknown
    ? undefined
    : {
        score,
        rating,
        concerns: performanceCandidate?.primary_concerns ?? [],
      }

  const testing = resolveRewriteTesting(
    streamedTesting,
    results.rewrite_testing ?? undefined,
    formatted?.rewrite_testing ?? undefined
  )
  const cacheability =
    streamedCacheability ??
    results.readyset_cacheability ??
    formatted?.readyset_cacheability ??
    undefined
  const readysetVerdict = cacheability
    ? getReadysetVerdict(cacheability)
    : undefined
  const indexRecommendations = modelAnalysis?.index_recommendations ?? []
  const indexTesting =
    results.index_testing ?? formatted?.index_testing ?? undefined
  const hasModelAnalysis =
    modelAnalysis?.success !== false &&
    Boolean(
      performance ||
        modelAnalysis?.rewrite_suggestions?.length ||
        indexRecommendations.length
    )

  const planOnly = isPlanOnly(results.explain_results)
  const nextStep = getNextStep({
    planOnly,
    testing: planOnly ? undefined : testing,
    indexes: indexRecommendations,
    indexTesting,
    readyset: readysetVerdict,
    rewrites: modelAnalysis?.rewrite_suggestions ?? [],
    opportunities: modelAnalysis?.optimization_opportunities ?? [],
  })
  return {
    performance,
    planOnly,
    explainResults: results.explain_results,
    testing: planOnly ? undefined : testing,
    cacheability,
    readysetVerdict,
    indexRecommendations,
    indexTesting,
    additionalRecommendations: modelAnalysis?.optimization_opportunities ?? [],
    hasModelAnalysis,
    nextStep,
  }
}
