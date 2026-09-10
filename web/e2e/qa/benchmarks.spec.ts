/**
 * Benchmarks regression suite: the /cache workspace's two modes, Compare
 * against Readyset and Load test.
 *
 * Consolidated from the recorded audit flows. Every test asserts the settled
 * behaviour and names the MASTER finding it guards; a finding that is still
 * open is kept as a `test.fixme` so the debt stays visible without turning the
 * suite red.
 */
import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  expect,
  mainContent,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  compareQueryList,
  comparisonEvents,
  comparisonFailure,
  DESKTOP,
  layoutProblems,
  NARROW,
  PHONE,
  prepareQueries,
  readySandbox,
  selectQueryByHash,
  settle,
  shot,
} from './_helpers'

const AGGREGATE =
  'SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id'
const SESSIONS = 'SELECT * FROM sessions WHERE expires_at > now()'
const USERS = 'SELECT id, email FROM users WHERE account_id = 7'
const PRODUCTS = 'SELECT p.id, p.name FROM products p'
const INVENTORY = 'SELECT i.id FROM inventory i WHERE i.stock > 0'

/** A 404 from an endpoint a stubbed run never registers is not a defect. */
const MISSING_RESOURCE =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

const LOAD_RUN_ID = 'benchmarks-qa-load-test'

/**
 * Half the default 30-second measurement window: the point at which a running
 * comparison steps its load up.
 */
const STEP_UP_SECONDS = 15

test.use({ viewport: DESKTOP })

/**
 * A comparison that settles on its opening load. The batch PATCHes the run's
 * concurrency once a running query passes the halfway mark, so a fixture that
 * reports a later elapsed time and completes in the same tick leaves that
 * PATCH to land on a finished run, which answers 409. Keeping every sample
 * inside the first half settles the run without one.
 */
function settledComparison(query: string, options?: { speedup?: number }) {
  const fixture = comparisonEvents(query, options)
  return {
    ...fixture,
    events: fixture.events.filter(
      (event) =>
        event.type === 'cache_compare_complete' ||
        Number(event.elapsed_seconds) < STEP_UP_SECONDS
    ),
  }
}

/** The run summary's parameter-readiness value and the tone it is painted in. */
async function parameterReadiness(page: Page) {
  const value = mainContent(page)
    .getByText('Parameter readiness', { exact: true })
    .locator('xpath=following-sibling::*[1]')
  return {
    text: (await value.innerText()).trim(),
    color: await value.evaluate((el) => getComputedStyle(el).color),
  }
}

/** Confirm whichever run dialog is open, typing the target name if asked. */
async function confirmRunDialog(page: Page) {
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const typed = dialog.getByPlaceholder('e2e-guard')
  if ((await typed.count()) > 0) await typed.fill('e2e-guard')
  await dialog
    .getByRole('button', {
      name: /^(Start comparison|Run load test|Run against remote)$/,
    })
    .click()
}

/**
 * A settled chip's tone and its icon. Tags carry `transition-all`, so a chip
 * read in the tick it changed state is still crossfading out of the previous
 * tone; let that finish before sampling the colour.
 */
async function chipStyle(page: Page, label: string) {
  await settle(page, 400)
  return mainContent(page)
    .getByText(label, { exact: true })
    .first()
    .evaluate((el) => {
      const chip = (el.closest('[class*="rounded-sm"],[class*="rounded-md"]') ??
        el) as HTMLElement
      const style = getComputedStyle(chip)
      const icon = chip.querySelector('use')?.getAttribute('href') ?? 'NO ICON'
      return `${style.backgroundColor} / ${style.color} / ${icon}`
    })
}

/** The sprite reference a button's icon resolves to, or `NO ICON`. */
function buttonIcon(page: Page, name: string) {
  return page
    .getByRole('button', { name: new RegExp(`^${name}`) })
    .evaluate(
      (el) => el.querySelector('use')?.getAttribute('href') ?? 'NO ICON'
    )
}

function sse(event: string, data: Record<string, unknown>) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/**
 * Drive a load test through the background-run API rather than the fake
 * service, so each phase can be held on screen for exactly one assertion.
 */
