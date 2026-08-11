import {
  acceptExplainAnalyzeConsent,
  clearQueryRegistry,
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from './fixtures'

const query = `SELECT id, total
FROM orders
WHERE customer_id = 42
ORDER BY created_at DESC`

const completeAnalysis = {
  success: true,
  analysis_id: 'analysis-e2e-001',
  query_hash: 'query-e2e-001',
  explain_results: {
    success: true,
    database_engine: 'postgresql',
    execution_time_ms: 128.4,
    rows_examined: 12000,
    rows_returned: 25,
    cost_estimate: 87.3,
  },
  llm_analysis: {
    success: true,
    performance_assessment: {
      overall_rating: 'poor',
      efficiency_score: 32,
      primary_concerns: ['Sequential scan reads too many rows'],
    },
    index_recommendations: [
      {
        sql: 'CREATE INDEX idx_orders_customer_id ON orders (customer_id)',
        table: 'orders',
        columns: ['customer_id'],
        index_type: 'btree',
        rationale: 'Avoids the sequential scan for customer lookups.',
        estimated_impact: 'high',
        caveats: [],
      },
    ],
    optimization_opportunities: [
      {
        priority: 'medium',
        description: 'Return only columns needed by the caller.',
        type: 'projection',
      },
    ],
    rewrite_suggestions: [],
    token_usage: {
      input: 100,
      output: 50,
      total: 150,
      estimated_cost_usd: 0.01,
    },
  },
  rewrite_testing: {
    tested: false,
    skipped_reason: 'e2e_fixture',
    message: 'Skipped in browser tests',
  },
  readyset_cacheability: {
    checked: true,
    cacheable: true,
    confidence: 'medium',
    method: 'static_analysis',
    explanation: 'Static SQL screening found no obvious blockers.',
    issues: [],
    warnings: [],
  },
}

const successEvents = [
  {
    event: 'progress',
    data: { stage: 'normalizing', percent: 10, message: 'Preparing query' },
  },
  {
    event: 'progress',
    data: {
      stage: 'executing_explain',
      percent: 45,
      message: 'Running EXPLAIN ANALYZE',
    },
  },
  {
    event: 'progress',
    data: {
      stage: 'analyzing_llm',
      percent: 75,
      message: 'Analyzing the execution plan',
    },
  },
  { event: 'complete', data: completeAnalysis },
]

function serviceEvents(
  events: { event: string; data: Record<string, unknown> }[]
) {
  return events.map(({ event, data }) => ({ type: event, ...data }))
}

async function prepareResultsPage(
  page: Parameters<typeof configureTestTarget>[0],
  { consent = true, fast = false }: { consent?: boolean; fast?: boolean } = {}
) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  // Analyze preflights target reachability before POST /api/analyze.
  await mockConnectivityOk(page)
  if (consent) await acceptExplainAnalyzeConsent(page)
  const search = new URLSearchParams({
    query,
    target: 'e2e-guard',
    fast: String(fast),
  })
  await page.goto(`/results?${search.toString()}`)
  if (consent) {
    await expect(
      page.getByRole('heading', { name: 'Query analysis' })
    ).toBeVisible()
  } else {
    await expect(
      page.getByRole('dialog').getByText('Run EXPLAIN ANALYZE?')
    ).toBeVisible()
  }
}

test('gates the first EXPLAIN ANALYZE run behind explicit consent', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [{ events: serviceEvents(successEvents) }],
  })

  let analysisCalls = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/analyze'
    ) {
      analysisCalls += 1
    }
  })
  await prepareResultsPage(page, { consent: false })

  const consentDialog = page.getByRole('dialog')
  await expect(
    consentDialog.getByRole('heading', { name: 'Run EXPLAIN ANALYZE?' })
  ).toHaveCount(1)
  await expect(
    consentDialog.getByText(
      'Analyze runs EXPLAIN ANALYZE, which executes your query once against the database to measure it. Cancel if this query should not be executed.',
      { exact: true }
    )
  ).toBeVisible()
  expect(analysisCalls).toBe(0)

  await consentDialog.getByRole('checkbox', { name: "Don't ask again" }).check()
  const analysisRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/analyze'
  )
  await consentDialog.getByRole('button', { name: 'Run analyze' }).click()
  await analysisRequest

  await expect(
    page.getByText('Performance score', { exact: true })
  ).toBeVisible()
  expect(analysisCalls).toBe(1)
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem('rdst.explain-analyze-consent')
      )
    )
    .toBe('accepted')
})

