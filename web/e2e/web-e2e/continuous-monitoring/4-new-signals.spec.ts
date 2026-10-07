import { expect, test } from '../../fixtures'
import {
  base,
  body,
  engine,
  eventually,
  findItem,
  hold,
  hook,
  inboxRow,
  items,
  ok,
  openInbox,
  revealRow,
  selectTarget,
} from './cim'

test('held locks file a lock item that names the blocker, notifies once and recovers', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  await hook.locks(request, 4)
  let id: number
  try {
    const all = await eventually(
      testInfo,
      'lock-contention',
      () => items(request),
      (all) => Boolean(findItem(all, 'lock_contention', 'database')),
      { timeout: 180_000, diagnose: () => hook.stats(request) }
    )
    const item = findItem(all, 'lock_contention', 'database')!
    id = item.id
    expect(item.delivery).toBe('notify')
    if (engine === 'postgresql') {
      expect(body(item).blocker).toMatchObject({
        query: expect.stringMatching(/LOCK TABLE/i),
      })
    }
    await eventually(
      testInfo,
      'lock-notification',
      () => hook.events(request, item.id),
      (events) =>
        events.filter((event) => event.kind === 'notified').length === 1,
      { timeout: 30_000 }
    )
    await openInbox(page)
    const row = await revealRow(page, item.id)
    await expect(row).toContainText('Queries waiting on locks')
    await expect(
      page.getByRole('region', { name: 'Urgent', exact: true })
    ).toContainText('Queries waiting on locks')
    await hold(10)
    expect(
      (await hook.events(request, item.id)).filter(
        (event) => event.kind === 'notified'
      )
    ).toHaveLength(1)
  } finally {
    await hook.releaseLocks(request)
  }
  const recovered = await eventually(
    testInfo,
    'lock-recovery',
    async () => (await items(request)).find((item) => item.id === id),
    (item) => item?.state === 'resolved' && item.severity_band === 'recovered',
    { timeout: 120_000 }
  )
  expect(recovered?.severity_band).toBe('recovered')
  expect(
    (await hook.events(request, id!)).filter(
      (event) => event.kind === 'notified'
    )
  ).toHaveLength(1)
})

test('a spilling sort records temp spills for its query and sends them to Jev', async ({
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  await hook.spill(request, 150)
  const query = await eventually(
    testInfo,
    'spill-query',
    async () => {
      const rows = await ok<{ queries: { key: string; text: string }[] }>(
        request.get(`${base}/queries?range=1h&limit=500`)
      )
      return rows.queries.find(
        (row) => /cim_spill_demo/i.test(row.text) && /^SELECT/i.test(row.text)
      )
    },
    Boolean,
    { timeout: 90_000 }
  )
  const series = await eventually(
    testInfo,
    'temp-spills',
    () =>
      ok<{ points: { temp_blks: number | null }[] }>(
        request.get(`${base}/queries/${query!.key}/series?range=1h`)
      ),
    (series) => series.points.some((point) => (point.temp_blks ?? 0) > 0),
    { timeout: 90_000 }
  )
  expect(series.points.some((point) => (point.temp_blks ?? 0) > 0)).toBe(true)
  const run = await eventually(
    testInfo,
    'jev-temp-spills',
    () => hook.judge(request),
    (run) =>
      run.queries.some(
        (row) => row.query_key === query!.key && row.current_5m.temp_blks > 0
      ),
    { timeout: 150_000, interval: 5_000 }
  )
  expect(
    run.queries.find((row) => row.query_key === query!.key)?.current_5m
      .temp_blks
  ).toBeGreaterThan(0)
})

test('a nightly checkup opens an action for a new bad finding and pushes once', async ({
  page,
  request,
}, testInfo) => {
  test.skip(engine === 'mysql', 'Vacuum bloat requires PostgreSQL')
  test.setTimeout(8 * 60_000)
  await hook.bloat(request)
  const report = await hook.nightly(request)
  expect(report.state).toBe('done')
  type Card = { key: string; title: string; children?: Card[] }
  const actions = await ok<{ actions: Card[]; more: Card[] }>(
    request.get(`${base}/actions`)
  )
  const cards = [...actions.actions, ...actions.more].flatMap((card) => [
    card,
    ...(card.children ?? []),
  ])
  const vacuum = cards.find((card) => card.key === 'db:vacuum')!
  expect(vacuum).toBeTruthy()
  // Findings live on the Actions tab; the Inbox carries the checkup's notice.
  const all = await items(request)
  expect(all.some((item) => item.type === 'health_finding')).toBe(false)
  const summary = all.find(
    (item) =>
      item.type === 'daily_summary' && body(item).report_id === report.id
  )!
  expect(summary).toBeTruthy()
  expect(summary.delivery).toBe('notify')
  expect(summary.title).toBe(vacuum.title)
  await eventually(
    testInfo,
    'health-notification',
    () => hook.events(request, summary.id),
    (events) =>
      events.filter((event) => event.kind === 'notified').length === 1,
    { timeout: 30_000 }
  )
  // The notification's link opens the Inbox on the notice.
  await selectTarget(page)
  await page.goto(`/inbox?item=${summary.id}`)
  const row = inboxRow(page, summary.id)
  await expect(row).toBeVisible({ timeout: 30_000 })
  await expect(row).toContainText(vacuum.title)
  // The action was already open, so the next night's checkup doesn't push it again.
  expect((await hook.nightly(request)).state).toBe('done')
  await hold(10)
  expect(
    (await hook.events(request, summary.id)).filter(
      (event) => event.kind === 'notified'
    )
  ).toHaveLength(1)
})
