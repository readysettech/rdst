import type { APIRequestContext } from '@playwright/test'
import { expect, test } from '../../fixtures'
import {
  base,
  eventually,
  ok,
  openActions,
  openDatabase,
  queryParam,
  type Shape,
  searchParam,
  selectTarget,
  shapeByName,
  target,
} from './cim'

// Shard A: the Database page, the report and agent activity. These run first,
// while the setup's history work leaves the monitor's five-minute window; the
// Deep Analyze runs it started are checked last (3-inbox-late.spec.ts).

type Row = {
  key: string
  analyzable: boolean
  analyzable_code: string
  not_analyzable_reason: string | null
  analysis_note: string | null
  assessment: {
    status: string
    priority_score: number | null
    error_code: string | null
  } | null
}
type Report = {
  id: number
  trigger: string
  state: string
  period_start: number | null
  period_partial: boolean
  report_json: {
    snapshot?: {
      period: { prior_start: number | null; partial: boolean }
      numbers: { metric: string; value: number | null; prior: number | null }[]
    } | null
  } | null
}
let shapes: Record<string, Shape>
// Every shape the steady load runs; the burst-only, new heavy and stopped
// shapes haven't run live yet when this group starts.
const live = () =>
  Object.values(shapes).filter(
    (shape) => !['burst_only', 'new_heavy', 'stopped'].includes(shape.role)
  )

test.beforeAll(async ({ request }) => {
  shapes = await shapeByName(request)
})

const queryRows = (request: APIRequestContext) =>
  ok<{ queries: Row[]; total: number }>(
    request.get(`${base}/queries?range=1h&limit=500`)
  )

function localDayStart(offsetDays: number) {
  const day = new Date()
  day.setHours(0, 0, 0, 0)
  day.setDate(day.getDate() + offsetDays)
  return Math.floor(day.getTime() / 1000)
}

/** Classified, or paused after an unusable answer the Key Service billed. */
function settled(row: Row | undefined) {
  const assessment = row?.assessment
  return (
    assessment?.status === 'complete' ||
    (assessment?.status === 'paused' &&
      assessment.error_code === 'jev_invalid_response')
  )
}

test('every query shape gets a Jev classification', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  const expected = live().map((shape) => shape.key)
  // With the Database page open RDST classifies at full speed, not the gentler
  // pace it keeps for monitored databases nobody is looking at.
  await openDatabase(page)
  const rows = await eventually(
    testInfo,
    'classification',
    () => queryRows(request),
    (page) => {
      const byKey = new Map(page.queries.map((row) => [row.key, row]))
      return expected.every((key) => settled(byKey.get(key)))
    },
    {
      timeout: 4 * 60_000,
      interval: 5_000,
      diagnose: async () => {
        const byKey = new Map(
          (await queryRows(request)).queries.map((row) => [row.key, row])
        )
        return expected
          .filter((key) => !settled(byKey.get(key)))
          .map((key) => ({
            key,
            listed: byKey.has(key),
            assessment: byKey.get(key)?.assessment,
          }))
      },
    }
  )
  // The hosted model occasionally returns an unusable answer. That classification is billed
  // and pauses until the query changes, so it isn't repeated; one is tolerated and reported,
  // and every other shape must be classified.
  const byKey = new Map(rows.queries.map((row) => [row.key, row]))
  const paused = expected.filter(
    (key) => byKey.get(key)!.assessment!.status !== 'complete'
  )
  if (paused.length) {
    const detail = JSON.stringify(
      paused.map((key) => ({ key, ...byKey.get(key)!.assessment }))
    )
    console.log(`ci-shop: classification paused: ${detail}`)
    testInfo.annotations.push({
      type: 'classification-paused',
      description: detail,
    })
  }
  expect(
    paused.length,
    'at most one paused classification'
  ).toBeLessThanOrEqual(1)
  for (const key of expected.filter((entry) => !paused.includes(entry))) {
    const { priority_score } = byKey.get(key)!.assessment!
    expect(priority_score).toBeGreaterThanOrEqual(0)
    expect(priority_score).toBeLessThanOrEqual(100)
  }

  // A linked query opens in the detail pane, its row marked current.
  const heavy = [shapes.h01_orders_by_status_channel, ...live()]
    .filter((shape) => shape.role === 'heavy')
    .find((shape) => !paused.includes(shape.key))!.key
  await openDatabase(page, `&${queryParam(heavy)}`)
  const row = page.locator(`[id="query-${heavy}"]`)
  await expect(row).toHaveAttribute('aria-current', 'true')
  await expect(page.locator('[data-two-pane]')).toHaveAttribute(
    'data-open',
    'true'
  )
  const pane = page.locator('[data-two-pane] > div').nth(1)
  await expect(pane.getByRole('heading', { level: 2 })).toBeVisible()
  // The pane offers Analyze (and Estimate fix gain once there's a fix), or Cancel while
  // the query's first analysis is still running.
  await expect(
    pane
      .getByRole('button', { name: /^Analyze( again)?$/ })
      .or(pane.getByRole('button', { name: 'Estimate fix gain' }))
      .or(pane.getByRole('button', { name: 'Cancel' }))
      .first()
  ).toBeVisible()
})

