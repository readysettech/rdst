// Typed contract for /api/monitor (UI brief section 5). Timestamps are Unix seconds.
// The backend mirrors these shapes in Pydantic; CONTRACT_STATUS in ./contract says
// which endpoints are served live and which still resolve to fixtures.

export type MonitorRange = '1h' | '24h' | '7d' | '30d' | '90d' | 'all'
export const MONITOR_RANGES: readonly MonitorRange[] = [
  '1h',
  '24h',
  '7d',
  '30d',
  '90d',
  'all',
]
export type Resolution = 60 | 300 | 3600 | 86400
/** Every range the backend serves: 5m and 15m use 1-minute buckets like 1h. */
export type ApiRange = '5m' | '15m' | MonitorRange
export const API_RANGES: readonly ApiRange[] = ['5m', '15m', ...MONITOR_RANGES]
/** A response to an explicit [from, to] window reports range 'custom'. */
export type ScopedRange = ApiRange | 'custom'
/** A brushed chart window, Unix seconds; overrides the range when given. */
export interface TimeWindow {
  from: number
  to: number
}
export type MonitorEngine = 'postgresql' | 'mysql'

// 5.1 History span and health

export interface HistorySpan {
  first_ts: number | null
  last_ts: number | null
  span_seconds: number
  resolutions: Record<
    '60' | '300' | '3600' | '86400',
    { first_ts: number | null }
  >
}

export interface HealthNow {
  ts: number
  qps: number
  mean_ms: number
  active_conns: number | null
  max_conns: number | null
  cache_hit_ratio: number | null
  rollbacks_per_min: number | null
}

export interface HealthPeriod {
  qps: number
  mean_ms: number
  db_time_ms: number
  active_conns_peak: number | null
  cache_hit_ratio_min: number | null
  xact_rollback: number
  deadlocks: number
}

export interface HealthPoint {
  ts: number
  qps: number | null
  mean_ms: number | null
  active_conns: number | null
  max_conns: number | null
  cache_hit_ratio: number | null
  xact_rollback: number | null
  deadlocks: number | null
}

export type ChartMarkerKind =
  | 'index_added'
  | 'index_dropped'
  | 'fix_confirmed'
  | 'fix_no_effect'
  | 'analysis'
  | 'inbox'
  | 'report'

export interface ChartMarker {
  ts: number
  kind: ChartMarkerKind
  label: string
  matches_recommendation?: boolean
  index_name?: string
  table?: string
  inbox_id?: number
  severity?: number
  report_id?: number
  analysis_ref?: string
}

export interface MonitorHealth {
  detail_note?: string | null
  target: string
  engine: MonitorEngine
  engine_version: string | null
  range: MonitorRange
  res: Resolution
  history: HistorySpan
  now: HealthNow
  period: HealthPeriod
  prior: HealthPeriod | null
  series: HealthPoint[]
  markers: ChartMarker[]
  /** The last collector reading, and the next one expected (null while paused). */
  last_reading_at?: number | null
  next_reading_at?: number | null
  first_reading?: boolean
  delay_reason?: string | null
  /** A setup problem (password or statement statistics) blocks readings. */
  setup_blocked?: boolean
  /** Spans RDST couldn't reach the database; other gaps have no known cause. */
  outages?: MonitorOutage[]
}

/** A lost-connection episode, from the last reading to when monitoring resumed. */
export interface MonitorOutage {
  start: number
  /** Null while it's still going on. */
  end: number | null
  cause: 'unreachable'
  item_id: number
}

// 5.2 Pulse

export type PulseKind =
  | 'collect'
  | 'index_scan'
  | 'attention'
  | 'analyze'
  | 'report'
export type MonitorLiveState =
  | 'live'
  | 'late'
  | 'paused'
  | 'connection_lost'
  | 'ai_paused'

export interface MonitorPulse {
  ts: number
  kind: PulseKind
  ok: boolean
  detail: string
  next_collect_at: number
  state: MonitorLiveState
  last_reading_at?: number | null
  next_reading_at?: number | null
  first_reading?: boolean
  delay_reason?: string | null
  /** A setup problem (password or statement statistics) blocks readings. */
  setup_blocked?: boolean
}

export interface MonitorPulsePage {
  pulses: MonitorPulse[]
  last_reading_at?: number | null
  next_reading_at?: number | null
  first_reading?: boolean
  delay_reason?: string | null
  /** A setup problem (password or statement statistics) blocks readings. */
  setup_blocked?: boolean
}

// 5.3 Queries list, 5.4 series, 5.5 fix verdicts, 5.12 compare

export type QueryTextStatus = 'ok' | 'truncated' | 'unavailable'
export type QueryVerdict = 'regressed' | 'improved' | 'steady' | 'new'
export type QueryState =
  | 'attention'
  | 'new'
  | 'fixed'
  | 'fix_no_effect'
  | 'stuck'
  | 'muted'
export type AnalysisJobState =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'skipped'
export type FixStatus =
  | 'measuring'
  | 'confirmed'
  | 'no_effect'
  | 'regressed'
  | 'reverted'

