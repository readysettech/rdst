// Deterministic fixtures for every /api/monitor shape in ./types. Values come from a
// seeded generator, so the same (scenario, now) always yields the same data; times
// are relative to `now` so charts end at the live edge. Scenarios:
//   localdev      ~3 days of history, 36 queries incl. missing text, index changes
//                 (timeline facts only), a stuck pg_sleep, a regression, act-now inbox
//                 items and a recovery
//   first_hour    42 minutes of history, 12 queries, no prior period
//   long_history  95 days of history, same queries as localdev
//   empty         monitoring on, nothing collected yet
import { deriveQueryName } from '../queryIdentity'
import type {
  AnalyzeJob,
  AnalyzeJobPage,
  ChartMarker,
  CompositionSlice,
  DatabaseReport,
  DaySnapshot,
  FixVerdict,
  HealthPeriod,
  HealthPoint,
  HistorySpan,
  InboxAdvice,
  InboxDelivery,
  InboxItem,
  InboxItemView,
  InboxLink,
  InboxPage,
  InboxQuery,
  LastAnalysis,
  MiniSeriesPoint,
  MonitorHealth,
  MonitorPulse,
  MonitorPulsePage,
  MonitorQuery,
  MonitorQueryPage,
  MonitorRange,
  MonitorSettings,
  MonitorUsage,
  MuteRule,
  MuteRulePage,
  QuerySeries,
  QuerySeriesPoint,
  QueryState,
  RelevanceRule,
  ReportBody,
  ReportEvent,
  ReportHistory,
  ReportSummary,
  Resolution,
  SeverityBand,
} from './types'

export type FixtureScenario =
  | 'localdev'
  | 'first_hour'
  | 'long_history'
  | 'empty'
export const FIXTURE_SCENARIOS: readonly FixtureScenario[] = [
  'localdev',
  'first_hour',
  'long_history',
  'empty',
]
export const FIXTURE_TARGET = 'localdev'
/** A fixed clock for tests and screenshots: Mon 2026-09-28 16:00 UTC. */
export const FIXTURE_NOW = 1790611200

const MIN = 60
const HOUR = 3600
const DAY = 86400
const SPAN: Record<FixtureScenario, number> = {
  localdev: 2 * DAY + 19 * HOUR,
  first_hour: 42 * MIN,
  long_history: 95 * DAY,
  empty: 0,
}
const RANGE_SECONDS: Record<Exclude<MonitorRange, 'all'>, number> = {
  '1h': HOUR,
  '24h': DAY,
  '7d': 7 * DAY,
  '30d': 30 * DAY,
  '90d': 90 * DAY,
}

export function asScenario(value: string | null | undefined): FixtureScenario {
  return FIXTURE_SCENARIOS.includes(value as FixtureScenario)
    ? (value as FixtureScenario)
    : 'localdev'
}

export function fixtureNow(): number {
  return Math.floor(Date.now() / 1000 / MIN) * MIN
}

// Seeded generator and stable keys

function hash32(text: string, seed: number): number {
  let h = seed >>> 0
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619) >>> 0
  }
  return h >>> 0
}

function queryKey(sql: string): string {
  const a = hash32(sql, 2166136261).toString(16).padStart(8, '0')
  const b = hash32(sql, 3141592653).toString(16).padStart(8, '0')
  return `${a}${b}`.slice(0, 12)
}

/** Stable noise in [-1, 1] for a (label, bucket) pair. */
function noise(label: string, bucket: number): number {
  return (hash32(`${label}:${bucket}`, 2654435761) / 0xffffffff) * 2 - 1
}

const round = (value: number, digits = 3) => Number(value.toFixed(digits))

// History span and resolution

export function historySpan(
  scenario: FixtureScenario,
  now: number
): HistorySpan {
  const span = SPAN[scenario]
  if (span === 0)
    return {
      first_ts: null,
      last_ts: null,
      span_seconds: 0,
      resolutions: {
        '60': { first_ts: null },
        '300': { first_ts: null },
        '3600': { first_ts: null },
        '86400': { first_ts: null },
      },
    }
  const first = now - span
  // Minute rows are kept for 3 days, 5-minute rows for 30 days, coarser forever.
  return {
    first_ts: first,
    last_ts: now,
    span_seconds: span,
    resolutions: {
      '60': { first_ts: Math.max(first, now - 3 * DAY) },
      '300': { first_ts: Math.max(first, now - 30 * DAY) },
      '3600': { first_ts: first },
      '86400': { first_ts: first },
    },
  }
}

function resolutionFor(range: MonitorRange, span: number): Resolution {
  if (range === '1h' || range === '24h') return 60
  if (range === '7d') return 300
  if (range === '30d' || range === '90d') return 3600
  for (const res of [60, 300, 3600] as const) if (span / res <= 1000) return res
  return 86400
}

function domain(scenario: FixtureScenario, range: MonitorRange, now: number) {
  const span = SPAN[scenario]
  const seconds = range === 'all' ? span : RANGE_SECONDS[range]
  const start = Math.max(now - seconds, now - span)
  const res = resolutionFor(range, span)
  return { start, res, seconds }
}

function buckets(start: number, now: number, res: number): number[] {
  const out: number[] = []
  for (let ts = Math.floor(start / res) * res; ts <= now; ts += res)
    out.push(ts)
  return out
}

// A 25-minute collector outage 17 hours ago, rendered as a labeled gap.
function inOutage(ts: number, now: number): boolean {
  return ts >= now - 17 * HOUR && ts < now - 17 * HOUR + 25 * MIN
}

const diurnal = (ts: number) =>
  1 + 0.35 * Math.sin(((ts % DAY) / DAY) * 2 * Math.PI)

// Queries

interface QuerySpec {
  sql: string
  qps: number
  ms: number
  /** Relative age: first seen this many seconds before now (undefined = always). */
  newSince?: number
  regressAt?: number
  regressFactor?: number
  fix?: {
    at: number
    index: string
    table: string
    status: FixVerdict['status']
    factor: number
  }
  stuck?: boolean
  text?: 'truncated' | 'unavailable'
  muted?: boolean
  stoppedAt?: number
  analysis?: Partial<LastAnalysis> & { state: LastAnalysis['state'] }
  compare?: { speedup: number; verdict: string }
}

const TRUNCATED_TAIL =
  " AND o.placed_at >= $3 AND o.placed_at < $4 AND o.status IN ('paid','shipped','delivered') GROUP BY c.country, c.id, c.full_name ORDER BY revenue DESC"

