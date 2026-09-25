// Fixtures for the Database page v4 endpoints (CONTRACT.md "Database page v4"): the load
// breakdown, per-query latency, one query row, the deep analysis header, and the v4 fields
// on query rows. Built from the classic fixtures so rows, bands and colors agree.
import {
  type FixtureScenario,
  historySpan,
  queriesFixture,
} from './fixtures'
import type {
  AnalyzableCode,
  ApiRange,
  DeepAnalysisDigest,
  LatencyBreakdown,
  LoadBreakdown,
  LoadPoint,
  MonitorQuery,
  MonitorQueryPage,
  MonitorRange,
  RangeAvailability,
  Resolution,
  TimeWindow,
} from './types'

export const LOAD_TOP = 8
const MIN = 60
const HOUR = 3600
const DAY = 86400
const SECONDS: Record<Exclude<ApiRange, 'all'>, number> = {
  '5m': 5 * MIN,
  '15m': 15 * MIN,
  '1h': HOUR,
  '24h': DAY,
  '7d': 7 * DAY,
  '30d': 30 * DAY,
  '90d': 90 * DAY,
}
const ORDER: readonly ApiRange[] = ['5m', '15m', '1h', '24h', '7d', '30d', '90d', 'all']

/** The classic fixture range that covers an API range (5m and 15m clip the hour). */
export function fixtureRange(range: ApiRange): MonitorRange {
  return range === '5m' || range === '15m' ? '1h' : range
}

function recordedLabel(span: number): string {
  if (span <= 0) return 'No readings yet'
  if (span < HOUR) return `${Math.max(1, Math.round(span / MIN))} minutes recorded`
  if (span < 48 * HOUR) return `${Math.round((span / HOUR) * 10) / 10} hours recorded`
  return `${(span / DAY).toFixed(1)} days recorded`
}

function rangesFor(span: number): RangeAvailability[] {
  let previous = 0
  return ORDER.map((range) => {
    const enabled = range === 'all' ? span > HOUR : span > previous
    if (range !== 'all') previous = SECONDS[range]
    const res: Resolution =
      range === '24h'
        ? 300
        : range === '7d' || range === '30d'
          ? 3600
          : range === '90d'
            ? 86400
            : 60
    return { range, enabled, res, reason: enabled ? null : recordedLabel(span) }
  })
}

const REFUSED: [RegExp, AnalyzableCode, (verb: string) => string][] = [
  [
    /^\s*(insert|update|delete|merge)\b/i,
    'write',
    (verb) => `${verb} changes data, so RDST doesn't run it.`,
  ],
  [
    /^\s*(drop|create|alter|truncate)\b/i,
    'ddl',
    (verb) => `${verb} changes the schema, so RDST doesn't run it.`,
  ],
  [
    /^\s*(analyze|vacuum|reindex)\b/i,
    'maintenance',
    (verb) => `${verb} is a maintenance command, so there's no query plan to analyze.`,
  ],
]