test('the query list pages, switches the chart and opens a row in the pane', async ({
  page,
  request,
}) => {
  test.setTimeout(3 * 60_000)
  const { total, queries } = await queryRows(request)
  expect(total).toBeGreaterThanOrEqual(live().length)
  // The list leaves writes out until asked, so the pages hold the rest.
  const listed = queries.filter(
    (row) => !['write', 'ddl'].includes(row.analyzable_code)
  ).length
  await openDatabase(page, '&range=1h')
  await expect(page.getByTestId('load-card')).toBeVisible()
  const rows = page.getByTestId('query-row')
  await expect(rows.first()).toBeVisible()
  const firstPage = await rows.count()
  expect(firstPage).toBeGreaterThan(0)
  expect(firstPage).toBeLessThanOrEqual(listed)
  // More queries than one page: the pager says so and walks forward.
  if (listed > firstPage) {
    await expect(page.getByRole('button', { name: 'Next' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Previous' })).toHaveCount(0)
  }

  const series = page.getByRole('radiogroup', { name: 'Chart series' })
  for (const [name, metric] of [
    ['Latency', 'latency'],
    ['Time spent', 'time'],
    ['Queries/s', 'calls'],
  ] as const) {
    const radio = series.getByRole('radio', { name })
    await radio.click()
    await expect(radio).toHaveAttribute('aria-checked', 'true')
    if (metric !== 'calls')
      await expect(page).toHaveURL(new RegExp(`metric=${metric}`))
  }
  // A health number stands in for the series until Queries per second is picked again.
  const health = page.getByRole('region', { name: 'Database health' })
  const connections = health.getByRole('button', { name: /Connections/ })
  await connections.click()
  await expect(connections).toHaveAttribute('aria-pressed', 'true')
  await expect(page).toHaveURL(/health=connections/)
  await expect(
    page.getByRole('heading', { level: 2, name: 'Connections' })
  ).toBeVisible()
  await health.getByRole('button', { name: /Queries per second/ }).click()
  await expect(connections).toHaveAttribute('aria-pressed', 'false')
  await expect(series).toBeVisible()

  // A row opens in the pane beside the list and names itself in the URL.
  const third = rows.nth(Math.min(2, firstPage - 1))
  const key = (await third.getAttribute('id'))!.replace(/^query-/, '')
  await third.click()
  await expect.poll(() => searchParam(page, 'query')).toBe(key)
  await expect(third).toHaveAttribute('aria-current', 'true')
  await expect(page.locator('[data-two-pane]')).toHaveAttribute(
    'data-open',
    'true'
  )
  // The page never scrolls as a whole; each panel scrolls on its own.
  expect(
    await page.evaluate(() => {
      const scroller = document.scrollingElement
      return Boolean(scroller && scroller.scrollHeight > innerHeight + 2)
    })
  ).toBe(false)
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-two-pane]')).not.toHaveAttribute(
    'data-open',
    'true'
  )

  // A write is analyzed through the search for its rows, never run.
  const write = shapes.w1_set_order_status.key
  const writeRow = (await queryRows(request)).queries.find(
    (entry) => entry.key === write
  )!
  expect(writeRow.analyzable).toBe(true)
  expect(writeRow.analysis_note).toMatch(
    /finds its rows, without running the write/
  )
})

test('a checkup feeds Actions, and Reports keeps to the nightly days', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(5 * 60_000)
  const yesterday = localDayStart(-1)
  const history = await ok<{
    reports: { id: number; period_start: number | null; trigger: string }[]
  }>(request.get(`${base}/report/history?limit=10`))
  const nightly = history.reports.find(
    (report) =>
      report.trigger === 'nightly' && report.period_start === yesterday
  )
  expect(nightly, 'the nightly report for yesterday').toBeTruthy()

  // Checkups run on their own, at setup and nightly; the API starts one here.
  await ok(request.post(`${base}/report`))
  const today = await eventually(
    testInfo,
    'todays-checkup',
    () => ok<Report>(request.get(`${base}/report/latest`)),
    (report) => report.trigger === 'user' && report.state === 'done',
    { timeout: 4 * 60_000, interval: 3_000 }
  )
  expect(today.period_start).toBe(localDayStart(0))
  expect(today.report_json?.snapshot?.period.partial).toBe(true)
  // The checkup measures today against yesterday.
  expect(today.report_json?.snapshot?.period.prior_start).toBe(yesterday)
  const qps = today.report_json!.snapshot!.numbers.find(
    (entry) => entry.metric === 'qps'
  )!
  expect(qps.value).toBeGreaterThan(0)
  expect(qps.prior).toBeGreaterThan(0)
  // An old link to the Database page's Actions tab lands on Actions for this database.
  await selectTarget(page)
  await page.goto(`/database?target=${encodeURIComponent(target)}&view=actions`)
  await expect(page).toHaveURL(
    new RegExp(`/actions\\?(.*&)?db=${encodeURIComponent(target)}(&|$)`)
  )
  await expect(
    page.getByRole('heading', { level: 1, name: 'Actions' })
  ).toBeVisible()

  // The Report tab shows the nightly days only; the checkup fed Actions.
  await openDatabase(page, '&view=report')
  const document = page.getByTestId('report-document')
  await expect(document).toHaveAttribute(
    'data-report-id',
    String(nightly!.id),
    { timeout: 30_000 }
  )
  await expect(page.getByRole('region', { name: 'The day' })).toBeVisible()
  await expect(page.getByTestId('report-actions-now')).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Report menu' }).click()
  await page.getByRole('menuitem', { name: 'Download CSV' }).click()
  expect((await download).suggestedFilename()).toMatch(/\.csv$/)
  // The download leaves the report menu open; close it before opening the day menu.
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)

  // The day menu lists yesterday's nightly report and no checkup for today. A row's text
  // runs the date into its Latest tag ("Oct 11Latest"), so the day ends at the next non-digit.
  const dayLabel = (ts: number) =>
    new RegExp(
      `\\b${new Date(ts * 1000).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
      })}(?!\\d)`
    )
  await page.getByTestId('day-picker-trigger').click()
  await expect(page.getByRole('menu')).toBeVisible()
  const days = page.getByRole('menuitem')
  await expect(days.filter({ hasText: dayLabel(yesterday) })).toHaveCount(1)
  await expect(
    days.filter({ hasText: dayLabel(localDayStart(0)) })
  ).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(
    page.getByText(/couldn.t load|something went wrong/i)
  ).toHaveCount(0)

  // Detailed reads the same report with the day before under each number; the
  // switch is the report_view preference, left on Quick for the next spec.
  const detailed = await ok<{
    copy_count: number
    detailed: {
      tiles: { metric: string }[]
      health_checks: { check: string }[]
    } | null
  }>(request.get(`${base}/report/${nightly!.id}?view=detailed`))
  expect(detailed.detailed?.tiles.map((tile) => tile.metric)).toEqual([
    'qps',
    'mean_ms',
    'p95_ms',
    'connections',
    'cache_hit',
    'rollbacks_deadlocks',
  ])
  expect(detailed.detailed?.health_checks.length).toBeGreaterThan(0)
  const views = page.getByRole('tablist', { name: 'Report view' })
  await views.getByRole('tab', { name: 'Detailed' }).click()
  await expect(document).toHaveAttribute('data-view', 'detailed')
  await expect(
    page.getByRole('region', { name: 'Settings audit' })
  ).toBeVisible()
  await views.getByRole('tab', { name: 'Quick' }).click()
  await expect(document).toHaveAttribute('data-view', 'quick')

  // The checkup also refreshed the actions: the shop's heavy queries filter on columns
  // with no index, so the index plan adds at least one and Actions lists it under Index.
  type Plan = { add: { table: string; columns: string[] }[] } | null
  const plan = await eventually(
    testInfo,
    'index-plan',
    () => ok<Plan>(request.get(`${base}/index-plan`)),
    (body) => Boolean(body?.add.length),
    { timeout: 6 * 60_000, interval: 5_000 }
  )
  expect(plan!.add.length).toBeGreaterThan(0)
  // The index plan's old address lands on Actions for this database.
  await page.goto(`/database?target=${encodeURIComponent(target)}&view=indexes`)
  await expect(page).toHaveURL(
    new RegExp(`/actions\\?(.*&)?db=${encodeURIComponent(target)}(&|$)`)
  )
  // The checkup fed the plan: it says when it was updated.
  await openActions(page)
  await expect(page.getByTestId('actions-status')).toContainText(/updated/i)
  // The plan lists its index adds under Indexes, each led by its verb.
  const indexes = page
    .getByTestId('plan-group')
    .filter({ has: page.getByRole('heading', { name: 'Indexes' }) })
  await expect(
    indexes.getByTestId('plan-verb').filter({ hasText: 'Add' }).first()
  ).toBeVisible()
  // A link that names a kind narrows to it, as one chip that clears it.
  await openActions(page, '&kind=index')
  await expect(page.getByRole('button', { name: 'Index, clear' })).toBeVisible()
  await expect(
    page.getByTestId('plan-verb').filter({ hasText: 'Add' }).first()
  ).toBeVisible()
})