const SPECS: QuerySpec[] = [
  {
    sql: 'SELECT count(*) FROM orders WHERE status = $1',
    qps: 3.1,
    ms: 4.1,
    fix: {
      at: 20 * HOUR,
      index: 'idx_orders_status',
      table: 'orders',
      status: 'confirmed',
      factor: 0.013,
    },
    analysis: { state: 'done', analysis_ref: 'an_7f31c2' },
    compare: { speedup: 41.2, verdict: 'Readyset is 41x faster' },
  },
  {
    sql: 'SELECT id, status, total, placed_at FROM orders WHERE customer_id = $1 ORDER BY placed_at DESC LIMIT $2',
    qps: 24.6,
    ms: 0.42,
    analysis: { state: 'done', analysis_ref: 'an_1a9e04' },
    compare: { speedup: 3.4, verdict: 'Readyset is 3.4x faster' },
  },
  {
    sql: 'SELECT id, status, total, placed_at FROM orders WHERE status = $1 ORDER BY placed_at DESC LIMIT $2',
    qps: 6.2,
    ms: 0.58,
    regressAt: 14 * HOUR,
    regressFactor: 7.2,
    analysis: { state: 'running' },
  },
  {
    sql: 'SELECT pg_sleep($1)',
    qps: 0.004,
    ms: 180000,
    stuck: true,
    analysis: {
      state: 'skipped',
      reason: "This query calls pg_sleep, which RDST won't run.",
    },
  },
  {
    sql: 'SELECT id, full_name, lifetime_spend FROM customers WHERE lower(email) = lower($1)',
    qps: 4.4,
    ms: 0.5,
    fix: {
      at: 9 * HOUR,
      index: 'idx_customers_email',
      table: 'customers',
      status: 'no_effect',
      factor: 1.02,
    },
    analysis: { state: 'done', analysis_ref: 'an_c40b77' },
  },
  {
    sql: 'SELECT c.country, count(DISTINCT o.id) AS orders, round(sum(oi.quantity * oi.unit_price), 2) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id WHERE o.placed_at >= $1 GROUP BY c.country ORDER BY revenue DESC',
    qps: 0.9,
    ms: 96,
    newSince: 3 * HOUR,
    analysis: { state: 'queued' },
  },
  {
    sql: 'SELECT id, name, price FROM products WHERE category = $1 AND in_stock ORDER BY price DESC LIMIT $2',
    qps: 5.3,
    ms: 2.9,
    fix: {
      at: 12 * MIN,
      index: 'idx_products_category',
      table: 'products',
      status: 'measuring',
      factor: 0.6,
    },
    analysis: { state: 'done', analysis_ref: 'an_5d2f90' },
  },
  {
    sql: 'SELECT product_id, quantity, unit_price FROM order_items WHERE order_id = $1',
    qps: 17.9,
    ms: 0.11,
    analysis: { state: 'done', analysis_ref: 'an_0b77e1' },
  },
  {
    sql: 'SELECT p.category, count(*) AS items, round(sum(oi.quantity * oi.unit_price), 2) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.category',
    qps: 0.6,
    ms: 64,
    analysis: {
      state: 'failed',
      reason:
        'The analysis timed out after 60 s. Try again when the database is quieter.',
    },
  },
  {
    sql: 'SELECT id, sku, name FROM products WHERE name LIKE $1',
    qps: 2.1,
    ms: 7.4,
    analysis: { state: 'done', analysis_ref: 'an_9c3d18' },
  },
  {
    sql: 'UPDATE orders SET status = $1, updated_at = now() WHERE id = $2',
    qps: 3.8,
    ms: 0.21,
    analysis: {
      state: 'skipped',
      reason:
        'RDST only analyzes read-only queries. This statement runs UPDATE.',
    },
  },
  {
    sql: 'INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES ($1, $2, $3, $4)',
    qps: 5.6,
    ms: 0.17,
    analysis: {
      state: 'skipped',
      reason:
        'RDST only analyzes read-only queries. This statement runs INSERT.',
    },
  },
  {
    sql: 'SELECT country, count(*), round(avg(lifetime_spend), 2) FROM customers WHERE country = $1 GROUP BY country',
    qps: 1.3,
    ms: 21,
    muted: true,
  },
  {
    sql: 'SELECT o.id, o.total, c.full_name FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.total > $1 ORDER BY o.total DESC LIMIT $2',
    qps: 0.8,
    ms: 38,
    analysis: { state: 'done', analysis_ref: 'an_e2a511' },
  },
  {
    sql: 'SELECT id, full_name, email FROM customers WHERE id = $1',
    qps: 31.2,
    ms: 0.05,
  },
  {
    sql: 'SELECT id, name, price, in_stock FROM products WHERE id = $1',
    qps: 22.4,
    ms: 0.04,
  },
  {
    sql: 'SELECT id, customer_id, total FROM orders WHERE id = $1',
    qps: 19.7,
    ms: 0.05,
  },
  {
    sql: 'SELECT id, full_name FROM customers WHERE id IN ($1, $2, $3)',
    qps: 4.9,
    ms: 0.09,
  },
  {
    sql: 'SELECT id, full_name, country FROM customers WHERE id BETWEEN $1 AND $2 ORDER BY id LIMIT $3',
    qps: 2.7,
    ms: 0.31,
  },
  {
    sql: 'SELECT c.id, c.full_name, (SELECT count(*) FROM orders o WHERE o.customer_id = c.id) AS v FROM customers c WHERE c.country = $1 LIMIT $2',
    qps: 1.1,
    ms: 14.2,
  },
  {
    sql: 'SELECT p.id, p.name, (SELECT sum(oi.quantity) FROM order_items oi WHERE oi.product_id = p.id) AS v FROM products p WHERE p.category = $1 LIMIT $2',
    qps: 0.7,
    ms: 26.5,
  },
  {
    sql: 'SELECT o.id, o.total FROM orders o WHERE o.customer_id = $1 AND EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.quantity > 3) LIMIT $2',
    qps: 1.9,
    ms: 0.8,
  },
  {
    sql: "WITH recent AS (SELECT id, customer_id, total, placed_at FROM orders WHERE placed_at > now() - interval '7 days') SELECT date_trunc('week', r.placed_at), avg(r.total) FROM recent r GROUP BY 1",
    qps: 0.2,
    ms: 118,
  },
  {
    sql: 'SELECT r.customer_id, count(*) FROM orders r WHERE r.placed_at > $1 GROUP BY r.customer_id ORDER BY 2 DESC LIMIT $2',
    qps: 0.3,
    ms: 71,
    stoppedAt: 5 * HOUR,
  },
  {
    sql: 'SELECT a.id, b.id FROM customers a JOIN customers b ON b.country = a.country AND b.id <> a.id WHERE a.id = $1 LIMIT $2',
    qps: 0.4,
    ms: 9.8,
  },
  {
    sql: 'SELECT id, total FROM orders WHERE total > (SELECT avg(total) FROM orders o2 WHERE o2.customer_id = orders.customer_id) LIMIT $1',
    qps: 0.2,
    ms: 88,
    stoppedAt: 5 * HOUR,
  },
  {
    sql: 'SELECT id, name FROM products WHERE id IN (SELECT product_id FROM order_items WHERE quantity > $1) LIMIT $2',
    qps: 0.5,
    ms: 17.3,
  },
  {
    sql: 'UPDATE customers SET lifetime_spend = lifetime_spend + $1 WHERE id = $2',
    qps: 3.6,
    ms: 0.19,
  },
  {
    sql: 'SELECT order_id, quantity, unit_price FROM order_items WHERE product_id = $1 LIMIT $2',
    qps: 1.4,
    ms: 4.1,
  },
  {
    sql: 'BEGIN',
    qps: 9.1,
    ms: 0.01,
  },
  {
    sql: 'COMMIT',
    qps: 8.8,
    ms: 0.02,
  },
  {
    sql: 'SELECT c.country, c.id, c.full_name, sum(o.total) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.id WHERE c.country = $1 AND o.total > $2',
    qps: 0.3,
    ms: 142,
    text: 'truncated',
  },
  { sql: '<unavailable-1>', qps: 1.6, ms: 3.2, text: 'unavailable' },
  { sql: '<unavailable-2>', qps: 0.9, ms: 11.8, text: 'unavailable' },
  { sql: '<unavailable-3>', qps: 0.4, ms: 0.7, text: 'unavailable' },
  {
    sql: '<unavailable-4>',
    qps: 0.1,
    ms: 55,
    text: 'unavailable',
    stoppedAt: 5 * HOUR,
  },
]

/** The 12 queries seen in the first-hour scenario. */
const FIRST_HOUR = new Set([1, 2, 7, 10, 11, 14, 15, 16, 17, 21, 28, 29])

const UNAVAILABLE_REASON =
  "Postgres dropped this statement from pg_stat_statements before RDST read its text. It'll show once the query runs again."

function specsFor(scenario: FixtureScenario): QuerySpec[] {
  if (scenario === 'empty') return []
  if (scenario === 'first_hour')
    return SPECS.filter((_, index) => FIRST_HOUR.has(index)).map((spec) => ({
      sql: spec.sql,
      qps: spec.qps,
      ms: spec.ms,
    }))
  return SPECS
}

export const fixtureQueryKey = (sql: string) => queryKey(sql)
/** Keys of notable fixture queries, for tests and deep links. */
export const FIXTURE_KEYS = {
  fixConfirmed: queryKey(SPECS[0].sql),
  regressed: queryKey(SPECS[2].sql),
  stuck: queryKey(SPECS[3].sql),
  fixNoEffect: queryKey(SPECS[4].sql),
  newHeavy: queryKey(SPECS[5].sql),
  measuring: queryKey(SPECS[6].sql),
  analysisFailed: queryKey(SPECS[8].sql),
  muted: queryKey(SPECS[12].sql),
  truncated: queryKey(SPECS[31].sql),
  unavailable: queryKey(SPECS[32].sql),
} as const

function specText(spec: QuerySpec): string {
  return spec.text === 'unavailable' ? '' : spec.sql
}

function displayName(spec: QuerySpec): string {
  return spec.text === 'unavailable'
    ? `Query ${queryKey(spec.sql).slice(0, 8)}`
    : deriveQueryName(spec.sql)
}

function tablesOf(sql: string): string[] {
  const found = new Set<string>()
  for (const match of sql.matchAll(
    /\b(?:from|join|into|update)\s+([a-z_]+)/gi
  )) {
    const name = match[1].toLowerCase()
    if (name !== 'recent' && name !== 'set') found.add(name)
  }
  return [...found].sort()
}

/** qps and mean latency of one query at a moment, including its scripted events. */
function queryAt(spec: QuerySpec, ts: number, now: number) {
  const age = now - ts
  if (spec.newSince !== undefined && age > spec.newSince) return null
  if (spec.stoppedAt !== undefined && age < spec.stoppedAt) return null
  if (inOutage(ts, now)) return null
  const label = spec.sql
  let ms = spec.ms
  if (spec.regressAt !== undefined && age > spec.regressAt)
    ms = ms / (spec.regressFactor ?? 1)
  if (spec.fix && age > spec.fix.at) ms = ms / spec.fix.factor
  const bucket = Math.floor(ts / 60)
  const qps = spec.stuck
    ? age % (2 * HOUR) < 3 * MIN
      ? 0.017
      : 0
    : spec.qps * diurnal(ts) * (1 + 0.12 * noise(label, bucket))
  return {
    qps: Math.max(0, qps),
    mean_ms: ms * (1 + 0.08 * noise(`${label}:ms`, bucket)),
  }
}

function periodTotals(
  spec: QuerySpec,
  start: number,
  end: number,
  now: number
) {
  let calls = 0
  let totalMs = 0
  const step = Math.max(MIN, Math.floor((end - start) / 240 / MIN) * MIN)
  for (let ts = start; ts < end; ts += step) {
    const point = queryAt(spec, ts, now)
    if (!point) continue
    const bucketCalls = point.qps * step
    calls += bucketCalls
    totalMs += bucketCalls * point.mean_ms
  }
  return { calls, totalMs }
}

function lastAnalysis(spec: QuerySpec, now: number): LastAnalysis | null {
  if (!spec.analysis) return null
  const finished =
    spec.analysis.state === 'done' ||
    spec.analysis.state === 'failed' ||
    spec.analysis.state === 'skipped'
  return {
    state: spec.analysis.state,
    finished_at: finished
      ? now - 2 * HOUR - (hash32(spec.sql, 7) % 3600)
      : null,
    analysis_ref: spec.analysis.analysis_ref ?? null,
    mode: 'analyze',
    param_source: spec.analysis.state === 'done' ? 'sampled' : null,
    reason: spec.analysis.reason ?? null,
  }
}

function buildQuery(
  spec: QuerySpec,
  scenario: FixtureScenario,
  range: MonitorRange,
  now: number,
  totalDbMs: number
): MonitorQuery {
  const { start, seconds } = domain(scenario, range, now)
  const current = periodTotals(spec, start, now, now)
  const priorCovered = now - SPAN[scenario] <= start - seconds
  const prior = priorCovered
    ? periodTotals(spec, start - seconds, start, now)
    : null
  const mean = current.calls ? current.totalMs / current.calls : spec.ms
  const priorMean = prior?.calls ? prior.totalMs / prior.calls : null
  const latency = priorMean ? mean / priorMean - 1 : null
  const qpsChange = prior?.calls ? current.calls / prior.calls - 1 : null
  const isNew = spec.newSince !== undefined && spec.newSince < seconds
  const significant =
    latency !== null && Math.abs(latency) >= 0.2 && current.calls >= 200
  const verdict = isNew
    ? 'new'
    : latency === null
      ? null
      : significant
        ? latency > 0
          ? 'regressed'
          : 'improved'
        : 'steady'
  const state: QueryState[] = []
  if (verdict === 'regressed' || spec.stuck) state.push('attention')
  if (spec.stuck) state.push('stuck')
  if (isNew) state.push('new')
  if (spec.muted) state.push('muted')
  const miniStep = Math.max(MIN, Math.floor((now - start) / 48 / MIN) * MIN)
  const series: MiniSeriesPoint[] = buckets(start, now, miniStep).map((ts) => {
    const point = queryAt(spec, ts, now)
    return {
      ts,
      qps: point ? round(point.qps) : null,
      mean_ms: point ? round(point.mean_ms) : null,
    }
  })
  return {
    key: queryKey(spec.sql),
    text: specText(spec),
    text_status: spec.text ?? 'ok',
    text_reason:
      spec.text === 'unavailable'
        ? UNAVAILABLE_REASON
        : spec.text === 'truncated'
          ? 'Postgres stored only the first 1,024 characters of this statement.'
          : null,
    display_name: displayName(spec),
    tables: spec.text === 'unavailable' ? [] : tablesOf(spec.sql),
    qps: round(current.calls / Math.max(1, now - start)),
    mean_ms: round(mean),
    db_time_ms: round(current.totalMs, 1),
    db_time_share: totalDbMs ? round(current.totalMs / totalDbMs, 4) : 0,
    change: {
      latency: latency === null ? null : round(latency),
      qps: qpsChange === null ? null : round(qpsChange),
      significant,
      verdict,
    },
    series,
    state,
    fix: null,
    last_analysis: lastAnalysis(spec, now),
    last_compare: spec.compare
      ? {
          ts: now - 5 * HOUR,
          verdict: spec.compare.verdict,
          speedup: spec.compare.speedup,
        }
      : null,
  }
}

