import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  createMuteRule,
  deleteMuteRule,
  getHealth,
  getInbox,
  getMuteRules,
  getQueries,
  getReport,
  getReportHistory,
  inboxAction,
  resetStubState,
  setFixtureTiming,
  startReport,
  subscribeReportEvents,
} from './api'
import { CONTRACT_STATUS, setFixtureOverride } from './contract'
import {
  FIXTURE_KEYS,
  FIXTURE_NOW,
  healthFixture,
  inboxItems,
  queriesFixture,
  reportEventsFixture,
  reportEventsRunningFixture,
  seriesFixture,
} from './fixtures'
import type { ReportEvent } from './types'

beforeEach(() => {
  setFixtureTiming(0)
  setFixtureOverride('localdev')
  resetStubState()
})
afterEach(() => {
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
})

it('builds the same fixtures for the same clock', () => {
  expect(queriesFixture('localdev', '24h', FIXTURE_NOW)).toEqual(
    queriesFixture('localdev', '24h', FIXTURE_NOW)
  )
  expect(healthFixture('localdev', '7d', FIXTURE_NOW)).toEqual(
    healthFixture('localdev', '7d', FIXTURE_NOW)
  )
})

it('covers the list states the brief asks for', () => {
  const page = queriesFixture('localdev', '24h', FIXTURE_NOW)
  expect(page.total).toBeGreaterThanOrEqual(30)
  expect(
    page.queries.some((q) => q.text_status === 'unavailable' && q.text_reason)
  ).toBe(true)
  // No index attribution anywhere: no verdicts and no fix states.
  expect(
    page.queries.every(
      (q) =>
        q.fix === null &&
        !q.state.includes('fixed') &&
        !q.state.includes('fix_no_effect')
    )
  ).toBe(true)
  expect(page.queries.some((q) => q.state.includes('stuck'))).toBe(true)
  expect(page.queries.every((q) => q.display_name.length > 0)).toBe(true)
  expect(page.composition.at(-1)?.key).toBe('other')
  expect(page.history.span_seconds).toBeGreaterThan(2 * 86400)
  const firstHour = queriesFixture('first_hour', '1h', FIXTURE_NOW)
  expect(firstHour.queries.every((q) => q.change.latency === null)).toBe(true)
})

it('keeps health honest about history and gaps', () => {
  const health = healthFixture('localdev', '24h', FIXTURE_NOW)
  expect(health.res).toBe(60)
  expect(health.prior).not.toBeNull()
  expect(health.series.some((p) => p.qps === null)).toBe(true)
  expect(healthFixture('localdev', '7d', FIXTURE_NOW).prior).toBeNull()
  expect(healthFixture('empty', '24h', FIXTURE_NOW).history.first_ts).toBeNull()
  const series = seriesFixture(
    'localdev',
    FIXTURE_KEYS.fixNoEffect,
    '24h',
    FIXTURE_NOW
  )
  // Index changes are neutral timeline facts.
  expect(series.fix).toBeNull()
  const index = series.markers.find((m) => m.kind === 'index_added')
  expect(index?.label).toBe('idx_customers_email added')
  expect(index?.matches_recommendation).toBe(false)
  expect(series.markers.some((m) => m.kind.startsWith('fix_'))).toBe(false)
})

it('files 47 inbox items across four colour-coded bands', () => {
  const items = inboxItems(FIXTURE_NOW)
  expect(items).toHaveLength(47)
  const open = items.filter(
    (i) =>
      i.muted_by === null && i.state !== 'resolved' && i.state !== 'dismissed'
  )
  expect(new Set(open.map((i) => i.severity_band))).toEqual(
    new Set(['act_now', 'watch', 'fyi'])
  )
  expect(items.some((i) => i.severity_band === 'recovered')).toBe(true)
  // Every item opens somewhere specific; no index attribution types remain.
  expect(items.every((i) => i.link?.label)).toBe(true)
  expect(items.some((i) => i.type.startsWith('fix_'))).toBe(false)
  const stuck = items.find((i) => i.type === 'stuck_query' && i.state === 'new')
  expect(stuck?.advice?.steps[1]).toMatch(/pg_cancel_backend/)
  expect(stuck?.link).toMatchObject({ kind: 'query', tab: 'performance' })
  // Episode chips carry friendly names for every member query.
  const episode = items.find((i) => i.type === 'many_queries_changed')
  expect(Object.keys(episode?.query_labels ?? {})).toHaveLength(17)
})

