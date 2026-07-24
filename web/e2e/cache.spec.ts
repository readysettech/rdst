import type { APIRequestContext, Page } from '@playwright/test'
import {
  clearQueryRegistry,
  configureTestTarget,
  consumeBrowserError,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

const directQuery =
  'SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id'
const secondDirectQuery =
  'SELECT product_id, SUM(total) FROM orders GROUP BY product_id'
const parameterizedQuery =
  'SELECT id, email FROM users WHERE account_id = :account_id'

const readySandbox = {
  phase: 'ready',
  current_target: 'e2e-guard',
  generation: 1,
  lease_owner: null,
  lease_purpose: null,
  queued_requests: 0,
  dirty_reason: null,
  failed_target: null,
  last_error: null,
  last_released_at: '2026-07-23T12:00:00+00:00',
  expires_at: '2026-07-24T12:00:00+00:00',
  container_name: 'rdst-readyset-sandbox',
  healthy: true,
}

const originStats = {
  mean: 12,
  median: 11,
  min: 9,
  max: 18,
  p50: 11,
  p95: 17,
  p99: 18,
  stddev: 2,
}

function resultEvent(query: string, speedup = 4) {
  return {
    type: 'cache_run_complete',
    success: true,
    query,
    iterations: 15,
    origin_stats: originStats,
    cache_stats: {
      mean: originStats.mean / speedup,
      median: originStats.median / speedup,
      min: originStats.min / speedup,
      max: originStats.max / speedup,
      p50: originStats.p50 / speedup,
      p95: originStats.p95 / speedup,
      p99: originStats.p99 / speedup,
      stddev: 0.5,
    },
    speedup_mean: speedup,
    speedup_median: speedup,
    improvement_pct: 75,
    winner: 'readyset',
  }
}

async function addQuery(
  request: APIRequestContext,
  sql: string
): Promise<string> {
  const response = await request.post('/api/query-registry', {
    data: { sql, target: 'e2e-guard' },
  })
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as {
    success: boolean
    hash: string
  }
  expect(body.success).toBe(true)
  return body.hash
}

async function prepareSpeedTests(page: Page, queries: string[]) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  const hashes: string[] = []
  for (const query of queries) {
    hashes.push(await addQuery(page.request, query))
  }
  return hashes
}

test('first render shows useful query actions without an empty sandbox diagnostics card', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
  })
  await prepareSpeedTests(page, [directQuery])

  await page.goto('/cache')

  await expect(
    page.getByRole('heading', { name: 'Readyset Comparisons' })
  ).toBeVisible()
  await expect(page.getByTestId('speed-test-query')).toHaveCount(1)
  await expect(
    page.getByRole('button', { name: 'Compare with Readyset' })
  ).toBeVisible()
  await expect(page.getByText('Local Readyset sandbox')).toHaveCount(0)
  await expect(page.getByText('Prepared target')).toHaveCount(0)
  await expect(page.getByText('Current activity')).toHaveCount(0)
})

test('missing Docker explains setup and queues no Readyset work', async ({
  page,
}) => {
  setBackendFixtures({
    docker_runtime: [
      { value: { installed: false, running: false }, repeat: true },
    ],
  })
  await prepareSpeedTests(page, [directQuery])
  let prewarmRequests = 0
  let testRunRequests = 0
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path === '/api/cache/sandbox/prewarm') prewarmRequests += 1
    if (path === '/api/cache/test-runs') testRunRequests += 1
  })

  await page.goto('/cache')

  await expect(page.getByText('Install Docker to try Readyset')).toBeVisible()
  await expect(
    page.getByText(
      'Analyze Query works without Docker. Docker is only required to run this temporary local Readyset comparison.'
    )
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Get Docker' })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Compare with Readyset' })
  ).toBeDisabled()
  await page.waitForTimeout(250)
  expect(prewarmRequests).toBe(0)
  expect(testRunRequests).toBe(0)
})