test('agent activity lists the analyses, classifications, checks and reports', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(4 * 60_000)
  const expected = [
    'deep_analysis',
    'classification',
    'attention_check',
    'daily_snapshot',
  ]
  const read = () =>
    ok<{
      items: { action: string; outcome: string; query_key: string | null }[]
    }>(
      request.get(
        `/api/agent-activity?target=${encodeURIComponent(target)}&group=none&limit=200`
      )
    )
  // An analysis is listed once it finishes; the setup queued them moments ago.
  const activity = await eventually(
    testInfo,
    'agent-activity',
    read,
    (current) => {
      const actions = new Set(current.items.map((item) => item.action))
      return expected.every((action) => actions.has(action))
    },
    {
      timeout: 3 * 60_000,
      interval: 5_000,
      diagnose: async () => ({
        actions: [...new Set((await read()).items.map((item) => item.action))],
        jobs: (
          await ok<{ jobs: unknown[] }>(request.get(`${base}/analyze-jobs`))
        ).jobs,
      }),
    }
  )
  for (const item of activity.items.filter(
    (entry) => entry.action === 'deep_analysis'
  )) {
    expect(item.query_key).toBeTruthy()
    expect(item.outcome).toMatch(/^(done|failed|skipped)$/)
  }

  await selectTarget(page)
  await page.goto(
    `/agent-activity?target=${encodeURIComponent(target)}&range=7d`
  )
  await expect(
    page.getByRole('heading', { level: 1, name: 'Activity' })
  ).toBeVisible()
  // One line per thing done, each a link to its place; analyses name their query.
  await expect(
    page.getByRole('link', { name: /\bAnalyzed / }).first()
  ).toBeVisible()
  await expect(
    page.getByRole('link', { name: /Checked for changes/ }).first()
  ).toBeVisible()
  await expect(page.getByText('Kept 90 days.')).toBeVisible()

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Export CSV' }).click()
  const dialog = page.getByRole('dialog', { name: 'Export agent activity' })
  await dialog.getByRole('radio', { name: '7 days' }).click()
  const download = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Download CSV' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/\.csv$/)
})