export interface QueryChange {
  latency: number | null
  qps: number | null
  significant: boolean
  verdict: QueryVerdict | null
}

export interface MiniSeriesPoint {
  ts: number
  qps: number | null
  mean_ms: number | null
}

export interface LastAnalysis {
  state: AnalysisJobState
  finished_at: number | null
  analysis_ref: string | null
  mode: string
  /** "Plan only (EXPLAIN)" or "Ran the query (EXPLAIN ANALYZE)". */
  mode_label?: string | null
  param_source: string | null
  reason: string | null
  error_kind?: AnalysisErrorKind | null
  /** The latest finished analysis, even when a later run failed. */
  summary?: AnalysisSummary | null
  /** For a failure on the AI service: failed attempts in a row, and when RDST tries again
   * (null once it gave up for today). */
  attempts?: number | null
  retry_at?: number | null
  /** While queued: today (it starts today, at `queue_position`) or held (it won't start on
   * its own; `reason` says why). While running: 'running'. */
  queue_state?: AnalyzeQueueState | null
  /** 1 = next among the jobs that start today. */
  queue_position?: number | null
  /** While running: the step it's on. */
  phase?: AnalysisPhase | null
}

/** Why an analysis didn't finish, for styling; `reason` is the sentence. */
export type AnalysisErrorKind =
  | 'sign_in'
  | 'ai_unavailable'
  | 'ai_bad_answer'
  | 'not_analyzable'
  | 'timeout'
  | 'plan_failed'
  /** The AI service failed this time and a retry is pending: show it softly. */
  | 'retrying'

/** A header line for the latest finished analysis; analysis_ref opens the full record. */
export interface AnalysisSummary {
  analysis_ref: string
  analysis_id: string
  analyzed_at: number | null
  mode: string | null
  mode_label: string | null
  headline: string
  rating: string | null
  fix_kind: AnalysisFix['kind'] | null
  /** The analysis's efficiency score (0-100), for the row gauge. */
  efficiency_score?: number | null
  /** Show 'Fix ready' only when true (a recommended change with SQL); else 'Analyzed'. */
  fix_ready?: boolean
}

export type AnalyzableCode =
  | 'ok'
  | 'write'
  | 'ddl'
  | 'maintenance'
  | 'utility'
  | 'unsafe_function'
  | 'no_text'

/** The Query Library's per-query Jev quick assessment. */
export interface QueryAssessment {
  status: string
  band: string | null
  priority_score: number | null
  confidence: number | null
  findings: {
    id: string
    label: string
    verdict: string
    confidence: number | null
  }[]
  model: string | null
  rubric_version: string | null
  assessed_at: string | null
  error_code: string | null
  /** What an incomplete assessment waits for ("Waiting for sign-in. ..."). */
  waiting_reason?: string | null
  /** What the classification read: "relevant tables (3 tables, 2 indexes)". */
  schema_coverage?: string | null
  /** When that schema was read (ISO timestamp). */
  schema_collected_at?: string | null
}

export interface LastCompare {
  ts: number
  verdict: string
  speedup: number | null
  /** "4.0x faster" / "1.3x slower". */
  speedup_label?: string | null
  origin_ms?: number | null
  readyset_ms?: number | null
}

/** GET /api/cache/compare-runs?target=&query_hash= : in-flight and recent runs. */
export type CompareStage =
  | 'queued'
  | 'preparing'
  | 'image_pull'
  | 'cache_start'
  | 'cache_create'
  | 'warm'
  | 'run'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface CompareRunSummary {
  run_id: string
  kind: 'cache_compare' | 'speed_test'
  target: string
  status: string
  query_hash: string | null
  label: string | null
  started_at: string | null
  updated_at: string | null
  stage: CompareStage
  stage_label: string
  message: string | null
  percent: number | null
  /** What a queued or starting run waits for ("The sandbox is busy (prewarm). ..."). */
  waiting_for: string | null
  /** Queued runs: how many are ahead (0 = next once the sandbox is free). */
  queue_position?: number | null
  error: string | null
  result: {
    speedup: number | null
    speedup_label: string | null
    origin_ms: number | null
    readyset_ms: number | null
    winner: string | null
  } | null
}

export interface FixVerdict {
  status: FixStatus
  index_name: string
  detected_at: number
  before: { mean_ms: number; qps: number }
  after: { mean_ms: number; qps: number } | null
  sentence: string
}

export interface MonitorQuery {
  key: string
  text: string
  text_status: QueryTextStatus
  text_reason: string | null
  display_name: string
  tables: string[]
  qps: number
  mean_ms: number
  db_time_ms: number
  db_time_share: number
  /** What the Impact sort ranks by: live calls at a steadied cost per call. */
  impact_ms?: number
  change: QueryChange
  series: MiniSeriesPoint[]
  state: QueryState[]
  /** Always null: RDST no longer attributes latency changes to indexes. */
  fix: FixVerdict | null
  last_analysis: LastAnalysis | null
  last_compare: LastCompare | null
  /** Whether RDST analyzes it, by the analyzer's safety rules, and why not. */
  analyzable?: boolean
  analyzable_code?: AnalyzableCode
  not_analyzable_reason?: string | null
  /** Analyzable with a limit, e.g. too slow to run, so plan only. */
  analysis_note?: string | null
  /** Its band's color in the load chart for the same range or window; null outside the top. */
  color_index?: number | null
  assessment?: QueryAssessment | null
  /** Listed queries sharing one base name: group them as a family of `size`. */
  family?: { name: string; size: number } | null
}

