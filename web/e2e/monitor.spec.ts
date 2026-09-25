import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

const ORDERS_SQL = 'SELECT id, total FROM orders WHERE customer_id = $1'

// Each case seeds a full incident history through the real on-disk store.
test.describe.configure({ timeout: 90_000 })

test.beforeEach(async ({ page }) => {
  await configureTestTarget(page, { hasPassword: true })
  const seeded = await page.request.post('/api/testing/monitor/seed')
  expect(seeded.ok()).toBe(true)
})

test('database page shows live health and expandable query history', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await mockPrerequisites(page, 'e2e-guard')
  // The classic per-target URL still lands on the Database page for that target.
  await page.goto('/db/e2e-guard')
  await expect(page).toHaveURL(/\/database/)
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()
  await expect(page.getByTestId('load-hero')).toBeVisible()
  const health = page.getByRole('region', { name: 'Database health' })
  await expect(
    health.getByRole('button', { name: /^Connections/ })
  ).toBeVisible()
  await expect(
    page.getByRole('radio', { name: '15m', exact: true })
  ).toBeVisible()

  // A row shows its fix or reason when it has one, else its SQL.
  const row = page.locator('[id="query-orders-e2e"]')
  await expect(
    row.getByTestId('row-reason').or(row.getByRole('code'))
  ).toBeVisible()
  await row.getByRole('button', { name: 'Details' }).click()
  const detail = page.getByTestId('query-detail')
  for (const name of ['Latency and calls', 'SQL', 'Deep analysis'])
    await expect(detail.getByRole('region', { name })).toBeVisible()
  await expect(detail.getByRole('region', { name: 'SQL' })).toContainText(
    'FROM orders'
  )
  // Benchmarks shows once there's a run; the Benchmark button starts one.
  await expect(
    detail.getByRole('button', { name: 'Benchmark', exact: true })
  ).toBeVisible()
  await expect(page).toHaveURL(/query=orders-e2e/)

  await page.getByRole('button', { name: 'Turn on monitoring' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText(
    "You're signed out, so monitoring runs locally"
  )
  await expect(dialog).toContainText('Include deep analysis')
  // Signed out, the dialog asks for sign-in to turn on AI; local monitoring still starts.
  await expect(
    dialog.getByRole('heading', { name: 'AI monitoring' })
  ).toBeVisible()
  await expect(dialog.getByTestId('ai-monitoring-sign-in')).toContainText(
    'Sign in to turn on AI monitoring'
  )
  await expect(
    dialog.locator('[data-check="pg_stat_statements"][data-status="ok"]')
  ).toBeVisible()
  const execution = dialog.getByRole('radiogroup', {
    name: 'When RDST analyzes a problem query',
  })
  await expect(execution).toBeVisible()
  // EXPLAIN ANALYZE is preselected, with EXPLAIN only as the explained alternative.
  await expect(
    execution.getByRole('radio', { name: /Run EXPLAIN ANALYZE/ })
  ).toBeChecked()
  await expect(
    execution.getByRole('radio', { name: /EXPLAIN only/ })
  ).not.toBeChecked()
  // The actions stay in view however long the checklist is.
  await expect(
    dialog.getByRole('button', { name: /^Turn on/ })
  ).toBeInViewport()
})

test('database page opens Analyze a query from a link', async ({ page }) => {
  await page.goto('/database?target=e2e-guard&panel=analyze')
  const dialog = page.getByRole('dialog', { name: 'Analyze a query' })
  await expect(dialog).toBeVisible()
  const run = dialog.getByRole('button', { name: 'Run deep analysis' })
  await expect(run).toBeDisabled()
  await dialog.getByLabel('SQL').fill('UPDATE orders SET total = 0')
  await expect(dialog).toContainText('changes data')
  await dialog.getByLabel('SQL').fill('SELECT id FROM orders WHERE id = $1')
  await expect(dialog.getByRole('button', { name: 'Next' })).toBeEnabled()
  await page.keyboard.press('Escape')
  await expect(page).not.toHaveURL(/panel=analyze/)
})

test('health tiles and the metric control switch the chart', async ({
  page,
}) => {
  await page.goto('/database?target=e2e-guard')
  await expect(page.getByTestId('load-chart')).toBeVisible()
  const tiles = page.getByRole('region', { name: 'Database health' })
  const connections = tiles.getByRole('button', { name: /^Connections: / })
  // The health tiles switch the chart like the metric control: one selected.
  const metrics = page.getByRole('radiogroup', { name: 'Chart metric' })
  await connections.click()
  await expect(connections).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
  await expect(metrics.getByRole('radio', { checked: true })).toHaveCount(0)
  await metrics.getByRole('radio', { name: 'Queries/s' }).click()
  await expect(connections).toHaveAttribute('aria-pressed', 'false')
  await expect(
    page.getByRole('heading', { name: /^Queries per second, / })
  ).toBeVisible()
  await metrics.getByRole('radio', { name: 'Time spent' }).click()
  await expect(metrics.getByRole('radio', { name: 'Time spent' })).toBeChecked()
  await expect(
    page.getByRole('heading', { name: /^Time spent, / })
  ).toBeVisible()
})

test('a monitored database with no queries yet waits for real traffic', async ({
  page,
  browserErrors,
}) => {
  // The live stream is aborted below so the page runs on the mocked polls alone; the browser
  // logs that aborted request as a failed resource load.
  acceptBrowserError(browserErrors, 'Failed to load resource: net::ERR_FAILED')
  // Monitoring is on and readings tick, but nothing has run: the page explains how to send
  // it real traffic instead of drawing an empty chart. The served state is mocked; RDST
  // never simulates traffic.
  const now = Math.floor(Date.now() / 1000)
  const on = now - 75
  const first = on + 2
  const ticks: number[] = []
  for (let ts = first; ts <= now; ts += 10) ticks.unshift(ts)
  const minute = (ts: number) => Math.floor(ts / 60) * 60
  const history = {
    first_ts: minute(first),
    last_ts: minute(now),
    span_seconds: minute(now) - minute(first) + 60,
    resolutions: {
      '60': { first_ts: minute(first) },
      '300': { first_ts: null },
      '3600': { first_ts: null },
      '86400': { first_ts: null },
    },
  }
  const base = '**/api/monitor/targets/e2e-guard'
  const patch = (
    glob: string,
    edit: (real: Record<string, unknown>) => unknown
  ) =>
    page.route(glob, async (route) => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: edit(real) })
    })
  await page.route(`${base}/stream**`, (route) => route.abort())
  await patch(`${base}/pulse?**`, () => ({
    pulses: ticks.slice(0, 50).map((ts) => ({
      ts,
      kind: 'collect',
      ok: true,
      detail: 'Read 0 queries',
      next_collect_at: ts + 10,
      state: 'live',
    })),
  }))
  await patch(`${base}/health?**`, (real) => ({
    ...real,
    history,
    series: [],
    markers: [],
  }))
  await patch(`${base}/queries?**`, (real) => ({
    ...real,
    history,
    queries: [],
    total: 0,
    composition: [],
    counts: { all: 0, attention: 0, new: 0, fixed: 0, muted: 0 },
  }))
  await patch(`${base}/settings`, (real) => ({
    ...real,
    enabled: true,
    enabled_at: on,
  }))
  await page.goto('/database?target=e2e-guard')
  const waiting = page.getByTestId('waiting-for-queries')
  await expect(waiting).toContainText('Waiting for queries')
  await expect(waiting).toContainText('test suite')
  await expect(waiting).toContainText('within 10 seconds')
  await expect(page.getByTestId('first-run')).toContainText(
    'Waiting for the database to run some'
  )
  await expect(page.getByTestId('load-hero')).toHaveCount(0)
  await expect(page.getByTestId('query-section')).toHaveCount(0)
})

