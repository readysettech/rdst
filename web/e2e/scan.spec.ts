import {
  acceptExplainAnalyzeConsent,
  clearQueryRegistry,
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

const directory = process.env.RDST_E2E_HOME ?? '/tmp/rdst-web-e2e'
const sourceFile = `${directory}/src/orders.ts`
const query =
  "SELECT id, customer_id, status FROM orders WHERE status = 'pending'"

const sqlQuery = {
  file: sourceFile,
  function: 'listOrders',
  class: 'OrderRepository',
  orm_code: 'prisma.order.findMany({ where: { status } })',
  snippet_hash: 'scan-orders-001',
  terminal_method: 'findMany',
  start_line: 42,
  end_line: 48,
  orm_type: 'prisma',
  sql: query,
  status: 'sql',
  issues: ['Missing a selective index on orders.status'],
  hash: 'orders-query-hash',
}

const skippedQuery = {
  file: sourceFile,
  function: 'buildDynamicOrderQuery',
  class: 'OrderRepository',
  orm_code: 'prisma.order.findMany(dynamicFilters)',
  snippet_hash: 'scan-orders-002',
  terminal_method: 'findMany',
  start_line: 61,
  end_line: 67,
  orm_type: 'prisma',
  sql: '',
  status: 'skipped',
  skip_reason: 'Dynamic filters could not be resolved statically',
  issues: [],
  hash: 'orders-skipped-hash',
}

const analysisQuery = {
  hash: sqlQuery.hash,
  file: sourceFile,
  function: sqlQuery.function,
  line: sqlQuery.start_line,
  sql: query,
  risk_score: 52,
  rating: 'warning',
  issues: ['Sequential scan on orders'],
  recommendations: ['Add an index on orders(status)'],
  execution_time_ms: 37.4,
  rewrite_benchmarks: [],
}

const summary = {
  files_count: 1,
  queries_total: 2,
  queries_sql: 1,
  queries_skipped: 1,
  cache_hits: 0,
  cache_misses: 2,
  registry_new: 0,
  registry_updated: 0,
  registry_total: 0,
  registry_skipped: true,
  analysis: {
    mode: 'shallow',
    total_analyzed: 1,
    successful: 1,
    failed: 0,
    worst_score: 52,
    ci_status: 'warn',
    ci_exit_code: 0,
    warn_threshold: 70,
    fail_threshold: 45,
    by_query: [analysisQuery],
    performance_issues: [
      {
        hash: sqlQuery.hash,
        file: sourceFile,
        issue: 'Sequential scan on orders',
      },
    ],
    recommendations: [
      {
        hash: sqlQuery.hash,
        recommendation: 'Add an index on orders(status)',
      },
    ],
    failed_queries: [],
  },
}

const successfulScanEvents = [
  {
    event: 'status',
    data: { phase: 'discovery', message: 'Discovering ORM files...' },
  },
  {
    event: 'files_found',
    data: {
      files: [{ file: sourceFile, orms: ['prisma'], lines: 120 }],
      total: 1,
    },
  },
  {
    event: 'progress',
    data: {
      phase: 'extraction',
      current: 1,
      total: 1,
      message: 'Extracting queries from orders.ts',
    },
  },
  { event: 'query_result', data: { query: sqlQuery } },
  { event: 'query_result', data: { query: skippedQuery } },
  {
    event: 'registry',
    data: {
      new_queries: 0,
      updated_queries: 0,
      total_queries: 0,
      skipped: true,
    },
  },
  { event: 'complete', data: { success: true, summary } },
]

async function prepareScanPage(
  page: Parameters<typeof configureTestTarget>[0]
) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await acceptExplainAnalyzeConsent(page)
  await page.goto('/scan')
  await expect(page.getByRole('heading', { name: 'Scan' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start Scan' })).toBeDisabled()
}

async function chooseScanDirectory(
  page: Parameters<typeof configureTestTarget>[0]
) {
  await page.getByRole('button', { name: 'Choose a project folder...' }).click()
  await page.getByRole('button', { name: 'Select this folder' }).click()
  await expect(page.getByRole('button', { name: 'Start Scan' })).toBeEnabled()
}

test('scans a project, renders analysis, speed-tests a query, and hands off to Analyze', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'error',
            message: 'Analysis skipped in Code Scan E2E',
          },
        ],
      },
    ],
    speed_test: [
      {
        events: [
          {
            type: 'progress',
            stage: 'benchmarking_origin',
            percent: 65,
            message: 'Benchmarking origin and Readyset',
          },
          {
            type: 'cache_run_complete',
            success: true,
            query,
            iterations: 15,
            origin_stats: {
              mean: 10,
              median: 9,
              min: 8,
              max: 14,
              p50: 9,
              p95: 13,
              p99: 14,
            },
            cache_stats: {
              mean: 2,
              median: 2,
              min: 1.5,
              max: 3,
              p50: 2,
              p95: 2.8,
              p99: 3,
            },
            speedup_mean: 5,
            speedup_median: 4.5,
            improvement_pct: 80,
            winner: 'readyset',
          },
        ],
      },
    ],
    scan: [
      {
        events: successfulScanEvents.map(({ event, data }) => ({
          type: event,
          ...data,
        })),
      },
    ],
  })
  await prepareScanPage(page)
  await chooseScanDirectory(page)

  await page.getByRole('button', { name: /Advanced options/ }).click()
  await page.locator('[name="diff"]').fill('main')
  await page.locator('[name="file-pattern"]').fill('src/**/*.ts')
  const switches = page.getByRole('switch')
  await expect(switches).toHaveCount(5)
  await switches.nth(1).click()
  await switches.nth(2).click()
  await switches.nth(3).click()
  await switches.nth(4).click()
  await page.locator('[name="warn-threshold"]').fill('70')
  await page.locator('[name="fail-threshold"]').fill('45')

  const scanRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/scan'
  )
  await page.getByRole('button', { name: 'Start Scan' }).click()
  expect((await scanRequest).postDataJSON()).toEqual({
    target: 'e2e-guard',
    directory,
    analyze: true,
    shallow: true,
    dry_run: true,
    diff: 'main',
    check: true,
    warn_threshold: 70,
    fail_threshold: 45,
    file_pattern: 'src/**/*.ts',
    nosave: true,
  })
  await expect(page.getByText(directory, { exact: true })).toBeVisible()
  await expect(page.getByText('shallow analyze', { exact: true })).toBeVisible()
  await expect(page.getByText('CI check', { exact: true })).toBeVisible()
  await expect(page.getByText('Scan Summary', { exact: true })).toBeVisible()
  await expect(page.getByText('2 queries found', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Registry: skipped', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Analysis Results', { exact: true })
  ).toBeVisible()
  await expect(page.getByText('Worst score: 52', { exact: true })).toBeVisible()

  const fileToggle = page.getByTestId('scan-file-group-toggle')
  await expect(fileToggle).toHaveAttribute('data-file', sourceFile)
  await fileToggle.click()

  const queryRows = page.getByTestId('scan-query-row')
  await expect(queryRows).toHaveCount(2)
  const sqlRow = queryRows.filter({ hasText: sqlQuery.function })
  const skippedRow = queryRows.filter({ hasText: skippedQuery.function })
  await expect(sqlRow).toHaveAttribute('data-query-hash', sqlQuery.hash)
  await expect(skippedRow).toContainText(skippedQuery.skip_reason)

  await sqlRow.locator('button[title="View query detail"]').click()
  await expect(page.getByText('ORM Code', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Missing a selective index on orders.status', {
      exact: true,
    })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Close', exact: true }).click()

  const speedTestRequests: unknown[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/cache/test-runs') {
      speedTestRequests.push(request.postDataJSON())
    }
  })

  const speedTestButton = sqlRow.getByRole('button', {
    name: 'Compare speed',
  })
  await speedTestButton.click()
  await expect(
    page.getByText('Comparison started', { exact: true })
  ).toBeVisible()
  await expect(page.getByText('Performance test complete')).toBeVisible()
  expect(speedTestRequests).toEqual([
    {
      query,
      target: 'e2e-guard',
      query_hash: sqlQuery.snippet_hash,
      iterations: 15,
      warmup: 5,
    },
  ])

  await sqlRow.getByRole('button', { name: 'Analyze' }).click()
  await expect(page).toHaveURL(/\/results(?:\?|$)/)
  const resultsUrl = new URL(page.url())
  expect(resultsUrl.searchParams.get('query')).toBe(query)
  expect(resultsUrl.searchParams.get('target')).toBe('e2e-guard')
  await expect(
    page.getByText('Analysis skipped in Code Scan E2E', { exact: true })
  ).toBeVisible()
})

