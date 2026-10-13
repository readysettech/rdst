// The full analysis page's model: the contract's AnalysisView, built from the monitor
// digest when the query is monitored and from a finished analyze payload otherwise, plus
// the words the hero and the cards print.
import type {
  CompleteEvent,
  IndexRecommendation,
  IndexTesting,
  ReadysetCacheability,
  RewriteTesting,
} from '@/lib/api'
import { findPlannerResult, plannerVerificationOff } from '@/lib/indexTesting'
import type {
  AnalysisView,
  DeepAnalysisDigest,
  RewriteTested,
} from '@/lib/monitor/types'

const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five']

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export const EMPTY_VIEW: AnalysisView = {
  score: null,
  verdict: null,
  fix_detail: null,
  plan_findings: [],
  rewrites: [],
  also: [],
  readyset: null,
  benchmark: null,
  run: null,
  rows_read: null,
  rows_returned: null,
  planner_cost: null,
  calls_today: null,
  params: [],
  param_source: null,
  explain_text: null,
}

const PARAM_SOURCE: Record<string, string> = {
  sampled: 'from sampled values',
  table_sampled: 'from table samples',
  none: 'no values',
  estimated: 'with estimated values (plan only)',
}

/** Where the analysis's parameter values came from, in words; other text passes through. */
export function paramSourceText(
  source: string | null | undefined
): string | null {
  return source ? (PARAM_SOURCE[source] ?? source) : null
}

/** The digest's view fields, with the contract's defaults where a field is missing. */
export function viewFromDigest(digest: DeepAnalysisDigest): AnalysisView {
  return {
    ...EMPTY_VIEW,
    score: digest.score ?? digest.efficiency_score ?? null,
    verdict:
      digest.verdict ??
      (digest.headline
        ? {
            lead: `${digest.headline.replace(/\.$/, '')}.`,
            rest: digest.fix?.why ?? '',
          }
        : null),
    fix_detail:
      digest.fix_detail ??
      (digest.fix?.sql
        ? {
            sql: digest.fix.sql,
            serves: digest.fix.why || null,
            cost_before: null,
            cost_after: null,
            size: null,
            write_cost: null,
          }
        : null),
    plan_findings:
      digest.plan_findings ??
      digest.findings.map((title) => ({ title, detail: null, value: null })),
    rewrites: digest.rewrites ?? [],
    also: digest.also ?? [],
    readyset: digest.readyset ?? null,
    benchmark: digest.benchmark ?? null,
    run: digest.run ?? null,
    rows_read: digest.rows_read ?? digest.plan_summary?.rows_examined ?? null,
    rows_returned:
      digest.rows_returned ?? digest.plan_summary?.rows_returned ?? null,
    planner_cost: digest.planner_cost ?? null,
    calls_today: digest.calls_today ?? null,
    params: digest.params ?? [],
    param_source: paramSourceText(digest.param_source),
    explain_text: digest.explain_text ?? null,
  }
}

function rewriteOutcome(
  before: number | null,
  after: number | null
): RewriteTested['outcome'] {
  if (before == null || after == null) return 'untested'
  const change = (before - after) / Math.max(before, 0.001)
  if (change > 0.05) return 'faster'
  if (change < -0.05) return 'slower'
  return 'no_change'
}

/** "Add an index on orders(customer_id)", as the digest titles an index. */
function indexTitle(index: IndexRecommendation): string {
  const table = index.table.split('.').pop()
  const columns = index.columns.join(', ')
  return table && columns
    ? `Add an index on ${table}(${columns})`
    : 'Add an index'
}

/** The values a stored monitor analysis ran with: a list of placeholders, or a map. */
function recordedParams(value: unknown): AnalysisView['params'] {
  if (Array.isArray(value))
    return value.map((item, index) =>
      item && typeof item === 'object' && 'name' in item && 'value' in item
        ? { name: String(item.name), value: String(item.value) }
        : { name: `$${index + 1}`, value: String(item) }
    )
  if (value && typeof value === 'object')
    return Object.entries(value).map(([name, item]) => ({
      name,
      value: String(item),
    }))
  return []
}

/**
 * A finished analyze payload (a live run or a stored record) as the contract's view.
 * `entered` holds the values typed into the parameter form for this run.
 */