test('database page opens monitoring settings from a link', async ({
  page,
}) => {
  // Inbox items about settings (storage budget) link here with panel=settings.
  await page.goto('/database?target=e2e-guard&panel=settings')
  const sheet = page.getByRole('dialog', { name: 'Monitoring settings' })
  await expect(sheet).toBeVisible()
  await expect(
    sheet.getByRole('radiogroup', { name: 'Pushes a day' })
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)
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
  const nav = page.getByRole('navigation')
  for (const [link, heading] of [
    ['Ask', 'Ask'],
    ['Try the demo', 'See Readyset Platform in action'],
    ['Inbox', 'Inbox'],
  ] as const) {
    await nav
      .getByRole('link', { name: new RegExp(`^${link}`) })
      .first()
      .click()
    await expect(
      page.getByRole('heading', { level: 1, name: heading })
    ).toBeVisible()
  }
})

test('inbox resolves, reopens and marks alerts seen', async ({ page }) => {
  await page.goto('/inbox')
  await expect(
    page.getByRole('heading', { level: 1, name: 'Inbox' })
  ).toBeVisible()
  const actNow = page.getByRole('region', { name: 'Act now' })
  const alertName = 'Orders lookup is 3x slower'
  const alert = page.getByRole('heading', { name: alertName })
  await expect(actNow.getByRole('heading', { level: 3 })).toHaveText([
    'Orders lookup is 3x slower',
    'Stuck query',
  ])
  // Digest-only findings never reach the inbox.
  await expect(page.getByText('Query improved')).toHaveCount(0)
  // With a mouse, row actions float in on hover.
  const row = actNow.getByRole('article').filter({ has: alert })
  await row.hover()
  await row.getByRole('button', { name: 'Resolve', exact: true }).click()
  await expect(actNow.getByRole('heading', { name: alertName })).toHaveCount(0)
  // Closed alerts list as Recovered on Open for 24 hours, each one reopenable.
  const resolved = page.getByRole('region', {
    name: 'Recovered in the last 24 hours',
  })
  await resolved.getByRole('article').filter({ has: alert }).hover()
  await page.getByRole('button', { name: /^Reopen Orders lookup/ }).click()
  // A measured recovery lists under Recovered, worded as observed.
  await expect(resolved).toContainText(
    'Calls back to normal: 1.1 calls a second.'
  )
  await expect(resolved).toContainText(/Calls back to normal (yesterday )?at /)
  await expect(actNow.getByRole('heading', { name: alertName })).toBeVisible()
  await page.getByRole('button', { name: 'Mark all seen' }).click()
  await expect(page.getByText('Unread', { exact: true })).toHaveCount(0)
  await expect(alert).toBeVisible()
})

