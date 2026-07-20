import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  fillCodeMirror,
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
    confidence: 'high',
    method: 'static',
    explanation: 'The query is cacheable.',
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

async function prepareAnalysisPage(
  page: Parameters<typeof configureTestTarget>[0]
) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/analyze')
  await expect(page.getByText('e2e-guard', { exact: true })).toBeVisible()
}

test('submits SQL and renders streamed analysis results', async ({ page }) => {
  setBackendFixtures({
    analyze: [{ events: serviceEvents(successEvents), delay_ms: 250 }],
  })
  await prepareAnalysisPage(page)

  await fillCodeMirror(page.locator('.cm-editor'), query)
  // C-09 moved the fast-mode switch behind the editor's "Options" popover;
  // open it first — the switch only mounts while the popover is open.
  await page.getByRole('button', { name: 'Options' }).click()
  const fastMode = page.getByRole('switch')
  await fastMode.click()
  await expect(fastMode).toHaveAttribute('data-state', 'checked')

  const analysisRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/analyze'
  )
  await page.getByRole('button', { name: 'Analyze Query' }).click()

  const request = await analysisRequest
  expect(request.postDataJSON()).toEqual({
    fast: true,
    query,
    target: 'e2e-guard',
  })
  await expect(page).toHaveURL(/\/results(?:\?|$)/)
  // The analyzing view now shows "Preparing" twice (stage caption + current
  // stage headline), so pick the first match.
  await expect(
    page.getByText('Preparing', { exact: true }).first()
  ).toBeVisible()

  await expect(
    page.getByText('Performance Summary', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Sequential scan reads too many rows')
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'Index Recommendations' })
  ).toBeVisible()
  // The optimization-opportunities section collapsed into a quiet
  // "More recommendations (N)" disclosure on the results page; open it and
  // check the recommendation text it reveals.
  await page.getByRole('button', { name: /More recommendations/ }).click()
  await expect(
    page.getByText('Return only columns needed by the caller.')
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'Readyset Cacheability' })
  ).toBeVisible()
  await expect(
    page.getByRole('paragraph').filter({ hasText: /^Cacheable$/ })
  ).toBeVisible()

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

test('shows a streamed failure and can retry from query history', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'error',
            message: 'EXPLAIN ANALYZE timed out after 30 seconds',
          },
        ],
      },
      { events: serviceEvents(successEvents) },
    ],
  })
  await prepareAnalysisPage(page)
  let analysisCalls = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/analyze'
    ) {
      analysisCalls += 1
    }
  })

  await fillCodeMirror(page.locator('.cm-editor'), query)
  await page.getByRole('button', { name: 'Analyze Query' }).click()

  // C-01 rebuilt the analyze failure surface on the shared ErrorState. The
  // title is now reserved for invalid-SQL ("Analysis Failed"); every other
  // failure (a timeout classifies as `database`) titles "Analysis could not
  // complete". The streamed message is the humane summary and stays visible;
  // no technical-details expander renders here because the SSE error carried
  // no separate `detail`. Retry-from-history still runs through the page's
  // "Back" affordance (the ErrorState's own action routes to Settings).
  // Match the visible title paragraph by role: the branded ErrorState also
  // exposes the title as the status icon's accessible label, so a plain text
  // match collides with that second node. Scoping to the paragraph keeps the
  // assertion stable across the error-surface's mid-stack refinements.
  await expect(
    page
      .getByRole('paragraph')
      .filter({ hasText: /^Analysis could not complete$/ })
  ).toBeVisible()
  await expect(
    page.getByText('EXPLAIN ANALYZE timed out after 30 seconds')
  ).toBeVisible()

  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(page).toHaveURL(/\/analyze$/)
  // The query-history row is itself a div[role="button"] card that nests a real
  // <button> "Use query". The shared Button renders its label for BOTH the icon's
  // a11y-name and the visible text, so the accessible name is "Use query Use
  // query" — target the real <button> element (the card is a div) to dodge both
  // the doubled name and the strict-mode collision with the card.
  await page.locator('button', { hasText: 'Use query' }).click()
  // "Use query" reloads the saved query with its captured parameter values
  // applied (rdst-e7s.28), so :p1 comes back pre-filled with the 42 it last ran
  // with — re-analyzing prompts for no parameters and runs directly.
  await expect(page.locator('.cm-content[contenteditable="true"]')).toHaveText(
    /SELECT id, total FROM orders WHERE customer_id = 42 ORDER BY created_at DESC/
  )
  await page.getByRole('button', { name: 'Analyze Query' }).click()

  await expect(
    page.getByText('Performance Summary', { exact: true })
  ).toBeVisible()
  expect(analysisCalls).toBe(2)
})