async function mockLoadTestRun(page: Page, events: string[]) {
  await page.route('**/api/query-registry/load-test-runs', (route) =>
    route.fulfill({ json: { run_id: LOAD_RUN_ID } })
  )
  await page.route(new RegExp(`/api/runs/${LOAD_RUN_ID}$`), (route) =>
    route.fulfill({
      json: {
        run_id: LOAD_RUN_ID,
        kind: 'load_test',
        target: 'e2e-guard',
        status: 'running',
        last_seq: 0,
        metadata: {},
      },
    })
  )
  await page.route(new RegExp(`/api/runs/${LOAD_RUN_ID}/events`), (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: events.join(''),
    })
  )
}

async function selectFirstLoadTestQuery(page: Page) {
  await page
    .getByRole('region', { name: 'Queries available for load testing' })
    .getByRole('button', { name: /^Select / })
    .first()
    .click()
}

const laneQuery = {
  query_hash: 'q1hash',
  query_name: 'Orders by customer',
  executions: 900,
  successes: 900,
  failures: 0,
  avg_ms: 11,
  min_ms: 4,
  max_ms: 43,
  p50_ms: 10,
  p95_ms: 19,
  p99_ms: 31,
  lanes: {
    origin: { successes: 450, failures: 0, avg_ms: 18, p95_ms: 29, p99_ms: 40 },
    readyset: {
      successes: 450,
      failures: 0,
      avg_ms: 2.1,
      p95_ms: 3.4,
      p99_ms: 5,
    },
  },
}