test('dead upstream rejects a comparison without creating a queued job', async ({
  page,
  browserErrors,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    upstream_probe: [
      {
        value: {
          success: false,
          error: 'connection to server failed: connection refused',
        },
        repeat: true,
      },
    ],
  })
  const [queryHash] = await prepareSpeedTests(page, [directQuery])
  let eventStreamRequests = 0
  page.on('request', (request) => {
    if (/\/api\/runs\/[^/]+\/events$/.test(new URL(request.url()).pathname)) {
      eventStreamRequests += 1
    }
  })

  await page.goto('/cache')
  const row = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${queryHash}"]`
  )
  await row.getByRole('button', { name: 'Compare with Readyset' }).click()

  await expect(
    row.getByText(
      "Readyset comparisons require a reachable source database. RDST could not connect to 'e2e-guard', so no Readyset work was queued."
    )
  ).toBeVisible()
  await expect(row.getByText('Testing', { exact: true })).toHaveCount(0)
  expect(eventStreamRequests).toBe(0)
  consumeBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 503 (Service Unavailable)'
  )
})

test('job queue distinguishes the active sandbox test from queued tests', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    speed_test: [
      {
        events: [
          {
            type: 'progress',
            stage: 'waiting_for_readyset',
            percent: 10,
            message: 'Waiting for Readyset to accept SQL',
          },
          { ...resultEvent(directQuery, 4), _delay_ms: 1500 },
        ],
      },
      {
        delay_ms: 1500,
        events: [resultEvent(secondDirectQuery, 3)],
      },
    ],
  })
  const [firstHash, secondHash] = await prepareSpeedTests(page, [
    directQuery,
    secondDirectQuery,
  ])

  await page.goto('/cache')
  const first = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${firstHash}"]`
  )
  const second = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${secondHash}"]`
  )

  await first.getByRole('button', { name: 'Compare with Readyset' }).click()
  await expect(
    first.getByText('Waiting for Readyset to accept SQL')
  ).toBeVisible()
  await second.getByRole('button', { name: 'Compare with Readyset' }).click()

  await page.getByTestId('jobs-trigger').click()
  await expect(page.getByText('1 running · 1 queued')).toBeVisible()
  await expect(page.getByText('2 jobs running')).toHaveCount(0)

  await page.keyboard.press('Escape')
  await expect(first.getByText('4.0x faster with Readyset')).toBeVisible()
  await expect(second.getByText('3.0x faster with Readyset')).toBeVisible()
})

test('runs a comparison, survives refresh, and re-tests the query', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    speed_test: [
      {
        events: [
          {
            type: 'progress',
            stage: 'checking_query',
            percent: 25,
            message: 'Checking Readyset compatibility',
            _delay_ms: 250,
          },
          {
            type: 'progress',
            stage: 'benchmarking_origin',
            percent: 65,
            message: 'Benchmarking origin and Readyset',
            _delay_ms: 250,
          },
          { ...resultEvent(directQuery, 4), _delay_ms: 750 },
        ],
      },
      {
        events: [
          {
            type: 'progress',
            stage: 'benchmarking_origin',
            percent: 65,
            message: 'Benchmarking origin and Readyset',
            _delay_ms: 150,
          },
          resultEvent(directQuery, 6),
        ],
      },
    ],
  })
  const [queryHash] = await prepareSpeedTests(page, [directQuery])

  const requests: Record<string, unknown>[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/cache/test-runs') {
      requests.push(request.postDataJSON() as Record<string, unknown>)
    }
  })

  await page.goto('/cache')
  const row = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${queryHash}"]`
  )
  await row.getByRole('button', { name: 'Compare with Readyset' }).click()

  await expect(row.getByText('Testing', { exact: true })).toBeVisible()
  await expect(row.getByText('Benchmarking origin and Readyset')).toBeVisible()
  await expect(row.getByText('4.0x faster with Readyset')).toBeVisible()
  await expect(row.getByText('4.0x faster', { exact: true })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Re-test' })).toBeVisible()

  expect(requests[0]).toMatchObject({
    target: 'e2e-guard',
    query: directQuery,
    query_hash: queryHash,
    iterations: 15,
    warmup: 5,
  })

  await page.reload()
  const restoredRow = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${queryHash}"]`
  )
  await expect(restoredRow.getByText('4.0x faster with Readyset')).toBeVisible()
  await expect(
    restoredRow.getByText('4.0x faster', { exact: true })
  ).toBeVisible()

  await restoredRow.getByRole('button', { name: 'Re-test' }).click()
  await expect(restoredRow.getByText('6.0x faster with Readyset')).toBeVisible()
  expect(requests).toHaveLength(2)
})