test('inbox items say what to do and open the exact place', async ({
  page,
}) => {
  await page.goto('/inbox')
  const actNow = page.getByRole('region', { name: 'Act now' })
  const stuck = actNow
    .getByRole('article')
    .filter({ has: page.getByRole('heading', { name: 'Stuck query' }) })
  // Items start folded to one line; the title opens them.
  await expect(stuck.getByTestId('item-line')).toBeVisible()
  const tiles = stuck.getByRole('region', { name: 'What you can do' })
  await expect(tiles).toHaveCount(0)
  await stuck.getByRole('button', { name: 'Stuck query', exact: true }).click()
  await expect(tiles).toBeVisible()
  // Each kind of step has its own color.
  await expect(tiles.locator('[data-tile-kind]').first()).toBeVisible()
  await expect(
    tiles.getByText('SELECT pg_cancel_backend(<pid>);')
  ).toBeVisible()
  await stuck.hover()
  await stuck.getByRole('button', { name: 'Open query' }).click()
  await expect(page).toHaveURL(/\/database\?.*query=c0ffee0e2e00/)

  await page.goto('/inbox')
  // Reports list at the bottom of Watch, past its first rows.
  const watch = page.getByRole('region', { name: 'Watch' })
  const more = watch.getByRole('button', { name: /^Show \d+ more/ })
  if (await more.count()) await more.click()
  const report = watch
    .getByRole('article')
    .filter({ has: page.getByRole('heading', { name: 'Daily report ready' }) })
  await report.hover()
  await report.getByRole('button', { name: 'Open report' }).click()
  await expect(page).toHaveURL(/\/database\?.*view=report/)
  await expect(page.getByTestId('day-snapshot')).toBeVisible()
})

