"""Pydantic mirror of the monitor UI contract (web-apps/apps/rdst/src/lib/monitor/types.ts).

Routes declare these as documented responses, so the OpenAPI schema and the generated
client types follow the contract. Pre-contract fields that the classic pages still read
are listed as optional extras.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MonitorRange = Literal["5m", "15m", "1h", "24h", "7d", "30d", "90d", "all"]
Resolution = Literal[60, 300, 3600, 86400]
# A response to an explicit [from, to] window reports range "custom".
ScopedRange = Literal["5m", "15m", "1h", "24h", "7d", "30d", "90d", "all", "custom"]
StepState = Literal["queued", "running", "done", "skipped", "failed"]
# Why a queued analysis won't start on its own.
HeldReason = Literal["sign_in", "monitoring_off", "outside_top", "ai_paused", "automatic_off",
                     "over_allowance"]


class ResolutionStart(BaseModel):
    first_ts: int | None


class HistorySpan(BaseModel):
    first_ts: int | None
    last_ts: int | None
    span_seconds: int
    resolutions: dict[Literal["60", "300", "3600", "86400"], ResolutionStart]


class HealthNow(BaseModel):
    ts: int
    qps: float
    mean_ms: float
    lock_waits: int | None = None
    active_conns: int | None
    max_conns: int | None
    cache_hit_ratio: float | None
    rollbacks_per_min: int | None


class BenchmarkFixesRequest(BaseModel):
    query_keys: list[str]
    runs: int = 20
    scratch_schema: bool = True


class BenchmarkFixRow(BaseModel):
    key: str
    name: str
    before_ms: float | None
    after_ms: float | None
    change_pct: float | None
    # True when the after value comes from the planner, not a timed run.
    estimated: bool = True
    # Why there is no after value; null when there is one.
    reason: str | None = None


class BenchmarkFixesResult(BaseModel):
    target: str
    ran_at: int
    runs: int
    # Whether the fixes were built and timed on a scratch schema.
    scratch_schema: bool
    rows: list[BenchmarkFixRow]
    # The covered queries' share of database time over the last 24 h.
    share: float
    method: str


class HealthPeriod(BaseModel):
    qps: float
    mean_ms: float
    # The 95th percentile of the per-reading mean latency; null without readings.
    p95_ms: float | None = None
    db_time_ms: float
    lock_waits_peak: int | None = None
    active_conns_peak: int | None
    cache_hit_ratio_min: float | None
    xact_rollback: int
    deadlocks: int
    # The busiest whole minute, the same peak Home and the report show; never below `qps`.
    qps_peak: float | None = None
    qps_peak_at: int | None = None


class HealthPoint(BaseModel):
    ts: int
    qps: float | None
    mean_ms: float | None
    lock_waits: int | None = None
    active_conns: int | None
    max_conns: int | None
    cache_hit_ratio: float | None
    xact_rollback: int | None
    deadlocks: int | None


class ChartMarker(BaseModel):
    ts: int
    kind: Literal["index_added", "index_dropped", "fix_confirmed", "fix_no_effect", "analysis",
                  "inbox", "report"]
    label: str
    matches_recommendation: bool | None = None
    index_name: str | None = None
    table: str | None = None
    inbox_id: int | None = None
    severity: float | None = None
    report_id: int | None = None
    analysis_ref: str | None = None


class MonitorOutage(BaseModel):
    """A lost-connection episode: from the last reading to when monitoring resumed."""
    start: int
    end: int | None
    cause: Literal["unreachable"]
    item_id: int


class MonitorHealth(BaseModel):
    target: str
    engine: Literal["postgresql", "mysql"]
    engine_version: str | None
    range: MonitorRange
    res: Literal[60, 300, 3600, 86400]
    history: HistorySpan
    now: HealthNow
    period: HealthPeriod
    prior: HealthPeriod | None
    series: list[HealthPoint]
    markers: list[ChartMarker]
    window: str | None = None
    tiles: dict | None = None
    sparkline: list[dict] | None = None
    # When RDST last read the database and when it reads next; the UI renders the ring.
    last_reading_at: int | None = None
    next_reading_at: float | None = None
    first_reading: bool = False
    delay_reason: str | None = None
    # A setup problem (missing password or statement statistics) blocks readings.
    setup_blocked: bool = False
    # Spans RDST couldn't reach the database; other gaps in the series have no known cause.
    outages: list[MonitorOutage] = []
    detail_note: str | None = None
    # The last attention check that ran; null when none did.
    checks_ran_at: int | None = None


class DatabaseActivity(BaseModel):
    """What monitoring is doing on one database now: a pass checking it, an analysis or the
    report running, or readings alone. `done`/`total` count the first hour's analyses."""
    kind: Literal["reading", "checking", "analyzing", "report", "idle"]
    label: str
    done: int | None = None
    total: int | None = None
    last_reading_at: int | None = None
    # Seconds between readings.
    every_s: int = 60
    next_check_at: int | None = None


class MonitorPulse(BaseModel):
    ts: int
    kind: Literal["collect", "index_scan", "attention", "analyze", "report"]
    ok: bool
    detail: str
    next_collect_at: int
    state: Literal["live", "late", "paused", "connection_lost", "ai_paused"]
    last_reading_at: int | None = None
    next_reading_at: float | None = None
    first_reading: bool = False
    delay_reason: str | None = None
    # A setup problem (missing password or statement statistics) blocks readings.
    setup_blocked: bool = False
    checks_ran_at: int | None = None
    activity: DatabaseActivity | None = None


class ConnectionProblem(BaseModel):
    """Why RDST can't reach the database, and what to check."""
    reason: str
    fix: str


class MonitorPulsePage(BaseModel):
    pulses: list[MonitorPulse]
    connection_problem: ConnectionProblem | None = None
    last_reading_at: int | None = None
    next_reading_at: float | None = None
    first_reading: bool = False
    delay_reason: str | None = None
    # A setup problem (missing password or statement statistics) blocks readings.
    setup_blocked: bool = False
    # The last attention check that ran; null when none did.
    checks_ran_at: int | None = None
    activity: DatabaseActivity | None = None


class CollectNowResult(BaseModel):
    ok: bool
    detail: str


class QueryChange(BaseModel):
    latency: float | None
    qps: float | None
    significant: bool
    verdict: Literal["regressed", "improved", "steady", "new"] | None


class MiniSeriesPoint(BaseModel):
    ts: int
    qps: float | None
    mean_ms: float | None


class ReportVerdictText(BaseModel):
    """A two-clause verdict: the lead ("demo needs two things today:") and the rest."""
    lead: str
    rest: str


class AnalysisFixDetail(BaseModel):
    sql: str
    serves: str | None = None
    cost_before: float | None = None
    cost_after: float | None = None
    size: str | None = None
    write_cost: str | None = None


class PlanFinding(BaseModel):
    title: str
    detail: str | None = None
    value: str | None = None


class RewriteTested(BaseModel):
    name: str
    before_ms: float | None = None
    after_ms: float | None = None
    runs: int | None = None
    outcome: Literal["faster", "slower", "no_change", "untested"] = "untested"
    sql: str | None = None


class AlsoWorthDoing(BaseModel):
    title: str
    detail: str | None = None
    sql: str | None = None


class ReadysetFacts(BaseModel):
    blockers: list[str] = []
    verified: bool = False


class BenchmarkFacts(BaseModel):
    before_ms: float | None = None
    after_ms: float | None = None
    change: float | None = None
    method: str | None = None


class AnalysisRunFacts(BaseModel):
    model: str | None = None
    tokens: int | None = None
    seconds: float | None = None
    id: str | None = None


class AnalysisParam(BaseModel):
    name: str
    value: str


class LatestAnalysisSummary(BaseModel):
    """The latest finished analysis: a header line and where the complete record is."""
    analysis_ref: str
    analysis_id: str
    analyzed_at: int | None
    mode: str | None
    mode_label: str | None
    headline: str
    rating: str | None
    fix_kind: Literal["index", "rewrite", "config", "none"] | None
    # The stored analysis's efficiency score (0-100), for the row gauge.
    efficiency_score: float | None = None
    # True only for an actionable recommendation with SQL: the 'Fix ready' chip.
    fix_ready: bool = False
    score: float | None = None
    verdict: ReportVerdictText | None = None


