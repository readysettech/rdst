import type { APIRequestContext } from '@playwright/test'
import { expect, test } from '../../fixtures'
import {
  base,
  eventually,
  ok,
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

  // A row opens onto its details and classification.
  const heavy = [shapes.h01_orders_by_status_channel, ...live()]
    .filter((shape) => shape.role === 'heavy')
    .find((shape) => !paused.includes(shape.key))!.key
  await openDatabase(page, `&${queryParam(heavy)}`)
  const row = page.locator(`[id="query-${heavy}"]`)
  await expect(row.locator('button[aria-label="Details"]')).toHaveAttribute(
    'aria-expanded',
    'true'
  )
  await expect(page.getByTestId('jev-assessment')).toBeVisible()
  await expect(page.getByTestId('jev-waiting')).toHaveCount(0)
})

test('the query list pages, switches metrics and highlights a page-3 query', async ({
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
  await expect(page.getByTestId('load-chart')).toBeVisible()
  const pages = page.getByRole('navigation', { name: 'Query pages' })
  // The navigation shows a window of pages that always reaches the last one.
  await expect(
    pages.getByRole('button', {
      name: `Page ${Math.ceil(listed / 10)}`,
      exact: true,
    })
  ).toBeVisible()

  for (const [name, metric, heading] of [
    ['Latency', 'latency', /^Latency, /],
    ['Time spent', 'time', /^Time spent, /],
    ['Queries/s', 'calls', /^Queries per second, /],
  ] as const) {
    const radio = page
      .getByRole('radiogroup', { name: 'Chart metric' })
      .getByRole('radio', { name })
    await radio.click()
    await expect(radio).toHaveAttribute('aria-checked', 'true')
    await expect(page.locator('#load-hero-title')).toHaveText(heading)
    if (metric !== 'calls')
      await expect(page).toHaveURL(new RegExp(`metric=${metric}`))
  }
  const connections = page
    .getByRole('region', { name: 'Database health' })
    .getByRole('button', { name: /^Connections: / })
  await connections.click()
  await expect(connections).toHaveAttribute('aria-pressed', 'true')
  await expect(page).toHaveURL(/health=connections/)
  const calls = page
    .getByRole('radiogroup', { name: 'Chart metric' })
    .getByRole('radio', { name: 'Queries/s' })
  await calls.click()
  await expect(calls).toHaveAttribute('aria-checked', 'true')
  await expect(connections).toHaveAttribute('aria-pressed', 'false')

  // Page 3 keeps the list in view and a row there draws its own line.
  await pages.getByRole('button', { name: 'Page 3' }).click()
  await expect(pages.getByRole('button', { name: 'Page 3' })).toHaveAttribute(
    'aria-current',
    'page'
  )
  await expect(page.getByTestId('query-section')).toBeInViewport({ ratio: 0.1 })
  const rows = page.getByTestId('monitor-query-row')
  await expect(rows).toHaveCount(10)
  // Live updates can reorder the page, so the raised row names the query.
  await rows.nth(2).hover()
  const raised = page.locator(
    '[data-testid="monitor-query-row"][data-raised="true"]'
  )
  await expect(raised).toHaveCount(1)
  const key = await raised.getAttribute('data-query-hash')
  const row = page.locator(`[id="query-${key}"]`)
  await expect(
    page
      .getByTestId('load-chart')
      .locator(`[data-overlay="${key}"], [data-band="${key}"]`)
      .first()
  ).toBeAttached()
  // Open, it stays highlighted; with the hero scrolled away, the sticky chart
  // names it.
  await row.locator('button[aria-label="Details"]').click()
  await expect.poll(() => searchParam(page, 'query')).toBe(key)
  await page.getByTestId('query-pages').scrollIntoViewIfNeeded()
  await page.mouse.move(720, 450)
  await expect(async () => {
    await page.mouse.wheel(0, 500)
    await expect(page.getByTestId('load-strip')).toBeVisible({ timeout: 1_000 })
  }).toPass({ timeout: 15_000 })
  await expect(page.getByTestId('load-strip-highlight')).not.toBeEmpty()

  // Writes are listed but never analyzed, with the reason given.
  const write = shapes.w1_set_order_status.key
  const writeRow = (await queryRows(request)).queries.find(
    (entry) => entry.key === write
  )!
  expect(writeRow.analyzable).toBe(false)
  expect(writeRow.not_analyzable_reason).toMatch(/changes data/)
})

test("today's snapshot compares against yesterday and the day picker opens yesterday", async ({
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

  await openDatabase(page, '&view=report')
  await expect(page.getByTestId('day-snapshot')).toBeVisible()
  await page.getByRole('button', { name: 'Report menu' }).click()
  await page.getByRole('menuitem', { name: "Run today's snapshot now" }).click()
  const today = await eventually(
    testInfo,
    'todays-snapshot',
    () => ok<Report>(request.get(`${base}/report/latest`)),
    (report) => report.trigger === 'user' && report.state === 'done',
    { timeout: 4 * 60_000, interval: 3_000 }
  )
  expect(today.period_start).toBe(localDayStart(0))
  expect(today.report_json?.snapshot?.period.partial).toBe(true)
  // "The day in numbers" measures today against yesterday.
  expect(today.report_json?.snapshot?.period.prior_start).toBe(yesterday)
  const qps = today.report_json!.snapshot!.numbers.find(
    (entry) => entry.metric === 'qps'
  )!
  expect(qps.value).toBeGreaterThan(0)
  expect(qps.prior).toBeGreaterThan(0)

  const snapshot = page.getByTestId('day-snapshot')
  await expect(snapshot).toHaveAttribute('data-report-id', String(today.id), {
    timeout: 30_000,
  })
  const sections = page.getByRole('navigation', { name: 'Report sections' })
  await expect(sections).toBeVisible()
  await expect(sections.locator('[data-section]').first()).toHaveAttribute(
    'data-section',
    'snapshot-health'
  )
  const weekday = new Date(yesterday * 1000).toLocaleDateString('en-US', {
    weekday: 'long',
  })
  await expect(page.getByTestId('day-numbers-against')).toContainText(
    `Against ${weekday}`
  )
  const download = page.waitForEvent('download')
  await page.getByTestId('download-csv').first().click()
  expect((await download).suggestedFilename()).toMatch(/\.csv$/)

  // The picker lists today and yesterday; picking yesterday loads it whole.
  await page.getByTestId('day-picker-trigger').click()
  const days = page.getByRole('menuitem')
  await expect(days.filter({ hasText: /Today/ })).toHaveCount(1)
  await days.filter({ hasText: /Yesterday/ }).click()
  await expect(page).toHaveURL(
    new RegExp(`report=(%22)?${nightly!.id}(%22)?(&|$)`)
  )
  await expect(snapshot).toHaveAttribute('data-report-id', String(nightly!.id))
  await expect(page.getByTestId('snapshot-title')).toBeVisible()
  await expect(page.getByTestId('day-numbers-against')).toBeVisible()
  await expect(
    page.getByText(/couldn.t load|something went wrong/i)
  ).toHaveCount(0)
  await page.getByRole('button', { name: /^Next day, / }).click()
  await expect(snapshot).toHaveAttribute('data-report-id', String(today.id))
  await page.getByRole('button', { name: /^Previous day, / }).click()
  await expect(snapshot).toHaveAttribute('data-report-id', String(nightly!.id))
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
  const entries = page.locator('[data-activity-key]')
  await expect(entries.first()).toBeVisible()
  const entry = page.locator('[data-activity-key^="entry:"]').first()
  await entry.getByRole('button').first().click()
  const detail = page.getByTestId('activity-detail').first()
  await expect(detail).toBeVisible()
  await detail.getByRole('button', { name: /Technical details/ }).click()

  await page.getByRole('button', { name: 'Export CSV' }).click()
  const dialog = page.getByRole('dialog', { name: 'Export agent activity' })
  await dialog.getByRole('radio', { name: '7 days' }).click()
  const download = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Download CSV' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/\.csv$/)
})