test('the latest daily snapshot reads as one day, top to bottom', async ({
  page,
}) => {
  await page.goto('/database?target=e2e-guard')
  const latest = page.getByRole('button', {
    name: /^Latest daily snapshot, .*Open the day$/,
  })
  await expect(latest).toBeVisible()
  await latest.click()
  await expect(page).toHaveURL(/\/database\?.*view=report/)
  // A date title, no headline sentence, and a bar to jump between sections.
  await expect(page.getByTestId('snapshot-title')).toHaveText(
    /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), \w+ \d+$/
  )
  await expect(page.getByTestId('day-snapshot')).not.toContainText(
    /written at|night after/
  )
  await expect(page.getByTestId('report-verdict')).toHaveCount(0)
  const sections = page.getByRole('navigation', { name: 'Report sections' })
  await expect(sections).toBeVisible()
  await sections.getByRole('button', { name: /^What happened/ }).click()
  await expect(
    page.getByRole('heading', { name: 'What happened' })
  ).toBeInViewport()
  // Health comes first, then the day's numbers and the day in brief, then database-level
  // actions, the chart, events and queries.
  const order = [
    'Database health',
    'The day in numbers',
    'The day in brief',
    'Database actions',
    'Queries per second',
    'Spikes',
    'What happened',
    'Queries to look at',
  ]
  for (const name of order)
    await expect(page.getByRole('heading', { name })).toBeVisible()
  const headings = await page
    .getByTestId('day-snapshot')
    .locator('h2')
    .allTextContents()
  expect(headings.filter((text) => order.includes(text))).toEqual(order)
  await expect(page.getByTestId('day-chart')).toBeVisible()
  await expect(page.getByTestId('day-number')).toHaveCount(6)
  await expect(page.getByTestId('snapshot-timeline')).toContainText(
    'Got slower'
  )
  const action = page
    .getByTestId('db-action')
    .filter({ hasText: 'Vacuum 1 bloated table' })
  // Actions start as one line; a click opens the evidence and the SQL.
  await expect(action.getByTestId('db-action-detail')).toHaveCount(0)
  await action.getByRole('button').first().click()
  await expect(action).toContainText('18.0% dead')
  await expect(action).toContainText('VACUUM (ANALYZE) public.orders;')
  await expect(page.getByTestId('health-overview')).toBeVisible()
  // A query in what happened opens in place; the Database page is secondary.
  await page
    .getByTestId('snapshot-timeline')
    .locator('[data-query-key]')
    .first()
    .click()
  const panel = page.getByTestId('query-panel')
  await expect(panel).toBeVisible()
  await expect(page).toHaveURL(/view=report/)
  await expect(
    panel.getByRole('button', { name: /Open in Database/ })
  ).toBeVisible()
  // A tile puts its reading on the chart; the metric switch covers the rest.
  await page.getByTestId('day-number').nth(3).click()
  await expect(page.getByTestId('day-chart')).toHaveAttribute(
    'data-view',
    'cache_hit_ratio'
  )
  await page.getByRole('radio', { name: 'Latency' }).click()
  await expect(page.getByTestId('day-chart')).toHaveAttribute(
    'data-view',
    'latency'
  )
  const download = page.waitForEvent('download')
  await page.getByTestId('download-csv').click()
  expect((await download).suggestedFilename()).toMatch(/-queries-.*\.csv$/)
  // The day list and the menu that writes today's snapshot.
  await page.getByTestId('day-picker-trigger').click()
  await expect(page.getByRole('menuitem').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Report menu' }).click()
  await expect(
    page.getByRole('menuitem', { name: "Run today's snapshot now" })
  ).toBeVisible()
})

