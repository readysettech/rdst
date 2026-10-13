import { afterEach, beforeEach, expect, it } from 'vitest'
import {
  getHealth,
  getHome,
  getInbox,
  getInboxItem,
  getMonitoringLimit,
  getQueries,
  getQuery,
  getQueryAnalysis,
  getReport,
  getReportHistory,
  getSettings,
  resetStubState,
  setFixtureTiming,
} from './api'
import { setFixtureOverride } from './contract'
import {
  at,
  CA_FIX_COUNT,
  CA_KEYS,
  CA_NOW,
  CA_ROW_COUNT,
  CA_SCENARIOS,
  caBase,
  caFixtures,
} from './fixturesCa'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
})

afterEach(() => setFixtureOverride(undefined))

const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })

it('serves every scenario from a fixed clock on the canonical timeline', () => {
  expect(clock(CA_NOW)).toBe('9:56 AM')
  expect(new Date(CA_NOW * 1000).getDay()).toBe(4)
  for (const scenario of CA_SCENARIOS) {
    const set = caFixtures(scenario)
    expect(set.now).toBe(CA_NOW)
    expect(set.home().generated_at).toBe(CA_NOW)
  }
  expect(caBase('ca-home-empty')).toBe('empty')
  expect(caBase('ca-first-run')).toBe('first_hour')
  expect(caBase('ca-report')).toBe('localdev')
  expect(caBase('localdev')).toBe('localdev')
})

it('ca-home: one item needs you on demo, two of three watched, seven log rows', async () => {
  setFixtureOverride('ca-home')
  const home = await getHome()
  expect(home.status).toMatchObject({
    state: 'needs_you',
    needs_you: 1,
    to_watch: 1,
    target: 'demo',
    headline: 'a stuck query since 9:55 AM',
    watching: 2,
    act_now: 1,
    do_first: 3,
  })
  expect(clock(home.status.last_reading_at as number)).toBe('9:56 AM')
  expect(home.status.link).toMatchObject({ kind: 'inbox', item_id: 1 })
  expect(home.day).toMatchObject({
    qps: 11.7,
    qps_peak: 15.9,
    mean_ms: 15.9,
    fixes_ready: 9,
    fixes_share: 0.54,
  })
  expect(home.day?.cache_hit_reason).toBe('shared_buffers is the default')
  expect(home.databases.map((row) => row.target)).toEqual([
    'demo',
    'analytics',
    'staging',
  ])
  expect(
    home.databases_strip?.map((row) => [
      row.target,
      row.state,
      row.act_now,
      row.to_do,
      row.do_first,
      row.fixes_ready,
    ])
  ).toEqual([
    ['demo', 'watching', 1, 5, 2, 9],
    ['analytics', 'watching', 0, 2, 1, 3],
    ['staging', 'not_monitored', 0, 0, 0, 0],
  ])
  expect(home.not_monitored).toBe(1)
  expect(home.today).toHaveLength(7)
  expect(home.today[0]).toMatchObject({
    text: 'Stuck query filed',
    band: 'act_now',
  })
  expect(home.today.map((row) => row.link.label)).toEqual([
    'Inbox',
    'Inbox',
    'Chart',
    'Actions',
    'Queries',
    'Query',
    'Report',
  ])
  expect(home.since_yesterday).toEqual({ opened_closed: 4, fixes_confirmed: 1 })
  expect(home.recently_analyzed.map((item) => item.target)).toEqual([
    'demo',
    'analytics',
    'demo',
  ])
})

it('ca-home-one and ca-home-empty: the quiet sentence and the empty state', async () => {
  setFixtureOverride('ca-home-one')
  const one = await getHome()
  expect(one.status.state).toBe('quiet')
  expect(clock(one.status.since as number)).toBe('9:12 AM')
  expect(one.databases).toHaveLength(1)
  expect(one.status).toMatchObject({ act_now: 0, do_first: 2 })
  expect(one.databases_strip).toHaveLength(1)
  expect(one.day?.cache_hit_ratio).toBe(0.992)
  expect(one.day?.cache_hit_reason).toBeNull()
  expect(one.today[2].text).toBe('Stuck query ended, item closed')
  setFixtureOverride('ca-home-empty')
  const empty = await getHome()
  expect(empty.status.state).toBe('off')
  expect(empty.databases).toEqual([])
  expect(empty.databases_strip).toEqual([])
  expect(empty.day).toBeNull()
})