/** The truncated fixture row keeps its prefix only; its full text is not available. */
function applyTruncation(row: MonitorQuery): MonitorQuery {
  if (row.text_status !== 'truncated') return row
  return { ...row, text: `${row.text}${TRUNCATED_TAIL}`.slice(0, 160) }
}

export function queriesFixture(
  scenario: FixtureScenario,
  range: MonitorRange,
  now: number
): MonitorQueryPage {
  const specs = specsFor(scenario)
  const { start } = domain(scenario, range, now)
  const totals = specs.map(
    (spec) => periodTotals(spec, start, now, now).totalMs
  )
  const totalDbMs = totals.reduce((sum, value) => sum + value, 0)
  const queries = specs
    .map((spec) =>
      applyTruncation(buildQuery(spec, scenario, range, now, totalDbMs))
    )
    .filter((row) => row.db_time_ms > 0 || row.state.includes('stuck'))
    .sort((a, b) => b.db_time_ms - a.db_time_ms || a.key.localeCompare(b.key))
  const top = queries.slice(0, 6)
  const otherMs = queries.slice(6).reduce((sum, row) => sum + row.db_time_ms, 0)
  const composition: CompositionSlice[] = top.map((row) => ({
    key: row.key,
    db_time_ms: row.db_time_ms,
    share: row.db_time_share,
  }))
  if (queries.length > 6)
    composition.push({
      key: 'other',
      db_time_ms: round(otherMs, 1),
      share: totalDbMs ? round(otherMs / totalDbMs, 4) : 0,
    })
  const has = (state: QueryState) =>
    queries.filter((row) => row.state.includes(state)).length
  return {
    queries,
    total: queries.length,
    counts: {
      all: queries.length,
      attention: has('attention'),
      new: has('new'),
      fixed: has('fixed'),
      muted: has('muted'),
    },
    composition,
    history: historySpan(scenario, now),
  }
}

// Markers

const INDEX_EVENTS = [
  {
    ago: 20 * HOUR,
    kind: 'index_added',
    index: 'idx_orders_status',
    table: 'orders',
    matches: true,
  },
  {
    ago: 9 * HOUR,
    kind: 'index_added',
    index: 'idx_customers_email',
    table: 'customers',
    matches: true,
  },
  {
    ago: 12 * MIN,
    kind: 'index_added',
    index: 'idx_products_category',
    table: 'products',
    matches: true,
  },
  {
    ago: 50 * HOUR,
    kind: 'index_added',
    index: 'idx_orders_placed_at',
    table: 'orders',
    matches: false,
  },
  {
    ago: 26 * HOUR,
    kind: 'index_dropped',
    index: 'idx_customers_country_old',
    table: 'customers',
    matches: false,
  },
] as const

function indexMarkers(start: number, now: number): ChartMarker[] {
  return INDEX_EVENTS.filter((event) => now - event.ago >= start).map(
    (event) => ({
      ts: now - event.ago,
      kind: event.kind,
      label:
        event.kind === 'index_dropped'
          ? `${event.index} dropped`
          : event.matches
            ? `${event.index} added - matches the recommendation`
            : `${event.index} added`,
      index_name: event.index,
      table: event.table,
      matches_recommendation: event.matches,
    })
  )
}

function healthMarkers(
  scenario: FixtureScenario,
  start: number,
  now: number
): ChartMarker[] {
  if (scenario === 'empty' || scenario === 'first_hour') return []
  const inbox = inboxItems(now)
    .filter(
      (item) => item.target === FIXTURE_TARGET && item.created_at >= start
    )
    .slice(0, 6)
    .map<ChartMarker>((item) => ({
      ts: item.created_at,
      kind: 'inbox',
      label: `${item.title} filed`,
      inbox_id: item.id,
      severity: item.severity,
    }))
  const reports = reportSummaries(now)
    .filter((report) => report.ts >= start)
    .map<ChartMarker>((report) => ({
      ts: report.ts,
      kind: 'report',
      label: 'Report written',
      report_id: report.id,
    }))
  return [...indexMarkers(start, now), ...inbox, ...reports].sort(
    (a, b) => a.ts - b.ts
  )
}

// Health

export function healthFixture(
  scenario: FixtureScenario,
  range: MonitorRange,
  now: number,
  target = FIXTURE_TARGET
): MonitorHealth {
  const history = historySpan(scenario, now)
  const { start, res, seconds } = domain(scenario, range, now)
  const specs = specsFor(scenario)
  const baseQps = specs.reduce((sum, spec) => sum + spec.qps, 0)
  const point = (ts: number): HealthPoint => {
    if (scenario === 'empty' || inOutage(ts, now) || ts < now - SPAN[scenario])
      return {
        ts,
        qps: null,
        mean_ms: null,
        active_conns: null,
        max_conns: null,
        cache_hit_ratio: null,
        xact_rollback: null,
        deadlocks: null,
      }
    const bucket = Math.floor(ts / res)
    const spike = now - ts > 7 * HOUR && now - ts < 7 * HOUR + 40 * MIN
    const regressed = now - ts < 14 * HOUR && scenario !== 'first_hour'
    return {
      ts,
      qps: round(baseQps * diurnal(ts) * (1 + 0.06 * noise('qps', bucket)), 2),
      mean_ms: round(
        (regressed ? 2.9 : 2.4) * (1 + 0.1 * noise('ms', bucket)),
        3
      ),
      active_conns: Math.max(
        1,
        Math.round(9 * diurnal(ts) + 3 * noise('conns', bucket))
      ),
      max_conns: 100,
      cache_hit_ratio: round(
        Math.min(0.9999, 0.991 + 0.006 * noise('hit', bucket)),
        4
      ),
      xact_rollback: Math.max(
        0,
        Math.round(
          ((spike ? 42 : 2) * (res / 60) * (1 + noise('rb', bucket))) / 2
        )
      ),
      deadlocks: spike && bucket % 7 === 0 ? 1 : 0,
    }
  }
  const series = scenario === 'empty' ? [] : buckets(start, now, res).map(point)
  const summarize = (points: HealthPoint[]): HealthPeriod => {
    const live = points.filter((p) => p.qps !== null)
    const avg = (values: number[]) =>
      values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : 0
    const qps = avg(live.map((p) => p.qps as number))
    const meanMs = avg(live.map((p) => p.mean_ms as number))
    const peak = live.reduce<HealthPoint | null>(
      (best, p) => (!best || (p.qps ?? 0) > (best.qps ?? 0) ? p : best),
      null
    )
    return {
      qps: round(qps, 2),
      mean_ms: round(meanMs, 3),
      qps_peak: peak?.qps ?? null,
      qps_peak_at: peak?.ts ?? null,
      db_time_ms: round(qps * meanMs * live.length * res, 1),
      active_conns_peak: live.length
        ? Math.max(...live.map((p) => p.active_conns as number))
        : null,
      cache_hit_ratio_min: live.length
        ? Math.min(...live.map((p) => p.cache_hit_ratio as number))
        : null,
      xact_rollback: live.reduce((sum, p) => sum + (p.xact_rollback ?? 0), 0),
      deadlocks: live.reduce((sum, p) => sum + (p.deadlocks ?? 0), 0),
    }
  }
  const priorCovered = SPAN[scenario] >= 2 * seconds && range !== 'all'
  const latest = series.length ? series[series.length - 1] : point(now)
  return {
    target,
    engine: 'postgresql',
    engine_version: '16.4',
    range,
    res,
    history,
    now: {
      ts: latest.ts,
      qps: latest.qps ?? 0,
      mean_ms: latest.mean_ms ?? 0,
      active_conns: latest.active_conns,
      max_conns: latest.max_conns,
      cache_hit_ratio: latest.cache_hit_ratio,
      rollbacks_per_min:
        latest.xact_rollback === null
          ? null
          : latest.xact_rollback / (res / 60),
    },
    period: summarize(series),
    prior: priorCovered
      ? summarize(buckets(start - seconds, start - res, res).map(point))
      : null,
    series,
    markers: healthMarkers(scenario, start, now),
  }
}

// Query series

export function seriesFixture(
  scenario: FixtureScenario,
  key: string,
  range: MonitorRange,
  now: number
): QuerySeries {
  const history = historySpan(scenario, now)
  const { start, res } = domain(scenario, range, now)
  const spec = specsFor(scenario).find(
    (candidate) => queryKey(candidate.sql) === key
  )
  if (!spec) return { res, history, points: [], markers: [], fix: null }
  const points: QuerySeriesPoint[] = buckets(start, now, res).map((ts) => {
    const point = queryAt(spec, ts, now)
    if (!point)
      return { ts, calls: null, qps: null, mean_ms: null, total_ms: null }
    const calls = Math.round(point.qps * res)
    return {
      ts,
      calls,
      qps: round(point.qps),
      mean_ms: round(point.mean_ms),
      total_ms: round(calls * point.mean_ms, 1),
    }
  })
  const markers: ChartMarker[] = []
  if (spec.fix) {
    // An index change is a timeline fact, never a verdict on the query's latency.
    const at = now - spec.fix.at
    if (at >= start)
      markers.push({
        ts: at,
        kind: 'index_added',
        label: `${spec.fix.index} added`,
        index_name: spec.fix.index,
        table: spec.fix.table,
        matches_recommendation: false,
      })
  }
  const analysis = lastAnalysis(spec, now)
  if (analysis?.finished_at && analysis.finished_at >= start)
    markers.push({
      ts: analysis.finished_at,
      kind: 'analysis',
      label:
        analysis.state === 'done'
          ? 'Analyzed - index scan, optimal'
          : analysis.state === 'skipped'
            ? 'Analysis skipped'
            : 'Analysis failed',
      analysis_ref: analysis.analysis_ref ?? undefined,
    })
  for (const item of inboxItems(now))
    if (item.query_keys.includes(key) && item.created_at >= start)
      markers.push({
        ts: item.created_at,
        kind: 'inbox',
        label: `${item.title} filed`,
        inbox_id: item.id,
        severity: item.severity,
      })
  markers.sort((a, b) => a.ts - b.ts)
  return { res, history, points, markers, fix: null }
}