class LastAnalysis(BaseModel):
    state: Literal["queued", "running", "done", "failed", "skipped"]
    finished_at: int | None
    analysis_ref: str | None
    mode: str
    # "Plan only (EXPLAIN)" or "Ran the query (EXPLAIN ANALYZE)", for the analysis label.
    mode_label: str | None = None
    param_source: str | None
    reason: str | None
    # Why it didn't finish: sign_in, ai_unavailable, ai_bad_answer, not_analyzable, timeout,
    # plan_failed, or retrying (the AI service failed and RDST is trying again).
    error_kind: str | None = None
    summary: LatestAnalysisSummary | None = None
    # For a failure on the AI service: consecutive failed attempts, and when RDST tries
    # again (null once the retries gave up).
    attempts: int | None = None
    retry_at: int | None = None
    # While queued: today (it starts today, at queue_position) or held (it won't start on
    # its own; `reason` says why, `held_reason` in a word). running while it runs.
    queue_state: Literal["running", "today", "held"] | None = None
    held_reason: HeldReason | None = None
    queue_position: int | None = None
    # While running, the step it's on: sample (finding real parameter values), plan
    # (EXPLAIN), run (EXPLAIN ANALYZE), schema, advise (the AI) or verify (testing fixes).
    phase: Literal["sample", "plan", "run", "schema", "advise", "verify"] | None = None


class AssessmentFinding(BaseModel):
    id: str
    label: str
    verdict: str
    confidence: float | None = None


class QueryAssessment(BaseModel):
    """The latest per-query Jev quick assessment (the Query Library's rating)."""
    status: str
    band: str | None
    priority_score: int | None
    confidence: float | None
    findings: list[AssessmentFinding]
    model: str | None
    rubric_version: str | None
    assessed_at: str | None
    error_code: str | None
    # What an incomplete assessment waits for (sign-in, schema, retry), as a sentence.
    waiting_reason: str | None = None
    # What the classification read ("relevant tables (3 tables, 2 indexes)") and when.
    schema_coverage: str | None = None
    schema_collected_at: str | None = None


class LastCompare(BaseModel):
    ts: int
    verdict: str
    speedup: float | None
    # "4.0x faster" / "1.3x slower", with the measured means behind it.
    speedup_label: str | None = None
    origin_ms: float | None = None
    readyset_ms: float | None = None


class FixMeasurement(BaseModel):
    mean_ms: float
    qps: float


class FixVerdict(BaseModel):
    status: Literal["measuring", "confirmed", "no_effect", "regressed", "reverted"]
    index_name: str
    detected_at: int
    before: FixMeasurement
    after: FixMeasurement | None
    sentence: str


class QueryFamily(BaseModel):
    name: str
    size: int


class WriteCost(BaseModel):
    table: str
    verb: Literal["insert", "update", "delete"]
    set_columns: list[str]
    table_writes_per_s: float
    indexes_maintained: int
    hot_ratio: float | None


class SuggestedFix(BaseModel):
    sql: str
    label: str


class TrendMean(BaseModel):
    ts: int
    mean_ms: float | None


class MonitorQuery(BaseModel):
    key: str
    text: str
    text_status: Literal["ok", "truncated", "unavailable"]
    text_reason: str | None
    display_name: str
    tables: list[str]
    qps: float
    mean_ms: float
    db_time_ms: float
    db_time_share: float
    #: What the Impact sort ranks by: live calls at a steadied cost per call.
    impact_ms: float = 0.0
    change: QueryChange
    series: list[MiniSeriesPoint]
    state: list[Literal["attention", "new", "fixed", "fix_no_effect", "stuck", "muted"]]
    fix: FixVerdict | None
    last_analysis: LastAnalysis | None
    last_compare: LastCompare | None
    assessment: QueryAssessment | None = None
    # Whether RDST analyzes this statement, by the analyzer's safety rules, and why not.
    analyzable: bool = True
    analyzable_code: Literal["ok", "write", "ddl", "maintenance", "utility", "unsafe_function",
                             "no_text"] = "ok"
    not_analyzable_reason: str | None = None
    analysis_note: str | None = None
    # The query's band color in the load chart for the same range (top queries only).
    color_index: int | None = None
    # Queries sharing one base name ("COUNT on customers and orders by total"): the base
    # name and how many listed queries share it, so the list can group them.
    family: QueryFamily | None = None
    # A write's cost on its table; on the single-query read only.
    write_cost: WriteCost | None = None
    sparkline: list[dict] | None = None
    change_vs_prior: dict | None = None
    badges: list[str] | None = None
    # The list's plan score, 24 h latency delta and trend, the fix line and one status word.
    plan_score: float | None = None
    latency_delta_24h: float | None = None
    trend_24h: list[TrendMean] = []
    suggested_fix: SuggestedFix | None = None
    status: Literal["fix_suggested", "analyzing", "healthy", "stuck", "not_analyzed"] = \
        "not_analyzed"


class QueryCounts(BaseModel):
    all: int
    attention: int
    new: int
    fixed: int
    muted: int


class CompositionSlice(BaseModel):
    key: str
    db_time_ms: float
    share: float


class MonitorQueryPage(BaseModel):
    queries: list[MonitorQuery]
    total: int
    # Every application query RDST has seen on the database, listed in the range or not.
    seen_total: int = 0
    counts: QueryCounts
    composition: list[CompositionSlice]
    history: HistorySpan
    target: str | None = None
    range: ScopedRange | None = None
    window: str | None = None
    limit: int | None = None
    offset: int | None = None
    # Only with ?locate=<key>: its 0-based place in the whole filtered, sorted list, or null
    # when the filters leave it out.
    located: int | None = None


class QuerySeriesPoint(BaseModel):
    temp_blks: float | None = None
    ts: int
    calls: float | None
    qps: float | None
    mean_ms: float | None
    total_ms: float | None
    res: int | None = None


class QuerySeries(BaseModel):
    res: Literal[60, 300, 3600, 86400]
    history: HistorySpan
    points: list[QuerySeriesPoint]
    markers: list[ChartMarker]
    fix: FixVerdict | None
    target: str | None = None
    key: str | None = None
    range: MonitorRange | None = None
    resolution: int | None = None
    detail_note: str | None = None


class RangeAvailability(BaseModel):
    range: MonitorRange
    enabled: bool
    res: Resolution
    reason: str | None


class LoadBand(BaseModel):
    key: str
    kind: Literal["query", "other"]
    display_name: str
    color_index: int | None
    share: float
    total_ms: float
    calls: float
    mean_ms: float | None


class LoadPoint(BaseModel):
    ts: int
    seconds: int
    total_ms: list[float | None] | None
    calls: list[float | None] | None
    mean_ms: float | None


class LoadBreakdown(BaseModel):
    """Recorded execution time per bucket, split by the top queries of the range."""
    target: str
    range: ScopedRange
    res: Resolution
    start: int
    end: int
    history: HistorySpan
    recorded_label: str
    ranges: list[RangeAvailability]
    bands: list[LoadBand]
    points: list[LoadPoint]
    total_ms: float
    calls: float
    detail_note: str | None = None


class LatencyPoint(BaseModel):
    ts: int
    mean_ms: list[float | None]
    overall_mean_ms: float | None


class LatencyBreakdown(BaseModel):
    target: str
    range: ScopedRange
    res: Resolution
    keys: list[str]
    points: list[LatencyPoint]


class ExpectedGain(BaseModel):
    measured: bool
    text: str
    before_ms: float | None = None
    after_ms: float | None = None
    cost_reduction_pct: float | None = None


class AnalysisFix(BaseModel):
    kind: Literal["index", "rewrite", "config", "none"]
    title: str
    sql: str | None
    why: str
    expected_gain: ExpectedGain | None


class PlanSummary(BaseModel):
    text: str
    root_node: str | None
    execution_ms: float | None
    rows_examined: int | None
    rows_returned: int | None