export function viewFromResults(
  results: CompleteEvent,
  rewriteTesting?: RewriteTesting,
  cacheability?: ReadysetCacheability,
  entered?: Record<string, string | number>
): AnalysisView {
  const llm = results.llm_analysis
  const explain = results.explain_results
  const assessment = llm?.performance_assessment
  const indexes = llm?.index_recommendations ?? []
  // An index the planner picks up (hypopg) leads; otherwise the first recommendation.
  const used = indexes
    .map((index) => ({
      index,
      planner: findPlannerResult(results.index_testing, index),
    }))
    .filter((entry) => entry.planner?.planner_used_index)
    .sort(
      (a, b) =>
        (b.planner?.cost_reduction_pct ?? 0) -
        (a.planner?.cost_reduction_pct ?? 0)
    )[0]
  const first = used?.index ?? indexes[0]
  const planner = used?.planner
  const testing = rewriteTesting ?? results.rewrite_testing ?? null
  const tested = testing?.tested ? (testing.rewrite_results ?? []) : []
  const baseline = finite(testing?.original_performance?.execution_time_ms)
  const rewrites: RewriteTested[] = tested.length
    ? tested.map((result) => {
        const after = finite(result.performance?.execution_time_ms)
        return {
          name: result.suggestion_metadata?.explanation ?? 'Rewrite',
          before_ms: baseline,
          after_ms: after,
          runs: null,
          outcome: result.success
            ? rewriteOutcome(baseline, after)
            : 'untested',
          sql: result.sql?.trim() || null,
        }
      })
    : (llm?.rewrite_suggestions ?? []).map((suggestion) => ({
        name: suggestion.explanation,
        before_ms: null,
        after_ms: null,
        runs: null,
        outcome: 'untested' as const,
        sql: suggestion.rewritten_sql?.trim() || null,
      }))
  const concerns = assessment?.primary_concerns ?? []
  const readyset = cacheability ?? results.readyset_cacheability ?? null
  const tokens = llm?.token_usage?.total ?? null
  const plan = explain?.explain_plan
  // A stored monitor analysis records the values it ran with and where they came from.
  const stored = results as { parameters?: unknown; param_source?: unknown }
  const recorded = recordedParams(stored.parameters)
  const typed = Object.entries(entered ?? {}).map(([name, value]) => ({
    name,
    value: String(value),
  }))
  const source =
    typeof stored.param_source === 'string'
      ? stored.param_source
      : typeof explain?.param_source === 'string'
        ? explain.param_source
        : null
  return {
    ...EMPTY_VIEW,
    score: finite(assessment?.efficiency_score),
    verdict: first
      ? {
          lead: `${first.rationale.split(/(?<=\.)\s/)[0].replace(/\.$/, '')}.`,
          rest: concerns[0] ?? '',
        }
      : concerns.length
        ? {
            lead: `${concerns[0].replace(/\.$/, '')}.`,
            rest: concerns[1] ?? '',
          }
        : null,
    fix_detail: first
      ? {
          sql: first.sql,
          serves: first.rationale,
          cost_before: finite(planner?.cost_before),
          cost_after: finite(planner?.cost_after),
          size: null,
          write_cost: null,
        }
      : null,
    plan_findings: concerns.map((title) => ({
      title,
      detail: null,
      value: null,
    })),
    rewrites,
    also: [
      ...indexes
        .filter((index) => index !== first)
        .map((index) => ({
          title: indexTitle(index),
          detail: index.rationale || null,
          sql: index.sql,
        })),
      ...(llm?.optimization_opportunities ?? []).map((item) => ({
        title: item.description,
        detail: null,
      })),
    ],
    readyset: readyset
      ? {
          blockers: readyset.issues ?? [],
          verified: readysetVerified(readyset),
        }
      : null,
    run: {
      model: llm?.llm_model ?? llm?.token_usage?.model ?? null,
      tokens,
      seconds: null,
      id: results.stored_analysis_id ?? results.analysis_id ?? null,
    },
    rows_read: finite(explain?.rows_examined),
    rows_returned: finite(explain?.rows_returned),
    planner_cost: finite(explain?.cost_estimate),
    params: recorded.length ? recorded : typed,
    param_source:
      paramSourceText(source) ??
      (!recorded.length && typed.length ? 'with your values' : null),
    explain_text:
      plan && Object.keys(plan).length ? JSON.stringify(plan, null, 1) : null,
  }
}

/** A static screen, or a check run without Readyset, finds blockers but verifies nothing. */
export function readysetVerified(readyset: ReadysetCacheability): boolean {
  return (
    readyset.checked &&
    readyset.method !== 'static_analysis' &&
    readyset.method !== 'readyset_unavailable'
  )
}

/** How to turn planner verification on, when hypopg is missing and the run says how. */
export function plannerSetup(
  testing: IndexTesting | null | undefined
): { installSql: string } | null {
  if (!plannerVerificationOff(testing) || !testing?.install_sql) return null
  return { installSql: testing.install_sql }
}

export function formatInt(value: number | null): string {
  return value == null ? '\u2014' : Math.round(value).toLocaleString('en-US')
}

/** "one seq scan", from the plan text. */
export function scanWord(explainText: string | null): string | null {
  if (!explainText) return null
  const count = (explainText.match(/Seq Scan/g) ?? []).length
  if (count === 0) return null
  return `${NUMBER_WORDS[count] ?? count} seq ${count === 1 ? 'scan' : 'scans'}`
}

/** "65.4 to 64.9 ms". */
export function rewriteRange(rewrite: RewriteTested): string | null {
  if (rewrite.before_ms == null || rewrite.after_ms == null) return null
  return `${rewrite.before_ms.toFixed(1)} to ${rewrite.after_ms.toFixed(1)} ms`
}

export const OUTCOME_WORD: Record<RewriteTested['outcome'], string> = {
  faster: 'Faster',
  slower: 'Slower',
  no_change: 'No change',
  untested: 'Not tested',
}

/** "34x faster" from the benchmark's before and after. */
export function speedup(before: number | null, after: number | null) {
  if (before == null || after == null || after <= 0) return null
  const factor = before / after
  return factor >= 1
    ? {
        value: `${factor >= 10 ? Math.round(factor) : factor.toFixed(1)}x`,
        unit: 'faster',
      }
    : { value: `${(1 / factor).toFixed(1)}x`, unit: 'slower' }
}

/** The hot node, marked in the plan text: the first sequential scan line. */
export function planLines(
  explainText: string
): { text: string; hot: boolean }[] {
  let marked = false
  return explainText.split('\n').map((line) => {
    const hot = !marked && /Seq Scan/.test(line)
    if (hot) marked = true
    return { text: line, hot }
  })
}

/** Which of the four analysis steps a run's progress stage is on. */
export function stepForStage(stage: string | undefined): number {
  switch (stage) {
    case 'collecting_schema':
      return 1
    case 'analyzing_llm':
    case 'testing_rewrites':
      return 2
    case 'checking_readyset':
    case 'storing_results':
    case 'complete':
      return 3
    default:
      return 0
  }
}