// Pulse

export function pulsesFixture(
  scenario: FixtureScenario,
  now: number,
  limit = 5
): MonitorPulsePage {
  if (scenario === 'empty')
    return {
      pulses: [
        {
          ts: now - 20,
          kind: 'collect',
          ok: true,
          detail: 'Read 0 queries',
          next_collect_at: now + 40,
          state: 'live',
        },
      ],
    }
  const count = specsFor(scenario).length
  const lastTick = now - 23
  const pulses: MonitorPulse[] = [
    {
      ts: lastTick,
      kind: 'collect',
      ok: true,
      detail: `Read ${count} queries`,
      next_collect_at: lastTick + 60,
      state: 'live',
    },
    {
      ts: lastTick - 2,
      kind: 'index_scan',
      ok: true,
      detail: 'No index changes',
      next_collect_at: lastTick + 60,
      state: 'live',
    },
    {
      ts: lastTick - 60,
      kind: 'collect',
      ok: true,
      detail: `Read ${count} queries`,
      next_collect_at: lastTick,
      state: 'live',
    },
    {
      ts: lastTick - 118,
      kind: 'attention',
      ok: true,
      detail: `Checked ${count} queries, nothing new`,
      next_collect_at: lastTick - 60,
      state: 'live',
    },
    {
      ts: lastTick - 120,
      kind: 'collect',
      ok: true,
      detail: `Read ${count} queries`,
      next_collect_at: lastTick - 60,
      state: 'live',
    },
    {
      ts: lastTick - 480,
      kind: 'analyze',
      ok: true,
      detail: 'Analyzed Select - orders',
      next_collect_at: lastTick - 420,
      state: 'live',
    },
  ]
  return {
    pulses: pulses.slice(0, limit),
    activity: {
      kind: 'reading',
      label: 'Reading query statistics',
      last_reading_at: lastTick,
      every_s: 60,
      next_check_at: lastTick - 118 + 300,
    },
  }
}

export function nextPulseFixture(
  scenario: FixtureScenario,
  now: number
): MonitorPulse {
  return {
    ts: now,
    kind: 'collect',
    ok: true,
    detail:
      scenario === 'empty'
        ? 'Connected - waiting for the first statements'
        : `Read ${specsFor(scenario).length} queries`,
    next_collect_at: now + 60,
    state: 'live',
  }
}

// Analysis jobs

export function analyzeJobsFixture(
  scenario: FixtureScenario,
  now: number,
  key?: string,
  limit = 50
): AnalyzeJobPage {
  const jobs: AnalyzeJob[] = []
  specsFor(scenario).forEach((spec, index) => {
    if (!spec.analysis) return
    const queryKeyValue = queryKey(spec.sql)
    if (key && key !== queryKeyValue) return
    const analysis = lastAnalysis(spec, now) as LastAnalysis
    const enqueued = (analysis.finished_at ?? now) - 90
    jobs.push({
      id: 900 - index,
      target: FIXTURE_TARGET,
      query_key: queryKeyValue,
      reason: index === 5 ? 'new' : 'report',
      state: analysis.state,
      priority: round(1 - index / 40),
      param_source: analysis.state === 'done' ? 'sampled' : null,
      mode: analysis.state === 'done' ? 'analyze' : null,
      enqueued_at: enqueued,
      started_at: analysis.state === 'queued' ? null : enqueued + 5,
      finished_at: analysis.finished_at,
      error: analysis.state === 'failed' ? 'statement timeout' : null,
      analysis_ref: analysis.analysis_ref,
      reason_text: analysis.reason,
    })
  })
  jobs.sort((a, b) => b.enqueued_at - a.enqueued_at || b.id - a.id)
  return { jobs: jobs.slice(0, limit) }
}

// Settings and usage

export function settingsFixture(scenario: FixtureScenario): MonitorSettings {
  return {
    enabled: true,
    deep_analysis: scenario !== 'empty',
    execute: false,
    analysis_mode: 'plans_only',
    auto_analyze_new: true,
    analysis_daily_cap: 100,
    newcomer_daily_cap: 25,
    attention_every_min: 15,
    nightly_report: true,
    nightly_hour: 2,
    push_daily_cap: 3,
    notify_events: {
      database_unreachable: true,
      database_recovered: false,
      monitoring_setup: true,
      stuck_query: true,
      database_events: true,
      query_changes: true,
      daily_report: true,
    },
    quiet_hours: { from_hour: 23, to_hour: 7 },
    size_bytes:
      scenario === 'long_history'
        ? 61_800_000
        : scenario === 'localdev'
          ? 9_400_000
          : 180_000,
    budget_bytes: 256 * 1024 * 1024,
  }
}

export function usageFixture(
  scenario: FixtureScenario,
  now: number,
  days = 7
): MonitorUsage {
  const out = []
  const active = Math.ceil(SPAN[scenario] / DAY)
  for (let i = days - 1; i >= 0; i--) {
    const date = new Date((now - i * DAY) * 1000).toISOString().slice(0, 10)
    const on = i < active
    // One AI check per five-minute cycle with something to assess: at most 288 a day.
    const jev = on ? 250 + Math.round(15 * (1 + noise('jev', i))) : 0
    const analyses = on ? 6 + Math.round(4 * (1 + noise('an', i))) : 0
    const reports = on ? (i === 0 ? 2 : 1) : 0
    out.push({
      date,
      jev_calls: jev,
      analyses,
      reports,
      billable_calls: jev + analyses * 3 + reports,
      notifications: on ? 1 + (i % 3) : 0,
    })
  }
  return {
    days: out,
    caps: { jev_daily: 300, analysis_daily: 100, push_daily: 3 },
  }
}

// Mute rules and inbox

export function muteRulesFixture(now: number, target?: string): MuteRulePage {
  const rules: MuteRule[] = [
    {
      id: 1,
      target: FIXTURE_TARGET,
      query_key: FIXTURE_KEYS.muted,
      type: null,
      until: null,
      created_at: now - 30 * HOUR,
      note: 'Reporting query, slow on purpose',
      matched_count: 2,
    },
    {
      id: 2,
      target: FIXTURE_TARGET,
      query_key: null,
      type: 'cache_hit_ratio_drop',
      until: now + 6 * DAY,
      created_at: now - DAY,
      note: null,
      matched_count: 1,
    },
    {
      id: 3,
      target: null,
      query_key: null,
      type: 'daily_summary',
      until: null,
      created_at: now - 2 * DAY,
      note: null,
      matched_count: 0,
    },
  ]
  return {
    rules: rules.filter(
      (rule) => !target || rule.target === null || rule.target === target
    ),
  }
}

function band(type: string, severity: number): SeverityBand {
  if (severity >= 0.85 || type === 'stuck_query' || type === 'monitoring_issue')
    return 'act_now'
  if (type === 'daily_summary' || type === 'query_improved') return 'fyi'
  return severity >= 0.6 ? 'watch' : 'fyi'
}

interface InboxSpec {
  type: string
  severity: number
  title: string
  summary: string
  ago: number
  keys?: string[]
  target?: string
  state?: InboxItem['state']
  resolvedBy?: InboxItem['resolved_by']
  /** Closed by RDST because the measurements went back to normal (green). */
  recovered?: boolean
  mutedBy?: number
  delivery?: InboxDelivery
  subject?: string
  body?: Record<string, unknown>
  /** Where it opens, when not the type's usual place. */
  link?: InboxLink
  view?: Partial<InboxItemView>
}

function trend(label: string, filedAt: number, base: number, jump: number) {
  return Array.from({ length: 24 }, (_, i) => {
    const ts = filedAt - (16 - i) * 5 * MIN
    const after = ts >= filedAt - 10 * MIN
    return {
      ts,
      value: round(base * (after ? jump : 1) * (1 + 0.08 * noise(label, i)), 3),
    }
  })
}

const QUERY_TAB: Record<string, InboxLink['tab']> = {
  plan_change: 'analysis',
  new_heavy_query: 'analysis',
}

/** The fixture twin of the backend's inbox_link (features/monitor/presentation.py). */
function fixtureLink(
  spec: InboxSpec,
  target: string,
  keys: string[]
): InboxLink {
  if (spec.type === 'daily_summary')
    return {
      kind: 'report',
      target,
      report_id: Number(spec.body?.report_id ?? 60),
      label: 'Open report',
    }
  if (spec.type === 'monitoring_issue') {
    if (spec.subject === 'jev_login' || spec.subject === 'llm_login')
      return { kind: 'sign_in', target, label: 'Sign in' }
    if (spec.subject === 'storage_budget')
      return { kind: 'settings', target, label: 'Monitoring settings' }
    return { kind: 'configure', target, label: 'Check connection' }
  }
  if (keys.length === 1)
    return {
      kind: 'query',
      target,
      query_key: keys[0],
      tab: QUERY_TAB[spec.type] ?? 'performance',
      label: 'Open query',
    }
  if (keys.length > 1)
    return {
      kind: 'database',
      target,
      query_keys: keys,
      label: 'Open affected queries',
    }
  return { kind: 'database', target, label: 'Open database' }
}

