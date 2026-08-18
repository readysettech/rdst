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
WHERE customer_id = 42 AND status = 'shipped'`

const templatedQuery = 'SELECT id, total FROM orders WHERE customer_id = $1'

const indexRecommendations = [
  {
    sql: 'CREATE INDEX idx_orders_channel ON orders (channel)',
    table: 'orders',
    columns: ['channel'],
    index_type: 'btree',
    rationale: 'Speculative index on channel.',
    estimated_impact: 'high',
    caveats: [],
  },
  {
    sql: 'CREATE INDEX idx_orders_customer_status ON orders (customer_id, status)',
    table: 'orders',
    columns: ['customer_id', 'status'],
    index_type: 'btree',
    rationale: 'Covers the customer and status filters.',
    estimated_impact: 'medium',
    caveats: [],
  },
]

function completeAnalysis(indexTesting: Record<string, unknown>) {
  return {
    success: true,
    analysis_id: 'analysis-e2e-planner',
    query_hash: 'query-e2e-planner',
    explain_results: {
      success: true,
      database_engine: 'postgresql',
      execution_time_ms: 128.4,
      rows_examined: 12000,
      rows_returned: 25,
      cost_estimate: 49586,
    },
    llm_analysis: {
      success: true,
      performance_assessment: {
        overall_rating: 'poor',
        efficiency_score: 32,
        primary_concerns: ['Sequential scan reads too many rows'],
      },
      index_recommendations: indexRecommendations,
      optimization_opportunities: [],
      rewrite_suggestions: [],
    },
    rewrite_testing: {
      tested: false,
      skipped_reason: 'e2e_fixture',
      message: 'Skipped in browser tests',
    },
    index_testing: indexTesting,
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
}

function analyzeEvents(indexTesting: Record<string, unknown>) {
  return [
    {
      type: 'progress',
      stage: 'testing_indexes',
      percent: 78,
      message: 'Checking indexes with the planner...',
    },
    { type: 'complete', ...completeAnalysis(indexTesting) },
  ]
}

async function openResults(page: Parameters<typeof configureTestTarget>[0], sql = query) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await acceptExplainAnalyzeConsent(page)
  const search = new URLSearchParams({ query: sql, target: 'e2e-guard', fast: 'true' })
  await page.goto(`/results?${search.toString()}`)
}

test('promotes the index the planner verified with hypopg', async ({ page }) => {
  setBackendFixtures({
    analyze: [
      {
        events: analyzeEvents({
          tested: true,
          method: 'hypopg',
          baseline_cost: 49586,
          summary: 'Planner uses 1 of 2 recommended index(es) (estimated cost down 99.9%).',
          results: [
            {
              index_sql: 'CREATE INDEX idx_orders_channel ON orders (channel)',
              table: 'orders',
              columns: ['channel'],
              planner_used_index: false,
              hypothetical_index: '<1>btree_orders_channel',
              cost_before: 49586,
              cost_after: 49586,
              cost_reduction_pct: 0,
            },
            {
              index_sql:
                'CREATE INDEX idx_orders_customer_status ON orders (customer_id, status)',
              table: 'orders',
              columns: ['customer_id', 'status'],
              planner_used_index: true,
              hypothetical_index: '<2>btree_orders_customer_id_status',
              scan_type: 'Bitmap Index Scan',
              cost_before: 49586,
              cost_after: 35.8,
              cost_reduction_pct: 99.9,
            },
          ],
        }),
      },
    ],
  })
  await openResults(page)

  await expect(page.getByText('Recommended next step', { exact: true })).toBeVisible()
  // The planner-verified composite index wins over the guessed "high" one.
  await expect(page.getByText('Planner-verified', { exact: true })).toBeVisible()
  await expect(
    page.getByText(/hypopg check: the planner uses this index \(Bitmap Index Scan\)\. Estimated cost 49,586 to 36 \(99\.9% lower\)/)
  ).toBeVisible()
  // The index the planner ignores is still listed, with an explicit verdict.
  await expect(
    page.getByText(/hypopg check: the planner would not use this index for this query/)
  ).toBeVisible()
})

test('explains how to enable planner verification when hypopg is missing', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [
      {
        events: analyzeEvents({
          tested: false,
          skipped_reason: 'hypopg_not_installed',
          message:
            'hypopg is available on this server but not enabled in this database. Run the install statement as an administrator and re-run analyze to have index recommendations verified against the planner.',
          install_sql: 'CREATE EXTENSION IF NOT EXISTS hypopg;',
        }),
      },
    ],
  })
  await openResults(page)

  await expect(page.getByText('Recommended next step', { exact: true })).toBeVisible()
  await expect(page.getByText('Planner-verified', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/hypopg check unavailable: hypopg is available/)).toBeVisible()
  await expect(page.getByText('CREATE EXTENSION IF NOT EXISTS hypopg;')).toBeVisible()
})

test('offers sampled real values for a templated query before analyzing', async ({
  page,
}) => {
  const capturedSql = "SELECT id, total FROM orders WHERE customer_id = 4242"
  setBackendFixtures({
    parameter_suggestions: [
      {
        value: {
          placeholders: [
            {
              placeholder: '$1',
              index: 1,
              column: 'orders.customer_id',
              suggestions: [
                { value: '4242', provenance: 'pg_stat_activity (running now)' },
                { value: '17', provenance: 'Common value in orders.customer_id (pg_stats)' },
              ],
            },
          ],
          sample: {
            sql: capturedSql,
            source: 'pg_stat_activity (running now)',
            seen_at: null,
            aligned: true,
          },
        },
        repeat: true,
      },
    ],
    analyze: [{ events: analyzeEvents({ tested: false, skipped_reason: 'no_recommendations' }) }],
  })
  await openResults(page, templatedQuery)

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Enter parameter values')).toBeVisible()
  // Sampled values arrive from the database with their provenance.
  const chip = dialog.getByRole('button', { name: '17' })
  await expect(chip).toBeVisible()
  await expect(dialog.getByText('from orders.customer_id')).toBeVisible()
  await chip.click()
  await expect(dialog.getByLabel('Value for $1')).toHaveValue('17')
  await expect(
    dialog.getByText('Common value in orders.customer_id (pg_stats)')
  ).toBeVisible()

  // A captured real run can be analyzed as written.
  await expect(dialog.getByText(/A real run of this query was captured/)).toBeVisible()
  const analysisRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' && new URL(request.url()).pathname === '/api/analyze'
  )
  await dialog.getByRole('button', { name: 'Analyze captured statement' }).click()
  const request = await analysisRequest
  expect(request.postDataJSON()).toMatchObject({ query: capturedSql, target: 'e2e-guard' })
  await expect(page.getByText('Recommended next step', { exact: true })).toBeVisible()
})