export interface QueryCounts {
  all: number
  attention: number
  new: number
  fixed: number
  muted: number
}

export interface CompositionSlice {
  key: string // query key, or 'other'
  db_time_ms: number
  share: number
}

export interface MonitorQueryPage {
  queries: MonitorQuery[]
  total: number
  counts: QueryCounts
  composition: CompositionSlice[]
  history: HistorySpan
  range?: ScopedRange
  /** Only with `locate`: that query's 0-based place in the whole filtered, sorted list,
   * or null when the filters leave it out. */
  located?: number | null
}

// Load breakdown (DESIGN_V4 section 2)

export interface LoadBand {
  key: string // query key, or 'other'
  kind: 'query' | 'other'
  display_name: string
  color_index: number | null
  share: number
  total_ms: number
  calls: number
  mean_ms: number | null
}

export interface LoadPoint {
  ts: number
  /** Bucket length observed; throughput = calls / seconds. */
  seconds: number
  /** Per band, in bands order; null = no reading in the bucket. */
  total_ms: (number | null)[] | null
  calls: (number | null)[] | null
  mean_ms: number | null
}

export interface RangeAvailability {
  range: ApiRange
  enabled: boolean
  res: Resolution
  reason: string | null
}

export interface LoadBreakdown {
  detail_note?: string | null
  target: string
  range: ScopedRange
  res: Resolution
  start: number
  end: number
  history: HistorySpan
  recorded_label: string
  ranges: RangeAvailability[]
  bands: LoadBand[]
  points: LoadPoint[]
  total_ms: number
  calls: number
}

export interface LatencyBreakdown {
  target: string
  range: ScopedRange
  res: Resolution
  keys: string[]
  points: {
    ts: number
    mean_ms: (number | null)[]
    overall_mean_ms: number | null
  }[]
}

// Deep analysis header (query row and report card); analysis_ref is the full record.

export interface ExpectedGain {
  measured: boolean
  text: string
  before_ms: number | null
  after_ms: number | null
  cost_reduction_pct: number | null
}

export interface AnalysisFix {
  kind: 'index' | 'rewrite' | 'config' | 'none'
  title: string
  sql: string | null
  why: string
  expected_gain: ExpectedGain | null
}

export interface DeepAnalysisDigest {
  query_key: string
  analysis_ref: string | null
  analyzed_at: number | null
  mode: 'analyze' | 'explain' | null
  mode_label: string | null
  rating: string | null
  efficiency_score: number | null
  plan_summary: {
    text: string
    root_node: string | null
    execution_ms: number | null
    rows_examined: number | null
    rows_returned: number | null
  } | null
  findings: string[]
  fix: AnalysisFix | null
  other_fixes: AnalysisFix[]
  headline: string
  fix_ready?: boolean
}

export interface QuerySeriesPoint {
  ts: number
  calls: number | null
  qps: number | null
  mean_ms: number | null
  total_ms: number | null
}

export interface QuerySeries {
  detail_note?: string | null
  res: Resolution
  history: HistorySpan
  points: QuerySeriesPoint[]
  markers: ChartMarker[]
  fix: FixVerdict | null
}

// 5.11 Analysis jobs

/** POST /targets/{target}/queries: a query from "Analyze a query", now in the list. */
export interface AddedQuery {
  key: string
  created: boolean
  job: AnalyzeJob | null
}

export interface AddQueryInput {
  /** The SQL as pasted, placeholders and all. */
  sql: string
  query_hash?: string | null
  analysis_id?: string | null
  mode?: 'analyze' | 'explain' | null
}

export interface AnalyzeJob {
  id: number
  target: string
  query_key: string
  reason: 'new' | 'top15_stale' | 'user' | 'report'
  state: AnalysisJobState
  priority: number
  param_source: 'sampled' | 'table_sampled' | 'none' | null
  mode: 'analyze' | 'explain' | null
  enqueued_at: number
  started_at: number | null
  finished_at: number | null
  error: string | null
  analysis_ref: string | null
  reason_text: string | null
  /** Queue facts while waiting or running; absent from older backends. */
  queue_state?: AnalyzeQueueState | null
  /** 1 = next among the jobs that start today. */
  queue_position?: number | null
  estimated_start_at?: number | null
  /** Only on a click's response: it was already waiting (or running) and kept its place. */
  already_queued?: boolean | null
}

/** today: starts today; held: won't start on its own (automatic analysis is off, signed
 * out, or today's automatic analyses are done). Nothing waits for a later day. */
export type AnalyzeQueueState = 'running' | 'today' | 'held'

export interface AnalyzeQueueEntry extends AnalyzeJob {
  display_name: string
}