class DeepAnalysisDigest(BaseModel):
    """One stored deep analysis, as the query row and the report card show it."""
    query_key: str
    analysis_ref: str | None
    analyzed_at: int | None
    mode: Literal["analyze", "explain"] | None
    mode_label: str | None
    rating: str | None
    efficiency_score: float | None
    plan_summary: PlanSummary | None
    findings: list[str]
    fix: AnalysisFix | None
    other_fixes: list[AnalysisFix] = []
    headline: str
    fix_ready: bool = False
    # The Full analysis page's facts (views.analysis_view).
    score: float | None = None
    verdict: ReportVerdictText | None = None
    fix_detail: AnalysisFixDetail | None = None
    plan_findings: list[PlanFinding] = []
    rewrites: list[RewriteTested] = []
    also: list[AlsoWorthDoing] = []
    readyset: ReadysetFacts | None = None
    benchmark: BenchmarkFacts | None = None
    run: AnalysisRunFacts | None = None
    rows_read: int | None = None
    rows_returned: int | None = None
    planner_cost: float | None = None
    calls_today: int | None = None
    params: list[AnalysisParam] = []
    param_source: str | None = None
    explain_text: str | None = None


class AnalyzeJob(BaseModel):
    id: int
    target: str
    query_key: str
    reason: Literal["new", "top15_stale", "user", "report"]
    state: Literal["queued", "running", "done", "failed", "skipped"]
    priority: float
    param_source: Literal["sampled", "table_sampled", "none", "estimated"] | None
    mode: Literal["analyze", "explain"] | None
    enqueued_at: int
    started_at: int | None
    finished_at: int | None
    error: str | None
    analysis_ref: str | None
    reason_text: str | None
    # Queue facts: running, today (it starts today) or held (it won't start on its own:
    # automatic analysis is off, signed out, or today's automatic analyses are done); null
    # once the job has ended. Nothing waits for a later day.
    queue_state: Literal["running", "today", "held"] | None = None
    # Why a held job waits, for links: sign_in means automatic analyses need a Readyset
    # sign-in, and `reason_text` says so.
    held_reason: HeldReason | None = None
    # 1-based place among the queued jobs that start today.
    queue_position: int | None = None
    estimated_start_at: int | None = None
    # Only on a click's response: the query was already waiting its turn (or running), so
    # the click left it where it was.
    already_queued: bool | None = None
    # Stopped by a cancel: it ended skipped and never became the query's latest analysis.
    cancelled: bool = False


class AnalyzeQueueEntry(AnalyzeJob):
    display_name: str


class MonitorWorkAnalysis(BaseModel):
    """One deep analysis in the all-databases work list."""
    id: int
    target: str
    query_key: str
    display_name: str
    state: Literal["queued", "running", "done", "failed", "skipped"]
    # you: the user clicked Analyze now; monitoring: queued by monitoring on its own.
    source: Literal["you", "monitoring"]
    enqueued_at: int
    started_at: int | None
    finished_at: int | None
    # 1-based place among the jobs its database starts today.
    queue_position: int | None = None
    # today: starts today.
    queue_state: Literal["today"] | None = None
    estimated_start_at: int | None = None
    # A finished analysis's efficiency score (0-100).
    efficiency_score: float | None = None


class MonitorWorkRetry(BaseModel):
    """An analysis the AI service failed; RDST queues it again at `retry_at`."""
    target: str
    query_key: str
    display_name: str
    attempts: int
    retry_at: int


class MonitorWorkReport(BaseModel):
    id: int
    target: str
    trigger: Literal["nightly", "user"]
    started_at: int
    message: str | None = None


class MonitorWork(BaseModel):
    """Monitoring's real work across every monitored database."""
    running: list[MonitorWorkAnalysis]
    # Waiting jobs in each database's start order.
    queued: list[MonitorWorkAnalysis]
    retrying: list[MonitorWorkRetry]
    # The latest analyses finished today (at most 25); done_today_count counts them all.
    done_today: list[MonitorWorkAnalysis]
    done_today_count: int
    reports: list[MonitorWorkReport]


class FirstHourRamp(BaseModel):
    started_at: int
    ends_at: int
    # How many of the heaviest reads it covers: the top N setting.
    top: int
    # Completed first-hour analyses.
    analyzed: int
    scheduled: int | None = None
    failed: int | None = None
    max: int
    # When it next checks the top list; null when the hour ends first.
    next_check_at: int | None
    # The first successful reading since the turn-on, and when its first query was stored.
    first_reading_at: int | None = None
    first_queries_at: int | None = None
    # Successful readings since the turn-on.
    readings: int = 0


class AnalyzeQueueSummary(BaseModel):
    cap: int
    used: int
    left: int
    # Part of the cap only user clicks and reports may start.
    reserved: int
    automatic_left: int
    # Local midnight, when the day's budget resets.
    reset_at: int
    average_run_s: int
    automatic_enabled: bool
    running: AnalyzeQueueEntry | None
    next: list[AnalyzeQueueEntry]
    # The last ten done or failed jobs, newest first.
    recent: list[AnalyzeQueueEntry]
    waiting_today: int
    held: int
    # The first monitored hour's analysis ramp; null outside it.
    first_hour: FirstHourRamp | None = None


class AnalyzeJobPage(BaseModel):
    jobs: list[AnalyzeJob]
    # Target-wide queue facts; only the target's analyze-jobs endpoint fills it.
    queue: AnalyzeQueueSummary | None = None


class MuteRule(BaseModel):
    id: int
    target: str | None
    query_key: str | None
    type: str | None
    until: int | None
    created_at: int
    note: str | None
    matched_count: int


class MuteRulePage(BaseModel):
    rules: list[MuteRule]


class DeletedMuteRule(BaseModel):
    deleted: int


class TrendPoint(BaseModel):
    ts: int
    value: float


class InboxLink(BaseModel):
    """Where an item opens: a query row and tab, the Database page, a report or a setting."""
    # `actions` opens Actions narrowed to `target` (?db=): the plan for Changes ready, Applied
    # for a verified change.
    kind: Literal["query", "database", "report", "settings", "configure", "sign_in", "setup",
                  "actions"]
    target: str
    label: str
    query_key: str | None = None
    tab: Literal["performance", "analysis", "readyset"] | None = None
    filter: str | None = None
    # A `database` link for several queries: the list shows exactly these.
    query_keys: list[str] | None = None
    report_id: int | None = None


class InboxAdvice(BaseModel):
    """Steps for work RDST can't do itself, or facts it can't see."""
    summary: str
    steps: list[str]


class AlertMeasurement(BaseModel):
    """What an alert measured, which way it moved and by how much."""
    metric: Literal["latency", "calls", "load", "share", "rollbacks", "cache_hit_ratio",
                    "connections", "running_time"]
    direction: Literal["up", "down"]
    before: float | None
    after: float | None
    change: float | None
    unit: str
    text: str


class AlertMember(BaseModel):
    query_key: str
    display_name: str
    before: float | None
    after: float | None
    change: float | None
    impact_ms: float


class IncidentQuery(BaseModel):
    """One member of a group item: when its own change began and ended."""
    query_key: str
    start: int | None
    end: int | None
    change: float | None
    # The member's own metric and its before and after levels (ms, or calls a second).
    metric: Literal["latency", "calls"]
    before: float | None
    after: float | None
    spark: list[TrendPoint]


class Incident(BaseModel):
    """When the item's change began and ended, measured from the per-query minute series
    (`measured`) or taken from the item's own times."""
    start: int
    end: int | None
    ongoing: bool
    measured: bool
    metric: Literal["latency", "calls"] | None
    change: float | None
    queries: list[IncidentQuery] = []


class RecurringNote(BaseModel):
    """How a query's bursts usually recur: every `period_s`, or daily ("daily")."""
    kind: Literal["period", "daily"]
    period_s: int
    repeats: int
    last_onset: int
    verdict: Literal["bigger", "longer"]


class Occurrence(BaseModel):
    """One occurrence of a repeating burst: its time frame, size and query count."""
    start: int
    end: int | None
    size: float | None
    query_count: int
    quiet: bool
    verdict: Literal["usual", "bigger"] | None


class InboxLikelyFix(BaseModel):
    id: int
    title: str
    type: str


MonitorLinkKind = Literal["inbox", "chart", "query", "queries", "live", "report", "analysis",
                          "database", "settings", "sign_in", "actions"]


class MonitorLink(BaseModel):
    """Where a Home or Inbox row leads; `label` is the destination word the row shows."""
    kind: MonitorLinkKind
    target: str | None
    label: str
    item_id: int | None = None
    query_key: str | None = None
    report_id: int | None = None
    analysis_ref: str | None = None


class InboxStep(BaseModel):
    title: str
    sql: str


class InboxFix(BaseModel):
    rank: int
    query_key: str | None
    title: str
    sql: str
    share: float
    latency_ms: float | None
    group: str | None


