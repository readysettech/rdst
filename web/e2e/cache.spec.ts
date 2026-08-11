import type { APIRequestContext, Page } from '@playwright/test'
import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from './fixtures'

const directQuery =
  'SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id'
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

const upstream = {
  scheduled: 120,
  completed: 120,
  errors: 0,
  dropped: 0,
  in_flight: 0,
  throughput_rps: 4,
  error_rate: 0,
  mean_ms: 12,
  p50_ms: 11,
  p95_ms: 17,
  p99_ms: 18,
}

const readyset = {
  scheduled: 480,
  completed: 480,
  errors: 0,
  dropped: 0,
  in_flight: 0,
  throughput_rps: 16,
  error_rate: 0,
  mean_ms: 3,
  p50_ms: 2.8,
  p95_ms: 4,
  p99_ms: 5,
}

function comparisonEvents(query: string) {
  const sample = {
    type: 'cache_compare_sample',
    elapsed_seconds: 1,
    concurrency: 2,
    origin: upstream,
    readyset,
  }
  return [
    sample,
    {
      type: 'cache_compare_complete',
      success: true,
      query,
      duration_seconds: 30,
      elapsed_seconds: 30,
      concurrency: 2,
      origin: upstream,
      readyset,
      timeline: [sample],
      phases: [{ concurrency: 2, elapsed_seconds: 30 }],
      speedup_mean: 4,
      improvement_pct: 75,
      winner: 'readyset',
    },
  ]
}

async function addQuery(request: APIRequestContext, sql: string) {
  const response = await request.post('/api/query-registry', {
    data: { sql, target: 'e2e-guard' },
  })
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as { success: boolean; hash: string }
  expect(body.success).toBe(true)
  return body.hash
}

async function prepareQueries(page: Page, queries: string[]) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  const hashes: string[] = []
  for (const query of queries) hashes.push(await addQuery(page.request, query))
  return hashes
}

async function selectFirstQuery(page: Page) {
  await page.getByRole('checkbox').first().check()
}

test('offers unverified registry queries for comparison', async ({ page }) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
  })
  await prepareQueries(page, [directQuery])

  await page.goto('/cache')

  // FB-07/FB-13: there is no verified-queries gate. Compare lists every
  // registry query and states that cacheability is checked at run time.
  await expect(
    page.getByText('RDST checks cacheability for each query', { exact: false })
  ).toBeVisible()
  await expect(page.getByRole('checkbox')).toHaveCount(1)
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  await expect(
    page.getByRole('button', { name: 'Run comparison' })
  ).toBeDisabled()
})

test('missing Docker blocks comparison without creating a run', async ({
  page,
}) => {
  setBackendFixtures({
    docker_runtime: [
      { value: { installed: false, running: false }, repeat: true },
    ],
  })
  await prepareQueries(page, [directQuery])
  let compareRequests = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/cache/compare-runs') {
      compareRequests += 1
    }
  })

  await page.goto('/cache')

  await expect(
    page.getByRole('heading', { name: 'Docker is required for comparisons' })
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Check again' })).toBeVisible()
  expect(compareRequests).toBe(0)
})

test('substitutes parameters and starts the current comparison flow', async ({
  page,
}) => {
  const executedQuery = 'SELECT id, email FROM users WHERE account_id = 42'
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    cache_compare: [{ events: comparisonEvents(executedQuery) }],
  })
  const [queryHash] = await prepareQueries(page, [parameterizedQuery])
  // Starting a comparison preflights target reachability.
  await mockConnectivityOk(page)
  let compareRequest: Record<string, unknown> | undefined
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/cache/compare-runs') {
      compareRequest = request.postDataJSON() as Record<string, unknown>
    }
  })

  await page.goto('/cache')
  await selectFirstQuery(page)
  await page
    .getByRole('textbox', { name: 'Enter a representative value' })
    .fill('42')
  await page.getByRole('button', { name: 'Run comparison' }).click()

  await expect(
    page.getByRole('heading', { name: 'Start this comparison?' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Start comparison' }).click()
  await expect(page.getByText('4.0× faster with Readyset')).toBeVisible()
  expect(compareRequest).toMatchObject({
    target: 'e2e-guard',
    query: executedQuery,
    query_hash: queryHash,
    concurrency: 2,
    duration_seconds: 30,
  })
})

test('completes a comparison and restores it from history after reload', async ({
  page,
}) => {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    cache_compare: [{ events: comparisonEvents(directQuery) }],
  })
  await prepareQueries(page, [directQuery])
  // Starting a comparison preflights target reachability.
  await mockConnectivityOk(page)

  await page.goto('/cache')
  await selectFirstQuery(page)
  await page.getByRole('button', { name: 'Run comparison' }).click()
  await page.getByRole('button', { name: 'Start comparison' }).click()

  await expect(page.getByText('4.0× faster with Readyset')).toBeVisible()
  await expect(page.getByText('Complete', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'History 1' })).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'History 1' }).click()
  await page.getByRole('button', { name: 'View result' }).click()
  await expect(page.getByText('4.0× faster with Readyset')).toBeVisible()
})
