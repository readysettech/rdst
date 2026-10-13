import { expect, test } from '../../fixtures'
import {
  base,
  body,
  eventually,
  findItem,
  hold,
  hook,
  type InboxItem,
  inboxBadge,
  inboxDetail,
  items,
  judgeUntil,
  ok,
  openDatabase,
  openInbox,
  revealRow,
  type Shape,
  selectTarget,
  shapeByName,
  target,
} from './cim'

// After the Inbox scenarios: the Deep Analyze runs the setup started, the new
// heavy query, a cut connection once no analysis needs the database, and last
// the regression's recovery, once its slow calls have left the five-minute
// window.

const CHANGED = ['latency_regression', 'plan_change', 'table_slowdown']
type Job = {
  query_key: string
  reason: string
  state: string
  priority: number
  error: string | null
}

let shapes: Record<string, Shape>

test.beforeAll(async ({ request }) => {
  shapes = await shapeByName(request)
})

test('the ten heaviest queries get a deep analysis with a score and a fix', async ({
  request,
}, testInfo) => {
  test.setTimeout(8 * 60_000)
  // New queries and yesterday's report queue the heaviest queries by database
  // time; the day's cap of ten runs the ten heaviest that can be analyzed.
  const settled = await eventually(
    testInfo,
    'deep-analysis',
    async () =>
      (await ok<{ jobs: Job[] }>(request.get(`${base}/analyze-jobs`))).jobs,
    (all) =>
      all.filter((job) => ['done', 'failed'].includes(job.state)).length >= 10,
    {
      timeout: 7 * 60_000,
      interval: 5_000,
      diagnose: async () =>
        (await ok<{ jobs: Job[] }>(request.get(`${base}/analyze-jobs`))).jobs,
    }
  )
  // A run that fails before any AI answer doesn't spend the cap, so another may already be
  // under way; the ten heaviest finished runs are the ones checked.
  const ranked = settled
    .filter((job) => ['done', 'failed'].includes(job.state))
    .sort((left, right) => right.priority - left.priority)
    .slice(0, 10)
  expect(ranked).toHaveLength(10)
  // The hosted model occasionally returns an unreadable answer; that run
  // fails with its reason and uses its slot of the day's cap. One is tolerated
  // and reported; the rest must finish.
  const failed = ranked.filter((job) => job.state === 'failed')
  if (failed.length) {
    console.log(`ci-shop: deep analysis failed: ${JSON.stringify(failed)}`)
    testInfo.annotations.push({
      type: 'deep-analysis-failed',
      description: JSON.stringify(failed),
    })
  }
  expect(failed.length, 'at most one failed deep analysis').toBeLessThanOrEqual(
    1
  )
  for (const job of failed) expect(job.error ?? '').not.toBe('')
  const roles = new Map(
    Object.values(shapes).map((shape) => [shape.key, shape.role])
  )
  const names = new Map(
    Object.values(shapes).map((shape) => [shape.key, shape.name])
  )
  let missingIndex = 0
  const withoutIndex: unknown[] = []
  for (const job of ranked.filter((entry) => entry.state === 'done')) {
    type Fix = { kind: string; sql: string | null }
    const digest = await ok<{
      efficiency_score: number | null
      analysis_ref: string | null
      rating: string | null
      headline: string
      findings: string[]
      plan_summary: { text: string } | null
      fix: Fix | null
      other_fixes: Fix[]
    }>(request.get(`${base}/queries/${job.query_key}/analysis`))
    expect(digest.efficiency_score).toBeGreaterThanOrEqual(0)
    expect(digest.efficiency_score).toBeLessThanOrEqual(100)
    expect(digest.analysis_ref).toContain('/api/query-registry/')
    expect(digest.plan_summary?.text.length ?? 0).toBeGreaterThan(0)
    expect(digest.headline.length + digest.findings.length).toBeGreaterThan(0)
    if (roles.get(job.query_key) === 'heavy') {
      missingIndex += 1
      // The index may follow a rewrite the analyzer ranks first.
      const fixes = [digest.fix, ...(digest.other_fixes ?? [])].filter(
        Boolean
      ) as Fix[]
      const index = fixes.find(
        (fix) =>
          fix.kind === 'index' && /CREATE (UNIQUE )?INDEX/i.test(fix.sql ?? '')
      )
      if (!index) {
        withoutIndex.push({
          query: names.get(job.query_key) ?? job.query_key,
          rating: digest.rating,
          headline: digest.headline,
          findings: digest.findings,
          plan: digest.plan_summary?.text,
          fixes: fixes.map((fix) => fix.kind),
        })
      }
    }
  }
  expect(
    withoutIndex,
    'every missing-index query gets a CREATE INDEX fix'
  ).toEqual([])
  expect(missingIndex).toBeGreaterThanOrEqual(5)
})