class InboxFixGroup(BaseModel):
    group: str
    table: str
    columns: list[str]
    count: int
    share: float


class InboxChanges(BaseModel):
    """A Changes ready event's plan in brief: how many changes, how many to do first, the top
    titles and their action ids (do-first ones lead)."""
    count: int
    do_first: int
    titles: list[str]
    action_ids: list[int]


class InboxHistoryEntry(BaseModel):
    ts: int
    text: str


class InboxRelated(BaseModel):
    label: str
    detail: str | None
    link: MonitorLink


class InboxItem(BaseModel):
    id: int
    target: str
    type: str
    subject: str
    state: Literal["new", "seen", "snoozed", "dismissed", "resolved"]
    severity: float
    title: str
    body_json: str
    created_at: int
    updated_at: int
    resolved_at: int | None
    resolved_by: Literal["user", "code", "jev"] | None
    seen_count: int
    reopened_count: int
    severity_band: Literal["act_now", "watch", "fyi", "recovered"]
    tone: Literal["negative", "warning", "positive", "neutral"]
    summary: str
    query_keys: list[str]
    subject_label: str
    group_key: str
    muted_by: int | None
    trend: list[TrendPoint]
    # Display names for the item's queries ("COUNT on customers"), keyed by query key.
    query_labels: dict[str, str] = {}
    delivery: Literal["digest", "inbox", "notify"] = "inbox"
    # When the item last pushed; null when it never did (a skipped push stays null).
    notified_at: int | None = None
    relevance_rule: Literal["top_10_24h", "top_10_1h", "share_24h", "share_1h",
                            "top_10_calls_24h", "top_10_calls_1h", "big_change",
                            "new_heavy"] | None = None
    # Queries that moved in the same window but carry too little load to be listed.
    smaller_moved: int = 0
    # A member's usual schedule, when this burst was bigger or longer than usual.
    recurring: RecurringNote | None = None
    # A repeating burst's occurrences, newest first; empty for every other item.
    occurrences: list[Occurrence] = []
    link: InboxLink
    advice: InboxAdvice | None = None
    measurement: AlertMeasurement | None = None
    # Group alerts: the three members with the most impact, and the median change.
    members: list[AlertMember] = []
    median_change: float | None = None
    # The incident window the Database page zooms to; null for reports and sign-in items.
    incident: Incident | None = None
    # Where the item stands, from the readings its time frame shows (a repeating burst by its
    # latest occurrence), and when it got there. An open item back to normal reads
    # `recovered`, never Act now.
    status: Literal["ongoing", "back_to_normal", "closed", "unknown"] = "unknown"
    status_at: int | None = None
    # Why it's in its section, one line: "Act now: latency up 3.2x on a top-10 query today,
    # still happening".
    band_reason: str = ""
    # The item's kind and band, a stuck query's steps with its pid, a fixes item's ranked
    # fixes, a confirmed fix's before and after, the item's own log and its related rows.
    # fixes_ready is no longer filed (Changes ready replaces it); older rows still read.
    kind: Literal["stuck_query", "fixes_ready", "report_ready", "fix_confirmed", "traffic_spike",
                  "monitoring_issue", "changes_ready", "no_change_yet", "other"] = "other"
    band: Literal["act_now", "watch", "closed"] = "watch"
    pid: int | None = None
    running_s: float | None = None
    steps: list[InboxStep] = []
    fixes: list[InboxFix] = []
    fix_group: InboxFixGroup | None = None
    fixes_share: float | None = None
    source_report_id: int | None = None
    before_ms: float | None = None
    after_ms: float | None = None
    calls: int | None = None
    index_name: str | None = None
    # Changes ready: the plan in brief.
    changes: InboxChanges | None = None
    # Fix confirmed and No change yet from the actions check: the applied action measured.
    action_id: int | None = None
    history: list[InboxHistoryEntry] = []
    related: list[InboxRelated] = []
    # Likely fix: the open action that covers one of the item's queries, best score first; for
    # a database-wide item (connections, locks, cache hit), the action its subject maps to.
    action: InboxLikelyFix | None = None


class InboxPage(BaseModel):
    items: list[InboxItem]


class ReportSummaryCounts(BaseModel):
    fix_first: int
    one_fix: int
    health_warn: int
    health_ok: int


class ReportSummary(BaseModel):
    id: int
    ts: int
    trigger: Literal["user", "nightly"]
    duration_s: int
    headline: str
    verdict: str = ""
    counts: ReportSummaryCounts
    provisional_count: int
    # The local day the snapshot covers (a partial day is "today so far").
    period_start: int | None = None
    period_end: int | None = None
    period_partial: bool = False
    day: str = ""
    # Synthetic demo history, never real.
    sample: bool = False
    events: int | None = None
    open_events: int | None = None
    look_at: int | None = None
    health_problems: int | None = None
    qps: float | None = None


class ReportHistory(BaseModel):
    reports: list[ReportSummary]


class ReportHealthCheck(BaseModel):
    check: str
    label: str
    value: str
    status: Literal["ok", "warn", "bad", "info"]
    note: str
    # What to do about it; findings that matter are warn or bad.
    action: str | None = None
    matters: bool = False


class ReportFixFirst(BaseModel):
    query_key: str
    title: str
    why: str
    action: str
    display_name: str = ""
    share: float = 0.0
    db_time_ms: float = 0.0
    calls: float = 0.0
    mean_ms: float | None = None
    # The analysis fix that addresses this finding (never one for a different problem).
    fix: AnalysisFix | None = None
    fix_sql: str | None = None
    expected_gain: str | None = None
    analysis_ref: str | None = None
    analysis: DeepAnalysisDigest | None = None


class ReportOneFix(BaseModel):
    table: str
    columns: list[str]
    query_keys: list[str]
    action: str
    share: float = 0.0
    # What the shared fix is, from its own action text, and a title that matches it.
    kind: Literal["index", "rewrite", "config", "other"] = "other"
    title: str = ""


class ReportVerdict(BaseModel):
    sentence: str
    query_key: str | None
    share: float | None
    tone: Literal["negative", "warning", "positive", "neutral"]


class SinceYesterday(BaseModel):
    label: str
    query_key: str | None
    metric: Literal["load", "calls", "latency", "share"]
    before: float
    after: float
    change: float | None
    unit: Literal["ms", "calls", "ms_per_call", "ratio"]


class DigestEntry(BaseModel):
    """A finding that waited for the report: digest level, or over the daily push limit."""
    id: int
    type: str
    title: str
    summary: str
    query_keys: list[str]
    subject_label: str
    ts: int
    reason: Literal["digest", "push_limit"]


class SnapshotPeriod(BaseModel):
    start: int
    end: int
    partial: bool
    day: str
    prior_start: int | None = None
    prior_end: int | None = None
    # When recording began partway through the comparison day.
    prior_observed_from: int | None = None
    observed_from: int | None = None
    observed_to: int | None = None
    # Older reports covered the 24 h before they ran.
    rolling: bool = False


class SnapshotNumber(BaseModel):
    metric: Literal["qps", "mean_ms", "p95_ms", "active_conns_peak", "cache_hit_ratio_min",
                    "xact_rollback", "deadlocks"]
    label: str
    unit: Literal["qps", "ms", "count", "ratio"]
    value: float | None
    prior: float | None
    change: float | None
    # Which way is good; null when neither is.
    better: Literal["up", "down"] | None


class SnapshotBand(BaseModel):
    key: str
    kind: Literal["query", "other"]
    display_name: str
    color_index: int | None
    share: float
    calls: float
    qps: float
    mean_ms: float | None


class SnapshotMarker(BaseModel):
    ts: int
    kind: Literal["event", "index"]
    label: str
    query_keys: list[str] = []
    tone: str | None = None


class SnapshotChart(BaseModel):
    """Queries per second by query, frozen when the report was written."""
    res: int
    start: int
    end: int
    xs: list[int]
    bands: list[SnapshotBand]
    qps: list[list[float | None]]
    markers: list[SnapshotMarker]
    # Per band, per x: database milliseconds and average latency; overall average latency.
    ms: list[list[float | None]] | None = None
    mean_ms: list[list[float | None]] | None = None
    overall_mean_ms: list[float | None] | None = None