it('ca-inbox: two open items in two bands, eight closed within 7 days, pid in every step', async () => {
  setFixtureOverride('ca-inbox')
  const open = (await getInbox({ state: 'open' })).items
  expect(open.map((item) => [item.kind, item.band])).toEqual([
    ['stuck_query', 'act_now'],
    ['changes_ready', 'watch'],
  ])
  const stuck = await getInboxItem(1)
  expect(stuck.pid).toBe(48213)
  expect(stuck.steps).toHaveLength(3)
  expect(stuck.steps?.every((step) => step.sql.includes('48213'))).toBe(true)
  expect(stuck.history?.[1].text).toBe('Filed and pushed to this computer')
  expect(stuck.related?.map((row) => row.link.label)).toEqual([
    'Queries',
    'Live',
  ])
  const changes = await getInboxItem(2)
  expect(changes.title).toBe('5 changes ready on demo')
  expect(changes.link).toEqual({
    kind: 'actions',
    target: 'demo',
    label: 'Open Actions',
  })
  expect(changes.changes).toMatchObject({ count: 5, do_first: 2 })
  expect(changes.changes?.titles).toHaveLength(3)
  const closed = (await getInbox({ state: 'done' })).items
  expect(closed).toHaveLength(8)
  const none = closed.find((item) => item.kind === 'no_change_yet')
  expect(none).toMatchObject({
    band: 'closed',
    tone: 'neutral',
    resolved_by: 'code',
    link: { kind: 'actions', label: 'Open Applied' },
  })
  expect(
    closed.every((item) => (item.resolved_at ?? 0) >= CA_NOW - 7 * 86400)
  ).toBe(true)
  const confirmed = closed.find(
    (item) => item.kind === 'fix_confirmed' && item.target === 'demo'
  )
  expect(confirmed).toMatchObject({ before_ms: 27.6, after_ms: 1.9, calls: 40 })
  expect(clock(confirmed?.resolved_at as number)).toBe('8:12 AM')
  setFixtureOverride('ca-inbox-report')
  expect(
    (await getInbox({ state: 'open' })).items.map((item) => item.kind)
  ).toEqual(['stuck_query', 'changes_ready', 'report_ready'])
})

it('ca-database: 16 queries, 9 with a fix, the open row and its full analysis', async () => {
  setFixtureOverride('ca-database')
  const page = await getQueries('demo', '1h', 10)
  expect(page.total).toBe(16)
  expect(CA_ROW_COUNT).toBe(16)
  expect(CA_FIX_COUNT).toBe(9)
  expect(page.queries).toHaveLength(10)
  expect(page.queries[0]).toMatchObject({
    display_name: 'SELECT on posts and users by creationdate',
    qps: 0.64,
    mean_ms: 69.7,
    db_time_share: 0.19,
    plan_score: 42,
    status: 'fix_suggested',
    latency_delta_24h: 0.12,
  })
  expect(page.queries[0].suggested_fix?.label).toBe(
    'Add index posts (creationdate, owneruserid, score)'
  )
  expect(page.queries[0].trend_24h).toHaveLength(24)
  expect(
    page.queries.filter((row) => row.status === 'fix_suggested')
  ).toHaveLength(7)
  const healthy = page.queries.find(
    (row) => row.display_name === 'SELECT on users by id'
  )
  expect(healthy).toMatchObject({
    status: 'healthy',
    plan_score: 99,
    suggested_fix: null,
  })
  const row = await getQuery('demo', CA_KEYS.postsByOwneruserid, '1h')
  expect(row).toMatchObject({
    qps: 0.62,
    mean_ms: 65.4,
    db_time_share: 0.17,
    plan_score: 72,
  })
  expect(row.suggested_fix?.sql).toContain('idx_posts_owneruserid_creationdate')
  const analysis = await getQueryAnalysis('demo', CA_KEYS.postsByOwneruserid)
  expect(analysis).toMatchObject({
    score: 72,
    rows_read: 245290,
    rows_returned: 583,
    planner_cost: 107916,
    calls_today: 174,
  })
  expect(analysis.verdict).toEqual({
    lead: 'One index removes the scan and the sort.',
    rest: 'The SQL stays as it is.',
  })
  expect(analysis.fix_detail).toMatchObject({
    cost_before: 107916,
    cost_after: 41,
    size: 'About 9 MB',
  })
  expect(analysis.plan_findings).toHaveLength(3)
  expect(analysis.rewrites?.map((rewrite) => rewrite.outcome)).toEqual([
    'no_change',
    'no_change',
  ])
  expect(analysis.also).toHaveLength(1)
  expect(analysis.readyset).toEqual({ blockers: [], verified: false })
  expect(analysis.run).toEqual({
    model: 'Claude Sonnet 4.6',
    tokens: 8028,
    seconds: 18,
    id: '412',
  })
  const health = await getHealth('demo', '1h')
  expect(health.now).toMatchObject({
    qps: 14.2,
    active_conns: 8,
    max_conns: 100,
    cache_hit_ratio: 0.125,
    rollbacks_per_min: 14,
  })
  expect(health.checks_ran_at).toBe(at(9, 55))
})