export interface AnalyzeQueueSummary {
  cap: number
  used: number
  left: number
  /** Part of the cap only user clicks and reports may start. */
  reserved: number
  automatic_left: number
  reset_at: number
  average_run_s: number
  automatic_enabled: boolean
  running: AnalyzeQueueEntry | null
  next: AnalyzeQueueEntry[]
  /** The last ten done or failed, newest first. */
  recent: AnalyzeQueueEntry[]
  waiting_today: number
  held: number
  /** The first monitored hour's analysis ramp; null outside it. */
  first_hour?: FirstHourRamp | null
}

/** Deep analysis in the first hour: the top N (the daily setting) as soon as queries
 * appear, one at a time, with the top list re-checked as readings settle. */
export interface FirstHourRamp {
  started_at: number
  ends_at: number
  /** How many of the heaviest reads it covers: the top N setting. */
  top: number
  /** Completed first-hour analyses. */
  analyzed: number
  scheduled?: number | null
  failed?: number | null
  max: number
  /** When it next widens or checks the top list again. */
  next_check_at: number | null
  /** The first successful reading since the turn-on. */
  first_reading_at?: number | null
  /** When the first query seen since the turn-on was stored. */
  first_queries_at?: number | null
  /** Successful readings since the turn-on. */
  readings?: number
}

export interface AnalyzeJobPage {
  jobs: AnalyzeJob[]
  /** Target-wide queue facts; absent from older backends and the per-query endpoint. */
  queue?: AnalyzeQueueSummary | null
}

// 5.6 Mute rules, 5.7 inbox

export interface MuteRule {
  id: number
  target: string | null // null = all targets
  query_key: string | null // at least one of query_key / type is set
  type: string | null
  until: number | null
  created_at: number
  note: string | null
  matched_count: number
}

export type MuteRuleInput = Omit<
  MuteRule,
  'id' | 'created_at' | 'matched_count'
>

export interface MuteRulePage {
  rules: MuteRule[]
}

export type InboxTab = 'open' | 'muted' | 'done'
export type InboxItemState =
  | 'new'
  | 'seen'
  | 'snoozed'
  | 'dismissed'
  | 'resolved'
export type InboxAction = 'seen' | 'snooze' | 'dismiss' | 'resolve' | 'reopen'
/** act_now red, watch amber, fyi grey, recovered green (measured back to normal). */
export type SeverityBand = 'act_now' | 'watch' | 'fyi' | 'recovered'
/** Digest items only reach the daily report; inbox items list without a push. */
export type InboxDelivery = 'digest' | 'inbox' | 'notify'
/** Which relevance rule let the query reach the inbox (top 10 or 2% of recorded time). */
export type RelevanceRule =
  | 'top_10_24h'
  | 'top_10_1h'
  | 'share_24h'
  | 'share_1h'
  | 'top_10_calls_24h'
  | 'top_10_calls_1h'
  | 'big_change'
  | 'new_heavy'

/** One occurrence of a repeating burst: its time frame, size and query count. */
export interface Occurrence {
  start: number
  /** Back to normal from here; null while it lasts. */
  end: number | null
  /** The median member's level over its normal level (3.1 is 3.1x). */
  size: number | null
  query_count: number
  /** Added quietly: on its usual schedule and size. */
  quiet: boolean
  verdict: 'usual' | 'bigger' | null
}

/**
 * How a query's bursts usually recur (every `period_s`, or daily around the time of
 * `last_onset`), sent when this burst was bigger or longer than usual.
 */
export interface RecurringNote {
  kind: 'period' | 'daily'
  period_s: number
  repeats: number
  last_onset: number
  verdict: 'bigger' | 'longer'
}

/** Where an item opens: a query row and tab, the Database page, a report or a setting. */
export interface InboxLink {
  kind:
    | 'query'
    | 'database'
    | 'report'
    | 'settings'
    | 'configure'
    | 'sign_in'
    | 'setup'
  target: string
  label: string
  query_key?: string | null
  tab?: 'performance' | 'analysis' | 'readyset' | null
  filter?: string | null
  /** A `database` link for several queries: the list shows exactly these. */
  query_keys?: string[] | null
  report_id?: number | null
}

/** "What you can do": steps for work RDST can't do itself, or facts it can't see. */
export interface InboxAdvice {
  summary: string
  steps: string[]
}
export type InboxTone = 'negative' | 'warning' | 'positive' | 'neutral'