/** The fixture twin of the backend's inbox_advice, for the types the fixtures file. */
const ADVICE: Record<string, InboxAdvice> = {
  stuck_query: {
    summary: "RDST doesn't stop running statements. From a SQL session:",
    steps: [
      "Find it and what it waits on: SELECT pid, now() - query_start AS running_for, wait_event_type, wait_event, left(query, 80) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY query_start;",
      'Cancel it: SELECT pg_cancel_backend(<pid>);',
      "If it won't cancel and others wait on its locks: SELECT pg_terminate_backend(<pid>);",
    ],
  },
  rollback_or_deadlock_spike: {
    summary:
      "RDST counts rollbacks but can't see why the application rolled back.",
    steps: [
      'Check application logs for failed transactions from the time this started.',
      'For deadlocks, turn on lock logging (log_lock_waits = on) and read the server log.',
    ],
  },
  latency_regression: {
    summary:
      "If the table's data changed a lot, the planner may be using old statistics.",
    steps: [
      'Refresh them: ANALYZE orders;',
      'Check for a bulk job or a lock on the same tables at that time.',
    ],
  },
  many_queries_changed: {
    summary: 'Many queries changing together usually has one outside cause.',
    steps: [
      'Check for a release, a bulk job, a lock or host load at the time this started.',
    ],
  },
  new_heavy_query: {
    summary: "RDST can't see which release introduced a query.",
    steps: [
      'Find the code path that runs it and check it runs as often as intended.',
    ],
  },
  query_stopped_running: {
    summary: "RDST can't see your application.",
    steps: [
      'If a release or a job change removed this query, resolve this item.',
      'If not, check the service or scheduled job that runs it.',
    ],
  },
  traffic_spike: {
    summary: 'The extra calls come from outside the database.',
    steps: [
      'Check for a release, a retry loop or a batch job that started at the same time.',
      'If the same reads repeat, try caching this query on the Readyset tab.',
    ],
  },
}

/** `empty` files nothing; `first_hour` has only its first two open items. */
export function inboxItems(
  now: number,
  scenario: FixtureScenario = 'localdev'
): InboxItem[] {
  if (scenario === 'empty') return []
  const K = FIXTURE_KEYS
  const changedKeys = SPECS.slice(13, 30).map((spec) => queryKey(spec.sql))
  const open: InboxSpec[] = [
    {
      type: 'many_queries_changed',
      severity: 0.9,
      title: 'Many queries changed at once',
      summary: '17 queries changed at the same time.',
      ago: 2 * HOUR,
      keys: changedKeys,
      delivery: 'notify',
      body: { count: 17, heaviest_key: K.regressed, heaviest_change: 3.1 },
    },
    {
      type: 'stuck_query',
      severity: 0.8,
      title: 'Stuck query',
      summary: 'Running for over a minute and still going.',
      ago: 40 * MIN,
      keys: [K.stuck],
      delivery: 'notify',
      body: { seconds: 180 },
    },
    {
      type: 'latency_regression',
      severity: 0.74,
      title: 'Query latency increased',
      summary: 'Latency 0.08 ms -> 0.58 ms, 25% of database time.',
      ago: 14 * HOUR,
      keys: [K.regressed],
      state: 'seen',
      delivery: 'notify',
      body: { relevance_rule: 'top_10_24h' },
    },
    {
      type: 'rollback_or_deadlock_spike',
      severity: 0.7,
      title: 'Rollbacks or deadlocks increased',
      summary: 'Rollbacks 2.0 -> 42 a minute.',
      ago: 7 * HOUR,
      subject: 'database',
      delivery: 'notify',
      body: {
        numbers: { rollbacks_per_min: 42, rollbacks_per_min_before: 2 },
      },
    },
    {
      type: 'new_heavy_query',
      severity: 0.66,
      title: 'New heavy query',
      summary:
        'This new query takes 96 ms a call and uses 11% of database time.',
      ago: 3 * HOUR,
      keys: [K.newHeavy],
      delivery: 'notify',
      body: { relevance_rule: 'top_10_1h' },
    },
    {
      type: 'traffic_spike',
      severity: 0.62,
      title: 'Query traffic increased',
      summary:
        'This query runs 24.6 calls a second, up from its usual 6.1. It uses 9.8% of database time.',
      ago: 90 * MIN,
      keys: [queryKey(SPECS[1].sql)],
      delivery: 'inbox',
    },
    {
      type: 'query_stopped_running',
      severity: 0.45,
      title: 'Query stopped running',
      summary: 'Ran 18.0 calls a minute, then stopped.',
      ago: 5 * HOUR,
      keys: [queryKey(SPECS[23].sql)],
      delivery: 'inbox',
    },
    {
      type: 'query_stopped_running',
      severity: 0.45,
      title: 'Query stopped running',
      summary: 'Ran 12.0 calls a minute, then stopped.',
      ago: 5 * HOUR - 2 * MIN,
      keys: [queryKey(SPECS[25].sql)],
      delivery: 'inbox',
    },
    {
      type: 'query_stopped_running',
      severity: 0.45,
      title: 'Query stopped running',
      summary: 'Ran 6.0 calls a minute, then stopped.',
      ago: 5 * HOUR - 3 * MIN,
      keys: [K.unavailable],
      state: 'seen',
      delivery: 'inbox',
    },
    {
      type: 'daily_summary',
      severity: 0.4,
      title: 'Daily report ready',
      summary:
        'Two range queries on orders.status carry most of the load; one query sleeps 180 s a call.',
      ago: 14 * HOUR + 20 * MIN,
      subject: '2026-09-28',
      delivery: 'inbox',
      body: { report_id: 59 },
    },
    {
      type: 'daily_summary',
      severity: 0.4,
      title: 'Daily report ready',
      summary: 'The database is steady; one index would help three queries.',
      ago: 14 * HOUR + 25 * MIN,
      target: 'aurora-pg',
      subject: '2026-09-28',
      delivery: 'inbox',
      body: { report_id: 58 },
    },
  ]
  const recovered: InboxSpec[] = [
    {
      type: 'latency_regression',
      severity: 0.8,
      title: 'Query latency increased',
      summary: 'Back to normal: latency 312 ms -> 4.1 ms.',
      ago: 6 * HOUR,
      keys: [K.fixConfirmed],
      state: 'resolved',
      resolvedBy: 'code',
      recovered: true,
      delivery: 'notify',
    },
    {
      type: 'traffic_spike',
      severity: 0.62,
      title: 'Query traffic increased',
      summary: 'Back to normal: 4.4 calls a second.',
      ago: 4 * HOUR,
      keys: [K.fixNoEffect],
      state: 'resolved',
      resolvedBy: 'code',
      recovered: true,
      delivery: 'inbox',
    },
  ]
  const muted: InboxSpec[] = [
    {
      type: 'latency_regression',
      severity: 0.62,
      title: 'Query latency increased',
      summary: 'Latency 18 ms -> 24 ms, 3% of database time.',
      ago: 6 * HOUR,
      keys: [K.muted],
      mutedBy: 1,
      delivery: 'inbox',
    },
    {
      type: 'new_heavy_query',
      severity: 0.61,
      title: 'New heavy query',
      summary:
        'This new query takes 21 ms a call and uses 4% of database time.',
      ago: 28 * HOUR,
      keys: [K.muted],
      mutedBy: 1,
      delivery: 'inbox',
    },
    {
      type: 'cache_hit_ratio_drop',
      severity: 0.55,
      title: 'Cache hit ratio dropped',
      summary: 'Cache hit ratio 99.1% -> 97.9%.',
      ago: 20 * HOUR,
      subject: 'database',
      mutedBy: 2,
      delivery: 'inbox',
    },
  ]
  const doneTypes = [
    [
      'latency_regression',
      0.7,
      'Query latency increased',
      'Latency 1.2 ms -> 3.4 ms, 6% of database time.',
    ],
    [
      'stuck_query',
      0.8,
      'Stuck query',
      'Running for over a minute and still going.',
    ],
    [
      'traffic_spike',
      0.62,
      'Query traffic increased',
      '9.1 calls a second, usually 2.3.',
    ],
    [
      'query_stopped_running',
      0.45,
      'Query stopped running',
      'Ran 12.0 calls a minute, then stopped.',
    ],
    [
      'daily_summary',
      0.4,
      'Daily report ready',
      'The database is steady; nothing needs you.',
    ],
    [
      'rollback_or_deadlock_spike',
      0.68,
      'Rollbacks or deadlocks increased',
      'Rollbacks 2.0 -> 18 a minute.',
    ],
  ] as const
  const done: InboxSpec[] = Array.from({ length: 31 }, (_, i) => {
    const [type, severity, title, summary] = doneTypes[i % doneTypes.length]
    const byCode = type === 'traffic_spike' || type === 'query_stopped_running'
    return {
      type,
      severity,
      title,
      summary,
      ago: 26 * HOUR + i * 97 * MIN,
      keys:
        type === 'daily_summary' || type === 'rollback_or_deadlock_spike'
          ? []
          : [queryKey(SPECS[(i * 7) % 31].sql)],
      target: i % 5 === 4 ? 'aurora-pg' : FIXTURE_TARGET,
      state: i % 4 === 3 ? 'dismissed' : 'resolved',
      resolvedBy: i % 4 === 3 ? 'user' : byCode ? 'code' : 'user',
      recovered: i % 4 !== 3 && byCode,
      delivery: 'inbox',
    }
  })
  // The bridges to Actions: one Changes ready event, and a change that did not move.
  const bridges: InboxSpec[] = [
    {
      type: 'changes_ready',
      severity: 0.6,
      title: `3 changes ready on ${FIXTURE_TARGET}`,
      summary:
        '1 to do first, starting with index orders (status, created_at).',
      ago: 9 * HOUR,
      subject: 'plan',
      state: 'seen',
      delivery: 'notify',
      body: { db: FIXTURE_TARGET, count: 3, do_first: 1 },
      link: { kind: 'actions', target: FIXTURE_TARGET, label: 'Open Actions' },
      view: {
        kind: 'changes_ready',
        band: 'watch',
        changes: {
          count: 3,
          do_first: 1,
          titles: [
            'Index orders (status, created_at)',
            'Index customers (email)',
            'Lower idle_in_transaction_session_timeout',
          ],
          action_ids: [11, 12, 13],
        },
      },
    },
    {
      type: 'no_change_yet',
      severity: 0.3,
      title: 'No change yet: SELECT on customers by email',
      summary: '9.8 ms before, 9.6 ms after over 120 calls.',
      ago: 27 * HOUR,
      keys: [K.fixNoEffect],
      state: 'resolved',
      resolvedBy: 'code',
      delivery: 'inbox',
      body: {
        action_id: 12,
        detected_at: now - 27 * HOUR - 3 * DAY,
        before_ms: 9.8,
        after_ms: 9.6,
        calls: 120,
      },
      link: { kind: 'actions', target: FIXTURE_TARGET, label: 'Open Applied' },
      view: { kind: 'no_change_yet', band: 'closed' },
    },
  ]
  const specByKey = new Map(SPECS.map((spec) => [queryKey(spec.sql), spec]))
  const specs =
    scenario === 'first_hour'
      ? open.slice(0, 2)
      : [...open, ...recovered, ...muted, ...done, ...bridges]
  return specs.map((spec, index) => {
    const created = now - spec.ago
    const target = spec.target ?? FIXTURE_TARGET
    const keys = spec.keys ?? []
    const subject = spec.subject ?? (keys.length === 1 ? keys[0] : target)
    const itemBand: SeverityBand = spec.recovered
      ? 'recovered'
      : band(spec.type, spec.severity)
    const positive = spec.recovered || spec.type === 'query_improved'
    const state = spec.state ?? 'new'
    const finished = state === 'resolved' || state === 'dismissed'
    const closedAt = spec.recovered ? created + 3 * HOUR : created + 40 * MIN
    const subjectSpec = keys.length === 1 ? specByKey.get(keys[0]) : undefined
    const body = { ...(spec.body ?? {}) }
    if (spec.recovered) body.resolution = { before: {}, after: {} }
    return {
      id: 1000 - index,
      target,
      type: spec.type,
      subject,
      state,
      severity: spec.severity,
      title: spec.title,
      body_json: body,
      created_at: created,
      updated_at: finished ? closedAt : created,
      resolved_at: finished ? closedAt : null,
      resolved_by: finished ? (spec.resolvedBy ?? 'user') : null,
      seen_count: state === 'new' ? 0 : 1,
      reopened_count: 0,
      severity_band: itemBand,
      tone: positive
        ? 'positive'
        : itemBand === 'act_now'
          ? 'negative'
          : itemBand === 'watch'
            ? 'warning'
            : 'neutral',
      summary: spec.summary,
      query_keys: keys,
      subject_label: subjectSpec ? displayName(subjectSpec) : target,
      query_labels: Object.fromEntries(
        keys.flatMap((key) => {
          const known = specByKey.get(key)
          return known ? [[key, displayName(known)]] : []
        })
      ),
      group_key: `${spec.type}:${target}:${Math.floor(created / (6 * HOUR))}`,
      muted_by: spec.mutedBy ?? null,
      trend: trend(
        `${spec.type}:${index}`,
        created,
        subjectSpec?.ms ?? 1,
        positive ? 0.3 : 2.4
      ),
      delivery: spec.delivery ?? 'inbox',
      relevance_rule: (spec.body?.relevance_rule as RelevanceRule) ?? null,
      link: spec.link ?? fixtureLink(spec, target, keys),
      advice: finished ? null : (ADVICE[spec.type] ?? null),
      ...spec.view,
    }
  })
}

