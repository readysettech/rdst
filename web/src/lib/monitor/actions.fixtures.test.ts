import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchActions,
  fetchApplied,
  fetchGlobalActions,
  fetchIndexPlan,
  postActionsOpened,
  postActionVerb,
  resetActionStubState,
} from './actions'
import { setFixtureOverride } from './contract'
import { CA_KEYS, CA_SCENARIOS, caFixtures } from './fixturesCa'

beforeEach(() => resetActionStubState())

afterEach(() => {
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
})

describe('actions under a ca-* fixture scenario', () => {
  it('serves the demo plan: five open index adds and two keeps', async () => {
    setFixtureOverride('ca-database')
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const page = await fetchActions('demo')
    const plan = await fetchIndexPlan('demo')
    expect(fetch).not.toHaveBeenCalled()
    expect(page.checkup.state).toBe('done')
    expect(page.open_count).toBe(5)
    expect(page.actions).toHaveLength(5)
    expect(
      page.actions.every(
        (card) =>
          card.category === 'indexes' &&
          card.type === 'add_index' &&
          card.state === 'open'
      )
    ).toBe(true)
    for (const kind of ['settings', 'maintenance', 'rewrites'])
      expect(page.actions.some((card) => card.category === kind)).toBe(false)
    expect(plan?.add).toHaveLength(5)
    expect(plan?.keep).toHaveLength(2)
    expect(plan?.drop).toEqual([])
    // Each change is one of the plan's adds, and its first query is the one it was found on.
    expect(page.actions.map((card) => card.key)).toEqual(
      plan?.add.map((add) => add.id)
    )
    expect(plan?.add[0].helps[0].key).toBe(CA_KEYS.postsUsersByCreationdate)
    expect(plan?.add.every((add) => add.sql.startsWith('CREATE INDEX'))).toBe(
      true
    )
  })

  it('serves the same plan on the report scenario', async () => {
    setFixtureOverride('ca-report')
    expect((await fetchActions('demo')).open_count).toBe(5)
  })

  it('reports the first pass with no changes yet on the first run', async () => {
    setFixtureOverride('ca-first-run')
    const page = await fetchActions('demo')
    expect(page.checkup).toMatchObject({
      state: 'first_pass',
      analyzed: 4,
      total: 10,
    })
    expect(page.open_count).toBe(0)
    expect(page.actions).toEqual([])
    expect(await fetchIndexPlan('demo')).toBeNull()
  })

  it('agrees with the global page on ca-home: demo has 5 to do, analytics 2', async () => {
    setFixtureOverride('ca-home')
    const global = await fetchGlobalActions(null)
    expect(global.counts.by_database).toMatchObject({ demo: 5, analytics: 2 })
    const demo = await fetchActions('demo')
    expect(demo.open_count).toBe(5)
    expect((await fetchIndexPlan('demo'))?.add).toHaveLength(5)
    const analytics = await fetchActions('analytics')
    expect(analytics.checkup.state).toBe('done')
    expect(analytics.open_count).toBe(2)
    expect(analytics.actions.map((card) => [card.id, card.category])).toEqual([
      [101, 'indexes'],
      [102, 'maintenance'],
    ])
    expect(await fetchIndexPlan('analytics')).toBeNull()
    expect((await fetchActions('staging')).checkup.state).toBe('none')

    const aside = await postActionVerb('analytics', 101, 'set_aside', {
      until: 1_800_000_000,
    })
    expect(aside.open_count).toBe(1)
    expect(aside.set_aside).toMatchObject([
      { id: 101, state: 'snoozed', snoozed_until: 1_800_000_000 },
    ])
    expect((await fetchGlobalActions('analytics')).counts.total).toBe(1)
  })

  it('serves an idle, empty page everywhere else', async () => {
    const elsewhere = CA_SCENARIOS.filter(
      (name) =>
        !['ca-database', 'ca-report', 'ca-first-run', 'ca-home'].includes(name)
    )
    for (const scenario of elsewhere) {
      setFixtureOverride(scenario)
      const page = await fetchActions('demo')
      expect(page.checkup.state, scenario).toBe('none')
      expect(page.open_count, scenario).toBe(0)
      expect(await fetchIndexPlan('demo'), scenario).toBeNull()
    }
  })

  it('moves a change aside and back with verbs, and the plan follows', async () => {
    setFixtureOverride('ca-database')
    const [first, second] = (await fetchActions('demo')).actions
    const snoozed = await postActionVerb('demo', first.id as number, 'snooze', {
      until: 123,
    })
    expect(snoozed.open_count).toBe(4)
    expect(snoozed.set_aside).toMatchObject([
      { id: first.id, state: 'snoozed', snoozed_until: 123 },
    ])
    expect((await fetchIndexPlan('demo'))?.add).toHaveLength(4)

    const done = await postActionVerb('demo', second.id as number, 'check')
    expect(done.open_count).toBe(3)
    expect(done.recently_done).toMatchObject([
      { id: second.id, state: 'applied' },
    ])

    const reopened = await postActionVerb('demo', first.id as number, 'reopen')
    expect(reopened.open_count).toBe(4)
    expect(reopened.set_aside).toEqual([])

    await expect(postActionVerb('demo', 999, 'dismiss')).rejects.toMatchObject({
      status: 404,
    })

    resetActionStubState()
    expect((await fetchActions('demo')).open_count).toBe(5)
  })
})