export interface InboxItem {
  id: number
  target: string
  type: string
  subject: string
  state: InboxItemState
  severity: number
  title: string
  body_json: string | Record<string, unknown>
  created_at: number
  updated_at: number
  resolved_at: number | null
  resolved_by: 'user' | 'code' | 'jev' | null
  seen_count: number
  reopened_count: number
  severity_band: SeverityBand
  tone: InboxTone
  summary: string
  query_keys: string[]
  subject_label: string
  group_key: string
  muted_by: number | null
  trend: { ts: number; value: number }[]
  /** Always sent by the backend; optional for older items and hand-built test items. */
  /** Display names for the item's queries ("COUNT on customers"), by query key. */
  query_labels?: Record<string, string>
  delivery?: InboxDelivery
  /** When the latest push went out, null when none did. Absent from older backends. */
  notified_at?: number | null
  relevance_rule?: RelevanceRule | null
  link?: InboxLink
  advice?: InboxAdvice | null
  /** What was measured, which way and by how much. */
  measurement?: AlertMeasurement | null
  /** Group alerts: the three members with the most impact, and the median change. */
  members?: AlertMember[]
  median_change?: number | null
  /** Queries that moved in the same window but carry too little load to be listed. */
  smaller_moved?: number
  /** A member's usual schedule, when this burst was bigger or longer than usual. */
  recurring?: RecurringNote | null
  /** A repeating burst's occurrences, newest first; empty for every other item. */
  occurrences?: Occurrence[]
  /** When the change began and ended; the Database page zooms to it. Absent from older backends. */
  incident?: Incident | null
  /**
   * Where the item stands, from the readings its time frame shows (a repeating burst by its
   * latest occurrence), and since when. An open item back to normal reads `recovered`.
   */
  status?: ItemStatus
  status_at?: number | null
  /** Why it's in its section: 'Act now: latency up 3.2x on a top-10 query today, still happening'. */
  band_reason?: string
}

export type ItemStatus = 'ongoing' | 'back_to_normal' | 'closed' | 'unknown'

/**
 * An item's incident window in Unix seconds, measured from the per-query minute
 * series (`measured`) or taken from the item's own times. `end` is null while it lasts.
 */
export interface Incident {
  start: number
  end: number | null
  ongoing: boolean
  measured: boolean
  metric: 'latency' | 'calls' | null
  /** The level during the incident over the level before it, minus one. */
  change: number | null
  /** Group items: every member with its own onset, end and sparkline. */
  queries: IncidentQuery[]
}

export interface IncidentQuery {
  query_key: string
  start: number | null
  end: number | null
  change: number | null
  /** What this member's own jump moved; absent from older backends. */
  metric?: 'latency' | 'calls'
  spark: { ts: number; value: number }[]
}

export interface AlertMeasurement {
  metric:
    | 'latency'
    | 'calls'
    | 'load'
    | 'share'
    | 'rollbacks'
    | 'cache_hit_ratio'
    | 'connections'
    | 'running_time'
  direction: 'up' | 'down'
  before: number | null
  after: number | null
  change: number | null
  unit: string
  text: string
}

export interface AlertMember {
  query_key: string
  display_name: string
  before: number | null
  after: number | null
  change: number | null
  impact_ms: number
}

export interface InboxPage {
  items: InboxItem[]
}

export interface InboxQuery {
  state?: InboxTab
  target?: string
  include_muted?: boolean
  /** Digest items only reach the daily report; the inbox leaves them out. */
  include_digest?: boolean
  /** Measure each item's incident window (the Inbox page only; it's costly). */
  incidents?: boolean
}

// 5.8 Analyze database progress stream

export type StepState = 'queued' | 'running' | 'done' | 'skipped' | 'failed'
export type ReportRunState = 'running' | 'done' | 'failed' | 'cancelled'
export type ReportStep =
  | 'history'
  | 'select'
  | 'analyses'
  | 'health'
  | 'narrative'
/** Where a running analysis is: sample (finding real parameter values), plan (running
 * EXPLAIN), run (EXPLAIN ANALYZE running the query), schema (reading the schema), advise
 * (writing recommendations), verify (testing the suggested fixes). */
export type AnalysisPhase =
  | 'queued'
  | 'sample'
  | 'plan'
  | 'run'
  | 'schema'
  | 'advise'
  | 'verify'
export type ReportSectionName =
  | 'summary'
  | 'fix_first'
  | 'one_fix_several_queries'
  | 'health'
  | 'changed'

export type ReportEvent =
  | {
      type: 'run'
      state: ReportRunState
      started_at: number
      elapsed_s: number
    }
  | {
      type: 'step'
      step: ReportStep
      state: StepState
      detail: string
      done?: number
      total?: number
    }
  | {
      type: 'selected'
      queries: { key: string; display_name: string; db_time_share: number }[]
    }
  | {
      type: 'analysis'
      key: string
      state: StepState | 'provisional'
      reason: string | null
      result_line: string | null
      duration_s: number | null
      display_name?: string
      phase?: AnalysisPhase | null
      /** The finished analysis's headline ("Needs an index on orders(total)"). */
      headline?: string | null
      error_kind?: AnalysisErrorKind | null
    }
  | { type: 'health_check'; check: string; label: string; state: StepState }
  | { type: 'section'; section: ReportSectionName; state: 'writing' | 'ready' }
  | { type: 'budget'; message: string }
  /** The written text, whole: hosted inference doesn't stream tokens. */
  | { type: 'narrative'; delta: string; final: boolean }

// 5.9 Reports

export type ReportTrigger = 'user' | 'nightly'
export type HealthCheckStatus = 'ok' | 'warn' | 'bad' | 'info'