test('shows a scan failure, retries, and renders the empty result', async ({
  page,
}) => {
  setBackendFixtures({
    scan: [
      {
        events: [
          {
            type: 'error',
            message: 'Unable to read the selected project directory',
            phase: 'discovery',
          },
        ],
      },
      {
        events: [
          {
            type: 'complete',
            success: true,
            summary: {
              files_count: 0,
              queries_total: 0,
              queries_sql: 0,
              queries_skipped: 0,
              cache_hits: 0,
              cache_misses: 0,
              registry_new: 0,
              registry_updated: 0,
              registry_total: 0,
              registry_skipped: false,
            },
          },
        ],
      },
    ],
  })
  await prepareScanPage(page)
  await chooseScanDirectory(page)

  let scanCalls = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/scan') scanCalls += 1
  })

  await page.getByRole('button', { name: 'Start Scan' }).click()
  await expect(
    page.getByText('Error: Unable to read the selected project directory', {
      exact: true,
    })
  ).toBeVisible()

  await page.getByRole('button', { name: 'New Scan' }).click()
  await expect(page.getByRole('button', { name: 'Start Scan' })).toBeEnabled()
  await page.getByRole('button', { name: 'Start Scan' }).click()

  await expect(
    page.getByText('No ORM queries found in the scanned directory.', {
      exact: true,
    })
  ).toBeVisible()
  await expect(page.getByText('Scan Summary', { exact: true })).toBeVisible()
  expect(scanCalls).toBe(2)
})
