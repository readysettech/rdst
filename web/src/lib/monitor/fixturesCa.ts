// Fixtures for the Continuous Analysis redesign, one scenario per mock state, all on the
// canonical timeline of docs/plans/ca-redesign-plan.md section 3.7: demo monitored since
// October 1, today Thursday October 8 at 9:56:30, the daily report is Report #31 for
// October 7 run at 12:00 AM, 16 queries with 9 fixes, the stuck query filed at 9:55, the
// index-fix item at 9:51, the fix confirmed at 8:12. Values and copy come from the
// mockups in docs/plans/ca-redesign-mockups/. Endpoints a scenario leaves out fall back
// to the classic scenario `caBase` names.
import type {
  ActionCard,
  ActionsPage,
  IndexPlan,
  IndexPlanAdd,
  IndexPlanKeep,
} from './actions'
import type { FixtureScenario } from './fixtures'
import type {
  AnalysisView,
  ApiRange,
  BenchmarkFixesResult,
  DatabaseActivity,
  DatabaseReport,
  DeepAnalysisDigest,
  HealthPoint,
  HomeDatabase,
  HomeLogEntry,
  HomeModel,
  HomeStripRow,
  InboxItem,
  InboxItemView,
  LoadBreakdown,
  LoadPoint,
  MonitorHealth,
  MonitoringLimit,
  MonitorPulsePage,
  MonitorQuery,
  MonitorQueryPage,
  MonitorRange,
  MonitorSettings,
  QueryStatus,
  RdstDidRow,
  ReportDetailed,
  ReportFixItem,
  ReportHistory,
  ReportSummary,
  SnapshotChart,
  TimeWindow,
  WhatChangedRow,
} from './types'

export type CaScenario =
  | 'ca-home'
  | 'ca-home-one'
  | 'ca-home-empty'
  | 'ca-inbox'
  | 'ca-inbox-report'
  | 'ca-database'
  | 'ca-report'
  | 'ca-first-run'
  | 'ca-unreachable'
  | 'ca-unmonitored'
  | 'ca-signed-out'

export const CA_SCENARIOS: readonly CaScenario[] = [
  'ca-home',
  'ca-home-one',
  'ca-home-empty',
  'ca-inbox',
  'ca-inbox-report',
  'ca-database',
  'ca-report',
  'ca-first-run',
  'ca-unreachable',
  'ca-unmonitored',
  'ca-signed-out',
]

export function asCaScenario(
  value: string | null | undefined
): CaScenario | null {
  return CA_SCENARIOS.includes(value as CaScenario)
    ? (value as CaScenario)
    : null
}

/** The classic scenario that serves the endpoints a redesign scenario leaves out. */
export function caBase(value: string | null | undefined): string | null {
  const scenario = asCaScenario(value)
  if (!scenario) return value ?? null
  const base: FixtureScenario =
    scenario === 'ca-home-empty'
      ? 'empty'
      : scenario === 'ca-first-run'
        ? 'first_hour'
        : 'localdev'
  return base
}

const MIN = 60
const HOUR = 3600
const DAY = 86400

/** Local time on the timeline: Thursday October 8, 2026, plus `day` days. */
export function at(hour: number, minute: number, second = 0, day = 0): number {
  return new Date(2026, 9, 8 + day, hour, minute, second).getTime() / 1000
}

export const CA_NOW = at(9, 56, 30)
export const CA_MIDNIGHT = at(0, 0)
export const CA_TARGET = 'demo'

function noise(label: string, bucket: number): number {
  let h = 2166136261
  const text = `${label}:${bucket}`
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619) >>> 0
  }
  return (h / 0xffffffff) * 2 - 1
}

const round = (value: number, digits = 3) => Number(value.toFixed(digits))

// Databases

interface CaDatabase {
  target: string
  connection: string
  engine_version: string | null
  state: HomeDatabase['state']
  state_since: number | null
  qps: number | null
  open_items: number
  act_now: number
  fixes_ready: number
  fixes_item_id: number | null
}

const DATABASES: CaDatabase[] = [
  {
    target: 'demo',
    connection: 'PostgreSQL 17 at 127.0.0.1:15432',
    engine_version: '17',
    state: 'watching',
    state_since: null,
    qps: 11.7,
    open_items: 2,
    act_now: 1,
    fixes_ready: 9,
    fixes_item_id: 2,
  },
  {
    target: 'database',
    connection: 'PostgreSQL at localhost:5432',
    engine_version: null,
    state: 'cant_reach',
    state_since: at(9, 41),
    qps: null,
    open_items: 1,
    act_now: 1,
    fixes_ready: 0,
    fixes_item_id: null,
  },
  {
    target: 'staging',
    connection: 'PostgreSQL 16 at staging.internal:5432',
    engine_version: '16',
    state: 'watching',
    state_since: null,
    qps: 4.2,
    open_items: 0,
    act_now: 0,
    fixes_ready: 2,
    fixes_item_id: null,
  },
  {
    target: 'analytics',
    connection: 'PostgreSQL 16 at analytics.internal:5432',
    engine_version: '16',
    state: 'watching',
    state_since: null,
    qps: 1.8,
    open_items: 0,
    act_now: 0,
    fixes_ready: 0,
    fixes_item_id: null,
  },
  {
    target: 'warehouse',
    connection: 'PostgreSQL 15 at warehouse.internal:5432',
    engine_version: '15',
    state: 'watching',
    state_since: null,
    qps: 0.6,
    open_items: 0,
    act_now: 0,
    fixes_ready: 0,
    fixes_item_id: null,
  },
  {
    target: 'reports',
    connection: 'PostgreSQL 16 at reports.internal:5432',
    engine_version: '16',
    state: 'not_monitored',
    state_since: null,
    qps: null,
    open_items: 0,
    act_now: 0,
    fixes_ready: 0,
    fixes_item_id: null,
  },
]

// Queries: the 16 on demo (database.html lists the first nine; nine carry a fix).

interface CaRow {
  key: string
  name: string
  sql: string
  tables: string[]
  qps: number
  mean_ms: number
  share: number
  plan_score: number | null
  status: QueryStatus
  fix: { label: string; sql: string; group: string | null } | null
  /** Mean latency over the previous 24 h, minus one; null before a day of history. */
  delta_24h: number | null
}

export const CA_KEYS = {
  postsUsersByCreationdate: 'ca0000000001',
  votesByPostid: 'ca0000000002',
  postsByOwneruserid: 'ca0000000003',
  commentsByPostid: 'ca0000000004',
  postsByCreationdate: 'ca0000000005',
  badgesByUserid: 'ca0000000006',
  postsByPosttypeid: 'ca0000000007',
  usersById: 'ca0000000008',
  postsById: 'ca0000000009',
  commentsByUserid: 'ca0000000010',
  usersByReputation: 'ca0000000011',
  postsByParentid: 'ca0000000012',
  countPostsByParentid: 'ca0000000013',
  tagsByTagname: 'ca0000000014',
  votesByUserid: 'ca0000000015',
  stuck: 'ca0000000016',
} as const

const POSTS_GROUP = 'posts (creationdate, owneruserid, score)'
const PARENTID_GROUP = 'posts (parentid)'