export interface ReportSummary {
  id: number
  ts: number
  trigger: ReportTrigger
  duration_s: number
  headline: string
  /** The report's verdict sentence. */
  verdict?: string
  counts: {
    fix_first: number
    one_fix: number
    health_warn: number
    health_ok: number
  }
  provisional_count: number
  /** The local day the snapshot covers; a partial day is "today so far". */
  period_start?: number | null
  period_end?: number | null
  period_partial?: boolean
  /** YYYY-MM-DD, local. */
  day?: string
  /** Synthetic demo history, never real. */
  sample?: boolean
  events?: number | null
  open_events?: number | null
  look_at?: number | null
  health_problems?: number | null
  qps?: number | null
}

export interface ReportHistory {
  reports: ReportSummary[]
}

export interface ReportHealthCheck {
  check: string
  label: string
  value: string
  status: HealthCheckStatus
  note: string
  /** What to do; set when it matters (warn or bad). */
  action?: string | null
  matters?: boolean
}

export interface ReportFixFirst {
  query_key: string
  title: string
  why: string
  action: string
  display_name?: string
  share?: number
  db_time_ms?: number
  calls?: number
  mean_ms?: number | null
  /** The analysis fix that addresses this finding; null when none of them does. */
  fix?: AnalysisFix | null
  fix_sql?: string | null
  expected_gain?: string | null
  /** The complete stored analysis; `analysis` is only its header. */
  analysis_ref?: string | null
  analysis?: DeepAnalysisDigest | null
}

export interface ReportOneFix {
  table: string
  columns: string[]
  query_keys: string[]
  action: string
  share?: number
  /** From the fix's own action text; render `title`, never one built from table/columns. */
  kind?: 'index' | 'rewrite' | 'config' | 'other'
  title?: string
}

export interface ReportVerdict {
  sentence: string
  query_key: string | null
  share: number | null
  tone: InboxTone
}

export interface SinceYesterday {
  label: string
  query_key: string | null
  metric: 'load' | 'calls' | 'latency' | 'share'
  before: number
  after: number
  change: number | null
  unit: 'ms' | 'calls' | 'ms_per_call' | 'ratio'
}

export interface ReportBody {
  summary: string
  fix_first: ReportFixFirst[]
  one_fix_several_queries: ReportOneFix[]
  health: ReportHealthCheck[]
  changed_since_yesterday: string[]
  provisional_query_keys: string[]
  narrative_error: string | null
  /** When the written summary retries after narrative_error (Unix seconds). */
  narrative_retry_at?: number | null
  /** Findings that waited for the report: digest level, or over the daily push limit. */
  digest?: DigestEntry[]
  version?: number
  verdict?: ReportVerdict | null
  since_yesterday?: SinceYesterday[]
  /** The daily snapshot (version 3); rebuilt from stored data for older reports. */
  snapshot?: DaySnapshot | null
}

// Daily snapshot (report version 3): one local day, frozen when it was written.

export type SnapshotStatus =
  | 'needs_attention'
  | 'watch'
  | 'fix_ready'
  | 'healthy'
  | 'not_analyzed'

export interface SnapshotPeriod {
  start: number
  end: number
  partial: boolean
  day: string
  prior_start?: number | null
  prior_end?: number | null
  /** When recording began partway through the comparison day. */
  prior_observed_from?: number | null
  observed_from?: number | null
  observed_to?: number | null
  /** Older reports covered the 24 h before they ran. */
  rolling?: boolean
}

export type SnapshotMetric =
  | 'qps'
  | 'mean_ms'
  | 'active_conns_peak'
  | 'cache_hit_ratio_min'
  | 'xact_rollback'
  | 'deadlocks'

export interface SnapshotNumber {
  metric: SnapshotMetric
  label: string
  unit: 'qps' | 'ms' | 'count' | 'ratio'
  value: number | null
  prior: number | null
  change: number | null
  /** Which way is good; null when neither is. */
  better: 'up' | 'down' | null
}

export interface SnapshotBand {
  key: string
  kind: 'query' | 'other'
  display_name: string
  color_index: number | null
  share: number
  calls: number
  qps: number
  mean_ms: number | null
}

export interface SnapshotMarker {
  ts: number
  kind: 'event' | 'index'
  label: string
  query_keys?: string[]
  tone?: string | null
}

export interface SnapshotChart {
  res: number
  start: number
  end: number
  xs: number[]
  bands: SnapshotBand[]
  /** Per band (same order), per x: queries per second, null without a reading. */
  qps: (number | null)[][]
  markers: SnapshotMarker[]
  /** Per band, per x: database milliseconds spent (older snapshots lack it). */
  ms?: (number | null)[][] | null
  /** Per band, per x: average latency in ms. */
  mean_ms?: (number | null)[][] | null
  /** Per x: average latency over every query. */
  overall_mean_ms?: (number | null)[] | null
}

export interface SnapshotEvent {
  ts: number
  kind: 'created' | 'worse' | 'reopened' | 'resolved' | 'index'
  item_id: number | null
  type: string
  what: string
  title: string
  detail: string
  query_keys: string[]
  state: 'open' | 'resolved' | 'dismissed' | 'done' | 'unknown'
  resolved_at?: number | null
  tone?: string | null
  delivery?: string | null
  definition?: string | null
}