class SnapshotEvent(BaseModel):
    ts: int
    kind: Literal["created", "worse", "reopened", "resolved", "index"]
    item_id: int | None
    type: str
    what: str
    title: str
    detail: str
    query_keys: list[str]
    state: Literal["open", "resolved", "dismissed", "done", "unknown"]
    resolved_at: int | None = None
    tone: str | None = None
    delivery: str | None = None
    definition: str | None = None


class SnapshotFix(BaseModel):
    kind: str
    title: str
    sql: str | None = None


class SnapshotQueryNote(BaseModel):
    query_key: str
    title: str
    why: str
    action: str = ""


class SnapshotQuery(BaseModel):
    query_key: str
    display_name: str
    calls: float | None
    qps: float | None
    total_ms: float
    mean_ms: float | None
    share: float
    prior_calls: float | None
    prior_mean_ms: float | None
    latency_change: float | None
    status: Literal["needs_attention", "watch", "fix_ready", "healthy", "not_analyzed"]
    status_reason: str | None
    score: float | None
    grade: Literal["A", "B", "C", "D", "F"] | None
    fix: SnapshotFix | None
    # The complete stored deep analysis, rendered in full on expand.
    analysis_ref: str | None
    analysis_mode: str | None
    analyzed_at: int | None
    headline: str | None
    provisional: bool
    new: bool
    text: str | None = None
    # The deep analysis findings (full text), and the report's note on this query.
    findings: list[str] = []
    note: SnapshotQueryNote | None = None


class SnapshotHealthRow(BaseModel):
    name: str
    value: str
    detail: str


class SnapshotHealthProblem(BaseModel):
    check: str
    label: str
    status: Literal["warn", "bad"]
    summary: str
    rows: list[SnapshotHealthRow]
    action: str | None
    sql: str | None


class SnapshotHealthGap(BaseModel):
    check: str
    label: str
    reason: str


class SnapshotHealth(BaseModel):
    problems: list[SnapshotHealthProblem]
    fine: list[str]
    unavailable: list[SnapshotHealthGap]


class SnapshotEvidence(BaseModel):
    label: str
    value: str


class SnapshotSetting(BaseModel):
    name: str
    current: str
    target: str


class SnapshotAction(BaseModel):
    """A database-level change (settings, vacuum, indexes, connections), with its evidence."""
    id: str
    check: str
    title: str
    severity: Literal["bad", "warn", "info"]
    finding: str
    evidence: list[SnapshotEvidence]
    why: str
    steps: str
    rows: list[SnapshotHealthRow] = []
    sql: str | None = None
    setting: SnapshotSetting | None = None
    restart: bool = False
    caveat: str | None = None
    query_keys: list[str] = []
    # The narrative's rank (0 first) and its reason for this day; null when not picked.
    pick: int | None = None
    model_why: str | None = None


class SnapshotCheck(BaseModel):
    check: str
    label: str
    status: Literal["ok", "warn", "bad", "unknown"]
    value: str


class SnapshotOverview(BaseModel):
    status: Literal["ok", "warn", "bad"]
    label: str
    headline: str
    checks: list[SnapshotCheck]


class SnapshotVitals(BaseModel):
    """Database-wide readings over the day, one per x."""
    res: int
    xs: list[int]
    qps: list[float | None]
    mean_ms: list[float | None]
    active_conns: list[float | None]
    cache_hit_ratio: list[float | None]
    xact_rollback: list[float | None]
    deadlocks: list[float | None]


class SnapshotSpikeQuery(BaseModel):
    query_key: str
    before: float
    during: float
    ratio: float | None


class SnapshotSpikeEvent(BaseModel):
    ts: int
    what: str
    title: str


class SnapshotSpike(BaseModel):
    id: str
    metric: Literal["traffic", "latency"]
    start: int
    end: int
    peak_ts: int
    peak: float
    usual: float
    ratio: float | None
    queries: list[SnapshotSpikeQuery]
    events: list[SnapshotSpikeEvent]
    summary: str


class SnapshotBriefItem(BaseModel):
    topic: str | None = None
    # A sentence; queries appear as {{q:key}} tokens.
    text: str
    # One plain line on what it means for the application and why it matters.
    why: str | None = None


class SnapshotFact(BaseModel):
    kind: str
    # A sentence; queries appear as {{q:key}} tokens.
    text: str
    keys: list[str]


class DaySnapshot(BaseModel):
    """The daily snapshot (report version 3): one local day, frozen when written."""
    version: int
    legacy: bool = False
    period: SnapshotPeriod
    numbers: list[SnapshotNumber]
    peak: dict[str, float | None]
    chart: SnapshotChart | None
    events: list[SnapshotEvent]
    queries: list[SnapshotQuery]
    look_at: list[SnapshotQuery]
    health: SnapshotHealth
    names: dict[str, str]
    facts: list[SnapshotFact]
    # The day in brief: the narrative's wording when it keeps to the facts, else the facts.
    brief: list[str]
    brief_source: Literal["model", "facts"]
    brief_items: list[SnapshotBriefItem] = []
    overview: SnapshotOverview | None = None
    actions: list[SnapshotAction] = []
    vitals: SnapshotVitals | None = None
    spikes: list[SnapshotSpike] = []
    engine: str = "postgresql"


class ReportBody(BaseModel):
    summary: str
    fix_first: list[ReportFixFirst]
    one_fix_several_queries: list[ReportOneFix]
    health: list[ReportHealthCheck]
    changed_since_yesterday: list[str]
    provisional_query_keys: list[str]
    narrative_error: str | None
    narrative_retry_at: int | None = None
    digest: list[DigestEntry] = []
    version: int = 1
    verdict: ReportVerdict | None = None
    since_yesterday: list[SinceYesterday] = []
    snapshot: DaySnapshot | None = None


class ReportProgress(BaseModel):
    message: str
    done: int
    total: int


class QueryRef(BaseModel):
    display_name: str
    text_snippet: str


class AnalyzedQuery(BaseModel):
    key: str
    display_name: str
    db_time_share: float
    state: Literal["queued", "running", "done", "skipped", "failed", "provisional"]
    result_line: str | None
    reason: str | None
    headline: str | None = None
    error_kind: str | None = None


class ReportDayTile(BaseModel):
    metric: Literal["qps", "mean_ms", "connections", "cache_hit"]
    label: str
    value: float | None
    unit: Literal["qps", "ms", "count", "ratio"]
    detail: str | None
    state: Literal["ok", "warn", "bad", "collecting"]
    reason: str | None
    # Connections: the server's limit ("8 of 100").
    limit: float | None = None


class ReportDay(BaseModel):
    label: str
    tiles: list[ReportDayTile]
    chart: SnapshotChart | None
    spikes: list[SnapshotSpike]
    query_count: int
    # False when the day before has no readings: nothing is compared against it.
    compared: bool = True


class ReportFixItem(BaseModel):
    rank: int | None
    title: str
    detail: str | None
    sql: str | None
    share: float | None
    state: Literal["ready", "needs_attention", "worth_doing", "fixed", "smaller"]
    kind: Literal["index", "config", "rewrite", "other"]
    group: str | None
    query_keys: list[str]
    restart: bool = False
    fixed_at: int | None = None
    before_ms: float | None = None
    after_ms: float | None = None


class ReportHealthRow(BaseModel):
    check: str
    label: str
    state: Literal["bad", "warn", "ok", "unavailable"]
    finding: str
    detail: str | None
    fix_rank: int | None
    # Every warning ("Needs a look") and worse needs you.
    needs_you: bool = False
    # Actions lists a change for this warning; false when only the report shows it.
    in_actions: bool = False


class ReportRun(BaseModel):
    number: int
    trigger: Literal["nightly", "on_request"]
    ran_at: int
    model: str | None
    analyses: int
    rewrites: int


class DetailedTile(BaseModel):
    """One of the Detailed view's six numbers with the day before's. Rollbacks and deadlocks
    share a tile: `value` counts rollbacks, `second` deadlocks."""
    metric: Literal["qps", "mean_ms", "p95_ms", "connections", "cache_hit", "rollbacks_deadlocks"]
    label: str
    value: float | None
    prior: float | None
    change: float | None
    unit: Literal["qps", "ms", "count", "ratio"]
    state: Literal["ok", "warn", "bad", "collecting"]
    # Connections: the server's limit ("8 of 100").
    limit: float | None = None
    second: float | None = None
    second_prior: float | None = None
    # "No data for the day before" when there is nothing to compare with.
    prior_note: str | None = None