const RECOVERED_WINDOW = DAY

/** Closed by RDST on measurements within the last day and not yet seen. */
export function isFreshRecovery(item: InboxItem, now: number): boolean {
  const body =
    typeof item.body_json === 'string'
      ? (JSON.parse(item.body_json) as Record<string, unknown>)
      : item.body_json
  return (
    item.severity_band === 'recovered' &&
    (item.resolved_at ?? 0) >= now - RECOVERED_WINDOW &&
    !body.recovery_seen_at
  )
}

/** Tab and target filtering as the backend applies it; newest first. */
export function filterInbox(
  items: InboxItem[],
  query: InboxQuery = {},
  now: number = FIXTURE_NOW
): InboxItem[] {
  const tab = query.state ?? 'open'
  return items
    .filter((item) => {
      if (query.target && item.target !== query.target) return false
      if (item.delivery === 'digest' && !query.include_digest) return false
      const finished = item.state === 'resolved' || item.state === 'dismissed'
      if (tab === 'done') return finished
      if (tab === 'muted') return !finished && item.muted_by !== null
      if (finished) return isFreshRecovery(item, now)
      return item.muted_by === null || Boolean(query.include_muted)
    })
    .sort((a, b) => b.created_at - a.created_at)
}

export function inboxFixture(now: number, query: InboxQuery = {}): InboxPage {
  return { items: filterInbox(inboxItems(now), query, now) }
}

// Reports

const REPORT_TIMES = [
  { ago: 2 * HOUR + 14 * MIN, trigger: 'user' },
  { ago: 14 * HOUR, trigger: 'nightly' },
  { ago: 21 * HOUR, trigger: 'user' },
  { ago: 38 * HOUR, trigger: 'nightly' },
  { ago: 47 * HOUR, trigger: 'user' },
  { ago: 62 * HOUR, trigger: 'nightly' },
] as const

export function reportSummaries(now: number): ReportSummary[] {
  return REPORT_TIMES.map((entry, index) => ({
    id: 60 - index,
    ts: now - entry.ago,
    trigger: entry.trigger,
    duration_s: 48 + ((index * 37) % 70),
    headline:
      index === 0
        ? 'Load is dominated by two range queries on orders.status; one query sleeps 180 s per call.'
        : index === 1
          ? 'One index would speed up three queries on customers; everything else is steady.'
          : 'The database is healthy; two queries account for most of the load.',
    counts: {
      fix_first: [3, 2, 1, 2, 1, 1][index],
      one_fix: index === 1 ? 1 : index % 2,
      health_warn: index === 0 ? 2 : 1,
      health_ok: index === 0 ? 5 : 6,
    },
    provisional_count: index === 0 ? 2 : 0,
    ...snapshotPeriod(now - entry.ago, entry.trigger),
    sample: index === REPORT_TIMES.length - 1,
    events: [3, 1, 2, 0, 1, 2][index],
    open_events: index === 0 ? 1 : 0,
    look_at: [3, 2, 1, 2, 1, 1][index],
    health_problems: index === 0 ? 2 : 1,
    qps: 12 + index,
  }))
}

function localDay(ts: number): { start: number; day: string } {
  const date = new Date(ts * 1000)
  date.setHours(0, 0, 0, 0)
  const pad = (value: number) => String(value).padStart(2, '0')
  return {
    start: date.getTime() / 1000,
    day: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
  }
}

/** A nightly run covers the day before it; a run by hand covers its day so far. */
function snapshotPeriod(ts: number, trigger: 'user' | 'nightly') {
  const today = localDay(ts)
  if (trigger === 'user')
    return {
      period_start: today.start,
      period_end: ts,
      period_partial: true,
      day: today.day,
    }
  const before = localDay(today.start - 1)
  return {
    period_start: before.start,
    period_end: today.start,
    period_partial: false,
    day: before.day,
  }
}