test('a new heavy query files once it has ranked with the top queries', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  await hook.phase(request, 'new_heavy', true)
  try {
    // The query leaves the digest once two checks in a row rank it in the hour's top ten,
    // which takes it about a minute and a half of running on a quiet agent.
    await hold(60)
    // The key the collector records for it; until then the shape only has its text's hash,
    // which a MySQL digest identity needn't share.
    const heavy = (
      await eventually(
        testInfo,
        'new-heavy-observed',
        () => shapeByName(request),
        (shapes) => shapes.n1_category_revenue.observed,
        { timeout: 60_000 }
      )
    ).n1_category_revenue.key
    const { items: all } = await judgeUntil(
      request,
      testInfo,
      'new-heavy',
      (current) => {
        const item = findItem(current, 'new_heavy_query', heavy)
        return Boolean(item && item.delivery !== 'digest')
      },
      8,
      10
    )
    const item = findItem(all, 'new_heavy_query', heavy)!
    await openInbox(page)
    const row = await revealRow(page, item.id)

    // Opening the item marks it seen.
    await row.click()
    await expect(row).toHaveAttribute('aria-current', 'true')
    await expect(inboxDetail(page, item.id)).toBeVisible()
    await expect
      .poll(
        async () =>
          (await items(request)).find((entry) => entry.id === item.id)?.state
      )
      .toBe('seen')

    // Filtering by database keeps this target's items; the row names its database.
    await page
      .getByRole('group', { name: 'Filters' })
      .getByRole('button', { name: /^Database: / })
      .click()
    await page
      .getByRole('menuitem', { name: new RegExp(`^${target}\\b`) })
      .click()
    await expect(page).toHaveURL(new RegExp(`db=${target}`))
    await expect(
      (await revealRow(page, item.id)).getByTestId('inbox-item-database')
    ).toHaveText(target)

    // The sidebar badge counts the open items that need action now, and
    // shows only while there are any.
    const actNow = (await items(request, 'state=open')).filter(
      (entry) => entry.severity_band === 'act_now'
    ).length
    const badge = inboxBadge(page)
    if (actNow > 0) await expect(badge).toHaveText(String(actNow))
    else await expect(badge).toHaveCount(0)
  } finally {
    await hook.phase(request, 'new_heavy', false)
  }
})

test('a cut connection files at once, notifies, marks the chart and clears on return', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(4 * 60_000)
  const lost = (all: InboxItem[]) =>
    all.find(
      (item) =>
        item.type === 'monitoring_issue' && item.subject === 'lost_connection'
    )
  const cut = await hook.proxy(request, 'cut')
  expect(cut.open).toBe(false)
  let item: InboxItem | undefined
  try {
    item = lost(
      await eventually(
        testInfo,
        'lost-connection',
        () => items(request),
        (all) => Boolean(lost(all) && lost(all)!.state !== 'resolved'),
        { timeout: 60_000 }
      )
    )
    expect(item!.severity_band).toBe('act_now')
    expect(item!.delivery).toBe('inbox')
    await selectTarget(page)
    await page.goto('/inbox')
    await expect(
      page
        .getByRole('region', { name: 'Act now' })
        .locator(`[data-inbox-item="${item!.id}"]`)
    ).toBeVisible()
    // Hold the outage long enough to leave a gap in the minute readings.
    await new Promise((settle) => setTimeout(settle, 45_000))
  } finally {
    await hook.proxy(request, 'open')
  }
  const cleared = await eventually(
    testInfo,
    'lost-connection-cleared',
    () => items(request),
    (all) => lost(all)?.state === 'resolved',
    { timeout: 90_000 }
  )
  expect(lost(cleared)!.id).toBe(item!.id)
  // The outage filed in the Inbox with no push (that waits an hour); its return closed it.
  const events = (await hook.events(request, item!.id)).map(
    (event) => event.kind
  )
  expect(events).toEqual(expect.arrayContaining(['created', 'resolved']))
  expect(events).not.toContain('notified')
  const health = await ok<{
    outages: { start: number; end: number | null; cause: string }[]
  }>(request.get(`${base}/health?range=15m`))
  expect(health.outages).toEqual([
    expect.objectContaining({ cause: 'unreachable', end: expect.any(Number) }),
  ])
  await openDatabase(page, '&range=15m')
  await expect(
    page
      .getByTestId('load-chart')
      .locator('[data-testid="chart-pause"][data-cause="unreachable"]')
  ).toBeAttached()
})

test('the regression measures back to normal once the index is back', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(4 * 60_000)
  const regression = (await shapeByName(request)).m02_items_by_order.key
  const filed = (await items(request)).find(
    (item) => CHANGED.includes(item.type) && item.subject === regression
  )
  expect(filed, 'the regression filed earlier').toBeTruthy()
  // Code closes a recovered finding after three clean measurements at least
  // five minutes apart, longer than this run. The first clean one is recorded
  // once the slow calls have left the five-minute window.
  const measured = await eventually(
    testInfo,
    'regression-recovery',
    async () => {
      await hook.judge(request)
      return (await items(request)).find((item) => item.id === filed!.id)
    },
    (item) =>
      item?.state === 'resolved' ||
      Number(body(item!).recovery_streak ?? 0) >= 1,
    { timeout: 3 * 60_000, interval: 5_000 }
  )
  if (measured!.state !== 'resolved') {
    // It stays listed until the remaining clean checks.
    await openInbox(page)
    await revealRow(page, filed!.id)
  } else {
    expect(measured!.severity_band).toBe('recovered')
  }
})