class DetailedStretchQuery(BaseModel):
    """A query behind a busy stretch: its share of the stretch's time (busiest stretches), or
    its level before and during (spikes)."""
    query_key: str
    display_name: str
    share: float | None
    before: float | None
    during: float | None
    ratio: float | None


class DetailedStretch(BaseModel):
    """A spike the day crossed (`spike`), or when none did, one of its two busiest stretches
    (`busiest`), with the queries behind it and the rollbacks inside it."""
    kind: Literal["spike", "busiest"]
    metric: Literal["traffic", "latency"] | None
    start: int
    end: int
    peak: float | None
    usual: float | None
    ratio: float | None
    summary: str | None
    queries: list[DetailedStretchQuery]
    rollbacks: float | None


class WhatChangedRow(BaseModel):
    """A query against the day before, by average time per call. `plan_changed` rows carry
    the filing's time and detail instead of numbers."""
    kind: Literal["slower", "faster", "new_heavy", "plan_changed"]
    query_key: str | None
    display_name: str
    share: float | None
    before_ms: float | None
    after_ms: float | None
    change: float | None
    detail: str | None
    ts: int | None


class ConfirmedFixRow(BaseModel):
    """A fix measured as working in the report's day."""
    title: str
    applied_at: int | None
    confirmed_at: int | None
    before_ms: float | None
    after_ms: float | None
    query_keys: list[str]


class HealthCheckRow(BaseModel):
    """Every health check, worst first: its finding, the one number it shows and the Fix first
    rank that addresses it."""
    check: str
    label: str
    state: Literal["bad", "warn", "ok", "unavailable"]
    finding: str
    number: str | None
    fix_rank: int | None
    # Every warning ("Needs a look") and worse needs you.
    needs_you: bool = False
    # Actions lists a change for this warning; false when only the report shows it.
    in_actions: bool = False


class SettingsAuditRow(BaseModel):
    """A setting off the recommendation and when a change takes effect; `in_actions` when
    the database shows the problem it fixes, so Actions lists the change."""
    parameter: str
    current: str
    recommended: str
    takes_effect: Literal["after a restart", "on reload"]
    note: str | None
    in_actions: bool = False


class IndexChangeSeen(BaseModel):
    """An index added or dropped in the day; `from_rdst` when an RDST action named it, with
    that action's measuring status, and reads since for an added one."""
    ts: int
    kind: Literal["added", "dropped"]
    index_name: str
    table: str
    definition: str
    from_rdst: bool
    action_id: int | None
    status: Literal["measuring", "verified", "no_change", "undone"] | None
    scans: int | None


class RdstDidRow(BaseModel):
    """One timed line of RDST's own day; `until` ends a stretch (an analysis batch, the
    checks that ran all day)."""
    ts: int
    until: int | None
    text: str
    detail: str | None
    item_id: int | None
    report_id: int | None


class DetailedTotals(BaseModel):
    queries_seen: int
    # Minute readings the day kept.
    readings: int
    data_kept_days: int
    data_bytes: int
    report_number: int
    ran_at: int


class ReportDetailed(BaseModel):
    """GET report?view=detailed: the sections Detailed adds, in report-detailed.html order.
    Verdict, the day and Fix first stay in the top-level fields both views share."""
    tiles: list[DetailedTile]
    spikes: list[DetailedStretch]
    # False when the day before has no readings: What changed compares nothing.
    compared: bool = True
    what_changed: list[WhatChangedRow]
    fixes_confirmed: list[ConfirmedFixRow]
    health_checks: list[HealthCheckRow]
    settings_audit: list[SettingsAuditRow]
    index_changes_seen: list[IndexChangeSeen]
    what_rdst_did: list[RdstDidRow]
    totals: DetailedTotals


class DatabaseReport(BaseModel):
    id: int
    target: str
    ts: int
    trigger: Literal["user", "nightly"]
    state: Literal["running", "done", "failed", "cancelled"]
    provisional: bool
    finished_at: int | None = None
    progress: ReportProgress | None
    report_json: ReportBody | None
    refs: dict[str, QueryRef]
    queries: list[AnalyzedQuery] = []
    period_start: int | None = None
    period_end: int | None = None
    period_partial: bool = False
    sample: bool = False
    # The report as the Report page reads it (report_v2.view).
    verdict: ReportVerdictText | None = None
    day: ReportDay | None = None
    fix_first: list[ReportFixItem] = []
    health: list[ReportHealthRow] = []
    health_fine: list[str] = []
    run: ReportRun | None = None
    narrative_missing: bool = False
    # "Copy all N as SQL": the Fix first rows that carry SQL, on both views.
    copy_count: int = 0
    # Present with view=detailed.
    detailed: ReportDetailed | None = None


class NotifyEvents(BaseModel):
    database_unreachable: bool = True
    database_recovered: bool = False
    monitoring_setup: bool = True
    stuck_query: bool = True
    database_events: bool = True
    query_changes: bool = True
    daily_report: bool = True


class QuietHours(BaseModel):
    """Pushes are held between these local hours; equal hours mean no quiet hours."""
    from_hour: int
    to_hour: int


AIMonitoringState = Literal["off", "on", "local", "pending", "paused"]
AIMonitoringReason = Literal["signed_out", "unreachable", "service_error", "limit", "quota"]


class MonitorSettings(BaseModel):
    enabled: bool
    # Whether monitoring or its analysis preferences have been saved for this target.
    configured: bool = False
    # Why monitoring can't be turned on for this target (an Aurora reader), or null.
    unavailable_reason: str | None = None
    deep_analysis: bool
    execute: bool
    # Consent to run queries: plans_only (EXPLAIN, the default) or execute (EXPLAIN ANALYZE).
    analysis_mode: Literal["plans_only", "execute"]
    auto_analyze_new: bool
    # "Deeply analyze the top N queries per day": the top N, and the daily re-analyses.
    analysis_daily_cap: int
    # "Analyze up to M new top queries per day": queries entering the top N for the first time.
    newcomer_daily_cap: int = 25
    attention_every_min: Literal[5, 15, 60]
    nightly_report: bool
    nightly_hour: int
    push_daily_cap: int
    # What pushes a notification; switched-off events still reach the Inbox. Database
    # unreachable is never held back by push_daily_cap.
    notify_events: NotifyEvents
    quiet_hours: QuietHours = QuietHours(from_hour=23, to_hour=7)
    # Dev/demo lever; null means the default learning period applies.
    dev_learning_period_s: int | None = None
    # When monitoring was last turned on from the app.
    enabled_at: int | None = None
    # The first-hour start, or null when the enable has earlier recorded history.
    first_hour_started_at: int | None = None
    size_bytes: int
    budget_bytes: int
    # on: the account holds an AI monitoring slot for it. local: the account was full, so
    # only local monitoring runs. pending: the Key Service couldn't be asked yet. paused:
    # the account's AI trial limit is used up. off: monitoring is off.
    ai_monitoring: AIMonitoringState = "off"
    # Why AI monitoring isn't on, and since when; see MonitoringLimitTarget.
    ai_reason: AIMonitoringReason | None = None
    ai_since: int | None = None


class MonitoringLimitTarget(BaseModel):
    target: str
    ai: AIMonitoringState
    # Why AI monitoring isn't on: signed_out, unreachable (Key Service didn't answer),
    # service_error, limit (every slot in use) or quota (trial allowance used up). Null when
    # AI is on, or it's starting with no known cause yet.
    reason: AIMonitoringReason | None = None
    # When the current AI state began (epoch seconds).
    since: int | None = None


class MonitoringSlotElsewhere(BaseModel):
    """A slot the account holds for a database this computer doesn't have."""
    fingerprint: str
    tier: Literal["hosted", "own_key"] = "hosted"
    last_seen_at: int | None = None
    #: The name the database had where it's monitored, when that client sent one.
    label: str | None = None