it('reads every endpoint live from the backend', async () => {
  expect(
    Object.values(CONTRACT_STATUS).every((status) => status === 'live')
  ).toBe(true)
  setFixtureOverride(null)
  const fetchMock = vi.fn(async () =>
    Response.json(healthFixture('localdev', '24h', FIXTURE_NOW))
  )
  vi.stubGlobal('fetch', fetchMock)
  expect((await getHealth('localdev', '24h')).range).toBe('24h')
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/monitor/targets/localdev/health?range=24h',
    undefined
  )
})

it('serves the fixture override without the network', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  expect((await getHealth('localdev', '24h')).range).toBe('24h')
  expect((await getQueries('localdev', '7d')).queries.length).toBeGreaterThan(0)
  expect(
    (await getReport('localdev', 'latest')).report_json?.fix_first.length
  ).toBeGreaterThan(0)
  await expect(getReport('localdev', 9999)).rejects.toMatchObject({
    status: 404,
  })
  expect(fetchMock).not.toHaveBeenCalled()
})

it('round-trips stub writes', async () => {
  const rule = await createMuteRule({
    target: 'localdev',
    query_key: FIXTURE_KEYS.regressed,
    type: null,
    until: null,
    note: null,
  })
  expect((await getMuteRules('localdev')).rules.map((r) => r.id)).toContain(
    rule.id
  )
  await deleteMuteRule(rule.id)
  expect((await getMuteRules('localdev')).rules.map((r) => r.id)).not.toContain(
    rule.id
  )

  const [first] = (await getInbox({ state: 'open' })).items
  await inboxAction(first.id, 'resolve')
  expect((await getInbox({ state: 'done' })).items.map((i) => i.id)).toContain(
    first.id
  )
})

it('replays a report run from queued to done', async () => {
  const events: ReportEvent[] = []
  await new Promise<void>((resolve) => {
    subscribeReportEvents('localdev', 61, (event) => {
      events.push(event)
      if (event.type === 'run' && event.state !== 'running') resolve()
    })
  })
  expect(events).toEqual(
    reportEventsFixture(FIXTURE_NOW).map((e) =>
      expect.objectContaining({ type: e.type })
    )
  )
  const states = new Set(
    reportEventsRunningFixture(FIXTURE_NOW).flatMap((e) =>
      e.type === 'analysis' ? [e.state] : []
    )
  )
  expect([...states].sort()).toEqual([
    'done',
    'failed',
    'queued',
    'running',
    'skipped',
  ])
  expect(
    events.some((e) => e.type === 'analysis' && e.state === 'provisional')
  ).toBe(true)
})

it('serves a finished stub run as the latest done report', async () => {
  const run = await startReport('localdev')
  await new Promise<void>((resolve) => {
    subscribeReportEvents('localdev', run.id, (event) => {
      if (event.type === 'run' && event.state !== 'running') resolve()
    })
  })
  const latest = await getReport('localdev', 'latest')
  expect([latest.id, latest.state]).toEqual([run.id, 'done'])
  expect((await getReportHistory('localdev')).reports[0].id).toBe(run.id)
})

it('moves open items a new stub rule covers to Muted', async () => {
  const [first] = (await getInbox({ state: 'open' })).items
  const rule = await createMuteRule({
    target: first.target,
    query_key: null,
    type: first.type,
    until: null,
    note: null,
  })
  expect(rule.matched_count).toBeGreaterThan(0)
  const muted = (await getInbox({ state: 'muted' })).items
  expect(muted.some((item) => item.id === first.id)).toBe(true)
  setFixtureOverride('empty')
  expect((await getInbox({ state: 'open' })).items).toEqual([])
})

it('sends paging, search and sort to the list API for large libraries', async () => {
  setFixtureOverride(null)
  const fetchMock = vi.fn(
    async (_url: RequestInfo | URL) =>
      new Response(JSON.stringify({ queries: [], total: 20_000 }), {
        headers: { 'Content-Type': 'application/json' },
      })
  )
  vi.stubGlobal('fetch', fetchMock)
  await getQueries('localdev', '24h', 10, undefined, {
    offset: 12_000,
    keys: ['000000000599', '000000000600'],
    q: 'orders',
    sort: 'mean_ms',
    filter: 'all',
  })
  const requested = new URL(
    String(fetchMock.mock.calls[0][0]),
    'http://localhost'
  )
  expect(requested.searchParams.get('limit')).toBe('10')
  expect(requested.searchParams.get('offset')).toBe('12000')
  expect(requested.searchParams.get('keys')).toBe('000000000599,000000000600')
  expect(requested.searchParams.get('q')).toBe('orders')
  expect(requested.searchParams.get('sort')).toBe('mean_ms')
})