describe('the global page under a ca-* fixture scenario', () => {
  it('ranks every database in Do first, Then and Later, on the mockup numbers', async () => {
    setFixtureOverride('ca-database')
    const page = await fetchGlobalActions(null)
    const { first, later, total } = page.counts
    expect([first, page.counts.then, later, total]).toEqual([2, 3, 2, 7])
    expect(page.counts.by_database).toMatchObject({ demo: 5, analytics: 2 })
    expect(page.counts.by_kind).toEqual({
      index: 6,
      setting: 0,
      maintenance: 1,
      rewrite: 0,
    })
    expect(
      page.cards.map((card) => [
        card.rank,
        card.database,
        card.saves_ms_per_min,
      ])
    ).toEqual([
      [1, 'demo', 14_000],
      [2, 'analytics', 9_100],
      [3, 'demo', 5_200],
      [4, 'demo', 3_700],
      [5, 'analytics', 2_800],
      [6, 'demo', 2_400],
      [7, 'demo', 1_200],
    ])
    expect(page.cards[0]).toMatchObject({
      id: 1,
      table: 'posts',
      share: expect.closeTo(0.46, 5),
      rollback_sql:
        'DROP INDEX CONCURRENTLY public.idx_posts_creationdate_owneruserid_score;',
    })
    expect(page.cards[0].queries[0]).toMatchObject({
      key: CA_KEYS.postsUsersByCreationdate,
      share: 0.19,
    })
    expect(page.unmonitored).toEqual(
      caFixtures('ca-database')
        .home()
        .databases.filter((row) => row.state === 'not_monitored')
        .map((row) => row.target)
    )
    expect(page.dismissed.map((card) => card.reason)).toEqual([
      'The host caps memory at 16 GB.',
      'One nightly report reads it, not worth a cost on every write.',
    ])
    expect(page.keep).toEqual({ demo: 2 })

    const demo = await fetchGlobalActions('demo')
    expect(demo.cards.map((card) => card.rank)).toEqual([1, 2, 3, 4, 5])
    expect(demo.counts.total).toBe(5)
    expect(demo.counts.by_database).toMatchObject({ demo: 5, analytics: 2 })
  })

  it('has nothing to rank while the first plan is built', async () => {
    setFixtureOverride('ca-first-run')
    const page = await fetchGlobalActions(null)
    expect(page.cards).toEqual([])
    expect(page.updated_at).toBeNull()
  })

  it('serves Applied: two verified, one measuring, an undone one and two dismissed', async () => {
    setFixtureOverride('ca-database')
    const applied = await fetchApplied(null)
    expect(
      applied.days.map((day) => [day.day, day.rows.map((row) => row.status)])
    ).toEqual([
      ['2026-10-08', ['measuring', 'verified']],
      ['2026-10-06', ['verified']],
      ['2026-09-30', ['no_change']],
    ])
    expect(applied.totals).toMatchObject({
      changes_this_week: 3,
      verified: 2,
      measuring: 1,
    })
    expect(applied.totals.saved_share).toBeCloseTo(0.413, 2)
    expect(applied.undone.map((row) => row.reason)).toEqual([
      'It slowed the nightly import.',
    ])
    expect(applied.dismissed).toHaveLength(2)
  })

  it('moves changes with the plan verbs, reasons kept', async () => {
    setFixtureOverride('ca-home')
    await postActionVerb('analytics', 101, 'set_aside', {
      until: 1_800_000_000,
      reason: 'After the freeze',
    })
    await postActionVerb('demo', 2, 'dismiss', {
      reason: 'votes takes too many writes',
    })
    await postActionVerb('demo', 1, 'did_it')
    let page = await fetchGlobalActions(null)
    expect(page.cards.map((card) => card.id)).toEqual([3, 102, 4, 5])
    expect(page.set_aside).toMatchObject([
      { id: 101, set_aside_until: 1_800_000_000, reason: 'After the freeze' },
    ])
    expect(page.dismissed.map((card) => card.id)).toContain(2)
    let applied = await fetchApplied('demo')
    expect(applied.days[0].rows[0]).toMatchObject({
      id: 1,
      by: 'user',
      status: 'measuring',
    })

    await postActionVerb('demo', 1, 'undid_it', {
      reason: 'Locks during the build',
    })
    applied = await fetchApplied('demo')
    expect(applied.undone.map((row) => [row.id, row.reason])).toContainEqual([
      1,
      'Locks during the build',
    ])

    await postActionVerb('demo', 201, 'bring_back')
    await postActionVerb('analytics', 101, 'bring_back')
    page = await fetchGlobalActions(null)
    expect(page.cards.map((card) => card.id)).toEqual(
      expect.arrayContaining([101, 201])
    )
    expect(page.set_aside).toEqual([])
    expect((await postActionsOpened('demo')).closed).toBe(false)
  })
})