it('ca-report: Report #31 for October 7 with the day, Fix first and Health', async () => {
  setFixtureOverride('ca-report')
  const report = await getReport('demo', 'latest')
  expect(report.id).toBe(31)
  expect(report.run).toMatchObject({
    number: 31,
    trigger: 'nightly',
    analyses: 12,
    rewrites: 2,
  })
  expect(clock(report.run?.ran_at as number)).toBe('12:00 AM')
  expect(report.verdict?.lead).toBe('demo needs two things today:')
  expect(report.day?.label).toBe('October 7, vs the day before')
  expect(report.day?.tiles.map((tile) => tile.value)).toEqual([
    11.7, 15.9, 8, 0.115,
  ])
  expect(report.day?.tiles[3]).toMatchObject({
    state: 'warn',
    reason: 'shared_buffers is the default',
  })
  expect(report.day?.chart?.xs).toHaveLength(24)
  expect(report.day?.query_count).toBe(12)
  const ranked = report.fix_first?.filter((item) => item.rank !== null) ?? []
  expect(ranked.map((item) => item.rank)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
  expect(report.fix_first?.map((item) => item.state)).toEqual([
    'needs_attention',
    'ready',
    'ready',
    'ready',
    'worth_doing',
    'fixed',
    'smaller',
    'smaller',
    'smaller',
    'smaller',
  ])
  expect(report.fix_first?.[1]).toMatchObject({
    detail: '3 queries, 37% of time',
    group: 'posts (creationdate, owneruserid, score)',
  })
  expect(report.fix_first?.[1].query_keys).toHaveLength(3)
  expect(report.health?.map((row) => row.fix_rank)).toEqual([1, 1, 5])
  expect(report.health_fine).toHaveLength(5)
  expect(report.narrative_missing).toBe(false)
  const history = await getReportHistory('demo')
  expect(history.reports.map((row) => row.verdict)).toEqual([
    '9 fixes',
    '7 fixes',
    '8 fixes',
    '3 fixes',
    'quiet',
    '1 fix',
    'first report',
  ])
  expect(history.reports[0].day).toBe('2026-10-07')
  await expect(getReport('demo', 9999)).rejects.toMatchObject({ status: 404 })
})

it('ca-report: Detailed adds the mockup sections only when asked for', async () => {
  setFixtureOverride('ca-report')
  const quick = await getReport('demo', 'latest')
  expect(quick.copy_count).toBe(8)
  expect(quick.detailed).toBeNull()
  const report = await getReport('demo', 'latest', 'detailed')
  expect(report.copy_count).toBe(8)
  const detailed = report.detailed
  expect(detailed?.tiles.map((tile) => [tile.value, tile.prior])).toEqual([
    [11.7, 9.8],
    [15.9, 12.4],
    [69.7, 51.2],
    [8, 6],
    [0.115, 0.938],
    [143, 21],
  ])
  expect(detailed?.tiles[5]).toMatchObject({ second: 0, second_prior: 0 })
  expect(detailed?.spikes.map((stretch) => stretch.kind)).toEqual([
    'busiest',
    'busiest',
  ])
  expect(detailed?.what_changed.map((row) => row.kind)).toEqual([
    'slower',
    'slower',
    'faster',
    'new_heavy',
    'plan_changed',
  ])
  expect(detailed?.fixes_confirmed).toHaveLength(2)
  expect(detailed?.health_checks).toHaveLength(8)
  expect(detailed?.settings_audit).toHaveLength(4)
  expect(detailed?.index_changes_seen).toHaveLength(2)
  expect(detailed?.what_rdst_did).toHaveLength(7)
  expect(detailed?.totals).toMatchObject({ queries_seen: 12, readings: 1440 })
  const older = await getReport('demo', 30, 'detailed')
  expect(older.detailed).toBeNull()
})

it('ca-signed-out: numbers only, no SQL, the account signed out', async () => {
  setFixtureOverride('ca-signed-out')
  const report = await getReport('demo', 'latest')
  expect(report.narrative_missing).toBe(true)
  expect(report.verdict).toBeNull()
  expect(report.fix_first?.every((item) => item.sql === null)).toBe(true)
  expect(report.fix_first?.map((item) => item.title).slice(0, 2)).toEqual([
    'Raise shared_buffers to 2 GB',
    'Index posts (creationdate, owneruserid, score)',
  ])
  expect((await getMonitoringLimit()).signed_in).toBe(false)
})

it('ca-first-run, ca-unreachable, ca-unmonitored: the three database states', async () => {
  setFixtureOverride('ca-first-run')
  const first = await getQueries('demo', '1h')
  expect(first.total).toBe(3)
  expect(
    first.queries.every(
      (row) => row.status === 'analyzing' && row.plan_score === null
    )
  ).toBe(true)
  expect(first.queries[0]).toMatchObject({
    qps: 0.66,
    mean_ms: 68.9,
    db_time_share: 0.31,
  })
  expect(
    clock((await getHealth('demo', '1h')).history.first_ts as number)
  ).toBe('9:45 AM')
  await expect(getReport('demo', 'latest')).rejects.toMatchObject({
    status: 404,
  })
  expect((await getReportHistory('demo')).reports).toEqual([])
  expect((await getInbox({ state: 'open' })).items).toEqual([])

  setFixtureOverride('ca-unreachable')
  const health = await getHealth('database', '1h')
  expect(clock(health.last_reading_at as number)).toBe('9:40 AM')
  expect(health.outages?.[0]).toMatchObject({ cause: 'unreachable', end: null })
  expect(clock(health.outages?.[0].start as number)).toBe('9:41 AM')
  expect(health.series.at(-1)?.qps).toBeNull()
  expect(health.now.qps).toBe(14.2)
  const items = (await getInbox({ state: 'open' })).items
  expect(items.find((item) => item.target === 'database')).toMatchObject({
    kind: 'monitoring_issue',
    band: 'act_now',
  })
  expect((await getQueries('database', '1h')).total).toBe(16)

  setFixtureOverride('ca-unmonitored')
  expect((await getSettings('reports')).enabled).toBe(false)
  const scan = await getQueries('reports', '24h')
  expect(scan.total).toBe(3)
  expect(scan.queries.map((row) => row.mean_ms)).toEqual([41.8, 8.2, 0.31])
  expect(scan.queries.map((row) => row.db_time_share)).toEqual([
    0.62, 0.21, 0.17,
  ])
  await expect(getReport('reports', 'latest')).rejects.toMatchObject({
    status: 404,
  })
})

it('facts agree across screens: one query count, one fix count, one stuck query', async () => {
  setFixtureOverride('ca-home')
  const home = await getHome()
  const changes = await getInboxItem(2)
  const report = await getReport('demo', 'latest')
  const page = await getQueries('demo', '1h', 100)
  expect(home.day?.fixes_ready).toBe(9)
  expect(home.databases[0].fixes_ready).toBe(9)
  // The fixes themselves live on Actions; the Inbox only says changes are ready there.
  expect(changes.link).toMatchObject({ kind: 'actions', target: 'demo' })
  expect(
    page.queries.filter((row) => row.status === 'fix_suggested')
  ).toHaveLength(9)
  expect(report.run?.number).toBe(31)
  const stuck = await getInboxItem(1)
  expect(page.queries.find((row) => row.status === 'stuck')?.key).toBe(
    stuck.query_keys[0]
  )
  expect(home.status.link?.item_id).toBe(stuck.id)
})