class MonitoringSlotRelease(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class MonitoringLimit(BaseModel):
    """AI monitoring on `in_use` of `limit` databases for the signed-in account."""
    in_use: int
    # Null when the Key Service couldn't be asked and hasn't answered before.
    limit: int | None
    # The user's own Anthropic key pays for deep analysis and reports (the larger limit).
    own_key: bool
    known: bool
    full: bool
    # Signed in to a Readyset account; AI monitoring needs one.
    signed_in: bool = False
    # AI work paused because the account's trial limit is used up: jev (AI checks) and/or
    # hosted (deep analysis and reports). Resumes by itself once the limit is raised.
    allowance: list[Literal["jev", "hosted"]] = []
    # Who to contact to raise a limit.
    contact: str = ""
    # With ?target=: whether turning that database on would get AI monitoring.
    target_fits: bool | None = None
    targets: list[MonitoringLimitTarget]
    # Slots held for databases this computer doesn't have; each can be released here.
    elsewhere: list[MonitoringSlotElsewhere] = []


class UsageDay(BaseModel):
    date: str
    jev_calls: int
    analyses: int
    reports: int
    billable_calls: int
    notifications: int = 0


class UsageCaps(BaseModel):
    jev_daily: int
    analysis_daily: int
    push_daily: int = 3


class MonitorUsage(BaseModel):
    days: list[UsageDay]
    caps: UsageCaps


class LibraryEntry(BaseModel):
    hash: str
    created: bool


class AddedQuery(BaseModel):
    """A query added to the list from "Analyze a query", keyed like monitored queries."""
    key: str
    created: bool
    job: AnalyzeJob | None = None


class HomeStatus(BaseModel):
    """needs_you: an Act now item is open. quiet: attention checks ran in the last 3 cycles
    and nothing is open. paused: charts and stuck-query alerts run, AI checks do not. off:
    no database is monitored."""
    state: Literal["needs_you", "quiet", "paused", "off"]
    target: str | None
    needs_you: int
    to_watch: int
    headline: str | None
    link: MonitorLink | None
    checks_ran_at: int | None
    since: int | None
    last_reading_at: int | None
    next_report_at: int | None
    watching: int
    # Counted apart: Act now items in the Inbox, Do first changes in Actions. `target` names
    # the first configured database when none is monitored.
    act_now: int = 0
    do_first: int = 0


class HomeLogEntry(BaseModel):
    ts: int
    text: str
    detail: str | None
    link: MonitorLink
    band: Literal["act_now", "watch"] | None = None


class HomeDatabase(BaseModel):
    target: str
    connection: str
    state: Literal["watching", "cant_reach", "ai_paused", "not_monitored"]
    state_since: int | None
    qps: float | None
    open_items: int
    act_now: int
    # Queries an open index or rewrite action speeds up.
    fixes_ready: int
    # The database's open Changes ready event.
    fixes_item_id: int | None
    # Open actions, and how many are Do first.
    to_do: int = 0
    do_first: int = 0


class HomeStripRow(BaseModel):
    """One database in Home's "Across your databases" strip, in attention order."""
    target: str
    state: Literal["watching", "cant_reach", "ai_paused", "not_monitored"]
    act_now: int
    to_do: int
    do_first: int
    fixes_ready: int
    activity: DatabaseActivity | None = None
    # The first pass of analyses is still building its first plan.
    building: bool = False


class HomeSparkPoint(BaseModel):
    ts: int
    qps: float | None


class HomeDayCard(BaseModel):
    target: str
    qps: float | None
    qps_peak: float | None
    qps_peak_at: int | None
    mean_ms: float | None
    mean_ms_delta: float | None
    fixes_ready: int
    fixes_share: float | None
    fixes_item_id: int | None
    cache_hit_ratio: float | None
    cache_hit_reason: str | None
    series: list[HomeSparkPoint]
    report_id: int | None
    report_day: str | None


class HomeAnalyzed(BaseModel):
    target: str
    query_key: str
    display_name: str
    analyzed_at: int
    analysis_ref: str | None
    outcome: Literal["fix_suggested", "healthy", "failed"]


class HomeSinceYesterday(BaseModel):
    opened_closed: int
    fixes_confirmed: int


class HomeModel(BaseModel):
    """GET /home: the status line, the day card, every database and today's log."""
    status: HomeStatus
    day: HomeDayCard | None
    databases: list[HomeDatabase]
    databases_strip: list[HomeStripRow] = []
    not_monitored: int
    today: list[HomeLogEntry]
    since_yesterday: HomeSinceYesterday
    recently_analyzed: list[HomeAnalyzed]
    generated_at: int


def documented(model: type[BaseModel]) -> dict:
    """Route kwargs that publish `model` in OpenAPI without filtering the response."""
    return {"responses": {200: {"model": model}}}


ActionType = Literal["add_index", "drop_index", "rewrite_query", "change_setting",
                     "run_maintenance", "try_caching", "index_plan", "settings", "maintenance"]
ActionApply = Literal["run_sql", "setting", "config_restart", "app_change", "readyset"]


class ActionQuery(BaseModel):
    key: str
    name: str


class ActionWindow(BaseModel):
    observed_s: int
    counter_age_s: int


ActionState = Literal["open", "snoozed", "dismissed", "applied", "confirmed", "no_effect",
                      "retired", "undone"]
ActionTier = Literal["first", "then", "later"]
# What applying a change risks: a restart, every write on a write-heavy table, table locks.
ActionRisk = Literal["none", "restart", "locks", "writes"]
# What applying it takes: copy and run SQL, change a setting, or change application code.
ActionEffort = Literal["copy_sql", "setting", "code"]


class ActionCard(BaseModel):
    id: int | None
    key: str
    type: ActionType
    state: ActionState
    score: float
    risk: ActionRisk = "none"
    effort: ActionEffort = "copy_sql"
    # Database time it saves a minute (actions.saves_ms_per_min).
    saves_ms_per_min: float = 0.0
    # Its Do first / Then / Later tier while open; `priority` keeps the checkup's order.
    tier: ActionTier | None = None
    title: str
    summary: str
    note: str | None
    why: str | None
    apply: ActionApply
    sql: str | None
    restart: bool
    confidence: str | None
    queries: list[ActionQuery]
    opened_at: int
    updated_at: int
    snoozed_until: int | None
    children: list[ActionCard]
    detail: dict | None
    window: ActionWindow | None
    steps: str | None = None
    evidence: list[dict] = []
    rows: list[dict] = []
    priority: int | None
    jev: float
    # How much it matters, on one scale for every kind; colors the card.
    severity: Literal["low", "medium", "high"]
    category: Literal["indexes", "settings", "maintenance", "rewrites", "caching"]
    # Average latency of the queries it served, before and after the fix, once measured.
    result: dict[str, float] | None = None


class Coverage(BaseModel):
    """Where one kind of change stands: changes found, checked and clean, being checked, or
    waiting on deep analysis."""
    state: Literal["found", "checked", "checking", "waiting"]
    checked_at: int | None
    note: str
    # When this kind is checked again.
    next_at: int | None
    # While checking, what it's waiting on, when that's worth saying.
    waiting_on: str | None = None


class CheckupState(BaseModel):
    state: Literal["none", "first_pass", "running", "done"]
    analyzed: int
    total: int
    due_by: int | None
    report_id: int | None
    finished_at: int | None
    # sign_in while the first pass's analyses wait for a Readyset sign-in; `held_text` is
    # the sentence to show with the sign-in link.
    held: Literal["sign_in", "ai_paused"] | None = None
    held_text: str | None = None
    # With ai_paused: limit when the account's AI slots are all in use, quota when its AI
    # usage ran out.
    ai_reason: Literal["limit", "quota"] | None = None


class StoryFact(BaseModel):
    label: str
    value: str


class SectionStory(BaseModel):
    """The explanation at the top of an Actions section, from what RDST measured."""
    headline: str
    # What to do and why, in a sentence or two.
    lead: list[str]
    # The technical detail behind it: what RDST watched and measured.
    details: list[str]
    facts: list[StoryFact]


class ActionsPage(BaseModel):
    sections: dict[str, SectionStory]
    coverage: dict[str, Coverage]
    target: str
    computed_at: int | None
    card_count: int
    change_count: int
    checkup: CheckupState
    actions: list[ActionCard]
    more: list[ActionCard]
    open_count: int
    recently_done: list[ActionCard]
    snoozed: int
    dismissed: int
    set_aside: list[ActionCard]
    window: ActionWindow | None


ActionKind = Literal["index", "setting", "maintenance", "rewrite", "caching"]


class PlanQuery(BaseModel):
    """A query a change speeds up: its share of the database's time over the plan's window,
    its average latency there, and the latency expected after the change when the plan knows
    the gain."""
    key: str
    name: str
    share: float
    mean_ms: float | None
    expected_ms: float | None


class PlanCard(BaseModel):
    """One action on the global Actions page. `sql` applies it and `rollback_sql` reverses it
    (migration templates are built client-side from both). `before_ms`/`after_ms` are the
    measured averages once the actions check has verified it."""
    id: int
    key: str
    database: str
    type: Literal["add_index", "drop_index", "rewrite_query", "change_setting",
                  "run_maintenance", "try_caching"]
    kind: ActionKind
    state: ActionState
    # Open cards: Do first / Then / Later, and the 1-based rank inside the returned list.
    priority: ActionTier | None
    rank: int | None
    risk: ActionRisk
    effort: ActionEffort
    saves_ms_per_min: float
    score: float
    title: str
    summary: str
    note: str | None
    why: str | None
    apply: ActionApply
    sql: str | None
    rollback_sql: str | None
    restart: bool
    confidence: str | None
    table: str | None
    columns: list[str]
    index_name: str | None
    setting: dict | None
    queries: list[PlanQuery]
    # The queries' combined share of database time.
    share: float
    before_ms: float | None
    after_ms: float | None
    # An index add's write cost (writes_per_s, peak_writes_per_s, existing_indexes, ...).
    write_cost: dict | None
    steps: str | None
    evidence: list[dict]
    rows: list[dict]
    # A rewrite's original query text, shown beside the rewrite.
    original: str | None = None
    opened_at: int
    updated_at: int
    set_aside_until: int | None
    closed_at: int | None
    # The user's reason for a set aside, a dismissal or an undo.
    reason: str | None
    # The checkup's own order, when it ranked this action.
    checkup_rank: int | None


class ActionsCounts(BaseModel):
    """What each filter choice would show with the others kept: by database across every
    database, by kind and tier inside the chosen database."""
    by_database: dict[str, int]
    by_kind: dict[str, int]
    first: int
    then: int
    later: int
    total: int


class ActionsDatabase(BaseModel):
    """A database in the Actions filter: its open changes and how many are Do first."""
    database: str
    monitored: bool
    to_do: int
    do_first: int
    # Where its checkups stand; null for a database that isn't monitored.
    checkup: CheckupState | None = None


class ReportCheckOnly(BaseModel):
    """A check the report flags that has no change on Actions (a setting merely off a rule
    of thumb, an index with no reads in the report's window)."""
    check: str
    label: str
    finding: str


class ReportChecks(BaseModel):
    """The latest report's health checks: `needs_you` of `checks` ("2 of 8 checks need
    you"), and the ones needing you that only the report lists."""
    report_id: int
    checks: int
    needs_you: int
    not_in_actions: list[ReportCheckOnly]


class GlobalActionsPage(BaseModel):
    """GET /actions?db=: the plan across databases (decision 6). `cards` holds the open
    actions ranked Do first, Then, Later, each tier by time saved a minute; `set_aside` and
    `dismissed` list the held ones for Bring back."""
    db: str | None
    databases: list[ActionsDatabase]
    unmonitored: list[str]
    # When the newest plan in scope was computed, and how much traffic it read (seconds).
    updated_at: int | None
    observed_s: int | None
    counts: ActionsCounts
    cards: list[PlanCard]
    set_aside: list[PlanCard]
    dismissed: list[PlanCard]
    # Changes applied in the last 30 days, for the Applied scope's count.
    applied_count: int
    # Per database: indexes the plan keeps, and smaller wins left out because writes outweigh.
    keep: dict[str, int]
    left_out: dict[str, int]
    # Per database with a finished report: its health checks as the report counts them.
    report_checks: dict[str, ReportChecks] = {}


class AppliedQuery(BaseModel):
    """A query an applied change was meant to speed up: average latency over the 7 days
    before and since the change settled, and calls since."""
    key: str
    name: str
    before_ms: float | None
    after_ms: float | None
    calls_after: int


class AppliedRow(BaseModel):
    """One applied change. `by` is user when you said you did it, detected when RDST saw it
    land; `source` is how it closed (index_added, index_dropped, setting_changed,
    query_replaced, cleared, user). Measuring rows say when RDST checks next."""
    id: int
    key: str
    database: str
    type: str
    kind: ActionKind
    title: str
    applied_at: int
    by: Literal["user", "detected"]
    source: str | None
    status: Literal["measuring", "verified", "no_change", "undone"]
    queries: list[AppliedQuery]
    before_ms: float | None
    after_ms: float | None
    calls_after: int
    verified_at: int | None
    next_check_at: int | None
    rollback_sql: str | None
    saved_ms_per_min: float | None
    undone_at: int | None
    reason: str | None


class AppliedDay(BaseModel):
    """Applied rows of one local day, newest first; `day` is its ISO date."""
    start: int
    day: str
    rows: list[AppliedRow]


class DismissedRow(BaseModel):
    id: int
    database: str
    type: str
    kind: ActionKind
    title: str
    dismissed_at: int
    reason: str | None


class AppliedTotals(BaseModel):
    """The last 7 days: changes applied by status, and the database time the verified ones
    save a minute, also as a share of all database time."""
    changes_this_week: int
    verified: int
    measuring: int
    no_change: int
    saved_ms_per_min: float
    saved_share: float | None


class AppliedPage(BaseModel):
    """GET /actions/applied?db=: the last 30 days of applied changes by day, the undone and
    dismissed ones with their reasons, and the week's totals."""
    db: str | None
    since: int | None
    days: list[AppliedDay]
    undone: list[AppliedRow]
    dismissed: list[DismissedRow]
    totals: AppliedTotals


class ActionsOpened(BaseModel):
    """POST actions/opened: whether an open Changes ready event closed."""
    target: str
    closed: bool


class IndexPlanHelp(BaseModel):
    key: str
    name: str | None
    share: float
    gain: float
    verified: bool
    gain_pct: float | None
    impact: str | None


class IndexWriteCost(BaseModel):
    writes_per_s: float
    peak_writes_per_s: float
    existing_indexes: int
    updated_columns: list[str]
    hot_risk: bool


class IndexPlanAdd(BaseModel):
    id: str
    table: str
    replaces: list[str]
    columns: list[str]
    method: str
    # How the queries use each column: equality, sort or range (None when unknown).
    roles: list[Literal["equality", "sort", "range"] | None] = []
    merged: list[list[str]]
    helps: list[IndexPlanHelp]
    benefit: float
    verified: bool
    write_cost: IndexWriteCost
    label: Literal["safe", "worth_it", "skip"]
    label_reason: str
    sql: str
    undo_sql: str | None = None


class IndexPlanWrite(BaseModel):
    key: str
    name: str | None
    verb: Literal["insert", "update", "delete"]
    share: float
    calls_per_s: float


class IndexPlanDrop(BaseModel):
    id: str
    table: str
    schema_: str | None = Field(default=None, alias="schema")
    index_name: str
    columns: list[str]
    reason: Literal["unused", "duplicate", "stale"]
    covered_by: str | None
    size_bytes: int
    scans: int
    last_scan_at: int | None
    evidence_age_s: int
    confidence: Literal["high", "low"]
    table_writes_per_s: float
    replica_caveat: bool
    # The write statements that keep the index up to date, busiest first.
    slows: list[IndexPlanWrite] = []
    sql: str
    restore_sql: str | None = None


class IndexPlanKeep(BaseModel):
    index_name: str
    table: str
    columns: list[str]
    unique: bool
    primary: bool
    scans: int
    window_scans: int
    size_bytes: int


class IndexPlanCovered(BaseModel):
    table: str
    columns: list[str]
    covered_by: str
    queries: list[str]


class IndexPlan(BaseModel):
    engine: str
    computed_at: int
    observed_s: int
    counter_age_s: int
    keep: list[IndexPlanKeep]
    add: list[IndexPlanAdd]
    drop: list[IndexPlanDrop]
    skipped: list[IndexPlanAdd]
    later: list[IndexPlanAdd] = []
    covered: list[IndexPlanCovered]
    # Idle indexes whose leading column the queries filter on: they can't use them yet.
    defeated: list[dict] = []
    sql: str
    # Unused indexes too small or too new to drop yet; RDST checks them again.
    watch: list[IndexPlanDrop] = []
    rollback_sql: str | None = None
    explanation: str | None = None
    set_aside: list[str] = []