test('submits SQL and renders streamed analysis results', async ({ page }) => {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  // Analyze preflights target reachability before POST /api/analyze.
  await mockConnectivityOk(page)
  await acceptExplainAnalyzeConsent(page)
  const registryResponse = await page.request.post('/api/query-registry', {
    data: { sql: query, target: 'e2e-guard' },
  })
  expect(registryResponse.ok()).toBe(true)
  const registryHash = ((await registryResponse.json()) as { hash: string })
    .hash
  const matchingEvents = successEvents.map((entry) =>
    entry.event === 'complete'
      ? {
          ...entry,
          data: {
            ...completeAnalysis,
            query_hash: registryHash,
            // Hold completion back so the in-progress state stays observable
            // between the streamed progress events and the final render.
            _delay_ms: 1500,
          },
        }
      : entry
  )
  setBackendFixtures({
    analyze: [{ events: serviceEvents(matchingEvents), delay_ms: 250 }],
  })

  const analysisRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/analyze'
  )
  const search = new URLSearchParams({
    query,
    target: 'e2e-guard',
    fast: 'true',
  })
  await page.goto(`/results?${search.toString()}`)

  const request = await analysisRequest
  expect(request.postDataJSON()).toEqual({
    fast: true,
    query,
    target: 'e2e-guard',
  })
  await expect(page).toHaveURL(/\/results(?:\?|$)/)
  await expect(
    page.getByRole('status', { name: 'Analysis in progress' })
  ).toBeVisible()

  await expect(
    page.getByText('Performance score', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Sequential scan reads too many rows')
  ).toBeVisible()
  await expect(
    page.getByText('Recommended next step', { exact: true })
  ).toBeVisible()
  // The optimization-opportunities section collapsed into a quiet
  // "More recommendations (N)" disclosure on the results page; open it and
  // check the recommendation text it reveals.
  await page.getByRole('button', { name: /More recommendations/ }).click()
  await expect(
    page.getByText('Return only columns needed by the caller.')
  ).toBeVisible()
  await expect(
    page.getByText('Readyset compatibility', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('No obvious Readyset blockers', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Static check', { exact: true }).first()
  ).toBeVisible()
  await expect(page.getByText('Readyset verification required')).toBeVisible()

  await expect
    .poll(async () => {
      const response = await page.request.get('/api/query-registry?limit=10')
      const body = (await response.json()) as {
        queries: { sql: string; target: string }[]
      }
      return body.queries.map(({ sql, target }) => ({
        sql,
        target,
      }))
    })
    .toContainEqual({
      sql: 'SELECT id, total FROM orders WHERE customer_id = :p1 ORDER BY created_at DESC',
      target: 'e2e-guard',
    })
})

test('shows a streamed failure and can retry the analysis', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'error',
            code: 'rate_limited',
            message: 'The analysis service is temporarily busy',
          },
        ],
      },
      { events: serviceEvents(successEvents) },
    ],
  })
  let analysisCalls = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/analyze'
    ) {
      analysisCalls += 1
    }
  })
  await prepareResultsPage(page)

  await expect(
    page
      .getByRole('paragraph')
      .filter({ hasText: /^Analysis couldn't complete$/ })
  ).toBeVisible()
  await expect(
    page.getByText('The analysis service is temporarily busy')
  ).toBeVisible()

  await page.getByRole('button', { name: 'Try again' }).click()

  await expect(
    page.getByText('Performance score', { exact: true })
  ).toBeVisible()
  expect(analysisCalls).toBe(2)
})

test('presents an EXPLAIN connection failure as a target problem', async ({
  page,
}) => {
  const driverError =
    'PostgreSQL EXPLAIN failed: connection to server at "127.0.0.1", port 15434 failed: Connection refused'
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'complete',
            success: true,
            analysis_id: 'analysis-connection-failure',
            query_hash: 'query-connection-failure',
            explain_results: {
              success: false,
              database_engine: 'postgresql',
              execution_time_ms: 0,
              rows_examined: 0,
              rows_returned: 0,
              cost_estimate: 0,
              error: driverError,
            },
            llm_analysis: {},
          },
        ],
      },
    ],
  })
  await prepareResultsPage(page)

  await expect(
    page
      .getByRole('paragraph')
      .filter({ hasText: /^Analysis couldn't complete$/ })
  ).toBeVisible()
  await expect(
    page.getByText(
      'Could not connect to the database. Check that it is running and reachable, then try again.'
    )
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Check connection' })
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit query' })).toHaveCount(0)
  await expect(page.getByText(driverError)).toHaveCount(0)

  await page.getByText('Technical details').click()
  await expect(page.getByText(driverError)).toBeVisible()
})