describe('actions against the real backend', () => {
  it('calls the monitor API when no ca-* scenario is active', async () => {
    setFixtureOverride(null)
    const body = { open_count: 7 }
    const fetch = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify(body))
    )
    vi.stubGlobal('fetch', fetch)
    expect(await fetchActions('demo')).toEqual(body)
    expect(await fetchIndexPlan('demo')).toEqual(body)
    await postActionVerb('demo', 3, 'dismiss')
    await fetchGlobalActions(null)
    await fetchGlobalActions('my db')
    await fetchApplied('demo')
    await postActionVerb('demo', 3, 'set_aside', { until: 99, reason: 'Later' })
    await postActionsOpened('demo')
    expect(fetch.mock.calls.map((call) => String(call[0]))).toEqual([
      '/api/monitor/targets/demo/actions',
      '/api/monitor/targets/demo/index-plan',
      '/api/monitor/targets/demo/actions/3/dismiss',
      '/api/monitor/actions',
      '/api/monitor/actions?db=my%20db',
      '/api/monitor/actions/applied?db=demo',
      '/api/monitor/targets/demo/actions/3/set_aside',
      '/api/monitor/targets/demo/actions/opened',
    ])
    expect(JSON.parse(String(fetch.mock.calls[6][1]?.body))).toEqual({
      until: 99,
      reason: 'Later',
    })
    expect(fetch.mock.calls[7][1]?.method).toBe('POST')
  })

  it('leaves classic fixture scenarios on the backend too', async () => {
    setFixtureOverride('localdev')
    const fetch = vi.fn(async () => new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await fetchActions('demo')
    expect(fetch).toHaveBeenCalledOnce()
  })
})
