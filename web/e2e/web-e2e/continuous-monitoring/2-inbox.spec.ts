import { expect, test } from '../../fixtures'
import {
  body,
  findItem,
  hold,
  hook,
  items,
  judgeUntil,
  openInbox,
  revealRow,
  type Shape,
  shapeByName,
} from './cim'

// Shard B: the Inbox scenarios, in the order they build on each other. Each
// AI check is a real call to Jev through the keyservice.

const CHANGED = ['latency_regression', 'plan_change', 'table_slowdown']
const BURST_S = 45
let shapes: Record<string, Shape>

test.beforeAll(async ({ request }) => {
  shapes = await shapeByName(request)
})

test('a stopped steady write reaches Jev; cheap noise and a quiet burst-only query stay out', async ({
  request,
}, testInfo) => {
  test.setTimeout(3 * 60_000)
  const stopped = shapes.s1_record_payment.key
  const noise = shapes.c25_customer_exists.key
  const burstOnly = shapes.b7_status_channel_matrix.key
  const run = await hook.judge(request)
  // The write went quiet over 30 minutes ago: code shortlists it as stopped and
  // Jev judges it as a stopped query.
  expect(run.outcome).toBe('filed')
  expect(run.shortlist).toContain(stopped)
  const verdict = run.verdict.find((answer) => answer.query_key === stopped)
  expect(verdict?.kind).toBe('query_stopped_running')
  const all = await items(request)
  if (verdict!.attention === 'ignore') {
    // Jev decides whether a stop merits attention; with "ignore" nothing files.
    testInfo.annotations.push({
      type: 'jev',
      description: 'Jev judged the stopped write not worth an Inbox item',
    })
    console.log(`ci-shop: Jev ignored the stopped write (${JSON.stringify(verdict)})`)
    expect(findItem(all, 'query_stopped_running', stopped)).toBeUndefined()
  } else {
    expect(findItem(all, 'query_stopped_running', stopped)).toBeTruthy()
  }
  // Noise ran at 5x its calls the whole time, and the burst-only report
  // query has been quiet for two hours: the gate keeps both from Jev.
  expect(run.shortlist).not.toContain(noise)
  expect(run.shortlist).not.toContain(burstOnly)
  expect([...(run.gate?.smaller ?? []), ...(run.gate?.minor ?? [])]).toContain(noise)
  expect(all.filter((item) => [noise, burstOnly].includes(item.subject))).toEqual([])
  await hook.phase(request, 'noise', false)
})

test('dropping an index on a top query files a regression the open Inbox shows', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  const regression = shapes.m02_items_by_order.key
  await openInbox(page)
  // Survives only while this document stays loaded.
  await page.evaluate(() => {
    ;(window as Window & { inboxStillLoaded?: boolean }).inboxStillLoaded = true
  })
  const dropped = await hook.phase(request, 'regression', true)
  try {
    // A full scan: Seq Scan on PostgreSQL, access type ALL on MySQL.
    expect(JSON.stringify(dropped.plan)).toMatch(/Seq Scan|access_type\W+ALL/)
    await hold(40)
    const { items: all } = await judgeUntil(
      request,
      testInfo,
      'regression',
      (current) => Boolean(findItem(current, CHANGED, regression)),
      3
    )
    const item = findItem(all, CHANGED, regression)!
    expect(body(item)).toMatchObject({ query_key: regression, kind: item.type })
    expect(item.delivery).not.toBe('digest')

    const row = await revealRow(page, item.id)
    expect(
      await page.evaluate(
        () => (window as Window & { inboxStillLoaded?: boolean }).inboxStillLoaded
      )
    ).toBe(true)
    // Rows start collapsed unless a link opened one.
    await expect(row).not.toHaveAttribute('data-open', 'true')
    await row.getByRole('heading').getByRole('button').first().click()
    await expect(row).toHaveAttribute('data-open', 'true')
    await row.locator('a[href*="/database"]').first().click()
    await expect(page).toHaveURL(/\/database/)
    await expect(page).toHaveURL(/zoom=\d+-\d+/)
    await expect(page.getByTestId('time-frame-chip')).toContainText('From your Inbox')
  } finally {
    // The index goes back in. Recovery is checked once the slow calls have
    // left the five-minute window, after the Database page group
    // (3-inbox-late.spec.ts).
    await hook.phase(request, 'regression', false)
  }
})

test('a third report-group burst joins the repeating item with three time frames', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  await hook.phase(request, 'burst', true)
  await hold(BURST_S)
  await hook.phase(request, 'burst', false)
  const { items: all } = await judgeUntil(
    request,
    testInfo,
    'third-burst',
    (current) => {
      const repeat = findItem(current, 'many_queries_changed')
      return Boolean(
        repeat && ((body(repeat).occurrences as unknown[]) ?? []).length >= 3
      )
    },
    2
  )
  const repeat = findItem(all, 'many_queries_changed')!
  expect(['new', 'seen']).toContain(repeat.state)
  const members = body(repeat).episode_query_keys as string[]
  for (const name of ['b1_orders_by_status', 'b2_revenue_by_channel', 'b4_top_products']) {
    expect(members).toContain(shapes[name].key)
  }
  expect(all.filter((item) => item.type === 'many_queries_changed')).toHaveLength(1)

  await openInbox(page)
  const row = await revealRow(page, repeat.id)
  await row.getByRole('heading').getByRole('button').first().click()
  const times = row.getByRole('region', { name: 'Each time it happened' })
  await times.getByRole('button', { name: /^Show the 3 times$/ }).click()
  const frames = times.locator('li[data-occurrence]')
  await expect(frames).toHaveCount(3)
  const links = times.getByRole('link', { name: /^Open the Database page/ })
  await expect(links).toHaveCount(3)
  for (const link of await links.all()) {
    await expect(link).toHaveAttribute('href', /zoom=\d+-\d+/)
  }
  await links.first().click()
  await expect(page).toHaveURL(/\/database/)
  await expect(page).toHaveURL(/zoom=\d+-\d+/)
  await expect(page.getByTestId('time-frame-chip')).toContainText('From your Inbox')

  // Resolving it closes this burst.
  await openInbox(page)
  await row.hover()
  await row.getByRole('button', { name: 'Resolve', exact: true }).click()
  await expect(row).toHaveCount(0)
  await expect
    .poll(async () => (await items(request)).find((item) => item.id === repeat.id)?.state)
    .toBe('resolved')
})