test.describe('Compare', () => {
  /** MG-16 - the mode names itself the same way in the tab and on its card. */
  test('names the mode and its promise consistently', async ({ page }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE])

    await page.goto('/cache')
    await expect(
      page.getByRole('tab', { name: 'Compare against Readyset' })
    ).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Load test' })).toBeVisible()
    await expect(
      mainContent(page).getByText(
        'See how much faster these queries are with Readyset.'
      )
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Run comparison' })
    ).toBeDisabled()
  })

  /** D-22 - parameter readiness stays neutral while nothing is selected. */
  test('reports readiness only once queries are selected', async ({ page }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [
      AGGREGATE,
      SESSIONS,
      USERS,
      PRODUCTS,
      INVENTORY,
    ])

    await page.goto('/cache')
    await expect(
      mainContent(page).getByText('No queries selected')
    ).toBeVisible()
    await expect(mainContent(page).getByText('0 selected')).toBeVisible()
    const empty = await parameterReadiness(page)
    expect(empty.text).toBe('No queries selected')

    // Select-all stops at the four-query cap, leaving the fifth card inert
    // rather than silently dropping the click.
    await page.getByRole('button', { name: 'Select all' }).click()
    await expect(mainContent(page).getByText('4 selected')).toBeVisible()

    // The registry parameterises each stored literal, so the selection carries
    // observed values and the row states how many are ready -- in a tone the
    // empty summary never reaches for.
    const selected = await parameterReadiness(page)
    expect(selected.text).toMatch(/^\d+ ready$/)
    expect(selected.color).not.toEqual(empty.color)
    const remaining = compareQueryList(page).getByRole('button', {
      name: /^Select /,
    })
    await expect(remaining).toHaveCount(1)
    await expect(remaining.first()).toHaveAttribute('aria-disabled', 'true')
  })

  /** D-02 - the settled verdict band renders every icon it declares. */
  test('settled verdict band carries its action icons', async ({ page }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
      cache_compare: [settledComparison(AGGREGATE, { speedup: 4 })],
    })
    const [aggregate] = await prepareQueries(page, [AGGREGATE])
    await mockConnectivityOk(page)

    await page.goto('/cache')
    await selectQueryByHash(page, aggregate)
    await page.getByRole('button', { name: 'Run comparison' }).click()
    await confirmRunDialog(page)

    await expect(page.getByText(/faster with Readyset/).first()).toBeVisible({
      timeout: 30_000,
    })
    for (const name of ['History', 'Adjust', 'Run again']) {
      expect(`${name}: ${await buttonIcon(page, name)}`).not.toContain(
        'NO ICON'
      )
    }
    await shot(page, 'benchmarks-compare-settled')
  })

  /** D-04 - success and shortfall paint distinct tones, each with an icon. */
  test('paints compared and unsupported queries in distinct tones', async ({
    page,
  }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
      cache_compare: [
        settledComparison(AGGREGATE, { speedup: 4 }),
        comparisonFailure('This query is unsupported by Readyset'),
      ],
    })
    await prepareQueries(page, [AGGREGATE, SESSIONS])
    await mockConnectivityOk(page)

    await page.goto('/cache')
    await page.getByRole('button', { name: 'Select all' }).click()
    await page.getByRole('button', { name: 'Run comparison' }).click()
    await confirmRunDialog(page)

    await expect(
      mainContent(page).getByText('2 of 2 done').first()
    ).toBeVisible({ timeout: 15_000 })
    const compared = await chipStyle(page, 'Compared')
    const unsupported = await chipStyle(page, 'Unsupported')
    expect(compared).not.toEqual(unsupported)
    expect(compared).not.toContain('NO ICON')
    expect(unsupported).not.toContain('NO ICON')

    // The batch's own tag is a third tone, and carries an icon too.
    const batch = await chipStyle(page, 'Completed with errors')
    expect(batch).not.toContain('NO ICON')
    expect(batch).not.toEqual(compared)
  })

  /** D-09 - a query that is still warming up does not claim a lost result. */
  test('a starting query waits for its first sample', async ({ page }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
      cache_compare: [
        comparisonEvents(AGGREGATE, { startDelayMs: 4000, sampleDelayMs: 800 }),
      ],
    })
    const [aggregate] = await prepareQueries(page, [AGGREGATE])
    await mockConnectivityOk(page)

    await page.goto('/cache')
    await selectQueryByHash(page, aggregate)
    await page.getByRole('button', { name: 'Run comparison' }).click()
    await confirmRunDialog(page)

    await expect(page.getByText('Preparing comparison')).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page.getByText('No measurement was produced for this query.')
    ).toHaveCount(0)
    await expect(page.getByText('Waiting for the first sample.')).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Stop comparison' })
    ).toBeVisible()
  })

  /** D-05, D-06 - a failing status check settles on the error and stops polling. */
  test('a failing sandbox status check settles and stops retrying', async ({
    page,
    browserErrors,
  }) => {
    setBackendFixtures({
      sandbox_diagnostics: [
        {
          error: { status: 503, detail: 'sandbox manager offline' },
          repeat: true,
        },
      ],
    })
    await prepareQueries(page, [AGGREGATE])

    let statusRequests = 0
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/cache/sandbox') {
        statusRequests += 1
      }
    })

    await page.goto('/cache')
    const errorPanel = mainContent(page).getByText(
      "Cache status couldn't be checked"
    )
    await expect(errorPanel).toBeVisible({ timeout: 20_000 })

    // Longer than the poll interval the page used before it settled, so a
    // resumed poll would show up as another request and another skeleton.
    const settledRequests = statusRequests
    await settle(page, 8000)
    await expect(errorPanel).toBeVisible()
    await expect(page.getByLabel('Loading comparison')).toHaveCount(0)
    expect(statusRequests).toBe(settledRequests)

    // Each attempt against the down endpoint logs its own 503.
    const unrelated = browserErrors.filter((message) => !/503/.test(message))
    for (const message of new Set(browserErrors)) {
      if (/503/.test(message)) acceptBrowserError(browserErrors, message)
    }
    browserErrors.splice(0, browserErrors.length, ...unrelated)
  })

  /**
   * D-15 - the compare run carries the display name the setup card showed, so
   * a query keeps one name from selection through to its result.
   */
  test('D-15 - a query keeps its name once the run starts', async ({
    page,
  }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
      cache_compare: [comparisonEvents(AGGREGATE, { speedup: 4 })],
    })
    const [aggregate] = await prepareQueries(page, [AGGREGATE])
    await mockConnectivityOk(page)

    await page.goto('/cache')
    await expect(
      mainContent(page).getByText('COUNT on orders').first()
    ).toBeVisible()
    await selectQueryByHash(page, aggregate)
    await page.getByRole('button', { name: 'Run comparison' }).click()
    await confirmRunDialog(page)

    await expect(page.getByText(/faster with Readyset/).first()).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.locator('[data-cache-id]').first()).toContainText(
      'COUNT on orders'
    )
  })
})