test('substitutes query parameters before starting the comparison', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    speed_test: [
      {
        events: [
          resultEvent('SELECT id, email FROM users WHERE account_id = 42', 3),
        ],
      },
    ],
  })
  const [queryHash] = await prepareSpeedTests(page, [parameterizedQuery])

  let testRequest: Record<string, unknown> | undefined
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/cache/test-runs') {
      testRequest = request.postDataJSON() as Record<string, unknown>
    }
  })

  await page.goto('/cache')
  const row = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${queryHash}"]`
  )
  await row.getByRole('button', { name: 'Compare with Readyset' }).click()

  await expect(
    page.getByRole('heading', { name: 'Enter Parameter Values' })
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run test' })).toBeDisabled()
  await page.locator('[name="param-:account_id"]').fill('42')
  await page.getByRole('button', { name: 'Run test' }).click()

  await expect(row.getByText('3.0x faster with Readyset')).toBeVisible()
  expect(testRequest).toMatchObject({
    target: 'e2e-guard',
    query: 'SELECT id, email FROM users WHERE account_id = 42',
    query_hash: queryHash,
  })
})

test('shows a failed run in its query row and allows a successful retry', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    speed_test: [
      {
        events: [
          {
            type: 'progress',
            stage: 'checking_query',
            percent: 25,
            message: 'Checking Readyset compatibility',
          },
          {
            type: 'error',
            message: 'This query is unsupported by Readyset.',
            code: 'readyset_unsupported',
            stage: 'checking_query',
          },
        ],
      },
      {
        events: [resultEvent(directQuery, 2.5)],
      },
    ],
  })
  const [queryHash] = await prepareSpeedTests(page, [directQuery])

  await page.goto('/cache')
  const row = page.locator(
    `[data-testid="speed-test-query"][data-query-hash="${queryHash}"]`
  )
  await row.getByRole('button', { name: 'Compare with Readyset' }).click()

  await expect(
    row.getByText('This query is unsupported by Readyset.')
  ).toBeVisible()
  await expect(
    row.getByRole('button', { name: 'Compare with Readyset' })
  ).toBeEnabled()

  await row.getByRole('button', { name: 'Compare with Readyset' }).click()
  await expect(row.getByText('2.5x faster with Readyset')).toBeVisible()
})

test('surfaces sandbox preparation failure and sends retry without restoring the old diagnostics card', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [
      {
        value: {
          ...readySandbox,
          phase: 'error',
          current_target: null,
          failed_target: 'e2e-guard',
          last_error: 'Readyset could not connect to this database.',
          healthy: false,
        },
        repeat: true,
      },
    ],
  })
  await prepareSpeedTests(page, [directQuery])

  let retryRequests = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/cache/sandbox/prewarm') {
      retryRequests += 1
    }
  })

  await page.goto('/cache')
  await expect(
    page.getByText('Readyset could not connect to this database.')
  ).toBeVisible()
  await expect(page.getByText('Local Readyset sandbox')).toHaveCount(0)

  const beforeRetry = retryRequests
  await page.getByRole('button', { name: 'Retry preparation' }).click()
  await expect.poll(() => retryRequests).toBeGreaterThan(beforeRetry)
})