/** The v4 row fields: analyzability and the band color for the same range. */
export function withV4Fields(page: MonitorQueryPage): MonitorQueryPage {
  const ranked = [...page.queries]
    .filter((row) => row.db_time_ms > 0)
    .sort((a, b) => b.db_time_ms - a.db_time_ms || a.key.localeCompare(b.key))
  const colors = new Map(ranked.slice(0, LOAD_TOP).map((row, index) => [row.key, index]))
  const queries = page.queries.map((row): MonitorQuery => {
    const refused = REFUSED.find(([pattern]) => pattern.test(row.text))
    const sleeps = /\bpg_sleep\s*\(/i.test(row.text)
    const reason = refused
      ? refused[2](row.text.trim().split(/\s+/)[0].toUpperCase())
      : sleeps
        ? "This query calls pg_sleep, which RDST won't run."
        : !row.text
          ? "RDST doesn't have this query's text yet, so there's nothing to analyze."
          : null
    return {
      ...row,
      analyzable: reason === null,
      analyzable_code: refused
        ? refused[1]
        : sleeps
          ? 'unsafe_function'
          : row.text
            ? 'ok'
            : 'no_text',
      not_analyzable_reason: reason,
      analysis_note: null,
      color_index: colors.get(row.key) ?? null,
      assessment: row.assessment ?? null,
    }
  })
  return { ...page, queries }
}

export function queriesV4Fixture(
  scenario: FixtureScenario,
  range: ApiRange,
  now: number
): MonitorQueryPage {
  return withV4Fields(queriesFixture(scenario, fixtureRange(range), now))
}

function clip<T extends { ts: number }>(
  points: T[],
  range: ApiRange,
  now: number,
  window?: TimeWindow
) {
  if (window)
    return points.filter((point) => point.ts >= window.from && point.ts <= window.to)
  if (range === '5m' || range === '15m')
    return points.filter((point) => point.ts >= now - SECONDS[range])
  return points
}

export function loadFixture(
  scenario: FixtureScenario,
  range: ApiRange,
  now: number,
  target = 'localdev',
  top = LOAD_TOP,
  window?: TimeWindow
): LoadBreakdown {
  const page = queriesV4Fixture(scenario, range, now)
  const ranked = [...page.queries]
    .filter((row) => row.db_time_ms > 0)
    .sort((a, b) => b.db_time_ms - a.db_time_ms || a.key.localeCompare(b.key))
  const leaders = ranked.slice(0, top)
  const rest = ranked.slice(top)
  const totalMs = ranked.reduce((sum, row) => sum + row.db_time_ms, 0)
  const totalCalls = ranked.reduce((sum, row) => sum + row.qps, 0)
  const bands: LoadBreakdown['bands'] = leaders.map((row, index) => ({
    key: row.key,
    kind: 'query',
    display_name: row.display_name,
    color_index: index,
    share: totalMs ? row.db_time_ms / totalMs : 0,
    total_ms: row.db_time_ms,
    calls: row.qps,
    mean_ms: row.mean_ms,
  }))
  if (rest.length) {
    const restMs = rest.reduce((sum, row) => sum + row.db_time_ms, 0)
    bands.push({
      key: 'other',
      kind: 'other',
      display_name: 'Everything else',
      color_index: null,
      share: totalMs ? restMs / totalMs : 0,
      total_ms: restMs,
      calls: rest.reduce((sum, row) => sum + row.qps, 0),
      mean_ms: null,
    })
  }
  const series = ranked[0]?.series ?? []
  const step = series.length > 1 ? series[1].ts - series[0].ts : MIN
  const points: LoadPoint[] = series.map((point, position) => {
    if (point.qps === null)
      return { ts: point.ts, seconds: step, total_ms: null, calls: null, mean_ms: null }
    const ms: number[] = []
    const calls: number[] = []
    leaders.forEach((row) => {
      const at = row.series[position]
      ms.push(at?.qps && at.mean_ms ? at.qps * step * at.mean_ms : 0)
      calls.push(at?.qps ? at.qps * step : 0)
    })
    if (rest.length) {
      let restMs = 0
      let restCalls = 0
      rest.forEach((row) => {
        const at = row.series[position]
        restMs += at?.qps && at.mean_ms ? at.qps * step * at.mean_ms : 0
        restCalls += at?.qps ? at.qps * step : 0
      })
      ms.push(restMs)
      calls.push(restCalls)
    }
    const allCalls = calls.reduce((sum, value) => sum + value, 0)
    return {
      ts: point.ts,
      seconds: step,
      total_ms: ms,
      calls,
      mean_ms: allCalls ? ms.reduce((sum, value) => sum + value, 0) / allCalls : null,
    }
  })
  const history = historySpan(scenario, now)
  const clipped = clip(points, range, now, window)
  return {
    target,
    range: window ? 'custom' : range,
    res: (step <= MIN ? 60 : step <= 300 ? 300 : step <= HOUR ? 3600 : 86400) as Resolution,
    start: clipped[0]?.ts ?? now,
    end: window?.to ?? now,
    history,
    recorded_label: recordedLabel(history.span_seconds),
    ranges: rangesFor(history.span_seconds),
    bands,
    points: clipped,
    total_ms: totalMs,
    calls: totalCalls,
  }
}

export function loadLatencyFixture(
  scenario: FixtureScenario,
  range: ApiRange,
  now: number,
  keys: string[],
  target = 'localdev',
  window?: TimeWindow
): LatencyBreakdown {
  const rows = new Map(
    queriesV4Fixture(scenario, range, now).queries.map((row) => [row.key, row])
  )
  const base = rows.get(keys[0])?.series ?? [...rows.values()][0]?.series ?? []
  const points = base.map((point, position) => {
    const means = keys.map((key) => rows.get(key)?.series[position]?.mean_ms ?? null)
    const present = means.filter((value): value is number => value !== null)
    return {
      ts: point.ts,
      mean_ms: means,
      overall_mean_ms: present.length
        ? present.reduce((sum, value) => sum + value, 0) / present.length
        : null,
    }
  })
  return {
    target,
    range: window ? 'custom' : range,
    res: 60,
    keys,
    points: clip(points, range, now, window),
  }
}

export function queryV4Fixture(
  scenario: FixtureScenario,
  key: string,
  range: ApiRange,
  now: number
): MonitorQuery | null {
  return (
    queriesV4Fixture(scenario, range, now).queries.find((row) => row.key === key) ?? null
  )
}

export function queryAnalysisFixture(
  scenario: FixtureScenario,
  key: string,
  now: number
): DeepAnalysisDigest | null {
  const row = queryV4Fixture(scenario, key, '24h', now)
  if (!row || !row.analyzable) return null
  const table = row.tables[0] ?? 'orders'
  return {
    query_key: key,
    analysis_ref: `/api/query-registry/${key}/analysis/fixture_${key}`,
    analyzed_at: now - 2 * HOUR,
    mode: 'analyze',
    mode_label: 'Ran the query (EXPLAIN ANALYZE)',
    rating: 'fair',
    efficiency_score: 58,
    plan_summary: {
      text: `Aggregate, mostly Seq Scan on ${table}; took 41 ms; read 60,069 rows to return 1`,
      root_node: 'Aggregate',
      execution_ms: 41,
      rows_examined: 60069,
      rows_returned: 1,
    },
    findings: [
      `Sequential scan reads all of ${table} to return one row.`,
      'The filter column has no index, so every call reads the table.',
    ],
    fix: {
      kind: 'index',
      title: `Add an index on ${table}(status)`,
      sql: `CREATE INDEX idx_${table}_status ON ${table} (status)`,
      why: 'An index on the filter column lets the planner read only the matching rows.',
      expected_gain: {
        measured: false,
        text: 'Estimated impact: high',
        before_ms: null,
        after_ms: null,
        cost_reduction_pct: null,
      },
    },
    other_fixes: [],
    headline: `Needs an index on ${table}(status)`,
    fix_ready: true,
  }
}
