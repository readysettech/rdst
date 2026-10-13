import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

const NOT_FOUND =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

// Each case seeds a full incident history through the real on-disk store.
test.describe.configure({ timeout: 90_000 })

test.beforeEach(async ({ page }) => {
  await configureTestTarget(page, { hasPassword: true })
  const seeded = await page.request.post('/api/testing/monitor/seed')
  expect(seeded.ok()).toBe(true)
})

/** The two panels of a split page. */
function twoPane(page: import('@playwright/test').Page) {
  return page.locator('[data-two-pane]')
}

test('database page shows the bar, the query list and a query pane', async ({
  page,
  browserErrors,
}) => {
  // A monitored query has no Query Library record, so that lookup returns 404.
  acceptBrowserError(browserErrors, NOT_FOUND)
  await mockPrerequisites(page, 'e2e-guard')
  // The classic per-target URL still lands on the Database page for that target.
  await page.goto('/db/e2e-guard')
  await expect(page).toHaveURL(/\/database/)
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()
  await expect(page.getByTestId('query-section')).toBeVisible()

  // A row opens the detail pane beside the page; the page itself never scrolls.
  const row = page.locator('[id="query-orders-e2e"]')
  await expect(row).toBeVisible()
  await expect(twoPane(page)).not.toHaveAttribute('data-open', 'true')
  await row.click()
  await expect(twoPane(page)).toHaveAttribute('data-open', 'true')
  await expect(row).toHaveAttribute('aria-current', 'true')
  await expect(page).toHaveURL(/query=orders-e2e/)
  const pane = twoPane(page).locator(':scope > div').nth(1)
  await expect(pane.getByRole('heading', { level: 2 })).toBeVisible()
  await expect(pane).toContainText('FROM orders')
  // The fix-gain estimate shows only for a query with a fix; Analyze for one RDST reads.
  await expect(pane.getByRole('button', { name: 'Benchmark' })).toHaveCount(0)
  await expect(
    pane.getByRole('button', { name: /^Analyze( again)?$/ })
  ).toBeVisible()
  await pane.getByRole('button', { name: 'Close' }).click()
  await expect(twoPane(page)).not.toHaveAttribute('data-open', 'true')
  await expect(page).not.toHaveURL(/query=/)

  // The seeded target is not monitored yet: one line and one solid button.
  await page.getByRole('button', { name: 'Turn on monitoring' }).click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByRole('heading', { name: 'Monitor e2e-guard' })
  ).toBeVisible()
  await expect(dialog).toContainText('Reads query statistics every minute')
  await expect(dialog).toContainText('Analyze automatically turns it off.')
  await expect(dialog.getByTestId('explain-analyze-warning')).toBeVisible()
  // The analysis switches sit behind Advanced, both reachable.
  await dialog.getByRole('button', { name: 'Advanced' }).click()
  await expect(
    dialog.getByRole('switch', { name: 'Analyze automatically' })
  ).toBeVisible()
  await expect(
    dialog.getByRole('switch', { name: 'Measure real timings' })
  ).toBeVisible()
  await expect(
    dialog.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeInViewport()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

test('database page opens Analyze a query from a link', async ({ page }) => {
  await page.goto('/database?target=e2e-guard&panel=analyze')
  const dialog = page.getByRole('dialog', { name: 'Analyze a query' })
  await expect(dialog).toBeVisible()
  const run = dialog.getByRole('button', { name: 'Analyze', exact: true })
  await expect(run).toBeDisabled()
  await dialog.getByLabel('SQL').fill('UPDATE orders SET total = 0')
  await expect(dialog).toContainText('changes data')
  await dialog.getByLabel('SQL').fill('SELECT id FROM orders WHERE id = 1')
  await expect(run).toBeEnabled()
  await page.keyboard.press('Escape')
  await expect(page).not.toHaveURL(/panel=analyze/)
})

test('health numbers and the chart series switch the chart', async ({
  page,
}) => {
  // The frozen redesign fixture: every number has readings and can be selected.
  // Its index plan has open actions, so without a view the page leads with
  // Actions; the numbers and the chart live on the Queries tab.
  await page.goto('/database?monitorFixture=ca-database&view=queries')
  await expect(page.getByTestId('load-card')).toBeVisible()
  const health = page.getByRole('region', { name: 'Database health' })
  const connections = health.getByRole('button', { name: /Connections/ })
  const series = page.getByRole('radiogroup', { name: 'Chart series' })
  await expect(series).toBeVisible()

  // A number puts its reading on the chart in place of the series control.
  await connections.click()
  await expect(connections).toHaveAttribute('aria-pressed', 'true')
  await expect(page).toHaveURL(/health=connections/)
  await expect(
    page.getByRole('heading', { level: 2, name: 'Connections' })
  ).toBeVisible()
  await expect(series).toHaveCount(0)

  // Queries per second brings the series control back.
  await health.getByRole('button', { name: /Queries per second/ }).click()
  await expect(connections).toHaveAttribute('aria-pressed', 'false')
  await expect(page).not.toHaveURL(/health=/)
  await series.getByRole('radio', { name: 'Latency' }).click()
  await expect(series.getByRole('radio', { name: 'Latency' })).toBeChecked()
  await expect(page).toHaveURL(/metric=latency/)
  await series.getByRole('radio', { name: 'Time spent' }).click()
  await expect(page).toHaveURL(/metric=time/)
})

test('a database in its first minutes shows the readings so far', async ({
  page,
}) => {
  // Monitoring just started: the chart spans the recorded minutes and the
  // Report tab waits for the first report. The query list sits on Live.
  await page.goto('/database?monitorFixture=ca-first-run&view=live')
  await expect(page.getByTestId('first-run-chart')).toBeVisible()
  await expect(page.locator('[data-page-bar]')).toContainText('min recorded')
  await expect(page.getByTestId('query-section')).toBeVisible()
  await page.getByRole('tab', { name: 'Report' }).click()
  await expect(page.getByTestId('report-first')).toContainText(
    'Your first daily report arrives tonight, just after midnight.'
  )
})

test('database page opens the monitoring settings pane from a link', async ({
  page,
}) => {
  // Inbox items about settings link here with panel=settings.
  await page.goto('/database?target=e2e-guard&panel=settings')
  const pane = page.getByRole('region', { name: 'Monitoring, e2e-guard' })
  await expect(pane).toBeVisible()
  await expect(twoPane(page)).toHaveAttribute('data-open', 'true')
  await expect(
    pane.getByRole('switch', { name: 'Monitor this database' })
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(pane).toHaveCount(0)
  await expect(page).not.toHaveURL(/panel=settings/)
})

test('sidebar links leave the database page while its streams are open', async ({
  page,
}) => {
  // The Database page holds the pulse, inbox and discovery streams. Every page after it
  // must replace it, not leave it on screen while the next page's code waits.
  await page.goto('/database?target=e2e-guard')
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()
  const sidebar = page.locator('#app-sidebar')
  for (const [link, heading] of [
    ['Ask', 'Ask'],
    ['Readyset Platform', 'Readyset Platform'],
    ['Inbox', 'Inbox'],
  ] as const) {
    await sidebar.getByRole('link', { name: new RegExp(`^${link}`) }).click()
    await expect(
      page.getByRole('heading', { level: 1, name: heading })
    ).toBeVisible()
  }
})

test('inbox opens an item, resolves it and keeps it under Closed', async ({
  page,
}) => {
  await page.goto('/inbox')
  await expect(
    page.getByRole('heading', { level: 1, name: 'Inbox' })
  ).toBeVisible()
  // The filter row: Open and Closed with their counts, then the database.
  const filters = page.getByRole('group', { name: 'Filters' })
  await expect(
    filters.getByRole('radio', { name: /^Open \d+$/, checked: true })
  ).toBeVisible()
  await expect(
    filters.getByRole('button', { name: 'Database: All databases' })
  ).toBeVisible()
  const actNow = page.getByRole('region', { name: 'Act now' })
  const alertName = 'Orders lookup is 3x slower'
  const row = actNow.getByRole('button', { name: alertName })
  await expect(row).toBeVisible()
  // The database sits under the time at the row's right edge.
  await expect(row.getByTestId('inbox-item-database')).toHaveText('e2e-guard')
  await expect(
    actNow.getByRole('button', { name: 'Stuck query' })
  ).toBeVisible()
  // Closed items live behind Closed.
  await expect(page.getByRole('region', { name: 'Closed today' })).toHaveCount(
    0
  )
  // Digest-only findings never reach the inbox.
  await expect(page.getByText('Query improved')).toHaveCount(0)
  // Nothing is selected until a row is picked.
  await expect(
    page.getByRole('region', { name: 'Nothing selected' })
  ).toBeVisible()

  await row.click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  await expect(page).toHaveURL(/item=\d+/)
  const detail = page.locator('[data-inbox-detail]')
  await expect(
    detail.getByRole('heading', { level: 2, name: alertName })
  ).toBeVisible()
  await expect(detail).toContainText('Act now')
  // The Inbox's verbs are Resolve, Reopen and Mute; set aside and dismiss belong to Actions.
  await detail
    .getByRole('button', { name: `More actions for ${alertName}` })
    .click()
  await expect(
    page.getByRole('menuitem', { name: 'Mark resolved' })
  ).toBeVisible()
  await expect(
    page.getByRole('menuitem', { name: /Snooze|Dismiss/ })
  ).toHaveCount(0)
  await page.keyboard.press('Escape')

  // Resolve closes it: it leaves Act now.
  await detail.getByRole('button', { name: 'Resolve', exact: true }).click()
  await expect(actNow.getByRole('button', { name: alertName })).toHaveCount(0)
  // The Closed view keeps it for a week, under the day it closed, and can reopen it.
  await filters.getByRole('radio', { name: /^Closed/ }).click()
  await expect(page).toHaveURL(/state=closed/)
  const closed = page
    .getByRole('region', { name: 'Today' })
    .getByRole('button', { name: alertName })
  await expect(closed).toBeVisible()
  await expect(
    page.getByText('Closed items stay 7 days.').first()
  ).toBeVisible()
  await closed.click()
  await detail.getByRole('button', { name: 'Reopen', exact: true }).click()
  await filters.getByRole('radio', { name: /^Open/ }).click()
  await expect(actNow.getByRole('button', { name: alertName })).toBeVisible()
})

test('inbox items say what to do and open the exact place', async ({
  page,
  browserErrors,
}) => {
  // A query only monitoring has seen has no Query Library row for the detail to load.
  acceptBrowserError(browserErrors, NOT_FOUND)
  await page.goto('/inbox')
  const actNow = page.getByRole('region', { name: 'Act now' })
  await actNow.getByRole('button', { name: 'Stuck query' }).click()
  const detail = page.locator('[data-inbox-detail]')
  // The steps are numbered, each with its SQL to copy.
  const steps = detail.getByRole('region', { name: 'What to do' })
  await expect(steps).toBeVisible()
  await expect(steps).toContainText('pg_cancel_backend')
  await expect(
    steps.getByRole('button', { name: 'Copy' }).first()
  ).toBeVisible()
  // The header's one solid action opens the query on the Database page.
  await detail.getByRole('link', { name: /^Open/ }).first().click()
  await expect(page).toHaveURL(/\/database\?.*query=c0ffee0e2e00/)

  await page.goto('/inbox')
  await page
    .getByRole('region', { name: 'Queue' })
    .getByRole('button', { name: 'Daily report ready' })
    .click()
  await detail.getByRole('link', { name: 'Open report' }).click()
  await expect(page).toHaveURL(/\/database\?.*view=report/)
  await expect(
    page.getByRole('heading', { name: 'Daily report' })
  ).toBeVisible()
})

test('the daily report reads as one document with its menu', async ({
  page,
}) => {
  await page.goto('/database?target=e2e-guard&view=report')
  await expect(
    page.getByRole('heading', { name: 'Daily report' })
  ).toBeVisible()
  const report = page.getByTestId('report-document')
  await expect(report).toBeVisible()
  await expect(report.getByRole('region', { name: 'The day' })).toBeVisible()
  // What to change lives on Actions; the report points there.
  await expect(
    page
      .getByTestId('report-actions-now')
      .getByRole('button', { name: 'Open Actions' })
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()

  // The day menu lists the reports; the kebab holds print and export.
  await page.getByTestId('day-picker-trigger').click()
  await expect(page.getByRole('menuitem').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Report menu' }).click()
  for (const name of ['Print', 'Copy as Markdown'])
    await expect(page.getByRole('menuitem', { name })).toBeVisible()
  await expect(
    page.getByRole('menuitem', { name: 'Run the report now' })
  ).toHaveCount(0)
  const download = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: 'Download CSV' }).click()
  expect((await download).suggestedFilename()).toMatch(/\.csv$/)
})

test('the report switches to Detailed and remembers the choice', async ({
  page,
}) => {
  await page.goto('/database?target=e2e-guard&view=report')
  const report = page.getByTestId('report-document')
  await expect(report).toHaveAttribute('data-view', 'quick')
  const views = page.getByRole('tablist', { name: 'Report view' })
  try {
    await views.getByRole('tab', { name: 'Detailed' }).click()
    await expect(report).toHaveAttribute('data-view', 'detailed')
    await expect(
      report.getByRole('region', { name: 'What changed' })
    ).toBeVisible()
    await expect(
      report.getByRole('region', { name: 'Settings audit' })
    ).toBeVisible()
    // The choice is the report_view preference: a reload opens on Detailed.
    await page.reload()
    await expect(page.getByTestId('report-document')).toHaveAttribute(
      'data-view',
      'detailed'
    )
  } finally {
    await page.request.put('/api/settings/preferences', {
      data: { report_view: 'quick' },
    })
  }
})

test('an old benchmark link opens the query and offers the benchmark', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(browserErrors, NOT_FOUND)
  // The old drawer's bench/select params are rewritten to the open query.
  await page.goto('/database?target=e2e-guard&bench=compare&select=orders-e2e')
  await expect(page).toHaveURL(/query=orders-e2e/)
  await expect(page).not.toHaveURL(/bench=|select=/)
  await expect(twoPane(page)).toHaveAttribute('data-open', 'true')
  await page.keyboard.press('Escape')
  await expect(twoPane(page)).not.toHaveAttribute('data-open', 'true')
  // A scan's list has no toolbar. On a monitored database every fix's benchmark sits over
  // the list.
  await page.goto('/database?monitorFixture=ca-database&view=live')
  await page
    .getByTestId('query-section')
    .getByRole('button', { name: 'Benchmark', exact: true })
    .click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByRole('heading', { name: /^Benchmark .* with fixes$/ })
  ).toBeVisible()
  await expect(
    dialog.getByRole('button', { name: 'Run benchmark' })
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

test('Activity lists what RDST did, filters by event and exports', async ({
  page,
}) => {
  await page.goto('/')
  await page
    .locator('#app-sidebar')
    .getByRole('link', { name: /^Activity/ })
    .click()
  await expect(page).toHaveURL(/\/agent-activity/)
  await expect(
    page.getByRole('heading', { level: 1, name: 'Activity' })
  ).toBeVisible()

  // One line per thing done, newest day first; checks fold into one line a day
  // per database, so the folded line is read on the first database.
  await expect(
    page.getByRole('link', { name: /Analyzed SELECT on orders by customer_id/ })
  ).toBeVisible()
  const checks = page
    .getByRole('link', { name: /Checked for changes 3 checks/ })
    .first()
  await expect(checks).toBeVisible()
  await expect(page.getByText('Kept 90 days.')).toBeVisible()

  await page.getByRole('button', { name: 'Events: All events' }).click()
  await page.getByRole('menuitem', { name: 'Deep analysis' }).click()
  await expect(page).toHaveURL(/action=deep_analysis/)
  await expect(checks).toHaveCount(0)

  // The export sits behind the kebab, takes a time range and keeps the filters.
  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Export CSV' }).click()
  const dialog = page.getByRole('dialog', { name: 'Export agent activity' })
  await dialog.getByRole('radio', { name: 'Today' }).click()
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    dialog.getByRole('button', { name: 'Download CSV' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(
    /^rdst-agent-activity-from-\d{4}-\d{2}-\d{2}-to-\d{4}-\d{2}-\d{2}\.csv$/
  )
})

test('an Inbox item opens the Database page on its time frame', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(browserErrors, NOT_FOUND)
  await page.goto('/inbox')
  // One burst, one item, over the two queries whose traffic jumped.
  await page
    .getByRole('region', { name: 'Queue' })
    .getByRole('button', { name: 'Traffic jumped on 2 queries' })
    .click()
  const detail = page.locator('[data-inbox-detail]')
  await expect(detail).toBeVisible()
  // Its place is the Database page zoomed to when it happened.
  await detail.getByRole('link', { name: /^Open/ }).first().click()
  await expect(page).toHaveURL(/\/database\?.*zoom=\d+-\d+/)
  await expect(page).toHaveURL(/from=inbox/)
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()
})

test('a live refresh updates the database page in place, without layout shift', async ({
  page,
}) => {
  await mockPrerequisites(page, 'e2e-guard')
  await page.clock.install()
  await page.goto('/database?target=e2e-guard&view=queries')
  await expect(page.getByTestId('query-row').first()).toBeVisible()
  await page.evaluate(() => {
    const state = window as Window & {
      liveRefresh?: { cls: number; fetches: number; rows: Element[] }
    }
    const watch = {
      cls: 0,
      fetches: 0,
      rows: [...document.querySelectorAll('[data-testid=query-row]')],
    }
    state.liveRefresh = watch
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as (PerformanceEntry & {
        value: number
        hadRecentInput: boolean
      })[])
        if (!entry.hadRecentInput) watch.cls += entry.value
    }).observe({ type: 'layout-shift' })
    new PerformanceObserver((list) => {
      watch.fetches += list
        .getEntries()
        .filter((entry) => entry.name.includes('/api/monitor/')).length
    }).observe({ type: 'resource' })
  })
  // The page's readings refetch every minute.
  await page.clock.fastForward(61_000)
  const read = () =>
    page.evaluate(() => {
      const watch = (
        window as Window & {
          liveRefresh?: { cls: number; fetches: number; rows: Element[] }
        }
      ).liveRefresh
      return {
        cls: watch?.cls ?? 1,
        fetches: watch?.fetches ?? 0,
        kept: watch?.rows.filter((row) => row.isConnected).length ?? 0,
        rows: watch?.rows.length ?? -1,
      }
    })
  await expect.poll(async () => (await read()).fetches).toBeGreaterThan(0)
  await page.waitForTimeout(1500)
  const after = await read()
  // Rows keep their nodes and nothing on the page moves.
  expect(after.kept).toBe(after.rows)
  expect(after.cls).toBeLessThan(0.01)
})

test('the settings pane turns monitoring off after a confirmation', async ({
  page,
}) => {
  await page.request.put('/api/monitor/targets/e2e-guard/settings', {
    data: { enabled: true },
  })
  await mockPrerequisites(page, 'e2e-guard')
  await page.goto('/database?target=e2e-guard&panel=settings')
  const pane = page.getByRole('region', { name: 'Monitoring, e2e-guard' })
  await expect(
    pane.getByRole('switch', { name: 'Monitor this database' })
  ).toBeChecked()
  await pane.getByRole('button', { name: 'Stop monitoring' }).click()
  const confirmation = page.getByRole('dialog')
  await expect(confirmation).toContainText('Turn off monitoring for e2e-guard?')
  await confirmation
    .getByRole('button', { name: 'Turn off monitoring', exact: true })
    .click()
  await expect(
    page.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeVisible()
  await expect(page.locator('[data-page-bar]')).toContainText('Not monitored')
})