test('a query benchmarks from its detail, with Readyset as an option', async ({
  page,
  browserErrors,
}) => {
  await page.route('**/api/cache/sandbox', (route) =>
    route.fulfill({
      json: { docker_installed: true, docker_running: true, phase: 'idle' },
    })
  )
  // The monitored query has no Query Library record, so that lookup returns 404.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  // An old drawer link opens the selected query on its Benchmarks section.
  await page.goto('/database?target=e2e-guard&bench=compare&select=orders-e2e')
  await expect(page).toHaveURL(/query=orders-e2e/)
  await expect(page).not.toHaveURL(/bench=|select=/)
  const detail = page.getByTestId('query-detail')
  await expect(detail.getByRole('region', { name: 'Benchmarks' })).toBeVisible()

  await detail
    .getByRole('button', { name: 'Benchmark', exact: true })
    .first()
    .click()
  const dialog = page.getByTestId('benchmark-dialog')
  await expect(
    dialog.getByRole('heading', { name: 'Benchmark queries' })
  ).toBeVisible()
  const picker = dialog.getByRole('region', {
    name: 'Queries for this benchmark',
  })
  await expect(picker).toContainText('Pick queries (1 selected)')
  const selected = picker.locator('[data-query-hash="orders-e2e"]')
  await expect(selected).toContainText(ORDERS_SQL)
  await expect(selected).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('benchmark-load-warning')).toContainText(
    'This runs against your live database and adds load to it while it runs.'
  )
  await expect(dialog.getByRole('radio', { name: /Quick/ })).toBeChecked()
  await dialog.getByRole('radio', { name: /Standard/ }).click()
  await expect(page.getByTestId('benchmark-load-warning')).toContainText(
    'Higher load'
  )
  const destinations = dialog.getByRole('radiogroup', { name: 'Where to run' })
  await expect(
    destinations.getByRole('radio', { name: /Source only/ })
  ).toBeChecked()
  await expect(
    destinations.getByRole('radio', { name: /Source plus Readyset/ })
  ).toBeVisible()
  const readysetOnly = destinations.getByRole('radio', {
    name: /Readyset only/,
  })
  await expect(readysetOnly).toBeEnabled()
  await readysetOnly.click()
  await expect(readysetOnly).toBeChecked()
  await expect(dialog).toContainText(
    'The first Readyset run downloads a small container.'
  )
  await dialog.getByPlaceholder('Enter a representative value').fill('1')
  await expect(
    dialog.getByRole('button', { name: 'Start benchmark' })
  ).toBeEnabled()
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog).toBeHidden()
})

