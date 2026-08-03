import {
  acceptExplainAnalyzeConsent,
  clearQueryRegistry,
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

const firstHistoricalQueries = [
  {
    query_hash: 'orders-historical-001',
    query_text:
      'SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id ORDER BY COUNT(*) DESC',
    normalized_query:
      'SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id ORDER BY COUNT(*) DESC',
    freq: 3200,
    total_time: '48.0s',
    avg_time: '15.0ms',
    pct_load: '31.5%',
  },
  {
    query_hash: 'customers-historical-002',
    query_text: 'SELECT id, email FROM customers ORDER BY created_at DESC',
    normalized_query:
      'SELECT id, email FROM customers ORDER BY created_at DESC',
    freq: 800,
    total_time: '21.0s',
    avg_time: '26.3ms',
    pct_load: '13.8%',
  },
]

const refreshedQuery = {
  query_hash: 'orders-refreshed-003',
  query_text:
    'SELECT product_id, SUM(quantity) FROM order_items GROUP BY product_id',
  normalized_query:
    'SELECT product_id, SUM(quantity) FROM order_items GROUP BY product_id',
  freq: 4100,
  total_time: '64.0s',
  avg_time: '15.6ms',
  pct_load: '42.0%',
}

const liveQuery = {
  query_hash: 'orders-live-004',
  query_text: 'SELECT id FROM orders WHERE status = $1',
  normalized_query: 'SELECT id FROM orders WHERE status = :p1',
  freq: 17,
  total_time: '820.0ms',
  avg_time: '48.2ms',
  pct_load: '18.0%',
  observation_count: 17,
  max_duration_ms: 92.4,
  current_instances_running: 2,
  qps: 4.25,
}

async function prepareTopPage(page: Parameters<typeof configureTestTarget>[0]) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await acceptExplainAnalyzeConsent(page)
  await page.goto('/top')
  await expect(
    page.getByRole('heading', { name: 'Slow Queries' })
  ).toBeVisible()
  // C-09 moved the idle CTA into the empty state and renamed it.
  await expect(
    page.getByRole('button', { name: 'Find slow queries' })
  ).toBeEnabled()
}

test('filters, refreshes, and analyzes historical slow queries', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'error',
            message: 'Analysis skipped in Slow Queries E2E',
          },
        ],
      },
    ],
    top_historical: [
      {
        events: [
          {
            type: 'connected',
            target_name: 'e2e-guard',
            db_engine: 'postgresql',
            source: 'pg_stat',
          },
          {
            type: 'db_limit_warning',
            db_limit_bytes: 1024,
            recommended_bytes: 4096,
            setting_name: 'track_activity_query_size',
            db_engine: 'postgresql',
          },
          {
            type: 'complete',
            success: true,
            queries: firstHistoricalQueries,
            source: 'pg_stat',
            newly_saved: 2,
          },
        ],
      },
      {
        events: [
          {
            type: 'connected',
            target_name: 'e2e-guard',
            db_engine: 'postgresql',
            source: 'pg_stat',
          },
          {
            type: 'complete',
            success: true,
            queries: [refreshedQuery],
            source: 'pg_stat',
            newly_saved: 1,
          },
        ],
      },
    ],
  })
  await prepareTopPage(page)

  let topRequests = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/top') topRequests += 1
  })

  // The filter inputs live behind the collapsed "Filters" disclosure now.
  await page.getByRole('button', { name: 'Filters' }).click()
  await page.locator('[name="filter"]').fill('orders')
  await page.locator('[name="min-freq"]').fill('50')
  await page.locator('[name="min-load"]').fill('5')

  const firstRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/top'
  )
  await page.getByRole('button', { name: 'Find slow queries' }).click()
  const requestUrl = new URL((await firstRequest).url())
  expect(Object.fromEntries(requestUrl.searchParams)).toMatchObject({
    auto_save: 'true',
    filter_pattern: 'orders',
    limit: '10',
    min_freq: '50',
    min_load_pct: '5',
    sort: 'total_time',
    source: 'auto',
    target: 'e2e-guard',
  })

  let queryRows = page.getByTestId('top-query-row')
  await expect(queryRows).toHaveCount(2)
  await expect(queryRows.first()).toHaveAttribute(
    'data-query-hash',
    'orders-historical-001'
  )
  await expect(queryRows.first()).toContainText('freq 3200')
  await expect(queryRows.first()).toContainText('total 48.0s')
  await expect(page.getByText('Low Database Query Size Limit')).toBeVisible()
  await expect(page.getByText('2 saved', { exact: true })).toBeVisible()

  // v3: SQL is always shown full + syntax-highlighted (no expand/collapse dual
  // state). The SQL element carries the full query as its `title` — a stable
  // per-row hook — and its text content is the query verbatim.
  const firstSql = queryRows.first().locator('[title]')
  await expect(firstSql).toContainText(firstHistoricalQueries[0].query_text)

  await page.getByRole('button', { name: 'Find slow queries' }).click()
  queryRows = page.getByTestId('top-query-row')
  await expect(queryRows).toHaveCount(1)
  await expect(queryRows).toHaveAttribute(
    'data-query-hash',
    refreshedQuery.query_hash
  )
  // Load folds into the v3 muted meta line ("… · load 42.0%").
  await expect(queryRows).toContainText('load 42.0%')
  expect(topRequests).toBe(2)

  await queryRows.getByRole('button', { name: 'Analyze' }).click()
  await expect(page).toHaveURL(/\/results(?:\?|$)/)
  const resultsUrl = new URL(page.url())
  expect(resultsUrl.searchParams.get('query')).toBe(refreshedQuery.query_text)
  expect(resultsUrl.searchParams.get('target')).toBe('e2e-guard')
  await expect(
    page.getByText('Analysis skipped in Slow Queries E2E', { exact: true })
  ).toBeVisible()
})