export interface SnapshotQuery {
  query_key: string
  display_name: string
  calls: number | null
  qps: number | null
  total_ms: number
  mean_ms: number | null
  share: number
  prior_calls: number | null
  prior_mean_ms: number | null
  latency_change: number | null
  status: SnapshotStatus
  status_reason: string | null
  score: number | null
  grade: 'A' | 'B' | 'C' | 'D' | 'F' | null
  fix: { kind: string; title: string; sql?: string | null } | null
  /** The complete stored deep analysis, rendered in full on expand. */
  analysis_ref: string | null
  analysis_mode: string | null
  analyzed_at: number | null
  headline: string | null
  provisional: boolean
  new: boolean
  text?: string | null
  /** The deep analysis findings, full text. */
  findings?: string[]
  /** The report's note on this query (look_at rows). */
  note?: SnapshotQueryNote | null
}

export interface SnapshotQueryNote {
  query_key: string
  title: string
  why: string
  action: string
}

/** A database-level change: settings, vacuum, indexes, connections, replication. */
export interface SnapshotAction {
  id: string
  check: string
  title: string
  severity: 'bad' | 'warn' | 'info'
  finding: string
  evidence: { label: string; value: string }[]
  why: string
  steps: string
  rows: { name: string; value: string; detail: string }[]
  sql: string | null
  setting: { name: string; current: string; target: string } | null
  restart: boolean
  caveat: string | null
  query_keys: string[]
  /** The report's rank for this day (0 first); null when not picked. */
  pick?: number | null
  model_why?: string | null
}

export interface SnapshotCheck {
  check: string
  label: string
  status: 'ok' | 'warn' | 'bad' | 'unknown'
  value: string
}

export interface SnapshotOverview {
  status: 'ok' | 'warn' | 'bad'
  label: string
  headline: string
  checks: SnapshotCheck[]
}

export interface SnapshotVitals {
  res: number
  xs: number[]
  qps: (number | null)[]
  mean_ms: (number | null)[]
  active_conns: (number | null)[]
  cache_hit_ratio: (number | null)[]
  xact_rollback: (number | null)[]
  deadlocks: (number | null)[]
}

export interface SnapshotSpike {
  id: string
  metric: 'traffic' | 'latency'
  start: number
  end: number
  peak_ts: number
  peak: number
  usual: number
  ratio: number | null
  queries: {
    query_key: string
    before: number
    during: number
    ratio: number | null
  }[]
  events: { ts: number; what: string; title: string }[]
  summary: string
}

export interface SnapshotBriefItem {
  topic: string | null
  /** A sentence; queries appear as {{q:key}} tokens. */
  text: string
  /** One plain line on what it means for the application and why it matters. */
  why?: string | null
}

export interface SnapshotHealthProblem {
  check: string
  label: string
  status: 'warn' | 'bad'
  summary: string
  rows: { name: string; value: string; detail: string }[]
  action: string | null
  sql: string | null
}

export interface SnapshotHealth {
  problems: SnapshotHealthProblem[]
  fine: string[]
  unavailable: { check: string; label: string; reason: string }[]
}

export interface DaySnapshot {
  version: number
  legacy?: boolean
  period: SnapshotPeriod
  numbers: SnapshotNumber[]
  peak: { qps: number | null; ts: number | null }
  chart: SnapshotChart | null
  events: SnapshotEvent[]
  queries: SnapshotQuery[]
  look_at: SnapshotQuery[]
  health: SnapshotHealth
  names: Record<string, string>
  facts: { kind: string; text: string; keys: string[] }[]
  /** The day in brief: sentences with {{q:key}} tokens. */
  brief: string[]
  brief_source: 'model' | 'facts'
  brief_items?: SnapshotBriefItem[]
  overview?: SnapshotOverview | null
  actions?: SnapshotAction[]
  vitals?: SnapshotVitals | null
  spikes?: SnapshotSpike[]
  engine?: string
}

export interface DigestEntry {
  id: number
  type: string
  title: string
  summary: string
  query_keys: string[]
  subject_label: string
  ts: number
  reason: 'digest' | 'push_limit'
}

/** One of the queries a run selected, with its final analysis state. */
export interface ReportAnalyzedQuery {
  key: string
  display_name: string
  db_time_share: number
  state: StepState | 'provisional'
  result_line: string | null
  reason: string | null
  headline?: string | null
  error_kind?: AnalysisErrorKind | null
}

export interface DatabaseReport {
  id: number
  target: string
  ts: number
  trigger: ReportTrigger
  state: ReportRunState
  provisional: boolean
  finished_at?: number | null
  progress: { message: string; done: number; total: number } | null
  report_json: ReportBody | null
  refs: Record<string, { display_name: string; text_snippet: string }>
  /** The run's selected queries; absent from fixtures and very old runs. */
  queries?: ReportAnalyzedQuery[]
  period_start?: number | null
  period_end?: number | null
  period_partial?: boolean
  /** Synthetic demo history, never real. */
  sample?: boolean
}

// 5.10 Settings and usage

export type AnalysisMode = 'plans_only' | 'execute'