/** A daily snapshot built from the fixture queries (report version 3). */
export function snapshotFixture(summary: ReportSummary): DaySnapshot {
  const K = FIXTURE_KEYS
  const start = summary.period_start ?? summary.ts - DAY
  const end = summary.period_end ?? summary.ts
  const specs = SPECS.filter((spec) => spec.text !== 'unavailable').slice(0, 12)
  const res = 300
  const xs: number[] = []
  for (let ts = start; ts < end; ts += res) xs.push(ts)
  const top = specs.slice(0, 8)
  const curve = (ts: number) =>
    0.6 + 0.4 * Math.sin((((ts - start) % DAY) / DAY) * 2 * Math.PI - 1.2)
  const qps = [
    ...top.map((spec) => xs.map((ts) => round(spec.qps * curve(ts), 3))),
    xs.map((ts) => round(2.4 * curve(ts), 3)),
  ]
  const totalQps = top.reduce((sum, spec) => sum + spec.qps, 0) + 2.4
  const names: Record<string, string> = {}
  for (const spec of specs) names[queryKey(spec.sql)] = displayName(spec)
  const queries = specs.map((spec, index) => {
    const key = queryKey(spec.sql)
    const status =
      key === K.regressed
        ? ('needs_attention' as const)
        : key === K.newHeavy
          ? ('watch' as const)
          : index % 3 === 0
            ? ('fix_ready' as const)
            : ('healthy' as const)
    return {
      query_key: key,
      display_name: displayName(spec),
      calls: round(spec.qps * (end - start), 0),
      qps: spec.qps,
      total_ms: spec.qps * (end - start) * spec.ms,
      mean_ms: spec.ms,
      share: round(0.4 / (index + 1), 3),
      prior_calls: round(spec.qps * (end - start) * 0.9, 0),
      prior_mean_ms: key === K.regressed ? spec.ms / 7 : spec.ms,
      latency_change: key === K.regressed ? 6 : 0,
      status,
      status_reason:
        status === 'needs_attention'
          ? 'Query latency increased'
          : status === 'watch'
            ? 'New heavy query'
            : null,
      score: [42, 88, 61, 95, 73, 55, 37, 91, 66, 80, 49, 99][index],
      grade: (
        ['D', 'B', 'C', 'A', 'B', 'C', 'F', 'A', 'C', 'B', 'D', 'A'] as const
      )[index],
      fix:
        status === 'fix_ready' || key === K.regressed
          ? {
              kind: 'index',
              title: 'Add an index on orders(status, placed_at)',
              sql: 'CREATE INDEX idx_orders_status_placed_at ON orders (status, placed_at DESC);',
            }
          : null,
      analysis_ref: spec.analysis?.analysis_ref
        ? `/api/query-registry/${key}/analysis/${spec.analysis.analysis_ref}`
        : null,
      analysis_mode: 'analyze',
      analyzed_at: start + 3600,
      headline: null,
      provisional: false,
      new: key === K.newHeavy,
      text: specText(spec),
    }
  })
  const rank = ['needs_attention', 'watch', 'fix_ready']
  const lookAt = queries
    .filter((row) => rank.includes(row.status))
    .sort((a, b) => rank.indexOf(a.status) - rank.indexOf(b.status))
    .slice(0, summary.look_at ?? 3)
  const events: DaySnapshot['events'] = [
    {
      ts: start + 14 * HOUR + 2 * MIN,
      kind: 'created',
      item_id: 401,
      type: 'latency_regression',
      what: 'Got slower',
      title: 'Query latency increased',
      detail: 'Latency up 7.2x: 0.08 ms -> 0.58 ms',
      query_keys: [K.regressed],
      state: 'open',
      tone: 'negative',
    },
    {
      ts: start + 9 * HOUR + 40 * MIN,
      kind: 'created',
      item_id: 402,
      type: 'traffic_spike',
      what: 'Traffic jumped',
      title: 'Query traffic increased',
      detail: 'Calls up 3.1x: 1.2 a second -> 3.7 a second',
      query_keys: [K.fixConfirmed],
      state: 'resolved',
      resolved_at: start + 10 * HOUR + 5 * MIN,
      tone: 'positive',
    },
    {
      ts: start + 11 * HOUR,
      kind: 'index',
      item_id: null,
      type: 'index_added',
      what: 'Index added',
      title: 'idx_orders_status on orders',
      detail: '',
      definition:
        'CREATE INDEX idx_orders_status ON public.orders USING btree (status)',
      query_keys: [],
      state: 'done',
      tone: 'positive',
    },
  ]
  const brief = [
    `localdev averaged ${round(totalQps, 1)} queries a second, up 12% on the day before.`,
    `{{q:${K.regressed}}} got 7.2x slower at 2:02 PM and is still slow.`,
    'An index on orders went in at 11:00 AM.',
  ]
  return {
    version: 3,
    period: {
      start,
      end,
      partial: Boolean(summary.period_partial),
      day: summary.day ?? localDay(start).day,
      prior_start: start - DAY,
      prior_end: start,
    },
    numbers: [
      {
        metric: 'qps',
        label: 'Queries per second',
        unit: 'qps',
        value: totalQps,
        prior: totalQps / 1.12,
        change: 0.12,
        better: null,
      },
      {
        metric: 'mean_ms',
        label: 'Average latency',
        unit: 'ms',
        value: 6.3,
        prior: 4.1,
        change: 0.54,
        better: 'down',
      },
      {
        metric: 'active_conns_peak',
        label: 'Peak connections',
        unit: 'count',
        value: 23,
        prior: 21,
        change: 0.1,
        better: 'down',
      },
      {
        metric: 'cache_hit_ratio_min',
        label: 'Lowest cache hit',
        unit: 'ratio',
        value: 0.972,
        prior: 0.991,
        change: -0.02,
        better: 'up',
      },
      {
        metric: 'xact_rollback',
        label: 'Rollbacks',
        unit: 'count',
        value: 640,
        prior: 720,
        change: -0.11,
        better: 'down',
      },
      {
        metric: 'deadlocks',
        label: 'Deadlocks',
        unit: 'count',
        value: 0,
        prior: 0,
        change: null,
        better: 'down',
      },
    ],
    peak: { qps: totalQps * 1.4, ts: start + 20 * HOUR },
    chart: {
      res,
      start,
      end,
      xs,
      bands: [
        ...top.map((spec, index) => ({
          key: queryKey(spec.sql),
          kind: 'query' as const,
          display_name: displayName(spec),
          color_index: index,
          share: round(0.4 / (index + 1), 3),
          calls: spec.qps * (end - start),
          qps: spec.qps,
          mean_ms: spec.ms,
        })),
        {
          key: 'other',
          kind: 'other' as const,
          display_name: 'Everything else',
          color_index: null,
          share: 0.05,
          calls: 2.4 * (end - start),
          qps: 2.4,
          mean_ms: 1.2,
        },
      ],
      qps,
      ms: qps.map((row, index) =>
        row.map((value) => round(value * res * (top[index]?.ms ?? 1.2), 3))
      ),
      mean_ms: qps.map((row, index) => row.map(() => top[index]?.ms ?? 1.2)),
      overall_mean_ms: xs.map(() => 6.3),
      markers: events.map((event) => ({
        ts: event.ts,
        kind: event.kind === 'index' ? ('index' as const) : ('event' as const),
        label: event.what,
        query_keys: event.query_keys,
        tone: event.tone,
      })),
    },
    events: events
      .slice(0, Math.max(1, summary.events ?? 3))
      .sort((a, b) => a.ts - b.ts),
    queries,
    look_at: lookAt,
    health: {
      problems: [
        {
          check: 'vacuum_bloat',
          label: 'Tables that need a vacuum',
          status: 'warn',
          summary: '1 table has a lot of dead rows',
          rows: [
            {
              name: 'orders',
              value: '18.0% dead',
              detail: '1,800 dead of 10,000 rows',
            },
          ],
          action:
            'Run VACUUM (ANALYZE) on these tables and check that autovacuum keeps up with their writes.',
          sql: 'VACUUM (ANALYZE) public.orders;',
        },
        {
          check: 'config_audit',
          label: 'Settings off recommendation',
          status: 'warn',
          summary: '1 setting differs from the recommendation',
          rows: [
            {
              name: 'work_mem',
              value: '4 MB, recommended 16 to 32 MB',
              detail: 'Low for aggregation-heavy queries',
            },
          ],
          action:
            "Review these settings against the server's memory and workload.",
          sql: null,
        },
      ].slice(
        0,
        summary.health_problems ?? 2
      ) as DaySnapshot['health']['problems'],
      fine: ['Indexes', 'Connections', 'Replication', 'Cache hit'],
      unavailable: [],
    },
    names,
    facts: brief.map((text) => ({ kind: 'fact', text, keys: [] })),
    brief,
    brief_source: 'facts',
    brief_items: brief.map((text, index) => ({
      topic: ['traffic', 'events', 'index'][index] ?? null,
      text,
      why: [
        'More queries a second means more work for the same server, so latency and connections tend to follow.',
        "An open event is a change RDST saw that hasn't gone back to normal, so it may still be slowing the app.",
      ][index],
    })),
    overview: {
      status: 'warn',
      label: 'Worth a look',
      headline:
        'orders is 18.0% dead rows. work_mem is 4 MB; the check recommends 16 to 32 MB.',
      checks: [
        {
          check: 'cache',
          label: 'Cache hit',
          status: 'ok',
          value: 'Lowest 97.2%',
        },
        {
          check: 'connections',
          label: 'Connections',
          status: 'ok',
          value: 'Peak 23 of 100',
        },
        {
          check: 'vacuum',
          label: 'Vacuum and bloat',
          status: 'warn',
          value: 'orders is 18.0% dead rows',
        },
        {
          check: 'indexes',
          label: 'Indexes',
          status: 'ok',
          value: '41 indexes, all read',
        },
        {
          check: 'settings',
          label: 'Settings',
          status: 'warn',
          value: 'work_mem is 4 MB',
        },
        {
          check: 'replication',
          label: 'Replication',
          status: 'ok',
          value: 'No lag, no idle slots',
        },
        {
          check: 'transactions',
          label: 'Rollbacks and deadlocks',
          status: 'ok',
          value: '640 rollbacks, 0 deadlocks',
        },
      ],
    },
    actions: [
      {
        id: 'vacuum',
        check: 'vacuum',
        title: 'Vacuum 1 bloated table',
        severity: 'warn',
        finding: 'orders is 18.0% dead rows.',
        evidence: [
          { label: 'Worst table', value: 'orders' },
          { label: 'Dead rows there', value: '18.0%' },
        ],
        why: 'Dead rows make every scan read more pages and push live data out of memory.',
        steps:
          'Vacuum them now, then check that autovacuum keeps up with their writes.',
        rows: [
          {
            name: 'orders',
            value: '18.0% dead',
            detail: '1,800 dead of 10,000 rows',
          },
        ],
        sql: 'VACUUM (ANALYZE) public.orders;',
        setting: null,
        restart: false,
        caveat: null,
        query_keys: [],
        pick: 0,
        model_why: `The heaviest query, {{q:${K.regressed}}}, reads orders.`,
      },
      {
        id: 'setting_work_mem',
        check: 'settings',
        title: 'Raise work_mem so sorts stay in memory',
        severity: 'warn',
        finding: 'work_mem is 4 MB; the check recommends 16 to 32 MB.',
        evidence: [
          { label: 'work_mem', value: '4 MB' },
          { label: 'Recommended', value: '16 to 32 MB' },
        ],
        why: 'Each sort or hash gets this much memory before it spills to disk.',
        steps: 'Change it and reload the configuration; no restart needed.',
        rows: [],
        sql: "ALTER SYSTEM SET work_mem = '16MB';\nSELECT pg_reload_conf();",
        setting: { name: 'work_mem', current: '4 MB', target: '16 to 32 MB' },
        restart: false,
        caveat: 'Low for aggregation-heavy queries',
        query_keys: [],
        pick: null,
        model_why: null,
      },
    ],
    vitals: {
      res,
      xs,
      qps: xs.map((ts) => round(totalQps * curve(ts), 3)),
      mean_ms: xs.map(() => 6.3),
      active_conns: xs.map((ts) => Math.round(12 + 10 * curve(ts))),
      cache_hit_ratio: xs.map((ts) => round(0.99 - 0.02 * curve(ts), 4)),
      xact_rollback: xs.map(() => 2),
      deadlocks: xs.map(() => 0),
    },
    spikes: [
      {
        id: 'S1',
        metric: 'traffic',
        start: start + 9 * HOUR + 40 * MIN,
        end: start + 10 * HOUR + 5 * MIN,
        peak_ts: start + 9 * HOUR + 50 * MIN,
        peak: round(totalQps * 2.1, 2),
        usual: round(totalQps, 2),
        ratio: 2.1,
        queries: [
          { query_key: K.fixConfirmed, before: 1.2, during: 3.7, ratio: 3.08 },
        ],
        events: [
          {
            ts: start + 9 * HOUR + 40 * MIN,
            what: 'Traffic jumped',
            title: 'Query traffic increased',
          },
        ],
        summary: 'Traffic spiked 9:40 AM to 10:05 AM.',
      },
    ],
    engine: 'postgresql',
  }
}

export function reportHistoryFixture(
  scenario: FixtureScenario,
  now: number,
  limit = 10
): ReportHistory {
  if (scenario === 'empty' || scenario === 'first_hour') return { reports: [] }
  return { reports: reportSummaries(now).slice(0, limit) }
}

function reportRefs(keys: string[]) {
  const refs: DatabaseReport['refs'] = {}
  for (const spec of SPECS) {
    const key = queryKey(spec.sql)
    if (!keys.includes(key)) continue
    const text = specText(spec)
    refs[key] = {
      display_name: displayName(spec),
      text_snippet: text
        ? text.length > 90
          ? `${text.slice(0, 89)}...`
          : text
        : '',
    }
  }
  return refs
}