test('shows the historical empty state', async ({ page }) => {
  setBackendFixtures({
    top_historical: [
      {
        events: [
          {
            type: 'connected',
            target_name: 'e2e-guard',
            db_engine: 'postgresql',
            source: 'pg_stat',
          },
          {
            type: 'complete',
            success: true,
            queries: [],
            source: 'pg_stat',
            newly_saved: 0,
          },
        ],
      },
    ],
  })
  await prepareTopPage(page)

  await page.getByRole('button', { name: 'Find slow queries' }).click()
  await expect(
    page.getByText(
      'No queries matched. Loosen the filters or wait for more traffic.',
      { exact: true }
    )
  ).toBeVisible()
})

test('shows a realtime connection failure and retries the stream', async ({
  page,
}) => {
  setBackendFixtures({
    top_realtime: [
      {
        events: [
          {
            type: 'error',
            code: 'database_connection_failed',
            message: 'Unable to connect to database telemetry',
            stage: 'connect',
          },
        ],
      },
      {
        events: [
          {
            type: 'connected',
            target_name: 'e2e-guard',
            db_engine: 'postgresql',
            source: 'activity',
          },
          {
            type: 'source_fallback',
            from_source: 'pg_stat',
            to_source: 'activity',
            reason: 'pg_stat_statements is unavailable',
          },
          {
            type: 'queries',
            queries: [
              {
                ...liveQuery,
                current_instances: liveQuery.current_instances_running,
                current_instances_running: undefined,
              },
            ],
            source: 'activity',
            target_name: 'e2e-guard',
            db_engine: 'postgresql',
            runtime_seconds: 4.2,
            total_tracked: 17,
          },
          {
            type: 'query_saved',
            query_hash: liveQuery.query_hash,
            is_new: true,
          },
          {
            type: 'complete',
            success: true,
            queries: [
              {
                ...liveQuery,
                current_instances: liveQuery.current_instances_running,
                current_instances_running: undefined,
              },
            ],
            source: 'activity',
            newly_saved: 1,
          },
        ],
      },
    ],
  })
  await prepareTopPage(page)

  let realtimeCalls = 0
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (
      url.pathname === '/api/top' &&
      url.searchParams.get('realtime') === 'true'
    ) {
      realtimeCalls += 1
    }
  })

  // Fill the shared thresholds behind the Filters disclosure while the mode
  // is still historical (the values persist across the mode switch).
  await page.getByRole('button', { name: 'Filters' }).click()
  await page.locator('[name="min-freq"]').fill('3')
  await page.locator('[name="min-load"]').fill('2')
  await page.getByRole('button', { name: 'Realtime Realtime' }).click()
  const firstRealtimeRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/top' && url.searchParams.get('realtime') === 'true'
    )
  })
  await page.getByRole('button', { name: 'Start live monitoring' }).click()
  const realtimeUrl = new URL((await firstRealtimeRequest).url())
  expect(Object.fromEntries(realtimeUrl.searchParams)).toMatchObject({
    min_freq: '3',
    min_load_pct: '2',
    realtime: 'true',
    target: 'e2e-guard',
  })
  await expect(
    page.getByText('Unable to connect to database telemetry', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Open connection settings' })
  ).toBeVisible()
  await expect(
    page.getByText('Error: Unable to connect to database telemetry', {
      exact: true,
    })
  ).toHaveCount(0)

  await page.getByRole('button', { name: 'Start live monitoring' }).click()
  const liveRow = page.getByTestId('top-query-row')
  await expect(liveRow).toHaveCount(1)
  await expect(liveRow).toContainText('2 running')
  // Realtime meta folds seen/max/qps into the one muted stat line.
  await expect(liveRow).toContainText('seen 17')
  await expect(liveRow).toContainText('max 92.4ms')
  await expect(liveRow).toContainText('qps 4.25')
  await expect(
    page.getByText('Fallback: pg_stat → activity', { exact: true })
  ).toBeVisible()
  await expect(page.getByText('1 saved', { exact: true })).toBeVisible()
  expect(realtimeCalls).toBe(2)
})