test.describe('Load test', () => {
  /** D-13 - the lane that proves the fix is armed when the mode opens. */
  test('opens with the Readyset lane armed', async ({ page }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE, USERS])

    await page.goto('/cache?view=load-test')
    await expect(
      mainContent(page).getByText(
        'See how your database behaves under sustained read traffic.'
      )
    ).toBeVisible()
    const lane = page.getByRole('switch', { name: 'Compare against Readyset' })
    await expect(lane).toBeVisible()
    await expect(lane).toHaveAttribute('aria-checked', 'true')
    await expect(
      mainContent(page).getByText(
        'Run against your database and Readyset side by side.'
      )
    ).toBeVisible()
  })

  /** D-11 - a missing Docker is disclosed before the Readyset lane runs. */
  test('discloses a missing Docker before the run', async ({ page }) => {
    setBackendFixtures({
      docker_runtime: [
        { value: { installed: false, running: false }, repeat: true },
      ],
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE])

    // Compare hard-gates on the same sandbox, and says so on arrival.
    await page.goto('/cache')
    await expect(
      page.getByRole('heading', { name: 'Docker is required for comparisons' })
    ).toBeVisible()

    await page.getByRole('tab', { name: 'Load test' }).click()
    await expect(
      mainContent(page).getByText(
        'Docker is required to compare against Readyset'
      )
    ).toBeVisible()
    const lane = page.getByRole('switch', { name: 'Compare against Readyset' })
    await expect(lane).toBeDisabled()
    await expect(lane).toHaveAttribute('aria-checked', 'false')
  })

  /** D-18 - the cache warm-up reports determinate progress and an elapsed clock. */
  test('cache preparation shows measurable progress', async ({
    page,
    browserErrors,
  }) => {
    acceptBrowserError(browserErrors, MISSING_RESOURCE)
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE])
    await mockConnectivityOk(page)
    await mockLoadTestRun(page, [
      sse('progress', {
        seq: 1,
        phase: 'preparing',
        prepared_count: 1,
        prepare_total: 3,
        elapsed_seconds: 0,
        total_executions: 0,
        total_successes: 0,
        total_failures: 0,
        qps: 0,
        queries: [],
      }),
    ])

    await page.goto('/cache?view=load-test')
    await selectFirstLoadTestQuery(page)
    await page.getByRole('button', { name: 'Run load test' }).click()
    await confirmRunDialog(page)

    await expect(
      page.getByText(/Preparing Readyset caches/).first()
    ).toBeVisible({ timeout: 20_000 })
    await expect(
      page.getByRole('progressbar', { name: 'Cache preparation' })
    ).toBeVisible()
    await expect(
      mainContent(page).getByText('1 of 3 queries cached')
    ).toBeVisible()
    await expect(mainContent(page).getByText(/elapsed/)).toBeVisible()
  })

  /** D-14 - the paced comparative card leads with the metric it measured. */
  test('a paced comparison is stated in latency', async ({
    page,
    browserErrors,
  }) => {
    acceptBrowserError(browserErrors, MISSING_RESOURCE)
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE])
    await mockConnectivityOk(page)
    await mockLoadTestRun(page, [
      sse('complete', {
        seq: 1,
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 900,
        total_successes: 900,
        total_failures: 0,
        qps: 30,
        queries: [laneQuery],
        lanes_run: ['origin', 'readyset'],
        readyset_setup: { status: 'ok' },
        skipped_queries: [
          {
            query_hash: 'skipped1',
            query_name: 'Sessions sweep',
            reason: 'No stored value for parameter $1',
          },
        ],
      }),
      sse('run_end', { seq: 2, status: 'done' }),
    ])

    await page.goto('/cache?view=load-test')
    await selectFirstLoadTestQuery(page)
    await page.getByRole('button', { name: 'Run load test' }).click()
    await confirmRunDialog(page)

    await expect(
      mainContent(page)
        .getByText(/faster with Readyset/)
        .first()
    ).toBeVisible({ timeout: 20_000 })
    await expect(
      mainContent(page).getByText(
        'The same workload ran against both lanes side by side at the same paced rate; the comparison is p95 latency.'
      )
    ).toBeVisible()
    // A query excluded before the run is reported as skipped, not as a failure.
    await expect(mainContent(page).getByText('1 query skipped')).toBeVisible()
    await expect(mainContent(page).getByText('Sessions sweep')).toBeVisible()
  })

  /** D-19 - a failed run leads with the reason instead of a grid of zeros. */
  test('a failed run leads with its reason', async ({
    page,
    browserErrors,
  }) => {
    acceptBrowserError(browserErrors, MISSING_RESOURCE)
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE])
    await mockConnectivityOk(page)
    await mockLoadTestRun(page, [
      sse('error', {
        seq: 1,
        message: 'Database connection closed unexpectedly.',
        category: 'database',
      }),
      sse('run_end', { seq: 2, status: 'failed' }),
    ])

    await page.goto('/cache?view=load-test')
    await selectFirstLoadTestQuery(page)
    await page.getByRole('button', { name: 'Run load test' }).click()
    await confirmRunDialog(page)

    await expect(
      mainContent(page).getByText('Load test failed').first()
    ).toBeVisible({
      timeout: 20_000,
    })
    await expect(
      mainContent(page).getByText('The load test could not complete')
    ).toBeVisible()
    await expect(
      mainContent(page).getByText('Database connection closed unexpectedly.')
    ).toBeVisible()
    // Nothing ran, so there is no metric grid and no pacing explainer to read.
    await expect(mainContent(page).getByText('p95 latency')).toHaveCount(0)
    await expect(
      mainContent(page).getByText('This run is intentionally paced')
    ).toHaveCount(0)
  })

  /** D-03 - every control in the setup card stays reachable at desktop size. */
  test('setup card does not clip its own controls', async ({ page }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE, USERS])

    await page.goto('/cache?view=load-test')
    await expect(
      page.getByRole('switch', { name: 'Compare against Readyset' })
    ).toBeVisible()
    const hiddenPixels = await page.evaluate(() => {
      const nodes = Array.from(
        document.querySelectorAll('#main-content *')
      ) as HTMLElement[]
      for (const el of nodes) {
        const overflow = el.scrollHeight - el.clientHeight
        if (overflow > 24 && getComputedStyle(el).overflowY === 'hidden') {
          return overflow
        }
      }
      return 0
    })
    expect(hiddenPixels).toBe(0)
    await expect(
      page.getByRole('button', { name: 'Advanced load settings' })
    ).toBeVisible()
  })
})

test.describe('Setup layout', () => {
  /** D-16, D-17 - both setup forms stack and stay whole at 1024 and 390. */
  test('setup forms hold together at the narrow and phone widths', async ({
    page,
  }) => {
    setBackendFixtures({
      sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    })
    await prepareQueries(page, [AGGREGATE, USERS, PRODUCTS])

    for (const view of ['compare', 'load-test'] as const) {
      for (const size of [NARROW, PHONE]) {
        await page.setViewportSize(size)
        await page.goto(`/cache?view=${view}`)
        await settle(page, 1200)
        const problems = await layoutProblems(page)
        const where = `${view} setup at ${size.width}`
        expect(
          problems.filter((problem) => problem.kind === 'occluded'),
          where
        ).toEqual([])
        expect(
          problems.filter((problem) => problem.kind === 'offscreen'),
          where
        ).toEqual([])
      }
    }
    await shot(page, 'benchmarks-setup-390')
  })
})