test('agent activity reads as decisions, with statements behind technical details', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Agent activity' }).click()
  await expect(page).toHaveURL(/\/agent-activity/)
  await expect(
    page.getByRole('region', { name: 'What RDST does on your database' })
  ).toContainText('Only reads')

  const analysis = page
    .locator('[data-activity-key]')
    .filter({ hasText: 'The agent analyzed SELECT on orders by customer_id.' })
  await analysis.getByRole('button').first().click()
  const detail = analysis.getByTestId('activity-detail')
  await expect(detail).toContainText('Ran EXPLAIN ANALYZE with a 1.5 s timeout')
  await expect(detail).toContainText('1 parameter sampled from live sessions')
  // Statements and AI calls stay collapsed until asked for.
  const statements = detail.getByRole('region', {
    name: 'Statements on your database (2)',
  })
  await expect(statements).toHaveCount(0)
  await detail.getByRole('button', { name: /Technical details/ }).click()
  await expect(statements).toContainText(
    'SELECT id, total FROM orders WHERE customer_id = ?'
  )
  await expect(
    detail.getByRole('region', { name: 'AI calls (1)' })
  ).toContainText('z-ai/glm-5.3-flash')

  // Attention checks fold into one line per day, which opens to hours, then checks.
  const checks = page
    .locator('[data-activity-key^="attention_check:e2e-guard"]')
    .first()
  await expect(checks).toContainText(
    'The agent ran 3 attention checks and found nothing new to file.'
  )
  await checks.getByRole('button').first().click()
  const hour = checks.locator('[data-activity-key^="attention_check:"]')
  await expect(hour).toHaveCount(1)
  await expect(hour).toContainText('Ran 3 attention checks')
  await hour.getByRole('button').first().click()
  await expect(hour.locator('[data-activity-key^="entry:"]')).toHaveCount(3)

  await page.getByRole('button', { name: 'Action: All actions' }).click()
  await page.getByRole('menuitem', { name: 'Deep analysis' }).click()
  await expect(page).toHaveURL(/action=deep_analysis/)
  await expect(
    page.locator('[data-activity-key^="attention_check"]')
  ).toHaveCount(0)

  // The export takes a time range and keeps the page's filters.
  await page.getByRole('button', { name: 'Export CSV' }).click()
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

test('an Inbox query line opens that query zoomed to when it jumped', async ({
  page,
  browserErrors,
}) => {
  // A query only monitoring has seen has no Query Library row for the detail to load.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await page.goto('/inbox')
  const alert = page.getByRole('article').filter({
    has: page.getByRole('heading', { name: 'Traffic jumped on 2 queries' }),
  })
  // One burst, one item. Its traffic is measured back to normal, so it sits in Recovered as a
  // compact row: the first query, a count of the rest, and the reason it recovered.
  await expect(alert.getByText('and 1 other query')).toBeVisible()
  const first = alert.getByRole('link', {
    name: 'SELECT on sessions by token',
  })
  // The query's link carries the window in which its traffic jumped.
  await expect(first).toHaveAttribute(
    'href',
    /query=0e2ea0000002.*zoom=\d+-\d+.*from=inbox%3A\d+/
  )
  await first.click()

  // The Database page opens that query with the incident window and the item.
  await expect(page).toHaveURL(
    /\/database\?.*query=0e2ea0000002.*zoom=\d+-\d+.*from=inbox%3A\d+/
  )
  const banner = page.getByTestId('inbox-context')
  await expect(banner).toContainText('From your Inbox')
  await expect(banner).toContainText(
    /This query.* (yesterday )?at \d{1,2}:\d{2} [AP]M\./
  )
  await expect(banner).not.toContainText(/One of \d+ queries|down 0%/)
  await expect(banner.getByTestId('inbox-context-state')).toHaveText(
    /^Calls measured back to normal (yesterday )?at \d{1,2}:\d{2} [AP]M\.$/
  )
  await expect(
    page.getByRole('heading', { name: /^Queries per second, from / })
  ).toBeVisible()
  await expect(page.getByTestId('chart-highlight').first()).toBeAttached()
  // The window is the list's filter: a removable chip, every status shown.
  await expect(page.getByTestId('time-frame-chip')).toContainText(
    'From your Inbox'
  )
  await expect(page.getByTestId('filter-status')).toHaveText('Status')

  await banner.getByRole('button', { name: 'Show the last 24 hours' }).click()
  await expect(page).not.toHaveURL(/zoom=/)
  await expect(page).toHaveURL(/range=24h/)
  await banner.getByRole('button', { name: 'Dismiss' }).click()
  await expect(banner).toHaveCount(0)
  await expect(page).not.toHaveURL(/from=/)
})

test('a live refresh updates the database page in place, without layout shift', async ({
  page,
}) => {
  await mockPrerequisites(page, 'e2e-guard')
  await page.clock.install()
  await page.goto('/database?target=e2e-guard')
  await expect(page.getByTestId('monitor-query-row').first()).toBeVisible()
  await expect(page.getByTestId('load-chart')).toBeVisible()
  await page.evaluate(() => {
    const state = window as Window & {
      liveRefresh?: { cls: number; fetches: number; rows: Element[] }
    }
    const watch = {
      cls: 0,
      fetches: 0,
      rows: [...document.querySelectorAll('[data-testid=monitor-query-row]')],
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
  await expect(page.getByTestId('query-card-skeleton')).toHaveCount(0)
})

test('monitoring header offers off and settings without a separate analysis pause', async ({
  page,
}) => {
  await page.request.put('/api/monitor/targets/e2e-guard/settings', {
    data: { enabled: true },
  })
  await mockPrerequisites(page, 'e2e-guard')
  await page.goto('/database?target=e2e-guard')
  await page.getByTestId('monitoring-menu-trigger').click()
  const menu = page.getByTestId('monitoring-menu')
  await expect(
    menu.getByRole('button', { name: 'Monitoring settings' })
  ).toBeVisible()
  await expect(
    menu.getByRole('button', {
      name: /Pause deep analysis|Resume deep analysis/,
    })
  ).toHaveCount(0)
  await menu.getByRole('button', { name: /Turn off monitoring/ }).click()
  const confirmation = page.getByRole('dialog')
  await expect(confirmation).toContainText('Turn off monitoring for e2e-guard?')
  await confirmation
    .getByRole('button', { name: 'Turn off monitoring', exact: true })
    .click()
  await expect(page.getByTestId('monitoring-menu-trigger')).toContainText(
    'Monitoring off'
  )
})