export interface MonitorSettings {
  configured?: boolean
  enabled: boolean
  /** Why monitoring can't be turned on here (an Aurora reader), or null. */
  unavailable_reason?: string | null
  deep_analysis: boolean
  /** Consent to run queries with EXPLAIN ANALYZE; mirrors analysis_mode. */
  execute: boolean
  /** plans_only (EXPLAIN, the default) or execute (EXPLAIN ANALYZE). */
  analysis_mode: AnalysisMode
  auto_analyze_new: boolean
  /** "Deeply analyze the top N queries per day": the top N, and the daily re-analyses. */
  analysis_daily_cap: number
  /** "Analyze up to M new top queries per day": queries entering the top N for the first
   * time. Defaults to 25. */
  newcomer_daily_cap: number
  attention_every_min: 5 | 15 | 60
  nightly_report: boolean
  nightly_hour: number
  /** Push notifications per day for this database; the rest roll into the report. */
  push_daily_cap: number
  /** What pushes a notification. Switched-off events still reach the Inbox. */
  notify_events?: NotifyEvents
  /** When monitoring was last turned on from the app. */
  enabled_at?: number | null
  /** The first-hour start, or null when the enable has earlier recorded history. */
  first_hour_started_at?: number | null
  size_bytes: number
  budget_bytes: number
  /** on: the account holds an AI monitoring slot. local: the account was full, so only
   * local monitoring runs. pending: the Key Service couldn't be asked yet. paused: the
   * account reached its AI trial limit. */
  ai_monitoring?: AiMonitoringState
  /** Why AI monitoring isn't on, and since when (epoch seconds). */
  ai_reason?: AiMonitoringReason | null
  ai_since?: number | null
}

export type AiMonitoringState = 'off' | 'on' | 'local' | 'pending' | 'paused'

/** One deep analysis in the all-databases work list. */
export interface MonitorWorkAnalysis {
  id: number
  target: string
  query_key: string
  display_name: string
  state: AnalyzeJob['state']
  /** you: the user clicked Analyze now; monitoring: queued on its own. */
  source: 'you' | 'monitoring'
  enqueued_at: number
  started_at: number | null
  finished_at: number | null
  /** 1-based place among the jobs its database starts today. */
  queue_position?: number | null
  /** today: starts today. */
  queue_state?: 'today' | null
  estimated_start_at?: number | null
  efficiency_score?: number | null
}

/** Monitoring's real work across every monitored database. */
export interface MonitorWork {
  running: MonitorWorkAnalysis[]
  queued: MonitorWorkAnalysis[]
  /** Analyses the AI service failed; RDST queues each again at `retry_at`. */
  retrying: {
    target: string
    query_key: string
    display_name: string
    attempts: number
    retry_at: number
  }[]
  /** The latest analyses finished today; done_today_count counts them all. */
  done_today: MonitorWorkAnalysis[]
  done_today_count: number
  reports: {
    id: number
    target: string
    trigger: 'nightly' | 'user'
    started_at: number
    message?: string | null
  }[]
}

/** Why AI monitoring isn't on: signed out, the Key Service unreachable or erroring, every
 * slot in use, or the account's AI trial allowance used up. */
export type AiMonitoringReason =
  | 'signed_out'
  | 'unreachable'
  | 'service_error'
  | 'limit'
  | 'quota'

/** AI monitoring on `in_use` of `limit` databases for the signed-in account. */
export interface MonitoringLimit {
  in_use: number
  /** Null when the Key Service couldn't be asked and hasn't answered before. */
  limit: number | null
  /** The user's own Anthropic key pays for deep analysis and reports. */
  own_key: boolean
  known: boolean
  full: boolean
  /** Signed in to a Readyset account; AI monitoring needs one. */
  signed_in?: boolean
  /** AI work paused at the account's trial limit: jev (AI checks), hosted (analysis, reports). */
  allowance?: ('jev' | 'hosted')[]
  /** Who to contact to raise a limit. */
  contact?: string
  /** With a target: whether turning it on would get AI monitoring. */
  target_fits?: boolean | null
  targets: {
    target: string
    ai: AiMonitoringState
    reason?: AiMonitoringReason | null
    since?: number | null
  }[]
  /** Slots held for databases this computer doesn't have; each can be released. */
  elsewhere?: {
    fingerprint: string
    tier: 'hosted' | 'own_key'
    last_seen_at?: number | null
    /** The database's name where it's monitored, when that client sent one. */
    label?: string | null
  }[]
}

export interface NotifyEvents {
  /** Never held back by push_daily_cap. */
  database_unreachable: boolean
  database_recovered: boolean
  monitoring_setup: boolean
  stuck_query: boolean
  database_events: boolean
  query_changes: boolean
}

export type MonitorSettingsUpdate = Partial<
  Omit<
    MonitorSettings,
    | 'size_bytes'
    | 'budget_bytes'
    | 'notify_events'
    | 'enabled_at'
    | 'first_hour_started_at'
    | 'ai_monitoring'
  >
> & { notify_events?: Partial<NotifyEvents> }

export interface UsageDay {
  date: string // YYYY-MM-DD, local
  jev_calls: number
  analyses: number
  reports: number
  billable_calls: number
  notifications?: number
}

export interface MonitorUsage {
  days: UsageDay[]
  caps: { jev_daily: number; analysis_daily: number; push_daily?: number }
}