const ROWS: CaRow[] = [
  {
    key: CA_KEYS.postsUsersByCreationdate,
    name: 'SELECT on posts and users by creationdate',
    sql: 'select p.id, p.title, u.displayname from posts p join users u on u.id = p.owneruserid where p.creationdate >= $1 order by p.score desc limit $2',
    tables: ['posts', 'users'],
    qps: 0.64,
    mean_ms: 69.7,
    share: 0.19,
    plan_score: 42,
    status: 'fix_suggested',
    fix: {
      label: 'Add index posts (creationdate, owneruserid, score)',
      sql: 'CREATE INDEX CONCURRENTLY idx_posts_creationdate_owneruserid_score ON public.posts (creationdate, owneruserid, score);',
      group: POSTS_GROUP,
    },
    delta_24h: 0.12,
  },
  {
    key: CA_KEYS.votesByPostid,
    name: 'SELECT on votes by postid',
    sql: 'select id, votetypeid from votes where postid = $1',
    tables: ['votes'],
    qps: 1.36,
    mean_ms: 29.8,
    share: 0.17,
    plan_score: 52,
    status: 'fix_suggested',
    fix: {
      label: 'Add index votes (postid) include (id, votetypeid)',
      sql: 'CREATE INDEX CONCURRENTLY idx_votes_postid ON public.votes (postid) INCLUDE (id, votetypeid);',
      group: null,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.postsByOwneruserid,
    name: 'SELECT on posts by owneruserid',
    sql: 'select id, title from posts where owneruserid = $1 order by creationdate desc limit $2',
    tables: ['posts'],
    qps: 0.62,
    mean_ms: 65.4,
    share: 0.17,
    plan_score: 72,
    status: 'fix_suggested',
    fix: {
      label: 'Add index posts (owneruserid, creationdate desc)',
      sql: 'CREATE INDEX CONCURRENTLY idx_posts_owneruserid_creationdate\n  ON public.posts (owneruserid, creationdate DESC);',
      group: POSTS_GROUP,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.commentsByPostid,
    name: 'COUNT on comments by postid',
    sql: 'select count(*) from comments where postid = $1',
    tables: ['comments'],
    qps: 1.2,
    mean_ms: 27.4,
    share: 0.12,
    plan_score: 52,
    status: 'fix_suggested',
    fix: {
      label: 'Add index comments (postid)',
      sql: 'CREATE INDEX CONCURRENTLY idx_comments_postid ON public.comments (postid);',
      group: null,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.postsByCreationdate,
    name: 'COUNT on posts by creationdate',
    sql: 'select count(*) from posts where creationdate >= $1',
    tables: ['posts'],
    qps: 0.32,
    mean_ms: 70.6,
    share: 0.1,
    plan_score: 38,
    status: 'fix_suggested',
    fix: {
      label: 'Add index posts (creationdate)',
      sql: 'CREATE INDEX CONCURRENTLY idx_posts_creationdate ON public.posts (creationdate);',
      group: POSTS_GROUP,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.badgesByUserid,
    name: 'SELECT on badges by userid',
    sql: 'select id, name, date from badges where userid = $1 order by date desc',
    tables: ['badges'],
    qps: 0.9,
    mean_ms: 23.1,
    share: 0.08,
    plan_score: 52,
    status: 'fix_suggested',
    fix: {
      label: 'Add index badges (userid, date desc)',
      sql: 'CREATE INDEX CONCURRENTLY idx_badges_userid_date ON public.badges (userid, date DESC);',
      group: null,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.postsByPosttypeid,
    name: 'COUNT on posts by posttypeid',
    sql: 'select count(*) from posts where posttypeid = $1 and answercount > $2',
    tables: ['posts'],
    qps: 0.29,
    mean_ms: 67.2,
    share: 0.04,
    plan_score: 38,
    status: 'fix_suggested',
    fix: {
      label: 'Add index posts (posttypeid, answercount)',
      sql: 'CREATE INDEX CONCURRENTLY idx_posts_posttypeid_answercount ON public.posts (posttypeid, answercount);',
      group: null,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.usersById,
    name: 'SELECT on users by id',
    sql: 'select id, displayname, reputation from users where id = $1',
    tables: ['users'],
    qps: 1.05,
    mean_ms: 0.62,
    share: 0.004,
    plan_score: 99,
    status: 'healthy',
    fix: null,
    delta_24h: null,
  },
  {
    key: CA_KEYS.postsById,
    name: 'SELECT on posts by id',
    sql: 'select id, title, score, viewcount from posts where id = $1',
    tables: ['posts'],
    qps: 1.85,
    mean_ms: 0.23,
    share: 0.002,
    plan_score: 99,
    status: 'healthy',
    fix: null,
    delta_24h: null,
  },
  {
    key: CA_KEYS.commentsByUserid,
    name: 'SELECT on comments by userid',
    sql: 'select id, postid, text from comments where userid = $1 order by creationdate desc limit $2',
    tables: ['comments'],
    qps: 0.4,
    mean_ms: 1.9,
    share: 0.015,
    plan_score: 91,
    status: 'healthy',
    fix: null,
    delta_24h: -0.93,
  },
  {
    key: CA_KEYS.postsByParentid,
    name: 'SELECT on posts by parentid',
    sql: 'select id, score, body from posts where parentid = $1 order by score desc',
    tables: ['posts'],
    qps: 0.55,
    mean_ms: 12.8,
    share: 0.02,
    plan_score: 55,
    status: 'fix_suggested',
    fix: {
      label: 'Add index posts (parentid)',
      sql: 'CREATE INDEX CONCURRENTLY idx_posts_parentid ON public.posts (parentid);',
      group: PARENTID_GROUP,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.usersByReputation,
    name: 'SELECT on users by reputation',
    sql: 'select id, displayname from users where reputation >= $1 order by reputation desc limit $2',
    tables: ['users'],
    qps: 0.21,
    mean_ms: 9.4,
    share: 0.015,
    plan_score: 48,
    status: 'fix_suggested',
    fix: {
      label: 'Add index users (reputation)',
      sql: 'CREATE INDEX CONCURRENTLY idx_users_reputation ON public.users (reputation);',
      group: null,
    },
    delta_24h: null,
  },
  {
    key: CA_KEYS.countPostsByParentid,
    name: 'COUNT on posts by parentid',
    sql: 'select count(*) from posts where parentid = $1',
    tables: ['posts'],
    qps: 0.3,
    mean_ms: 1.1,
    share: 0.004,
    plan_score: 93,
    status: 'healthy',
    fix: null,
    delta_24h: null,
  },
  {
    key: CA_KEYS.tagsByTagname,
    name: 'SELECT on tags by tagname',
    sql: 'select id, count from tags where tagname = $1',
    tables: ['tags'],
    qps: 0.8,
    mean_ms: 0.4,
    share: 0.001,
    plan_score: 97,
    status: 'healthy',
    fix: null,
    delta_24h: null,
  },
  {
    key: CA_KEYS.votesByUserid,
    name: 'SELECT on votes by userid',
    sql: 'select id, postid, votetypeid from votes where userid = $1',
    tables: ['votes'],
    qps: 0.15,
    mean_ms: 9.1,
    share: 0.004,
    plan_score: 88,
    status: 'healthy',
    fix: null,
    delta_24h: null,
  },
  {
    key: CA_KEYS.stuck,
    name: 'COUNT on posts with pg_sleep',
    sql: 'select count(*), pg_sleep(600) from posts',
    tables: ['posts'],
    qps: 0.0003,
    mean_ms: 0,
    share: 0,
    plan_score: null,
    status: 'stuck',
    fix: null,
    delta_24h: null,
  },
]

const rowByKey = new Map(ROWS.map((row) => [row.key, row]))
const BY_SHARE = [...ROWS].sort(
  (a, b) => b.share - a.share || a.key.localeCompare(b.key)
)
const COLORS = new Map(
  BY_SHARE.filter((row) => row.share > 0)
    .slice(0, 8)
    .map((row, i) => [row.key, i])
)
const FIX_COUNT = ROWS.filter((row) => row.fix).length

/** Per-row minute series: mean latency and calls around the row's own level. */
function rowSeries(row: CaRow, start: number, end: number, step: number) {
  const points = []
  for (let ts = start; ts <= end; ts += step) {
    const n = noise(row.key, ts)
    points.push({
      ts,
      qps: round(Math.max(0, row.qps * (1 + 0.18 * n)), 4),
      mean_ms: round(
        Math.max(0.01, row.mean_ms * (1 + 0.12 * noise(`${row.key}:ms`, ts))),
        3
      ),
    })
  }
  return points
}

function trend24h(row: CaRow) {
  return Array.from({ length: 24 }, (_, hour) => {
    const ts = CA_NOW - (23 - hour) * HOUR
    const lift = row.delta_24h && hour > 20 ? row.delta_24h : 0
    return {
      ts,
      mean_ms: round(
        row.mean_ms * (1 + lift + 0.08 * noise(`${row.key}:trend`, hour)),
        3
      ),
    }
  })
}

function monitorQuery(
  row: CaRow,
  target: string,
  options: { analyzing?: boolean } = {}
): MonitorQuery {
  const analyzing = options.analyzing ?? false
  const status: QueryStatus = analyzing ? 'analyzing' : row.status
  return {
    key: row.key,
    text: row.sql,
    text_status: 'ok',
    text_reason: null,
    display_name: row.name,
    tables: row.tables,
    qps: row.qps,
    mean_ms: row.mean_ms,
    db_time_ms: round(row.qps * row.mean_ms * HOUR, 1),
    db_time_share: row.share,
    impact_ms: round(row.qps * row.mean_ms * HOUR, 1),
    change: {
      latency: row.delta_24h,
      qps: null,
      significant: row.delta_24h !== null && Math.abs(row.delta_24h) >= 0.2,
      verdict:
        row.delta_24h === null
          ? null
          : row.delta_24h > 0.2
            ? 'regressed'
            : row.delta_24h < -0.2
              ? 'improved'
              : 'steady',
    },
    series: rowSeries(row, CA_NOW - 12 * MIN, CA_NOW, MIN),
    state: row.status === 'stuck' ? ['stuck'] : [],
    fix: null,
    last_analysis: analyzing
      ? {
          state: 'queued',
          finished_at: null,
          analysis_ref: null,
          mode: '',
          param_source: null,
          reason: null,
          queue_state: 'today',
          queue_position: 1,
        }
      : row.status === 'stuck'
        ? null
        : {
            state: 'done',
            finished_at: CA_NOW - 9 * MIN,
            analysis_ref: `/api/query-registry/${row.key}/analysis/ca_${row.key}`,
            mode: 'explain',
            mode_label: 'Plan only (EXPLAIN)',
            param_source: null,
            reason: null,
            summary: {
              analysis_ref: `/api/query-registry/${row.key}/analysis/ca_${row.key}`,
              analysis_id: `ca_${row.key}`,
              analyzed_at: CA_NOW - 9 * MIN,
              mode: 'explain',
              mode_label: 'Plan only (EXPLAIN)',
              headline: row.fix
                ? row.fix.label.replace('Add index', 'Needs an index on')
                : 'Already efficient',
              rating: row.fix ? 'fair' : 'excellent',
              fix_kind: row.fix ? 'index' : null,
              efficiency_score: row.plan_score,
              fix_ready: Boolean(row.fix),
              score: row.plan_score,
            },
          },
    last_compare: null,
    analyzable: true,
    analyzable_code: 'ok',
    not_analyzable_reason: null,
    analysis_note: null,
    color_index: COLORS.get(row.key) ?? null,
    assessment: null,
    plan_score: analyzing ? null : row.plan_score,
    latency_delta_24h: analyzing ? null : row.delta_24h,
    trend_24h: analyzing ? [] : trend24h(row),
    suggested_fix:
      analyzing || !row.fix ? null : { sql: row.fix.sql, label: row.fix.label },
    status,
    ...(target ? {} : {}),
  }
}

function queryPage(
  target: string,
  rows: CaRow[],
  options: {
    analyzing?: boolean
    total?: number
    seenTotal?: number
    firstTs?: number
  } = {}
): MonitorQueryPage {
  const queries = rows.map((row) => monitorQuery(row, target, options))
  const totalMs = queries.reduce((sum, row) => sum + row.db_time_ms, 0)
  const leaders = [...queries]
    .sort((a, b) => b.db_time_ms - a.db_time_ms)
    .slice(0, 6)
  const rest = totalMs - leaders.reduce((sum, row) => sum + row.db_time_ms, 0)
  const firstTs = options.firstTs ?? at(0, 0, 0, -7)
  return {
    queries,
    total: options.total ?? rows.length,
    seen_total: options.seenTotal ?? rows.length,
    counts: {
      all: options.total ?? rows.length,
      attention: queries.filter((row) => row.state.includes('attention'))
        .length,
      new: 0,
      fixed: 0,
      muted: 0,
    },
    composition: [
      ...leaders.map((row) => ({
        key: row.key,
        db_time_ms: row.db_time_ms,
        share: totalMs ? row.db_time_ms / totalMs : 0,
      })),
      ...(rest > 0
        ? [
            {
              key: 'other',
              db_time_ms: rest,
              share: totalMs ? rest / totalMs : 0,
            },
          ]
        : []),
    ],
    history: historySpan(firstTs),
    range: '1h',
  }
}

function historySpan(firstTs: number | null) {
  return {
    first_ts: firstTs,
    last_ts: firstTs === null ? null : CA_NOW,
    span_seconds: firstTs === null ? 0 : CA_NOW - firstTs,
    resolutions: {
      '60': {
        first_ts: firstTs === null ? null : Math.max(firstTs, CA_NOW - 2 * DAY),
      },
      '300': { first_ts: firstTs },
      '3600': { first_ts: firstTs },
      '86400': { first_ts: firstTs },
    },
  }
}

// Health

interface HealthFacts {
  target: string
  qps: number
  qps_peak: number
  mean_ms: number
  conns: number
  cache_hit: number | null
  rollbacks: number | null
  last_reading_at: number
  first_ts: number
  /** Readings stop here (an outage) and the live state follows. */
  outage_from: number | null
  checks_ran_at: number | null
}

const DEMO_HEALTH: HealthFacts = {
  target: 'demo',
  qps: 14.2,
  qps_peak: 15.9,
  mean_ms: 15.9,
  conns: 8,
  cache_hit: 0.125,
  rollbacks: 14,
  last_reading_at: at(9, 56, 30),
  first_ts: at(9, 45, 0, -7),
  outage_from: null,
  checks_ran_at: at(9, 55),
}

function healthPoint(facts: HealthFacts, ts: number, res: number): HealthPoint {
  if (
    ts < facts.first_ts ||
    (facts.outage_from !== null && ts >= facts.outage_from)
  )
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
  const n = noise(`${facts.target}:health`, ts)
  return {
    ts,
    qps: round(Math.max(0, facts.qps * (1 + 0.1 * n)), 3),
    mean_ms: round(
      facts.mean_ms * (1 + 0.08 * noise(`${facts.target}:ms`, ts)),
      3
    ),
    active_conns: facts.conns,
    max_conns: 100,
    cache_hit_ratio: facts.cache_hit,
    xact_rollback:
      facts.rollbacks === null
        ? null
        : Math.round((facts.rollbacks * res) / MIN),
    deadlocks: 0,
  }
}

const RANGE_SECONDS: Record<MonitorRange, number> = {
  '1h': HOUR,
  '24h': DAY,
  '7d': 7 * DAY,
  '30d': 30 * DAY,
  '90d': 90 * DAY,
  all: 90 * DAY,
}

function health(
  facts: HealthFacts,
  range: MonitorRange,
  engineVersion: string | null = '17'
): MonitorHealth {
  const seconds = RANGE_SECONDS[range]
  const res = seconds <= DAY ? 60 : seconds <= 7 * DAY ? 300 : 3600
  const start = Math.floor((CA_NOW - seconds) / res) * res
  const series: HealthPoint[] = []
  for (let ts = start; ts <= CA_NOW; ts += res)
    series.push(healthPoint(facts, ts, res))
  const period = {
    qps: facts.qps,
    mean_ms: facts.mean_ms,
    p95_ms: facts.cache_hit === null ? null : 69.7,
    qps_peak: facts.qps_peak,
    qps_peak_at: Math.max(facts.first_ts, at(9, 50)),
    db_time_ms: round(facts.qps * facts.mean_ms * seconds, 1),
    active_conns_peak: facts.conns,
    cache_hit_ratio_min: facts.cache_hit,
    xact_rollback:
      facts.rollbacks === null ? 0 : facts.rollbacks * (seconds / MIN),
    deadlocks: 0,
  }
  const last =
    facts.outage_from === null ? facts.last_reading_at : facts.outage_from - 2
  return {
    target: facts.target,
    engine: 'postgresql',
    engine_version: engineVersion,
    range,
    res,
    history: historySpan(facts.first_ts),
    now: {
      ts: last,
      qps: facts.qps,
      mean_ms: facts.mean_ms,
      active_conns: facts.conns,
      max_conns: 100,
      cache_hit_ratio: facts.cache_hit,
      rollbacks_per_min: facts.rollbacks,
    },
    period,
    prior:
      facts.first_ts <= start - seconds
        ? { ...period, qps: round(facts.qps * 0.97, 3), mean_ms: facts.mean_ms }
        : null,
    series,
    markers: [],
    last_reading_at: last,
    next_reading_at: facts.outage_from === null ? last + MIN : null,
    first_reading: false,
    delay_reason:
      facts.outage_from === null ? null : 'The server refused the connection.',
    setup_blocked: false,
    outages:
      facts.outage_from === null
        ? []
        : [
            {
              start: facts.outage_from,
              end: null,
              cause: 'unreachable',
              item_id: 11,
            },
          ],
    checks_ran_at: facts.checks_ran_at,
  }
}

function pulses(facts: HealthFacts, limit: number): MonitorPulsePage {
  const lost = facts.outage_from !== null
  const last = lost ? (facts.outage_from as number) - 2 : facts.last_reading_at
  const state = lost ? 'connection_lost' : 'live'
  const list = [
    {
      ts: last,
      kind: 'collect' as const,
      ok: true,
      detail: `Read ${ROWS.length} queries`,
    },
    {
      ts: facts.checks_ran_at ?? last - 90,
      kind: 'attention' as const,
      ok: true,
      detail: `Checked ${ROWS.length} queries, filed 1 item`,
    },
    {
      ts: last - MIN,
      kind: 'collect' as const,
      ok: true,
      detail: `Read ${ROWS.length} queries`,
    },
    {
      ts: last - 2 * MIN,
      kind: 'index_scan' as const,
      ok: true,
      detail: 'No index changes',
    },
    {
      ts: at(9, 51),
      kind: 'report' as const,
      ok: true,
      detail: 'Report written',
    },
  ]
  if (lost)
    list.unshift({
      ts: CA_NOW - 30,
      kind: 'collect',
      ok: false,
      detail: 'The server refused the connection.',
    })
  const activity = lost
    ? caActivity('database', 'cant_reach')
    : caActivity(CA_TARGET, 'watching')
  return {
    pulses: list.slice(0, limit).map((pulse) => ({
      ...pulse,
      next_collect_at: last + MIN,
      state,
      last_reading_at: last,
      next_reading_at: lost ? null : last + MIN,
      activity: { ...activity, last_reading_at: last },
    })),
    last_reading_at: last,
    next_reading_at: lost ? null : last + MIN,
    checks_ran_at: facts.checks_ran_at,
    activity: { ...activity, last_reading_at: last },
  }
}

// Load breakdown for the chart card: the last hour at one minute, readings for 12 minutes.

function load(
  target: string,
  range: ApiRange,
  top: number,
  window?: TimeWindow,
  recordedFrom = at(9, 45)
): LoadBreakdown {
  const leaders = BY_SHARE.filter((row) => row.share > 0).slice(0, top)
  const rest = BY_SHARE.filter((row) => row.share > 0).slice(top)
  const totalMs = ROWS.reduce(
    (sum, row) => sum + row.qps * row.mean_ms * HOUR,
    0
  )
  const bands: LoadBreakdown['bands'] = leaders.map((row, index) => ({
    key: row.key,
    kind: 'query',
    display_name: row.name,
    color_index: index,
    share: row.share,
    total_ms: round(row.qps * row.mean_ms * HOUR, 1),
    calls: round(row.qps * HOUR),
    mean_ms: row.mean_ms,
  }))
  if (rest.length)
    bands.push({
      key: 'other',
      kind: 'other',
      display_name: 'Everything else',
      color_index: null,
      share: round(
        Math.max(0, 1 - leaders.reduce((sum, row) => sum + row.share, 0)),
        3
      ),
      total_ms: round(
        rest.reduce((sum, row) => sum + row.qps * row.mean_ms * HOUR, 0),
        1
      ),
      calls: round(rest.reduce((sum, row) => sum + row.qps * HOUR, 0)),
      mean_ms: null,
    })
  const seconds =
    range === '5m'
      ? 5 * MIN
      : range === '15m'
        ? 15 * MIN
        : range === '1h'
          ? HOUR
          : DAY
  const step = seconds <= HOUR ? MIN : 5 * MIN
  const from = window?.from ?? Math.floor((CA_NOW - seconds) / step) * step
  const to = window?.to ?? CA_NOW
  const points: LoadPoint[] = []
  for (let ts = from; ts <= to; ts += step) {
    if (ts < recordedFrom) {
      points.push({
        ts,
        seconds: step,
        total_ms: null,
        calls: null,
        mean_ms: null,
      })
      continue
    }
    const ms = leaders.map((row) =>
      round(row.qps * step * row.mean_ms * (1 + 0.15 * noise(row.key, ts)), 2)
    )
    const calls = leaders.map((row) =>
      round(row.qps * step * (1 + 0.15 * noise(row.key, ts)), 2)
    )
    if (rest.length) {
      ms.push(
        round(
          rest.reduce((sum, row) => sum + row.qps * step * row.mean_ms, 0),
          2
        )
      )
      calls.push(
        round(
          rest.reduce((sum, row) => sum + row.qps * step, 0),
          2
        )
      )
    }
    const allCalls = calls.reduce((sum, value) => sum + value, 0)
    points.push({
      ts,
      seconds: step,
      total_ms: ms,
      calls,
      mean_ms: allCalls
        ? round(ms.reduce((sum, value) => sum + value, 0) / allCalls, 3)
        : null,
    })
  }
  const recorded = Math.max(0, CA_NOW - recordedFrom)
  return {
    target,
    range: window ? 'custom' : range,
    res: step as 60 | 300,
    start: from,
    end: to,
    history: historySpan(recordedFrom),
    recorded_label:
      recorded < HOUR
        ? `${Math.round(recorded / MIN)} min recorded`
        : `${round(recorded / HOUR, 1)} hours recorded`,
    ranges: (
      ['5m', '15m', '1h', '24h', '7d', '30d', '90d', 'all'] as ApiRange[]
    ).map((entry) => ({
      range: entry,
      enabled:
        entry === '5m' || entry === '15m' || entry === '1h' || recorded >= DAY,
      res:
        entry === '24h'
          ? 300
          : entry === '7d' || entry === '30d'
            ? 3600
            : entry === '90d'
              ? 86400
              : 60,
      reason: null,
    })),
    bands,
    points,
    total_ms: round(totalMs, 1),
    calls: round(ROWS.reduce((sum, row) => sum + row.qps * HOUR, 0)),
  }
}

// The full analysis of "SELECT on posts by owneruserid" (analysis.html).

const OWNERUSERID_EXPLAIN = `Limit  (cost=107915.98..107916.01 rows=10 width=68)
  ->  Sort  (cost=107915.98..107917.44 rows=583 width=68)
        Sort Key: creationdate DESC
        ->  Seq Scan on posts  (cost=0.00..107903.38 rows=583 width=68)
              Filter: (owneruserid = 1876)
              Rows Removed by Filter: 244707`

function analysisView(row: CaRow): AnalysisView {
  const full = row.key === CA_KEYS.postsByOwneruserid
  return {
    score: row.plan_score,
    verdict: full
      ? {
          lead: 'One index removes the scan and the sort.',
          rest: 'The SQL stays as it is.',
        }
      : row.fix
        ? {
            lead: 'One index removes the scan.',
            rest: 'The SQL stays as it is.',
          }
        : {
            lead: 'The plan is already efficient.',
            rest: 'Nothing to change.',
          },
    fix_detail: row.fix
      ? {
          sql: row.fix.sql,
          serves: full
            ? 'Serves the filter and the sort from one index.'
            : 'Serves the filter from one index.',
          cost_before: full ? 107916 : null,
          cost_after: full ? 41 : null,
          size: full ? 'About 9 MB' : null,
          write_cost: 'one more index on writes',
        }
      : null,
    plan_findings: full
      ? [
          {
            title: 'Sequential scan on posts',
            detail: 'no index on owneruserid',
            value: '245,290 rows',
          },
          {
            title: 'Sort on creationdate',
            detail: 'in memory, after the filter',
            value: '583 rows',
          },
          {
            title: 'LIMIT cannot stop early',
            detail: 'the sort finishes first',
            value: '$2 = 10',
          },
        ]
      : row.fix
        ? [
            {
              title: `Sequential scan on ${row.tables[0]}`,
              detail: 'no usable index',
              value: null,
            },
          ]
        : [],
    rewrites: full
      ? [
          {
            name: 'Filter before sort, subquery',
            before_ms: 65.4,
            after_ms: 64.9,
            runs: 20,
            outcome: 'no_change',
          },
          {
            name: 'LIMIT inside a lateral join',
            before_ms: 65.4,
            after_ms: 66.1,
            runs: 20,
            outcome: 'no_change',
          },
        ]
      : [],
    also: full
      ? [
          {
            title: 'Select only id and title',
            detail: '17 columns come back, 2 are used',
          },
        ]
      : [],
    readyset: { blockers: [], verified: false },
    benchmark: full
      ? {
          before_ms: 65.4,
          after_ms: null,
          change: null,
          method: '20 runs each on demo',
        }
      : null,
    run: { model: 'Claude Sonnet 4.6', tokens: 8028, seconds: 18, id: '412' },
    rows_read: full ? 245290 : null,
    rows_returned: full ? 583 : null,
    planner_cost: full ? 107916 : null,
    calls_today: full ? 174 : Math.round(row.qps * (CA_NOW - CA_MIDNIGHT)),
    params: full
      ? [
          { name: '$1', value: '1876' },
          { name: '$2', value: '10' },
        ]
      : [],
    param_source: full ? 'sampled from table data' : null,
    explain_text: full ? OWNERUSERID_EXPLAIN : null,
  }
}

function queryAnalysis(row: CaRow): DeepAnalysisDigest {
  const table = row.tables[0]
  return {
    query_key: row.key,
    analysis_ref: `/api/query-registry/${row.key}/analysis/ca_${row.key}`,
    analyzed_at: CA_NOW - 9 * MIN,
    mode: 'explain',
    mode_label: 'Plan only (EXPLAIN)',
    rating: row.fix ? 'fair' : 'excellent',
    efficiency_score: row.plan_score,
    plan_summary: {
      text:
        row.key === CA_KEYS.postsByOwneruserid
          ? 'Estimated plan: Limit, mostly Seq Scan on posts; estimated 245,290 rows scanned and 583 returned'
          : `Estimated plan: Seq Scan on ${table}`,
      root_node: 'Limit',
      execution_ms: null,
      rows_examined: row.key === CA_KEYS.postsByOwneruserid ? 245290 : null,
      rows_returned: row.key === CA_KEYS.postsByOwneruserid ? 583 : null,
    },
    findings:
      row.key === CA_KEYS.postsByOwneruserid
        ? ['Reads all 245,290 rows to return 583: no index on owneruserid.']
        : row.fix
          ? [`Sequential scan on ${table}.`]
          : [],
    fix: row.fix
      ? {
          kind: 'index',
          title: row.fix.label.replace('Add index', 'Add an index on'),
          sql: row.fix.sql,
          why:
            row.key === CA_KEYS.postsByOwneruserid
              ? 'Reads all 245,290 rows to return 583: no index on owneruserid.'
              : 'The filter column has no index, so every call reads the table.',
          expected_gain: {
            measured: false,
            text: 'Planner estimate: cost down 99% with a hypothetical index',
            before_ms: null,
            after_ms: null,
            cost_reduction_pct: 99,
          },
        }
      : null,
    other_fixes: [],
    headline: row.fix
      ? row.fix.label.replace('Add index', 'Needs an index on')
      : 'Already efficient',
    fix_ready: Boolean(row.fix),
    ...analysisView(row),
  }
}

// Inbox

type Link = InboxItem['link']

function link(
  kind: 'query' | 'database' | 'report',
  target: string,
  label: string,
  extra: Partial<NonNullable<Link>> = {}
): NonNullable<Link> {
  return { kind, target, label, ...extra }
}

interface CaItemSpec {
  id: number
  target: string
  type: string
  subject: string
  title: string
  summary: string
  severity: number
  created_at: number
  state: InboxItem['state']
  resolved_at?: number
  resolved_by?: 'user' | 'code'
  delivery: 'inbox' | 'notify'
  keys: string[]
  link: NonNullable<Link>
  view: Partial<InboxItemView>
  body?: Record<string, unknown>
}

const STUCK_PID = 48213

function stuckSteps(pid: number): InboxItemView['steps'] {
  return [
    {
      title: 'Find it and what it waits on',
      sql: `SELECT pid, now() - query_start AS running, wait_event, query\nFROM pg_stat_activity WHERE pid = ${pid};`,
    },
    { title: 'Cancel it', sql: `SELECT pg_cancel_backend(${pid});` },
    {
      title: "If it won't cancel",
      sql: `SELECT pg_terminate_backend(${pid});`,
    },
  ]
}

const STUCK_ITEM: CaItemSpec = {
  id: 1,
  target: 'demo',
  type: 'stuck_query',
  subject: CA_KEYS.stuck,
  title: 'Stuck query on demo',
  summary: 'Running for over a minute and still going.',
  severity: 0.9,
  created_at: at(9, 55),
  state: 'new',
  delivery: 'notify',
  keys: [CA_KEYS.stuck],
  link: link('query', 'demo', 'Open query', {
    query_key: CA_KEYS.stuck,
    tab: 'performance',
  }),
  body: { query_key: CA_KEYS.stuck, pid: STUCK_PID, duration_s: 240 },
  view: {
    kind: 'stuck_query',
    band: 'act_now',
    pid: STUCK_PID,
    running_s: 240,
    steps: stuckSteps(STUCK_PID),
    history: [
      { ts: at(9, 56), text: 'Still running, 4 min, waiting on nothing' },
      { ts: at(9, 55), text: 'Filed and pushed to this computer' },
      { ts: at(9, 55), text: 'Statement passed the 60 s limit' },
    ],
    related: [
      {
        label: 'COUNT on posts with pg_sleep',
        detail: '1 call today, never seen before',
        link: {
          kind: 'queries',
          target: 'demo',
          label: 'Queries',
          query_key: CA_KEYS.stuck,
        },
      },
      {
        label: 'Connections 8 of 100',
        detail: 'one held by this statement',
        link: { kind: 'live', target: 'demo', label: 'Live' },
      },
    ],
  },
}

const CHANGES_ITEM: CaItemSpec = {
  id: 2,
  target: 'demo',
  type: 'changes_ready',
  subject: 'plan',
  title: '5 changes ready on demo',
  summary:
    '2 to do first, starting with index posts (creationdate, owneruserid, score).',
  severity: 0.6,
  created_at: at(9, 51),
  state: 'seen',
  delivery: 'notify',
  keys: [],
  link: { kind: 'actions', target: 'demo', label: 'Open Actions' },
  body: { db: 'demo', count: 5, do_first: 2, action_ids: [1, 2, 3] },
  view: {
    kind: 'changes_ready',
    band: 'watch',
    changes: {
      count: 5,
      do_first: 2,
      titles: [
        'Index posts (creationdate, owneruserid, score)',
        'Index votes (postid) include (id, votetypeid)',
        'Raise shared_buffers to 2 GB',
      ],
      action_ids: [1, 2, 3],
    },
    history: [
      { ts: at(9, 51), text: 'Pushed once to this computer' },
      { ts: at(9, 51), text: 'Filed: 5 changes in the plan, 2 to do first' },
    ],
    related: [
      {
        label: 'Actions on demo',
        detail: '5 changes to make',
        link: { kind: 'actions', target: 'demo', label: 'Actions' },
      },
    ],
  },
}

const REPORT_ITEM: CaItemSpec = {
  id: 3,
  target: 'demo',
  type: 'report_ready',
  subject: '2026-10-07',
  title: 'Daily report for October 7 is ready',
  summary: 'demo, 3 of 8 checks need you',
  severity: 0.6,
  created_at: at(0, 0),
  state: 'seen',
  delivery: 'notify',
  keys: [],
  link: link('report', 'demo', 'Open report', { report_id: 31 }),
  body: { report_id: 31 },
  view: {
    kind: 'report_ready',
    band: 'watch',
    source_report_id: 31,
    history: [
      { ts: at(0, 0), text: 'Filed and pushed once to this computer' },
      { ts: at(0, 0), text: 'Report ran, 12 analyses, 2 rewrites tested' },
    ],
    related: [],
  },
}

const CLOSED_ITEMS: CaItemSpec[] = [
  {
    id: 4,
    target: 'demo',
    type: 'fix_confirmed',
    subject: CA_KEYS.commentsByUserid,
    title: 'Fix confirmed: SELECT on comments by userid',
    summary: '27.6 ms to 1.9 ms',
    severity: 0.5,
    created_at: at(8, 10),
    state: 'resolved',
    resolved_at: at(8, 12),
    resolved_by: 'code',
    delivery: 'inbox',
    keys: [CA_KEYS.commentsByUserid],
    link: link('query', 'demo', 'Open query', {
      query_key: CA_KEYS.commentsByUserid,
      tab: 'performance',
    }),
    body: {
      query_key: CA_KEYS.commentsByUserid,
      index_name: 'idx_comments_userid_creationdate',
      before_ms: 27.6,
      after_ms: 1.9,
      calls: 40,
    },
    view: {
      kind: 'fix_confirmed',
      band: 'closed',
      before_ms: 27.6,
      after_ms: 1.9,
      calls: 40,
      index_name: 'comments (userid, creationdate)',
      history: [
        { ts: at(8, 12), text: 'Item closed by RDST' },
        { ts: at(8, 12), text: 'Verified: 1.9 ms a call over 40 calls' },
        {
          ts: at(8, 10),
          text: 'Index on comments (userid, creationdate) appeared',
        },
      ],
      related: [
        {
          label: 'SELECT on comments by userid',
          detail: '1.9 ms a call, healthy',
          link: {
            kind: 'queries',
            target: 'demo',
            label: 'Queries',
            query_key: CA_KEYS.commentsByUserid,
          },
        },
        {
          label: 'Daily report for October 7',
          detail: 'marked done in Fix first',
          link: {
            kind: 'report',
            target: 'demo',
            label: 'Report',
            report_id: 31,
          },
        },
      ],
    },
  },
  {
    id: 13,
    target: 'demo',
    type: 'no_change_yet',
    subject: 'action:7',
    title: 'No change yet: SELECT on users by reputation',
    summary: '14 ms before, 14 ms after over 40 calls.',
    severity: 0.3,
    created_at: at(7, 30),
    state: 'resolved',
    resolved_at: at(7, 30),
    resolved_by: 'code',
    delivery: 'inbox',
    keys: [CA_KEYS.usersByReputation],
    link: { kind: 'actions', target: 'demo', label: 'Open Applied' },
    body: {
      action_id: 7,
      action_title: 'Index users (reputation)',
      query_key: CA_KEYS.usersByReputation,
      index_label: 'users (reputation)',
      detected_at: at(7, 30, 0, -2),
      before_ms: 14.2,
      after_ms: 14,
      calls: 40,
    },
    view: {
      kind: 'no_change_yet',
      band: 'closed',
      history: [
        {
          ts: at(7, 30),
          text: 'Measured against the 7 days before the change',
        },
        { ts: at(7, 30, 0, -2), text: 'Index on users (reputation) applied' },
      ],
      related: [
        {
          label: 'The change in Applied',
          detail: 'Index users (reputation)',
          link: { kind: 'actions', target: 'demo', label: 'Applied' },
        },
      ],
    },
  },
  {
    id: 5,
    target: 'demo',
    type: 'traffic_spike',
    subject: CA_KEYS.postsById,
    title: 'Traffic spike on demo ended',
    summary: '2.1x for 11 minutes',
    severity: 0.6,
    created_at: at(9, 37),
    state: 'resolved',
    resolved_at: at(9, 48),
    resolved_by: 'code',
    delivery: 'inbox',
    keys: [CA_KEYS.postsById],
    link: link('database', 'demo', 'See the chart'),
    view: {
      kind: 'traffic_spike',
      band: 'closed',
      history: [
        { ts: at(9, 48), text: 'Back to normal, item closed by RDST' },
        { ts: at(9, 37), text: 'Filed: calls 2.1x the usual rate' },
      ],
      related: [
        {
          label: 'SELECT on posts by id',
          detail: '1.85 calls a second',
          link: {
            kind: 'queries',
            target: 'demo',
            label: 'Queries',
            query_key: CA_KEYS.postsById,
          },
        },
      ],
    },
  },
  {
    id: 6,
    target: 'analytics',
    type: 'stuck_query',
    subject: 'ca00000000a1',
    title: 'Stuck query on analytics ended by itself',
    summary: 'SELECT on votes, ran 3 min',
    severity: 0.9,
    created_at: at(16, 28, 0, -1),
    state: 'resolved',
    resolved_at: at(16, 31, 0, -1),
    resolved_by: 'code',
    delivery: 'notify',
    keys: ['ca00000000a1'],
    link: link('query', 'analytics', 'Open query', {
      query_key: 'ca00000000a1',
      tab: 'performance',
    }),
    view: {
      kind: 'stuck_query',
      band: 'closed',
      running_s: 180,
      history: [
        { ts: at(16, 31, 0, -1), text: 'Statement ended, item closed by RDST' },
        { ts: at(16, 28, 0, -1), text: 'Filed and pushed to this computer' },
      ],
      related: [],
    },
  },
  {
    id: 7,
    target: 'staging',
    type: 'fix_confirmed',
    subject: 'ca00000000b1',
    title: 'Fix confirmed: SELECT on badges by userid',
    summary: 'staging, 41.2 ms to 3.4 ms',
    severity: 0.5,
    created_at: at(11, 15, 0, -1),
    state: 'resolved',
    resolved_at: at(11, 20, 0, -1),
    resolved_by: 'code',
    delivery: 'inbox',
    keys: ['ca00000000b1'],
    link: link('query', 'staging', 'Open query', {
      query_key: 'ca00000000b1',
      tab: 'performance',
    }),
    view: {
      kind: 'fix_confirmed',
      band: 'closed',
      before_ms: 41.2,
      after_ms: 3.4,
      calls: 40,
      index_name: 'badges (userid, date)',
      history: [
        { ts: at(11, 20, 0, -1), text: 'Item closed by RDST' },
        {
          ts: at(11, 15, 0, -1),
          text: 'Index on badges (userid, date) appeared',
        },
      ],
      related: [],
    },
  },
  {
    id: 8,
    target: 'staging',
    type: 'report_ready',
    subject: '2026-10-07',
    title: 'Daily report for October 7 on staging',
    summary: 'filed, read by you',
    severity: 0.6,
    created_at: at(0, 0, 0, -1),
    state: 'resolved',
    resolved_at: at(9, 14, 0, -1),
    resolved_by: 'user',
    delivery: 'inbox',
    keys: [],
    link: link('report', 'staging', 'Open report', { report_id: 44 }),
    view: {
      kind: 'report_ready',
      band: 'closed',
      source_report_id: 44,
      history: [
        { ts: at(9, 14, 0, -1), text: 'Read by you' },
        { ts: at(0, 0, 0, -1), text: 'Filed' },
      ],
      related: [],
    },
  },
  {
    id: 9,
    target: 'analytics',
    type: 'traffic_spike',
    subject: 'ca00000000a2',
    title: 'Traffic spike on analytics ended',
    summary: '1.6x for 23 minutes',
    severity: 0.6,
    created_at: at(14, 2, 0, -2),
    state: 'resolved',
    resolved_at: at(14, 25, 0, -2),
    resolved_by: 'code',
    delivery: 'inbox',
    keys: ['ca00000000a2'],
    link: link('database', 'analytics', 'See the chart'),
    view: {
      kind: 'traffic_spike',
      band: 'closed',
      history: [
        { ts: at(14, 25, 0, -2), text: 'Back to normal, item closed by RDST' },
        { ts: at(14, 2, 0, -2), text: 'Filed: calls 1.6x the usual rate' },
      ],
      related: [],
    },
  },
  {
    id: 10,
    target: 'staging',
    type: 'stuck_query',
    subject: 'ca00000000b2',
    title: 'Stuck query on staging ended by itself',
    summary: 'UPDATE on users, ran 6 min',
    severity: 0.9,
    created_at: at(15, 40, 0, -3),
    state: 'resolved',
    resolved_at: at(15, 46, 0, -3),
    resolved_by: 'code',
    delivery: 'notify',
    keys: ['ca00000000b2'],
    link: link('query', 'staging', 'Open query', {
      query_key: 'ca00000000b2',
      tab: 'performance',
    }),
    view: {
      kind: 'stuck_query',
      band: 'closed',
      running_s: 360,
      history: [
        { ts: at(15, 46, 0, -3), text: 'Statement ended, item closed by RDST' },
        { ts: at(15, 40, 0, -3), text: 'Filed and pushed to this computer' },
      ],
      related: [],
    },
  },
]

const UNREACHABLE_ITEM: CaItemSpec = {
  id: 11,
  target: 'database',
  type: 'monitoring_issue',
  subject: 'lost_connection',
  title: "Can't reach the database",
  summary: 'localhost:5432 refused the connection. Is PostgreSQL running?',
  severity: 1,
  created_at: at(9, 44),
  state: 'new',
  delivery: 'inbox',
  keys: [],
  link: { kind: 'configure', target: 'database', label: 'Fix connection' },
  body: {
    target: 'database',
    failed_since: at(9, 41),
    gap_from: at(9, 40, 58),
    error: 'connection refused',
  },
  view: {
    kind: 'monitoring_issue',
    band: 'act_now',
    history: [
      { ts: at(9, 44), text: 'Filed after 3 failed readings' },
      { ts: at(9, 41), text: 'localhost:5432 refused the connection' },
    ],
    related: [],
  },
}

const QUIET_CLOSED: CaItemSpec[] = [
  {
    id: 12,
    target: 'demo',
    type: 'stuck_query',
    subject: CA_KEYS.stuck,
    title: 'Stuck query on demo ended by itself',
    summary: 'COUNT pg_sleep on posts, ran 6 min',
    severity: 0.9,
    created_at: at(9, 6),
    state: 'resolved',
    resolved_at: at(9, 12),
    resolved_by: 'code',
    delivery: 'notify',
    keys: [CA_KEYS.stuck],
    link: link('query', 'demo', 'Open query', {
      query_key: CA_KEYS.stuck,
      tab: 'performance',
    }),
    view: {
      kind: 'stuck_query',
      band: 'closed',
      running_s: 360,
      history: [
        { ts: at(9, 12), text: 'Statement ended, item closed by RDST' },
        { ts: at(9, 6), text: 'Filed and pushed to this computer' },
      ],
      related: [],
    },
  },
  CLOSED_ITEMS[0],
]

function viewDefaults(spec: CaItemSpec): InboxItemView {
  return {
    kind: 'other',
    band:
      spec.state === 'resolved' || spec.state === 'dismissed'
        ? 'closed'
        : 'watch',
    pid: null,
    running_s: null,
    steps: [],
    fixes: [],
    fix_group: null,
    fixes_share: null,
    source_report_id: null,
    before_ms: null,
    after_ms: null,
    calls: null,
    index_name: null,
    changes: null,
    action_id: null,
    history: [],
    related: [],
    ...spec.view,
  }
}

function inboxItem(spec: CaItemSpec): InboxItem {
  const view = viewDefaults(spec)
  const finished = spec.state === 'resolved' || spec.state === 'dismissed'
  // A change that did not move closes neutral, whoever closed it.
  const positive =
    spec.type === 'fix_confirmed' ||
    (finished && spec.resolved_by === 'code' && spec.type !== 'no_change_yet')
  const band =
    view.band === 'closed' ? (positive ? 'recovered' : 'fyi') : view.band
  const labels = Object.fromEntries(
    spec.keys.flatMap((key) =>
      rowByKey.get(key) ? [[key, rowByKey.get(key)?.name]] : []
    )
  )
  return {
    id: spec.id,
    target: spec.target,
    type: spec.type,
    subject: spec.subject,
    state: spec.state,
    severity: spec.severity,
    title: spec.title,
    body_json: {
      ...(spec.body ?? {}),
      ...(finished ? { recovery_seen_at: spec.resolved_at } : {}),
    },
    created_at: spec.created_at,
    updated_at: spec.resolved_at ?? spec.created_at,
    resolved_at: finished ? (spec.resolved_at ?? spec.created_at) : null,
    resolved_by: finished ? (spec.resolved_by ?? 'user') : null,
    seen_count: spec.state === 'new' ? 0 : 1,
    reopened_count: 0,
    severity_band: band,
    tone: positive
      ? 'positive'
      : band === 'act_now'
        ? 'negative'
        : band === 'watch'
          ? 'warning'
          : 'neutral',
    summary: spec.summary,
    query_keys: spec.keys,
    subject_label:
      spec.keys.length === 1
        ? (labels[spec.keys[0]] ?? spec.target)
        : spec.target,
    query_labels: labels as Record<string, string>,
    group_key: `${spec.type}:${spec.target}:${Math.floor(spec.created_at / (6 * HOUR))}`,
    muted_by: null,
    trend: [],
    delivery: spec.delivery,
    notified_at: spec.delivery === 'notify' ? spec.created_at : null,
    relevance_rule: null,
    link: spec.link,
    advice:
      spec.type === 'stuck_query' && !finished
        ? {
            summary:
              "RDST doesn't stop running statements. From a SQL session:",
            steps: view.steps.map((step) => `${step.title}: ${step.sql}`),
          }
        : null,
    status: finished ? 'closed' : 'ongoing',
    status_at: finished ? (spec.resolved_at ?? null) : spec.created_at,
    band_reason: '',
    ...view,
  }
}

// Reports

interface CaReportDay {
  id: number
  day: number
  fixes: number
  word: string
}

/** Oct 7 back to Oct 1: 9 / 7 / 8 / 3 / quiet / 1 / first (plan 3.7). */
const REPORT_DAYS: CaReportDay[] = [
  { id: 31, day: -1, fixes: 9, word: '9 fixes' },
  { id: 30, day: -2, fixes: 7, word: '7 fixes' },
  { id: 29, day: -3, fixes: 8, word: '8 fixes' },
  { id: 28, day: -4, fixes: 3, word: '3 fixes' },
  { id: 27, day: -5, fixes: 0, word: 'quiet' },
  { id: 26, day: -6, fixes: 1, word: '1 fix' },
  { id: 25, day: -7, fixes: 0, word: 'first report' },
]

function isoDay(ts: number): string {
  const date = new Date(ts * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function reportSummary(entry: CaReportDay): ReportSummary {
  const start = at(0, 0, 0, entry.day)
  return {
    id: entry.id,
    ts: at(0, 0, 0, entry.day + 1),
    trigger: 'nightly',
    duration_s: 61,
    headline: entry.word,
    verdict: entry.word,
    counts: {
      fix_first: entry.fixes,
      one_fix: entry.id === 31 ? 2 : 0,
      health_warn: entry.id === 31 ? 3 : 1,
      health_ok: entry.id === 31 ? 5 : 7,
    },
    provisional_count: 0,
    period_start: start,
    period_end: start + DAY,
    period_partial: false,
    day: isoDay(start),
    sample: false,
    events: entry.id === 31 ? 4 : 1,
    open_events: 0,
    look_at: entry.fixes,
    health_problems: entry.id === 31 ? 3 : 1,
    qps: 11.7,
  }
}

function dayChart(start: number): SnapshotChart {
  const xs = Array.from({ length: 24 }, (_, hour) => start + hour * HOUR)
  const bands = BY_SHARE.filter((row) => row.share > 0).slice(0, 8)
  const series = (row: CaRow, label: string, base: number, swing: number) =>
    xs.map((ts) =>
      round(
        Math.max(0, base * (1 + swing * noise(`${row.key}:${label}`, ts))),
        4
      )
    )
  return {
    res: HOUR,
    start,
    end: start + DAY,
    xs,
    bands: bands.map((row, index) => ({
      key: row.key,
      kind: 'query',
      display_name: row.name,
      color_index: index,
      share: row.share,
      calls: round(row.qps * DAY),
      qps: row.qps,
      mean_ms: row.mean_ms,
    })),
    qps: bands.map((row) => series(row, 'qps', row.qps, 0.3)),
    ms: bands.map((row) =>
      series(row, 'ms', row.qps * HOUR * row.mean_ms, 0.3)
    ),
    mean_ms: bands.map((row) => series(row, 'mean', row.mean_ms, 0.1)),
    overall_mean_ms: xs.map((ts) =>
      round(15.9 * (1 + 0.1 * noise('overall', ts)), 3)
    ),
    markers: [],
  }
}

const FIX_FIRST: ReportFixItem[] = [
  {
    rank: 1,
    title: 'Raise shared_buffers to 2 GB',
    detail: 'Restart needed. Lifts cache hit for every query.',
    sql: "ALTER SYSTEM SET shared_buffers = '2GB';",
    share: null,
    state: 'needs_attention',
    kind: 'config',
    group: null,
    query_keys: [],
    restart: true,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 2,
    title: 'Index posts (creationdate, owneruserid, score)',
    detail: '3 queries, 37% of time',
    sql: 'CREATE INDEX CONCURRENTLY idx_posts_creationdate_owneruserid_score ON public.posts (creationdate, owneruserid, score);',
    share: 0.37,
    state: 'ready',
    kind: 'index',
    group: POSTS_GROUP,
    query_keys: [
      CA_KEYS.postsUsersByCreationdate,
      CA_KEYS.postsByOwneruserid,
      CA_KEYS.postsByCreationdate,
    ],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 3,
    title: 'Index votes (postid) include (id, votetypeid)',
    detail: '17% of time',
    sql: 'CREATE INDEX CONCURRENTLY idx_votes_postid ON public.votes (postid) INCLUDE (id, votetypeid);',
    share: 0.17,
    state: 'ready',
    kind: 'index',
    group: null,
    query_keys: [CA_KEYS.votesByPostid],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 4,
    title: 'Index comments (postid)',
    detail: '12% of time',
    sql: 'CREATE INDEX CONCURRENTLY idx_comments_postid ON public.comments (postid);',
    share: 0.12,
    state: 'ready',
    kind: 'index',
    group: null,
    query_keys: [CA_KEYS.commentsByPostid],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 5,
    title: 'Find why transactions roll back',
    detail: "143 today. Check the app's error log after 9:40.",
    sql: null,
    share: null,
    state: 'worth_doing',
    kind: 'other',
    group: null,
    query_keys: [],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: null,
    title: 'Index comments (userid, creationdate)',
    detail: 'Fixed 8:12 AM, 27.6 ms to 1.9 ms',
    sql: null,
    share: null,
    state: 'fixed',
    kind: 'index',
    group: null,
    query_keys: [CA_KEYS.commentsByUserid],
    restart: false,
    fixed_at: at(8, 12),
    before_ms: 27.6,
    after_ms: 1.9,
  },
  {
    rank: 6,
    title: 'Index badges (userid, date)',
    detail: '4% of time',
    sql: 'CREATE INDEX CONCURRENTLY idx_badges_userid_date ON public.badges (userid, date DESC);',
    share: 0.04,
    state: 'smaller',
    kind: 'index',
    group: null,
    query_keys: [CA_KEYS.badgesByUserid],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 7,
    title: 'Index users (reputation)',
    detail: '3% of time',
    sql: 'CREATE INDEX CONCURRENTLY idx_users_reputation ON public.users (reputation);',
    share: 0.03,
    state: 'smaller',
    kind: 'index',
    group: null,
    query_keys: [CA_KEYS.usersByReputation],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 8,
    title: 'Index posts (parentid)',
    detail: '2 queries, 3% of time',
    sql: 'CREATE INDEX CONCURRENTLY idx_posts_parentid ON public.posts (parentid);',
    share: 0.03,
    state: 'smaller',
    kind: 'index',
    group: PARENTID_GROUP,
    query_keys: [CA_KEYS.postsByParentid, CA_KEYS.countPostsByParentid],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
  {
    rank: 9,
    title: 'Drop unused index votes_creationdate_idx',
    detail: '0 scans since the stats reset, 41 MB',
    sql: 'DROP INDEX CONCURRENTLY public.votes_creationdate_idx;',
    share: null,
    state: 'smaller',
    kind: 'other',
    group: null,
    query_keys: [],
    restart: false,
    fixed_at: null,
    before_ms: null,
    after_ms: null,
  },
]

/** The Detailed sections for Report #31, October 7 (report-detailed.html). */
function detailedReport(): ReportDetailed {
  const on = (hour: number, minute: number) => at(hour, minute, 0, -1)
  return {
    compared: true,
    tiles: [
      {
        metric: 'qps',
        label: 'Queries per second',
        value: 11.7,
        prior: 9.8,
        change: 0.194,
        unit: 'qps',
        state: 'ok',
      },
      {
        metric: 'mean_ms',
        label: 'Average latency',
        value: 15.9,
        prior: 12.4,
        change: 0.282,
        unit: 'ms',
        state: 'ok',
      },
      {
        metric: 'p95_ms',
        label: 'p95 latency',
        value: 69.7,
        prior: 51.2,
        change: 0.361,
        unit: 'ms',
        state: 'ok',
      },
      {
        metric: 'connections',
        label: 'Peak connections',
        value: 8,
        prior: 6,
        change: 0.333,
        unit: 'count',
        state: 'ok',
        limit: 100,
      },
      {
        metric: 'cache_hit',
        label: 'Lowest cache hit',
        value: 0.115,
        prior: 0.938,
        change: -0.877,
        unit: 'ratio',
        state: 'warn',
      },
      {
        metric: 'rollbacks_deadlocks',
        label: 'Rollbacks, deadlocks',
        value: 143,
        prior: 21,
        change: 5.81,
        unit: 'count',
        state: 'warn',
        second: 0,
        second_prior: 0,
      },
    ],
    spikes: [
      {
        kind: 'busiest',
        metric: 'traffic',
        start: on(9, 40),
        end: on(10, 5),
        peak: 15.9,
        usual: 11.4,
        ratio: 1.4,
        summary: null,
        rollbacks: 121,
        queries: [
          [CA_KEYS.votesByPostid, 0.31],
          [CA_KEYS.postsById, 0.18],
          [CA_KEYS.commentsByPostid, 0.14],
        ].map(([key, share]) => stretchQuery(key as string, share as number)),
      },
      {
        kind: 'busiest',
        metric: 'traffic',
        start: on(14, 30),
        end: on(14, 50),
        peak: 13.2,
        usual: 11.4,
        ratio: 1.16,
        summary: '13.2 queries/s, p95 88.4 ms',
        rollbacks: 0,
        queries: [
          [CA_KEYS.postsUsersByCreationdate, 0.26],
          [CA_KEYS.postsByPosttypeid, 0.19],
        ].map(([key, share]) => stretchQuery(key as string, share as number)),
      },
    ],
    what_changed: [
      changedRow('slower', CA_KEYS.postsByOwneruserid, 0.17, 48.7, 65.0, {
        detail: 'reads went to disk as cache hit fell',
      }),
      changedRow('slower', CA_KEYS.votesByPostid, 0.17, 22.1, 29.0, {
        detail: 'fix 3 covers it',
      }),
      changedRow('faster', CA_KEYS.commentsByUserid, 0.02, 27.6, 1.9, {
        detail: 'After the index added at 8:12 AM',
      }),
      changedRow('new_heavy', CA_KEYS.postsByPosttypeid, 0.096, null, 63.3, {
        detail: 'First seen 9:46 AM',
      }),
      changedRow('plan_changed', CA_KEYS.commentsByUserid, null, null, null, {
        detail:
          'Sequential scan to index scan on idx_comments_userid_creationdate',
        ts: on(8, 12),
      }),
    ],
    fixes_confirmed: [
      {
        title: 'Index comments (userid, creationdate)',
        applied_at: on(8, 12),
        confirmed_at: on(9, 12),
        before_ms: 27.6,
        after_ms: 1.9,
        query_keys: [CA_KEYS.commentsByUserid],
      },
      {
        title: 'Index posts (acceptedanswerid)',
        applied_at: at(16, 30, 0, -2),
        confirmed_at: on(0, 40),
        before_ms: 41.3,
        after_ms: 3.4,
        query_keys: [],
      },
    ],
    health_checks: [
      {
        check: 'cache',
        label: 'Cache hit',
        state: 'bad',
        needs_you: true,
        finding: 'Was 94% before the load started. shared_buffers is 128 MB.',
        number: '11.5% lowest',
        fix_rank: 1,
      },
      {
        check: 'settings',
        label: 'Settings',
        state: 'warn',
        needs_you: true,
        finding: 'Still at their defaults, listed below',
        number: '4 to change',
        fix_rank: 1,
      },
      {
        check: 'transactions',
        label: 'Rollbacks and deadlocks',
        state: 'warn',
        needs_you: true,
        finding:
          '121 of them between 9:40 and 10:05 AM. Nothing in the queries explains it yet.',
        number: '143 and 0',
        fix_rank: 5,
      },
      {
        check: 'connections',
        label: 'Connections',
        state: 'ok',
        finding:
          'Peak at 9:50 AM. At most 2 idle in a transaction, none for over a minute.',
        number: '8 of 100',
        fix_rank: null,
      },
      {
        check: 'vacuum',
        label: 'Vacuum and bloat',
        state: 'ok',
        finding: 'Autovacuum kept up. Most dead rows on votes, 2.1%.',
        number: '0 tables',
        fix_rank: null,
      },
      {
        check: 'indexes',
        label: 'Indexes',
        state: 'ok',
        finding: 'No duplicates. One unread, 41 MB, is fix 9.',
        number: '18 indexes',
        fix_rank: null,
      },
      {
        check: 'replication',
        label: 'Replication',
        state: 'ok',
        finding: 'No replicas and no replication slots',
        number: 'none',
        fix_rank: null,
      },
      {
        check: 'locks',
        label: 'Locks',
        state: 'ok',
        finding:
          'Longest wait at 9:44 AM. No statement blocked for over a second.',
        number: '120 ms longest',
        fix_rank: null,
      },
    ],
    settings_audit: [
      ['shared_buffers', '128 MB', '2 GB', 'after a restart'],
      ['work_mem', '4 MB', '32 MB', 'on reload'],
      ['effective_cache_size', '4 GB', '6 GB', 'on reload'],
      ['random_page_cost', '4', '1.1', 'on reload'],
    ].map(([parameter, current, recommended, takes_effect]) => ({
      parameter,
      current,
      recommended,
      takes_effect: takes_effect as 'after a restart' | 'on reload',
      note: null,
      in_actions: parameter === 'shared_buffers',
    })),
    index_changes_seen: [
      {
        ts: on(8, 12),
        kind: 'added',
        index_name: 'idx_comments_userid_creationdate',
        table: 'comments',
        definition:
          'CREATE INDEX idx_comments_userid_creationdate ON public.comments (userid, creationdate)',
        from_rdst: true,
        action_id: 7,
        status: 'verified',
        scans: 412,
      },
      {
        ts: on(16, 5),
        kind: 'added',
        index_name: 'idx_users_displayname',
        table: 'users',
        definition:
          'CREATE INDEX idx_users_displayname ON public.users (displayname)',
        from_rdst: false,
        action_id: null,
        status: null,
        scans: 0,
      },
    ],
    what_rdst_did: [
      didRow(on(0, 0), 'Filed the report for October 6', { report_id: 30 }),
      didRow(on(8, 12), 'Saw the new index on comments and started measuring', {
        detail: '1 query',
      }),
      didRow(on(9, 12), 'Confirmed the comments fix', {
        detail: '27.6 ms to 1.9 ms',
      }),
      didRow(on(9, 47), 'Analyzed the 12 busiest queries', {
        until: on(9, 49),
        detail: '9 fixes suggested',
      }),
      didRow(on(9, 52), 'Tested 2 rewrites against the index fixes', {
        detail: 'indexes kept',
      }),
      didRow(on(9, 55), 'Filed an Inbox item, cache hit fell to 11.5%', {
        item_id: 4,
      }),
      didRow(on(0, 0), 'Ran the 8 health checks every 15 minutes', {
        until: at(0, 0),
        detail: '96 runs',
      }),
    ],
    totals: {
      queries_seen: 12,
      readings: 1440,
      data_kept_days: 7,
      data_bytes: 18 * 1024 * 1024,
      report_number: 31,
      ran_at: at(0, 0),
    },
  }
}

function stretchQuery(key: string, share: number) {
  return {
    query_key: key,
    display_name: rowByKey.get(key)?.name ?? key,
    share,
    before: null,
    during: null,
    ratio: null,
  }
}

function changedRow(
  kind: WhatChangedRow['kind'],
  key: string,
  share: number | null,
  before: number | null,
  after: number | null,
  extra: { detail?: string; ts?: number } = {}
): WhatChangedRow {
  return {
    kind,
    query_key: key,
    display_name: rowByKey.get(key)?.name ?? key,
    share,
    before_ms: before,
    after_ms: after,
    change: before != null && after != null ? round(after / before - 1) : null,
    detail: extra.detail ?? null,
    ts: extra.ts ?? null,
  }
}

function didRow(
  ts: number,
  text: string,
  extra: Partial<Omit<RdstDidRow, 'ts' | 'text'>> = {}
): RdstDidRow {
  return {
    ts,
    text,
    until: null,
    detail: null,
    item_id: null,
    report_id: null,
    ...extra,
  }
}

function report(
  target: string,
  entry: CaReportDay,
  options: { signedOut?: boolean; onRequest?: boolean } = {}
): DatabaseReport {
  const summary = reportSummary(entry)
  const start = summary.period_start as number
  const latest = entry.id === 31
  const signedOut = options.signedOut ?? false
  const chart = dayChart(start)
  return {
    id: options.onRequest ? 32 : summary.id,
    target,
    ts: options.onRequest ? at(9, 51) : summary.ts,
    trigger: options.onRequest ? 'user' : 'nightly',
    state: 'done',
    provisional: false,
    finished_at: (options.onRequest ? at(9, 51) : summary.ts) + 61,
    progress: null,
    report_json: {
      summary: '',
      fix_first: [],
      one_fix_several_queries: [],
      health: [],
      changed_since_yesterday: [],
      provisional_query_keys: [],
      narrative_error: null,
      version: 3,
      verdict: latest
        ? {
            sentence:
              'demo needs two things today: a bigger buffer cache and one index on posts.',
            query_key: CA_KEYS.postsUsersByCreationdate,
            share: 0.19,
            tone: 'warning',
          }
        : null,
      since_yesterday: [],
      snapshot: null,
    },
    refs: Object.fromEntries(
      ROWS.map((row) => [
        row.key,
        { display_name: row.name, text_snippet: row.sql.slice(0, 89) },
      ])
    ),
    period_start: summary.period_start,
    period_end: summary.period_end,
    period_partial: false,
    sample: false,
    verdict: signedOut
      ? null
      : latest
        ? {
            lead: 'demo needs two things today:',
            rest: 'a bigger buffer cache and one index on posts. 9 fixes are ready, 3 of 8 checks need you, everything else held.',
          }
        : {
            lead: `${target} had ${entry.word} ready:`,
            rest: 'everything else held.',
          },
    day: {
      label: `${new Date(start * 1000).toLocaleDateString('en-US', { month: 'long', day: 'numeric' })}, vs the day before`,
      tiles: [
        {
          metric: 'qps',
          label: 'Queries per second',
          value: 11.7,
          unit: 'qps',
          detail: 'peak 15.9 at 9:50',
          state: 'ok',
          reason: null,
        },
        {
          metric: 'mean_ms',
          label: 'Average latency',
          value: 15.9,
          unit: 'ms',
          detail: 'p95 69.7 ms',
          state: 'ok',
          reason: null,
        },
        {
          metric: 'connections',
          label: 'Peak connections',
          value: 8,
          unit: 'count',
          detail: '0 deadlocks',
          state: 'ok',
          reason: null,
          limit: 100,
        },
        {
          metric: 'cache_hit',
          label: 'Lowest cache hit',
          value: 0.115,
          unit: 'ratio',
          detail: null,
          state: 'warn',
          reason: 'shared_buffers is the default',
        },
      ],
      chart,
      spikes: [],
      query_count: 12,
      compared: true,
    },
    fix_first: latest
      ? FIX_FIRST.map((item) => (signedOut ? { ...item, sql: null } : item))
      : FIX_FIRST.slice(1, 1 + entry.fixes).map((item, index) => ({
          ...item,
          rank: index + 1,
          state: 'ready' as const,
          sql: signedOut ? null : item.sql,
        })),
    health: latest
      ? [
          {
            check: 'cache',
            label: 'Cache hit',
            state: 'bad',
            needs_you: true,
            finding: 'Cache hit fell to 11.5%',
            detail:
              'Was 94% before the load started. shared_buffers is 128 MB.',
            fix_rank: 1,
          },
          {
            check: 'settings',
            label: 'Settings',
            state: 'warn',
            needs_you: true,
            finding: '4 settings still at their defaults',
            detail:
              'shared_buffers, work_mem, effective_cache_size, random_page_cost',
            fix_rank: 1,
          },
          {
            check: 'transactions',
            label: 'Rollbacks and deadlocks',
            state: 'warn',
            needs_you: true,
            finding: '143 transactions rolled back',
            detail: '0 deadlocks. Nothing in the queries explains it yet.',
            fix_rank: 5,
          },
        ]
      : [
          {
            check: 'cache',
            label: 'Cache hit',
            state: 'warn',
            needs_you: true,
            finding: 'Cache hit at 12%',
            detail: 'shared_buffers is the default',
            fix_rank: 1,
          },
        ],
    health_fine: latest
      ? ['Connections', 'vacuum and bloat', 'indexes', 'replication', 'locks']
      : [
          'Connections',
          'vacuum and bloat',
          'indexes',
          'replication',
          'locks',
          'settings',
          'rollbacks and deadlocks',
        ],
    run: {
      number: options.onRequest ? 32 : summary.id,
      trigger: options.onRequest ? 'on_request' : 'nightly',
      ran_at: options.onRequest ? at(9, 51) : summary.ts,
      model: signedOut ? null : 'Claude Sonnet 4.6',
      analyses: 12,
      rewrites: 2,
    },
    narrative_missing: signedOut,
    copy_count: signedOut
      ? 0
      : (latest ? FIX_FIRST : FIX_FIRST.slice(1, 1 + entry.fixes)).filter(
          (item) => item.sql && item.state !== 'fixed'
        ).length,
    detailed: latest ? detailedReport() : null,
  }
}

function reportHistory(target: string, limit: number): ReportHistory {
  void target
  return { reports: REPORT_DAYS.slice(0, limit).map(reportSummary) }
}

// Home

function spark(target: string, qps: number) {
  const points = []
  for (let ts = CA_MIDNIGHT; ts <= CA_NOW; ts += 5 * MIN) {
    const hour = (ts - CA_MIDNIGHT) / HOUR
    const shape =
      0.45 + 0.55 * Math.max(0, Math.sin(((hour - 4) / 12) * Math.PI))
    points.push({
      ts,
      qps: round(qps * shape * (1 + 0.1 * noise(`${target}:spark`, ts)), 3),
    })
  }
  return points
}

const DEMO_DAY = (
  fixesItemId: number | null,
  cacheHit: number,
  cacheReason: string | null
): HomeModel['day'] => ({
  target: 'demo',
  qps: 11.7,
  qps_peak: 15.9,
  qps_peak_at: at(9, 50),
  mean_ms: 15.9,
  mean_ms_delta: 0,
  fixes_ready: 9,
  fixes_share: 0.54,
  fixes_item_id: fixesItemId,
  cache_hit_ratio: cacheHit,
  cache_hit_reason: cacheReason,
  series: spark('demo', 15.9),
  report_id: 31,
  report_day: '2026-10-08',
})

const RECENTLY_ANALYZED: HomeModel['recently_analyzed'] = [
  {
    target: 'demo',
    query_key: CA_KEYS.postsUsersByCreationdate,
    display_name: 'SELECT on posts and users by creationdate',
    analyzed_at: at(15, 15, 0, -1),
    analysis_ref: `/api/query-registry/${CA_KEYS.postsUsersByCreationdate}/analysis/ca_${CA_KEYS.postsUsersByCreationdate}`,
    outcome: 'fix_suggested',
  },
  {
    target: 'demo',
    query_key: CA_KEYS.postsByOwneruserid,
    display_name: 'SELECT on posts by owneruserid',
    analyzed_at: at(13, 12, 0, -1),
    analysis_ref: `/api/query-registry/${CA_KEYS.postsByOwneruserid}/analysis/ca_${CA_KEYS.postsByOwneruserid}`,
    outcome: 'fix_suggested',
  },
  {
    target: 'demo',
    query_key: CA_KEYS.votesByPostid,
    display_name: 'SELECT on votes by postid',
    analyzed_at: at(13, 11, 0, -2),
    analysis_ref: `/api/query-registry/${CA_KEYS.votesByPostid}/analysis/ca_${CA_KEYS.votesByPostid}`,
    outcome: 'fix_suggested',
  },
]

const HOME_LOG: HomeLogEntry[] = [
  {
    ts: at(9, 55),
    text: 'Stuck query filed',
    detail: null,
    link: { kind: 'inbox', target: 'demo', label: 'Inbox', item_id: 1 },
    band: 'act_now',
  },
  {
    ts: at(9, 51),
    text: 'Report ran: 9 index fixes ready',
    detail: '54% of database time',
    link: { kind: 'inbox', target: 'demo', label: 'Inbox', item_id: 2 },
    band: null,
  },
  {
    ts: at(9, 48),
    text: 'Traffic spike ended',
    detail: '2.1x for 11 minutes',
    link: { kind: 'chart', target: 'demo', label: 'Chart', item_id: 5 },
    band: null,
  },
  {
    ts: at(9, 40),
    text: '2 new queries analyzed',
    detail: 'both healthy',
    link: { kind: 'queries', target: 'demo', label: 'Queries' },
    band: null,
  },
  {
    ts: at(8, 12),
    text: 'Fix confirmed on comments (userid, creationdate)',
    detail: '27.6 ms to 1.9 ms',
    link: {
      kind: 'query',
      target: 'demo',
      label: 'Query',
      query_key: CA_KEYS.commentsByUserid,
      item_id: 4,
    },
    band: null,
  },
  {
    ts: at(0, 0),
    text: 'Daily report for October 7',
    detail: null,
    link: { kind: 'report', target: 'demo', label: 'Report', report_id: 31 },
    band: null,
  },
]

const HOME_ONE_LOG: HomeLogEntry[] = [
  HOME_LOG[1],
  HOME_LOG[3],
  {
    ts: at(9, 12),
    text: 'Stuck query ended, item closed',
    detail: 'ran 6 minutes',
    link: { kind: 'inbox', target: 'demo', label: 'Inbox', item_id: 12 },
    band: null,
  },
  HOME_LOG[4],
  {
    ts: at(7, 30),
    text: 'Plan changed on SELECT on votes by postid',
    detail: 'no slower',
    link: {
      kind: 'query',
      target: 'demo',
      label: 'Query',
      query_key: CA_KEYS.votesByPostid,
    },
    band: null,
  },
  HOME_LOG[5],
]

/** ca-home's analytics report, filed on another database than demo. */
const ANALYTICS_LOG: HomeLogEntry = {
  ts: at(9, 30),
  text: 'Report ran: 2 changes ready',
  detail: null,
  link: { kind: 'actions', target: 'analytics', label: 'Actions' },
  band: null,
}

type HomeCaDatabase = CaDatabase & { to_do: number; do_first: number }

/** ca-home: demo needs you, analytics has 2 changes to do, staging is not monitored. */
const HOME_DATABASES: HomeCaDatabase[] = [
  { ...DATABASES[0], to_do: 5, do_first: 2 },
  {
    ...(DATABASES.find((row) => row.target === 'analytics') as CaDatabase),
    fixes_ready: 3,
    to_do: 2,
    do_first: 1,
  },
  {
    ...(DATABASES.find((row) => row.target === 'staging') as CaDatabase),
    state: 'not_monitored',
    qps: null,
    fixes_ready: 0,
    to_do: 0,
    do_first: 0,
  },
]

function homeDatabases(list: HomeCaDatabase[]): HomeDatabase[] {
  const order: Record<HomeDatabase['state'], number> = {
    cant_reach: 1,
    watching: 2,
    ai_paused: 3,
    not_monitored: 4,
  }
  const rank = (row: CaDatabase) => (row.act_now > 0 ? 0 : order[row.state])
  return [...list]
    .sort((a, b) => rank(a) - rank(b) || b.fixes_ready - a.fixes_ready)
    .map(({ engine_version: _version, ...row }) => row)
}

/** What monitoring is doing on a fixture database: demo works through its first analyses. */
function caActivity(
  target: string,
  state: HomeDatabase['state']
): DatabaseActivity {
  if (state === 'not_monitored')
    return { kind: 'idle', label: 'Monitoring is off', every_s: 60 }
  if (target === 'demo')
    return {
      kind: 'analyzing',
      label: 'Analyzing SELECT on votes by postid',
      done: 4,
      total: 10,
      last_reading_at: at(9, 56, 30),
      every_s: 60,
      next_check_at: at(9, 59, 30),
    }
  return {
    kind: 'reading',
    label: 'Reading query statistics',
    last_reading_at: at(9, 56, 4),
    every_s: 60,
    next_check_at: at(10, 0, 30),
  }
}

function homeStrip(rows: HomeDatabase[]): HomeStripRow[] {
  return rows.map((row) => ({
    target: row.target,
    state: row.state,
    act_now: row.act_now,
    to_do: row.to_do ?? 0,
    do_first: row.do_first ?? 0,
    fixes_ready: row.fixes_ready,
    activity: caActivity(row.target, row.state),
  }))
}

function homeModel(scenario: CaScenario): HomeModel {
  const nextReport = at(0, 0, 0, 1)
  if (scenario === 'ca-home-empty')
    return {
      status: {
        state: 'off',
        target: null,
        needs_you: 0,
        to_watch: 0,
        headline: null,
        link: null,
        checks_ran_at: null,
        since: null,
        last_reading_at: null,
        next_report_at: null,
        watching: 0,
      },
      day: null,
      databases: [],
      databases_strip: [],
      not_monitored: 0,
      today: [],
      since_yesterday: { opened_closed: 0, fixes_confirmed: 0 },
      recently_analyzed: [],
      generated_at: CA_NOW,
    }
  if (scenario === 'ca-home-one' || scenario === 'ca-first-run') {
    const firstRun = scenario === 'ca-first-run'
    const one: HomeDatabase[] = [DATABASES[0]].map(
      ({ engine_version: _version, ...row }) => ({
        ...row,
        open_items: firstRun ? 0 : 1,
        act_now: 0,
        fixes_ready: firstRun ? 0 : 9,
        fixes_item_id: firstRun ? null : 2,
        to_do: firstRun ? 0 : 5,
        do_first: firstRun ? 0 : 2,
      })
    )
    return {
      status: {
        state: 'quiet',
        target: 'demo',
        needs_you: 0,
        to_watch: firstRun ? 0 : 1,
        headline: null,
        link: null,
        checks_ran_at: firstRun ? at(9, 55) : at(9, 55),
        since: firstRun ? at(9, 45) : at(9, 12),
        last_reading_at: at(9, 56, 30),
        next_report_at: nextReport,
        watching: 1,
        act_now: 0,
        do_first: firstRun ? 0 : 2,
      },
      day: firstRun ? null : DEMO_DAY(2, 0.992, null),
      databases: one,
      databases_strip: homeStrip(one),
      not_monitored: 0,
      today: firstRun ? [] : HOME_ONE_LOG,
      since_yesterday: {
        opened_closed: firstRun ? 0 : 1,
        fixes_confirmed: firstRun ? 0 : 1,
      },
      recently_analyzed: firstRun ? [] : RECENTLY_ANALYZED,
      generated_at: CA_NOW,
    }
  }
  const databases = homeDatabases(HOME_DATABASES)
  return {
    status: {
      state: 'needs_you',
      target: 'demo',
      needs_you: 1,
      to_watch: 1,
      headline: 'a stuck query since 9:55 AM',
      link: { kind: 'inbox', target: 'demo', label: 'Inbox', item_id: 1 },
      checks_ran_at: at(9, 55),
      since: at(9, 55),
      last_reading_at: at(9, 56, 30),
      next_report_at: nextReport,
      watching: 2,
      act_now: 1,
      do_first: 3,
    },
    day: DEMO_DAY(2, 0.125, 'shared_buffers is the default'),
    databases,
    databases_strip: homeStrip(databases),
    not_monitored: 1,
    today: [...HOME_LOG.slice(0, 3), ANALYTICS_LOG, ...HOME_LOG.slice(3)],
    since_yesterday: { opened_closed: 4, fixes_confirmed: 1 },
    recently_analyzed: [
      RECENTLY_ANALYZED[0],
      { ...RECENTLY_ANALYZED[1], target: 'analytics' },
      RECENTLY_ANALYZED[2],
    ],
    generated_at: CA_NOW,
  }
}

// Settings and the account

function settings(enabled: boolean, enabledAt: number | null): MonitorSettings {
  return {
    configured: true,
    enabled,
    deep_analysis: true,
    execute: false,
    analysis_mode: 'plans_only',
    auto_analyze_new: true,
    analysis_daily_cap: 15,
    newcomer_daily_cap: 100,
    attention_every_min: 5,
    nightly_report: true,
    nightly_hour: 0,
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
    enabled_at: enabledAt,
    first_hour_started_at:
      enabledAt !== null && enabledAt > CA_NOW - HOUR ? enabledAt : null,
    size_bytes: 9_400_000,
    budget_bytes: 256 * 1024 * 1024,
    ai_monitoring: 'on',
  }
}

function limit(signedIn: boolean, target?: string): MonitoringLimit {
  return {
    in_use: signedIn ? 4 : 0,
    limit: 5,
    own_key: false,
    known: true,
    full: false,
    signed_in: signedIn,
    target_fits: target ? true : null,
    targets: [],
  }
}

// The fixture set each scenario serves

/** benchmark-result.html: every fix estimated faster, 54% of database time covered. */
const BENCHMARK_AFTER_MS: Record<string, number> = {
  [CA_KEYS.postsUsersByCreationdate]: 3.1,
  [CA_KEYS.votesByPostid]: 0.9,
  [CA_KEYS.postsByOwneruserid]: 1.2,
  [CA_KEYS.commentsByPostid]: 0.8,
  [CA_KEYS.postsByCreationdate]: 18.4,
  [CA_KEYS.badgesByUserid]: 0.7,
  [CA_KEYS.postsByPosttypeid]: 31.5,
  [CA_KEYS.postsByParentid]: 0.6,
  [CA_KEYS.usersByReputation]: 4.1,
}

function benchmarkFixesResult(
  target: string,
  keys: string[]
): BenchmarkFixesResult {
  const rows = keys.flatMap((key) => {
    const row = rowByKey.get(key)
    if (!row) return []
    const after = BENCHMARK_AFTER_MS[key] ?? null
    return [
      {
        key,
        name: row.name,
        before_ms: row.mean_ms,
        after_ms: after,
        change_pct:
          after === null ? null : Math.round((after / row.mean_ms - 1) * 100),
        estimated: true,
      },
    ]
  })
  return {
    target,
    ran_at: CA_NOW - 2 * MIN,
    runs: 20,
    scratch_schema: false,
    rows,
    share: 0.54,
    method: `Estimated from the planner on ${target}: each fix tested as a hypothetical index, the same estimate Actions shows.`,
  }
}

// Actions and the index plan: one index per fix group, ranked by the database time its queries
// take. The top five are the plan's changes; the rest wait for later. Verbs move an action
// through `CaActionStates`; an action without one is open.

export interface CaActionState {
  state: ActionCard['state']
  snoozed_until: number | null
  updated_at: number
}
export type CaActionStates = ReadonlyMap<number, CaActionState>

type CaActions = 'plan' | 'first_pass' | 'idle'

const PLAN_ADDS = 5
const ACTIONS_AT = CA_NOW - 4 * MIN
const ACTIONS_WINDOW = {
  observed_s: CA_NOW - at(9, 45, 0, -7),
  counter_age_s: 30 * DAY,
}
/** Writes a second and the indexes each table already has. */
const TABLE_WRITES: Record<string, { writes: number; indexes: number }> = {
  posts: { writes: 0.6, indexes: 3 },
  votes: { writes: 2.4, indexes: 1 },
  comments: { writes: 1.1, indexes: 1 },
  badges: { writes: 0.3, indexes: 1 },
  users: { writes: 0.2, indexes: 2 },
}

const KEEPS: IndexPlanKeep[] = [
  {
    table: 'posts',
    index_name: 'posts_pkey',
    columns: ['id'],
    primary: true,
    unique: true,
    scans: 1_284_000,
    window_scans: 412_000,
    size_bytes: 48 * 1024 ** 2,
  },
  {
    table: 'users',
    index_name: 'users_pkey',
    columns: ['id'],
    primary: true,
    unique: true,
    scans: 903_000,
    window_scans: 287_000,
    size_bytes: 9 * 1024 ** 2,
  },
]

function fixShape(row: CaRow): { table: string; columns: string[] } {
  const match = /^Add index (\w+) \((.+?)\)/.exec(row.fix?.label ?? '')
  return {
    table: match?.[1] ?? row.tables[0],
    columns: (match?.[2] ?? '')
      .split(', ')
      .map((column) => column.replace(/ desc$/i, '')),
  }
}

function indexAdd(rows: CaRow[]): IndexPlanAdd {
  const [lead] = rows
  const sql = lead.fix?.sql ?? ''
  const { table, columns } = fixShape(lead)
  const name = /INDEX CONCURRENTLY (\w+)/.exec(sql)?.[1] ?? `idx_${table}`
  const writes = TABLE_WRITES[table] ?? { writes: 0.2, indexes: 1 }
  const helps = rows.map((row) => ({
    key: row.key,
    name: row.name,
    share: row.share,
    gain: round(row.share * 0.8),
    gain_pct: 100 - (row.plan_score ?? 50),
    impact: null,
    verified: true,
  }))
  return {
    id: `add:${table}(${columns.join(',')})`,
    table,
    columns,
    replaces: [],
    method: 'btree',
    merged: rows.slice(1).map((row) => fixShape(row).columns),
    helps,
    benefit: round(helps.reduce((sum, helped) => sum + helped.gain, 0)),
    verified: true,
    write_cost: {
      writes_per_s: writes.writes,
      peak_writes_per_s: round(writes.writes * 3, 1),
      existing_indexes: writes.indexes,
      updated_columns: [],
      hot_risk: false,
    },
    label: writes.writes >= 1 ? 'worth_it' : 'safe',
    label_reason:
      writes.writes >= 1
        ? 'Saves more read time than it costs writes'
        : 'Its table takes few writes',
    sql,
    undo_sql: `DROP INDEX CONCURRENTLY public.${name};`,
  }
}

interface CaIndexFix {
  id: number
  rows: CaRow[]
  add: IndexPlanAdd
}

const totalShare = (rows: CaRow[]) =>
  rows.reduce((sum, row) => sum + row.share, 0)

function indexFixes(): CaIndexFix[] {
  const groups = new Map<string, CaRow[]>()
  for (const row of ROWS) {
    if (!row.fix) continue
    const group = row.fix.group ?? row.fix.label
    groups.set(group, [...(groups.get(group) ?? []), row])
  }
  return [...groups.values()]
    .map((rows) => [...rows].sort((a, b) => b.share - a.share))
    .sort((a, b) => totalShare(b) - totalShare(a))
    .map((rows, index) => ({ id: index + 1, rows, add: indexAdd(rows) }))
}

const INDEX_FIXES = indexFixes()

function indexCard(fix: CaIndexFix, states: CaActionStates): ActionCard {
  const { add, rows } = fix
  const moved = states.get(fix.id)
  const share = totalShare(rows)
  return {
    id: fix.id,
    key: add.id,
    type: 'add_index',
    category: 'indexes',
    apply: 'run_sql',
    state: moved?.state ?? 'open',
    snoozed_until: moved?.snoozed_until ?? null,
    title: `Add an index on ${add.table} (${add.columns.join(', ')})`,
    summary: `Speeds up ${rows.length === 1 ? '1 query' : `${rows.length} queries`}, ${Math.round(share * 100)}% of database time`,
    why: null,
    note: null,
    sql: add.sql,
    steps: null,
    restart: false,
    confidence: 'measured',
    queries: rows.map((row) => ({ key: row.key, name: row.name })),
    opened_at: at(9, 51),
    updated_at: moved?.updated_at ?? ACTIONS_AT,
    children: [],
    detail: null,
    window: ACTIONS_WINDOW,
    evidence: [],
    rows: [],
    priority: fix.id,
    jev: round(share),
    score: round(share),
    severity: share >= 0.15 ? 'high' : share >= 0.08 ? 'medium' : 'low',
  }
}

const SET_ASIDE: readonly ActionCard['state'][] = ['snoozed', 'dismissed']
const DONE: readonly ActionCard['state'][] = [
  'applied',
  'confirmed',
  'no_effect',
]

function planCards(states: CaActionStates): ActionCard[] {
  return INDEX_FIXES.slice(0, PLAN_ADDS).map((fix) => indexCard(fix, states))
}

const checked = (note: string) => ({
  state: 'checked' as const,
  checked_at: ACTIONS_AT,
  next_at: CA_NOW + 11 * MIN,
  note,
})

function actionsPage(
  target: string,
  kind: CaActions,
  states: CaActionStates
): ActionsPage {
  const idle: ActionsPage = {
    target,
    computed_at: null,
    checkup: {
      state: 'none',
      analyzed: 0,
      total: 0,
      due_by: null,
      finished_at: null,
      report_id: null,
    },
    actions: [],
    more: [],
    set_aside: [],
    recently_done: [],
    card_count: 0,
    change_count: 0,
    open_count: 0,
    snoozed: 0,
    dismissed: 0,
    coverage: {},
    sections: {},
    window: null,
  }
  if (kind === 'idle') return idle
  if (kind === 'first_pass')
    return {
      ...idle,
      checkup: {
        ...idle.checkup,
        state: 'first_pass',
        analyzed: 4,
        total: 10,
        due_by: CA_NOW + 6 * MIN,
      },
      coverage: {
        indexes: {
          state: 'waiting',
          checked_at: null,
          next_at: null,
          note: 'Weighing the busiest queries',
          waiting_on: 'The index plan builds from the first deep analyses.',
        },
        settings: {
          state: 'checking',
          checked_at: null,
          next_at: null,
          note: 'Comparing the server settings with what RDST measures',
        },
        maintenance: {
          state: 'checking',
          checked_at: null,
          next_at: null,
          note: 'Checking dead rows, stale statistics and transaction age',
        },
        rewrites: {
          state: 'waiting',
          checked_at: null,
          next_at: null,
          note: 'Testing rewrites for the busiest queries',
        },
      },
    }
  const cards = planCards(states)
  const open = cards.filter((card) => card.state === 'open')
  const share = totalShare(
    INDEX_FIXES.slice(0, PLAN_ADDS)
      .filter((fix) => open.some((card) => card.id === fix.id))
      .flatMap((fix) => fix.rows)
  )
  const queries = open.reduce((sum, card) => sum + card.queries.length, 0)
  const tables = [
    ...new Set(open.map((card) => card.key.replace(/^add:(\w+).*/, '$1'))),
  ]
  return {
    ...idle,
    computed_at: ACTIONS_AT,
    checkup: {
      state: 'done',
      analyzed: 10,
      total: 10,
      due_by: null,
      finished_at: at(9, 51),
      report_id: 31,
    },
    actions: open,
    set_aside: cards.filter((card) => SET_ASIDE.includes(card.state)),
    recently_done: cards.filter((card) => DONE.includes(card.state)),
    card_count: open.length,
    change_count: open.length,
    open_count: open.length,
    snoozed: cards.filter((card) => card.state === 'snoozed').length,
    dismissed: cards.filter((card) => card.state === 'dismissed').length,
    coverage: {
      indexes: {
        ...checked(
          `Weighed ${ROWS.length} queries against the indexes they use`
        ),
        state: open.length ? 'found' : 'checked',
      },
      settings: checked('Compared the server settings with what RDST measured'),
      maintenance: checked(
        'Checked dead rows, stale statistics and transaction age'
      ),
      rewrites: checked('Tested rewrites for the busiest queries'),
    },
    sections: open.length
      ? {
          indexes: {
            headline: `Add ${open.length} ${open.length === 1 ? 'index' : 'indexes'}`,
            lead: [
              `Add ${open.length} ${open.length === 1 ? 'index' : 'indexes'} on ${tables.join(', ')} to speed up ${queries} queries that take ${Math.round(share * 100)}% of database time.`,
            ],
            details: [
              'Reads took 94% of database time and writes 6%, so the new indexes cost little to keep up.',
              `${INDEX_FIXES.length - PLAN_ADDS} smaller wins wait until these are in.`,
            ],
            facts: [
              { label: 'Queries sped up', value: String(queries) },
              { label: 'Database time', value: `${Math.round(share * 100)}%` },
              { label: 'Writes', value: '6% of time' },
              { label: 'Indexes kept', value: String(KEEPS.length) },
            ],
          },
        }
      : {},
    window: ACTIONS_WINDOW,
  }
}

function indexPlan(kind: CaActions, states: CaActionStates): IndexPlan | null {
  if (kind !== 'plan') return null
  const cards = planCards(states)
  const fixes = INDEX_FIXES.slice(0, PLAN_ADDS)
  const stateOf = (fix: CaIndexFix) =>
    cards.find((card) => card.id === fix.id)?.state ?? 'open'
  const add = fixes
    .filter((fix) => stateOf(fix) === 'open')
    .map((fix) => fix.add)
  return {
    engine: 'postgresql',
    computed_at: ACTIONS_AT,
    observed_s: ACTIONS_WINDOW.observed_s,
    counter_age_s: ACTIONS_WINDOW.counter_age_s,
    add,
    drop: [],
    keep: KEEPS,
    covered: [],
    skipped: [],
    later: INDEX_FIXES.slice(PLAN_ADDS).map((fix) => fix.add),
    defeated: [],
    watch: [],
    set_aside: fixes
      .filter((fix) => SET_ASIDE.includes(stateOf(fix)))
      .map((fix) => fix.add.id),
    explanation: null,
    sql: add.map((change) => change.sql).join('\n'),
    rollback_sql: add.map((change) => change.undo_sql).join('\n'),
  }
}

export interface CaFixtureSet {
  now: number
  home: () => HomeModel
  health: (target: string, range: MonitorRange) => MonitorHealth
  pulses: (target: string, limit: number) => MonitorPulsePage
  queries: (
    target: string,
    range: ApiRange,
    limit: number,
    offset: number
  ) => MonitorQueryPage
  query: (target: string, key: string) => MonitorQuery | null
  queryAnalysis: (target: string, key: string) => DeepAnalysisDigest | null
  load: (
    target: string,
    range: ApiRange,
    top: number,
    window?: TimeWindow
  ) => LoadBreakdown
  inbox: () => InboxItem[]
  report: (target: string, id: number | 'latest') => DatabaseReport | null
  reportHistory: (target: string, limit: number) => ReportHistory
  settings: (target: string) => MonitorSettings
  limit: (target?: string) => MonitoringLimit
  benchmarkFixes: (target: string, keys: string[]) => BenchmarkFixesResult
  actions: (target: string, states?: CaActionStates) => ActionsPage
  indexPlan: (target: string, states?: CaActionStates) => IndexPlan | null
}

function page(
  rows: MonitorQueryPage,
  limit: number,
  offset: number
): MonitorQueryPage {
  return { ...rows, queries: rows.queries.slice(offset, offset + limit) }
}

const UNMONITORED_ROWS: CaRow[] = [
  {
    key: 'ca00000000c1',
    name: 'SELECT on report_rows by run_id',
    sql: 'select metric, value from report_rows where run_id = $1',
    tables: ['report_rows'],
    qps: round(48210 / DAY, 4),
    mean_ms: 41.8,
    share: 0.62,
    plan_score: 44,
    status: 'fix_suggested',
    fix: {
      label: 'Add index report_rows (run_id)',
      sql: 'CREATE INDEX CONCURRENTLY idx_report_rows_run_id ON public.report_rows (run_id);',
      group: null,
    },
    delta_24h: null,
  },
  {
    key: 'ca00000000c2',
    name: 'SELECT on report_runs by created_at',
    sql: 'select id, status from report_runs where created_at > $1 order by created_at desc',
    tables: ['report_runs'],
    qps: round(3904 / DAY, 4),
    mean_ms: 8.2,
    share: 0.21,
    plan_score: 81,
    status: 'not_analyzed',
    fix: null,
    delta_24h: null,
  },
  {
    key: 'ca00000000c3',
    name: 'INSERT into report_rows',
    sql: 'insert into report_rows (run_id, metric, value) values ($1, $2, $3)',
    tables: ['report_rows'],
    qps: round(96330 / DAY, 4),
    mean_ms: 0.31,
    share: 0.17,
    plan_score: null,
    status: 'not_analyzed',
    fix: null,
    delta_24h: null,
  },
]

/** The first two minutes on demo: three queries seen, each still analyzing. */
const FIRST_RUN_ROWS: CaRow[] = [
  {
    ...ROWS[0],
    qps: 0.66,
    mean_ms: 68.9,
    share: 0.31,
    plan_score: null,
    fix: null,
    delta_24h: null,
  },
  {
    ...ROWS[1],
    qps: 1.32,
    mean_ms: 30.4,
    share: 0.28,
    plan_score: null,
    fix: null,
    delta_24h: null,
  },
  {
    ...ROWS[2],
    qps: 0.6,
    mean_ms: 64.8,
    share: 0.27,
    plan_score: null,
    fix: null,
    delta_24h: null,
  },
]

function buildSet(scenario: CaScenario): CaFixtureSet {
  const signedIn = scenario !== 'ca-signed-out'
  const firstRun = scenario === 'ca-first-run'
  const unreachable = scenario === 'ca-unreachable'
  const unmonitored = scenario === 'ca-unmonitored'
  const empty = scenario === 'ca-home-empty'
  const facts: HealthFacts = unreachable
    ? {
        ...DEMO_HEALTH,
        target: 'database',
        last_reading_at: at(9, 40, 58),
        outage_from: at(9, 41),
        checks_ran_at: at(9, 40),
      }
    : firstRun
      ? {
          ...DEMO_HEALTH,
          qps: 11.4,
          qps_peak: 11.4,
          cache_hit: null,
          rollbacks: null,
          first_ts: at(9, 45),
          checks_ran_at: null,
        }
      : unmonitored
        ? {
            ...DEMO_HEALTH,
            target: 'reports',
            qps: 0,
            qps_peak: 0,
            cache_hit: null,
            rollbacks: null,
            first_ts: CA_NOW,
            last_reading_at: at(0, 0, 0, -2),
            checks_ran_at: null,
          }
        : DEMO_HEALTH
  const items: CaItemSpec[] =
    empty || firstRun
      ? []
      : scenario === 'ca-home-one'
        ? [CHANGES_ITEM, ...QUIET_CLOSED]
        : scenario === 'ca-inbox-report'
          ? [STUCK_ITEM, CHANGES_ITEM, REPORT_ITEM, ...CLOSED_ITEMS]
          : unmonitored
            ? []
            : [
                STUCK_ITEM,
                CHANGES_ITEM,
                ...CLOSED_ITEMS,
                ...(unreachable ? [UNREACHABLE_ITEM] : []),
              ]
  const rows = unmonitored ? UNMONITORED_ROWS : firstRun ? FIRST_RUN_ROWS : ROWS
  const engineVersion = unreachable
    ? null
    : unmonitored || scenario === 'ca-home-one'
      ? '16'
      : '17'
  const target = unreachable ? 'database' : unmonitored ? 'reports' : 'demo'
  const actions: CaActions =
    scenario === 'ca-database' ||
    scenario === 'ca-report' ||
    scenario === 'ca-home'
      ? 'plan'
      : firstRun
        ? 'first_pass'
        : 'idle'
  return {
    now: CA_NOW,
    home: () => homeModel(scenario),
    health: (requested, range) =>
      health(
        { ...facts, target: requested || target },
        range,
        unmonitored ? '16' : engineVersion
      ),
    pulses: (_target, count) => pulses(facts, count),
    queries: (requested, range, count, offset) => {
      void range
      const list = queryPage(requested || target, rows, {
        analyzing: firstRun,
        total: firstRun ? 3 : rows.length,
        seenTotal: firstRun ? ROWS.length : rows.length,
        firstTs: firstRun
          ? at(9, 45)
          : unmonitored
            ? at(0, 0, 0, -2)
            : at(0, 0, 0, -7),
      })
      return page(list, count, offset)
    },
    query: (requested, key) => {
      const row =
        rows.find((candidate) => candidate.key === key) ?? rowByKey.get(key)
      return row
        ? monitorQuery(row, requested || target, { analyzing: firstRun })
        : null
    },
    queryAnalysis: (_target, key) => {
      if (firstRun) return null
      const row =
        rows.find((candidate) => candidate.key === key) ?? rowByKey.get(key)
      return row && row.fix !== null
        ? queryAnalysis(row)
        : row && row.status === 'healthy'
          ? queryAnalysis(row)
          : null
    },
    load: (requested, range, top, window) =>
      load(
        requested || target,
        range,
        top,
        window,
        firstRun ? at(9, 45) : unreachable ? at(9, 45, 0, -7) : at(9, 45)
      ),
    inbox: () => items.map(inboxItem),
    report: (requested, id) => {
      if (firstRun || unmonitored || empty) return null
      if (id === 32)
        return report(requested || target, REPORT_DAYS[0], {
          signedOut: !signedIn,
          onRequest: true,
        })
      const entry =
        id === 'latest'
          ? REPORT_DAYS[0]
          : REPORT_DAYS.find((candidate) => candidate.id === id)
      return entry
        ? report(requested || target, entry, { signedOut: !signedIn })
        : null
    },
    reportHistory: (requested, count) =>
      firstRun || unmonitored || empty
        ? { reports: [] }
        : reportHistory(requested, count),
    settings: () =>
      settings(!unmonitored && !empty, firstRun ? at(9, 45) : at(9, 45, 0, -7)),
    limit: (requested) => limit(signedIn, requested),
    benchmarkFixes: (requested, keys) =>
      benchmarkFixesResult(requested || target, keys),
    actions: (requested, states = new Map()) =>
      actionsPage(requested || target, actions, states),
    indexPlan: (_target, states = new Map()) => indexPlan(actions, states),
  }
}

const SETS = new Map<CaScenario, CaFixtureSet>()

/** The fixture set for a redesign scenario, built once per name. */
export function caFixtures(scenario: CaScenario): CaFixtureSet {
  let set = SETS.get(scenario)
  if (!set) {
    set = buildSet(scenario)
    SETS.set(scenario, set)
  }
  return set
}

export const CA_FIX_COUNT = FIX_COUNT
export const CA_ROW_COUNT = ROWS.length