export function reportFixture(
  scenario: FixtureScenario,
  id: number | 'latest',
  now: number,
  target = FIXTURE_TARGET
): DatabaseReport | null {
  const summaries = reportHistoryFixture(scenario, now).reports
  const summary =
    id === 'latest' ? summaries[0] : summaries.find((s) => s.id === id)
  if (!summary) return null
  const K = FIXTURE_KEYS
  const body: ReportBody = {
    summary: `Load is dominated by {{q:${K.fixConfirmed}}} and {{q:${K.regressed}}}, both range scans on orders.status. {{q:${K.stuck}}} sleeps 180 s per call and holds a connection each time. ${K.fixNoEffect} got a new index that didn't change its latency.`,
    fix_first: [
      {
        query_key: K.stuck,
        title: 'Stop the pg_sleep(180) caller',
        why: 'This query used 540 s of database time in 3 calls and accounted for 29 % of load. It first appeared in the last 24 h.',
        action:
          'Find the client issuing pg_sleep and remove the call, or move it off the primary.',
      },
      {
        query_key: K.regressed,
        title: 'Index orders by status and placed_at',
        why: 'Latency went from 0.08 ms to 0.58 ms after 14:02; 25 % of load.',
        action:
          'CREATE INDEX CONCURRENTLY idx_orders_status_placed_at ON orders (status, placed_at DESC);',
      },
      {
        query_key: K.newHeavy,
        title: 'Pre-aggregate revenue by country',
        why: 'This query first appeared 3 h ago. It takes 96 ms per call and uses 11 % of load.',
        action: 'Cache it in Readyset or keep a daily rollup table.',
      },
    ].slice(0, summary.counts.fix_first),
    one_fix_several_queries: summary.counts.one_fix
      ? [
          {
            table: 'customers',
            columns: ['country'],
            query_keys: [
              queryKey(SPECS[12].sql),
              queryKey(SPECS[19].sql),
              queryKey(SPECS[24].sql),
            ],
            action:
              'CREATE INDEX CONCURRENTLY idx_customers_country ON customers (country);',
          },
        ]
      : [],
    health: [
      {
        check: 'cache_hit_ratio',
        label: 'Cache hit ratio',
        value: '99.1 %',
        status: 'ok',
        note: 'Most reads come from memory.',
      },
      {
        check: 'deadlocks',
        label: 'Deadlocks',
        value: '3 in 24 h',
        status: 'warn',
        note: 'All during the 09:00 rollback spike.',
      },
      {
        check: 'connections',
        label: 'Connections',
        value: '14 of 100',
        status: 'ok',
        note: 'Peak 23 at 11:40.',
      },
      {
        check: 'vacuum',
        label: 'Vacuum and bloat',
        value: 'orders 18 % dead rows',
        status: 'warn',
        note: 'Autovacuum last ran 26 h ago on orders.',
      },
      {
        check: 'index_health',
        label: 'Index health',
        value: '1 unused index',
        status: 'info',
        note: 'idx_orders_placed_at has had no scans since it was added.',
      },
      {
        check: 'config',
        label: 'Configuration',
        value: 'Collected',
        status: 'info',
        note: 'shared_buffers 128 MB, work_mem 4 MB.',
      },
      {
        check: 'rollbacks',
        label: 'Rollbacks',
        value: '0.4 % of transactions',
        status: 'ok',
        note: '',
      },
    ],
    changed_since_yesterday: [
      `{{q:${K.regressed}}} is 7.2x slower since 14:02.`,
      `{{q:${K.fixConfirmed}}} is fixed: idx_orders_status took it from 312 ms to 4.1 ms.`,
      `idx_customers_email didn't help {{q:${K.fixNoEffect}}} (0.49 ms before, 0.5 ms after).`,
      `{{q:${K.newHeavy}}} is new and already 11 % of load.`,
    ],
    provisional_query_keys: summary.provisional_count
      ? [K.newHeavy, K.analysisFailed]
      : [],
    narrative_error: null,
  }
  const mentioned = new Set<string>()
  const text = JSON.stringify(body)
  for (const match of text.matchAll(/[0-9a-f]{12}/g)) mentioned.add(match[0])
  body.version = 3
  body.snapshot = snapshotFixture(summary)
  return {
    id: summary.id,
    target,
    ts: summary.ts,
    trigger: summary.trigger,
    state: 'done',
    provisional: summary.provisional_count > 0,
    progress: null,
    report_json: body,
    refs: reportRefs([...mentioned]),
    period_start: summary.period_start,
    period_end: summary.period_end,
    period_partial: summary.period_partial,
    sample: summary.sample,
  }
}

/**
 * One full "Analyze database" run, queued to done: 15 queries picked, analyses that
 * finish, skip, fail and go provisional after the budget, six health checks, then the
 * narrative. reportEventsRunningFixture is a mid-run snapshot of the same run.
 */
export function reportEventsFixture(now: number): ReportEvent[] {
  const startedAt = now - 102
  const top = SPECS.filter((spec) => spec.text !== 'unavailable').slice(0, 15)
  const selected = top.map((spec, index) => ({
    key: queryKey(spec.sql),
    display_name: displayName(spec),
    db_time_share: round(0.29 / (index + 1), 3),
  }))
  const events: ReportEvent[] = [
    { type: 'run', state: 'running', started_at: startedAt, elapsed_s: 0 },
    {
      type: 'step',
      step: 'select',
      state: 'running',
      detail: 'Picking the queries with the most database time',
    },
    { type: 'selected', queries: selected },
    {
      type: 'step',
      step: 'select',
      state: 'done',
      detail: `Picked the ${selected.length} queries with the most database time`,
    },
    {
      type: 'step',
      step: 'analyses',
      state: 'running',
      detail: `0 of ${selected.length}`,
    },
    { type: 'step', step: 'health', state: 'running', detail: '0 of 6' },
  ]
  const results = [
    'index scan, optimal',
    'index scan, optimal',
    'seq scan on orders, index recommended',
    null,
    'index on lower(email) needed, current index unused',
    'This query uses a hash join and takes 96 ms. Readyset can cache it.',
    'index scan after idx_products_category',
    'index scan, optimal',
    null,
    'seq scan on products, trigram index recommended',
    null,
    null,
    'group by customers.country, index recommended',
    'sort on orders.total, index recommended',
    'index scan, optimal',
  ]
  const checks = [
    ['cache_hit_ratio', 'Cache hit ratio'],
    ['deadlocks', 'Deadlocks'],
    ['connections', 'Connections'],
    ['vacuum', 'Vacuum and bloat'],
    ['index_health', 'Index health'],
    ['config', 'Configuration'],
  ] as const
  let done = 0
  selected.forEach((query, index) => {
    const spec = top[index]
    events.push({
      type: 'analysis',
      key: query.key,
      state: 'queued',
      reason: null,
      result_line: null,
      duration_s: null,
    })
    if (
      index === 3 ||
      spec.sql.startsWith('UPDATE') ||
      spec.sql.startsWith('INSERT')
    ) {
      events.push({
        type: 'analysis',
        key: query.key,
        state: 'skipped',
        reason: spec.analysis?.reason ?? 'Not safe to run.',
        result_line: null,
        duration_s: null,
      })
    } else if (index === 8) {
      events.push({
        type: 'analysis',
        key: query.key,
        state: 'running',
        reason: null,
        result_line: null,
        duration_s: null,
      })
      events.push({
        type: 'analysis',
        key: query.key,
        state: 'failed',
        reason: 'The analysis timed out after 60 s.',
        result_line: null,
        duration_s: 60,
      })
    } else if (index >= 13) {
      events.push({
        type: 'analysis',
        key: query.key,
        state: 'provisional',
        reason: 'Plan only - the full analysis continues in the background.',
        result_line: results[index],
        duration_s: 1,
      })
    } else {
      events.push({
        type: 'analysis',
        key: query.key,
        state: 'running',
        reason: null,
        result_line: null,
        duration_s: null,
      })
      events.push({
        type: 'analysis',
        key: query.key,
        state: 'done',
        reason: null,
        result_line: results[index] ?? 'index scan, optimal',
        duration_s: 3 + ((index * 5) % 11),
      })
    }
    done += 1
    events.push({
      type: 'step',
      step: 'analyses',
      state: 'running',
      detail: `${done} of ${selected.length}`,
    })
    if (index % 2 === 1 && index / 2 < checks.length) {
      const [check, label] = checks[Math.floor(index / 2)]
      events.push({ type: 'health_check', check, label, state: 'running' })
      events.push({ type: 'health_check', check, label, state: 'done' })
      events.push({
        type: 'step',
        step: 'health',
        state: 'running',
        detail: `${Math.floor(index / 2) + 1} of 6`,
      })
    }
    if (index === 12)
      events.push({
        type: 'budget',
        message:
          'Finishing with plans only for 2 queries - full analyses continue in the background',
      })
    events.push({
      type: 'run',
      state: 'running',
      started_at: startedAt,
      elapsed_s: 6 + index * 6,
    })
  })
  events.push({
    type: 'step',
    step: 'analyses',
    state: 'done',
    detail: `${selected.length} of ${selected.length}`,
  })
  events.push({ type: 'step', step: 'health', state: 'done', detail: '6 of 6' })
  events.push({
    type: 'step',
    step: 'narrative',
    state: 'running',
    detail: 'Writing the report',
  })
  const sections = [
    'summary',
    'fix_first',
    'one_fix_several_queries',
    'health',
    'changed',
  ] as const
  for (const section of sections)
    events.push({ type: 'section', section, state: 'writing' })
  for (const section of sections)
    events.push({ type: 'section', section, state: 'ready' })
  events.push({
    type: 'step',
    step: 'narrative',
    state: 'done',
    detail: 'Report written',
  })
  events.push({
    type: 'run',
    state: 'done',
    started_at: startedAt,
    elapsed_s: 102,
  })
  return events
}

/**
 * Mid-run snapshot of the same run: 9 of 15 analyses finished (done, skipped and
 * failed), the tenth running, the rest queued, health checks 4 of 6.
 */
export function reportEventsRunningFixture(now: number): ReportEvent[] {
  const events = reportEventsFixture(now)
  const selected = events.find((event) => event.type === 'selected')
  const tenth =
    selected?.type === 'selected' ? selected.queries[9]?.key : undefined
  const cut = events.findIndex(
    (event) =>
      event.type === 'analysis' &&
      event.key === tenth &&
      event.state === 'running'
  )
  const snapshot = events.slice(0, cut + 1)
  // Queued rows for every selected query come first so the list renders in full.
  const queued = events.filter(
    (event) =>
      event.type === 'analysis' &&
      event.state === 'queued' &&
      !snapshot.includes(event)
  )
  return [...snapshot, ...queued]
}
