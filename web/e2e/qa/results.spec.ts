/**
 * The analysis screen: how many query blocks one Analyze produces, what the
 * block is called before and after a run, reading a stored analysis back, and
 * what a failed run says.
 */
import type { Page } from '@playwright/test'
import {
  acceptExplainAnalyzeConsent,
  expect,
  mainContent,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  completeAnalysis,
  DESKTOP,
  prepareQueries,
  recordPosts,
  shot,
  successEvents,
} from './_helpers'

test.use({ viewport: DESKTOP })

const ORDERS = 'SELECT id, total FROM orders ORDER BY created_at DESC'
const PARAM_SQL = 'SELECT id, email FROM users WHERE account_id = :account_id'
const YESTERDAY = new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString()

const EXPLAIN_FAILURE = {
  type: 'complete',
  success: true,
  analysis_id: 'analysis-explain-fail',
  query_hash: 'query-explain-fail',
  explain_results: {
    success: false,
    database_engine: 'postgresql',
    execution_time_ms: 0,
    rows_examined: 0,
    rows_returned: 0,
    cost_estimate: 0,
    error: 'PostgreSQL EXPLAIN failed: relation "orders" does not exist',
  },
  llm_analysis: {},
}

const PARTIAL_RESULT = {
  type: 'complete',
  success: true,
  analysis_id: 'analysis-partial',
  query_hash: 'query-partial',
  explain_results: {
    success: true,
    database_engine: 'postgresql',
    execution_time_ms: 44.2,
    rows_examined: 900,
    rows_returned: 12,
    cost_estimate: 12.5,
  },
  llm_analysis: { success: false, error: 'LLM provider returned 503' },
}

/** Clean registry, one configured target, and the given queries registered. */
async function prepare(
  page: Page,
  queries: string[] = [],
  { consent = true }: { consent?: boolean } = {}
) {
  if (consent) await acceptExplainAnalyzeConsent(page)
  const hashes = await prepareQueries(page, queries)
  await mockConnectivityOk(page)
  return hashes
}

function resultsUrl(extra: Record<string, string> = {}) {
  return `/results?${new URLSearchParams({
    query: ORDERS,
    target: 'e2e-guard',
    fast: 'false',
    ...extra,
  })}`
}

/**
 * B-04 - while the parameter form is collecting values it carries the query
 * itself, instead of repeating the same SQL under a second heading.
 */
test('the parameter form owns the only query block', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [PARAM_SQL])

  await page.goto(`/queries?analyze=${hash}`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer.getByTestId('parameter-form-inline')).toBeVisible({
    timeout: 15_000,
  })
  await shot(page, 'results-parameter-form')

  const body = await drawer.innerText()
  expect(body).not.toContain('ORIGINAL QUERY')
  expect(body).not.toContain('ANALYZED QUERY')
  await expect(drawer.getByText('Analyzed query')).toHaveCount(0)
  await expect(
    drawer.locator('[data-testid="query-card-main-content"]')
  ).toHaveCount(0)
})

/**
 * B-04 - the block is the query as written until a run exists, and only then
 * becomes the analyzed query.
 */
test('the query block is only called analyzed once it has run', async ({
  page,
}) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  // Consent is unanswered, so nothing has been measured yet.
  const [hash] = await prepare(page, [ORDERS], { consent: false })

  await page.goto(`/queries?analyze=${hash}`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer.getByTestId('analyze-consent-inline')).toBeVisible({
    timeout: 15_000,
  })
  await expect(drawer.getByText('Query', { exact: true })).toBeVisible()
  await expect(drawer.getByText('Analyzed query')).toHaveCount(0)

  await drawer.getByRole('button', { name: 'Run analyze' }).click()
  await expect(drawer.getByText('Analyzed query')).toBeVisible({
    timeout: 20_000,
  })
  await expect(drawer.getByText('Query', { exact: true })).toHaveCount(0)
  await shot(page, 'results-analyzed-query')
})

/**
 * B-12 - a stored analysis opens from its own id: the screen says when it ran
 * and offers the earlier runs, and nothing is measured again.
 */
test('a stored analysis reopens without re-running', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [ORDERS])
  const analyzeCalls = recordPosts(page, '/api/analyze')

  const summary = {
    analysis_id: 'stored-yesterday',
    analyzed_at: YESTERDAY,
    created_at: YESTERDAY,
    target: 'e2e-guard',
    overall_rating: 'poor',
    efficiency_score: 32,
  }
  const older = {
    ...summary,
    analysis_id: 'stored-older',
    analyzed_at: YESTERDAY,
    created_at: YESTERDAY,
  }
  await page.route(`**/api/query-registry/${hash}/analyses`, (route) =>
    route.fulfill({ json: { hash, analyses: [summary, older] } })
  )
  await page.route(`**/api/query-registry/${hash}/analysis/stored-*`, (route) =>
    route.fulfill({
      json: {
        hash,
        ...summary,
        analysis: {
          display_payload: {
            analysis_id: summary.analysis_id,
            query_hash: hash,
            explain_results: completeAnalysis.explain_results,
            llm_analysis: completeAnalysis.llm_analysis,
            rewrite_testing: completeAnalysis.rewrite_testing,
            readyset_cacheability: completeAnalysis.readyset_cacheability,
          },
        },
      },
    })
  )

  await page.goto(resultsUrl({ hash, analysisId: 'stored-yesterday' }))
  await expect(page.getByTestId('stored-analysis-header')).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByText(/^Viewing analysis from /)).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Analysis history' })
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Re-run analysis' })
  ).toBeVisible()
  await expect(
    page.getByText('Performance score', { exact: true })
  ).toBeVisible()
  await shot(page, 'results-stored-analysis')

  // Reading a stored result measures nothing.
  expect(analyzeCalls).toEqual([])
})

/**
 * F-27, B-29 - a failed analysis offers one retry verb, and the partial result
 * it retries into states the missing model once.
 */
test('a failed analysis retries, and its partial result says so once', async ({
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
      { events: [PARTIAL_RESULT], repeat: true },
    ],
  })
  await prepare(page, [ORDERS])

  await page.goto(resultsUrl())
  await expect(
    mainContent(page).getByText(/Analysis couldn't complete/)
  ).toBeVisible({ timeout: 20_000 })
  const retry = mainContent(page).getByRole('button', { name: 'Try again' })
  await expect(retry).toBeVisible()
  await retry.click()

  await expect(
    mainContent(page).getByText(
      "The analysis model wasn't available. Measured execution data is still shown."
    )
  ).toHaveCount(1, { timeout: 20_000 })
  await shot(page, 'results-partial')
})

/**
 * B-21 - a failed EXPLAIN is titled in the user's own words and keeps the raw
 * driver text behind a Technical details disclosure.
 */
test('an EXPLAIN failure is titled plainly', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: [EXPLAIN_FAILURE], repeat: true }] })
  await prepare(page, [ORDERS])

  await page.goto(resultsUrl())
  // The route's own header carries a "Back to queries" control, so the failure
  // card is read as the alert it is.
  const failure = mainContent(page).getByRole('alert')
  await expect(failure.getByText(/We couldn't run this query/)).toBeVisible({
    timeout: 20_000,
  })

  const details = failure.getByRole('button', { name: 'Technical details' })
  await expect(details).toBeVisible()
  await details.click()
  // The point of the finding is where the driver string lives, so read it from
  // the disclosure's own <pre> rather than from anywhere on the card.
  await expect(
    failure
      .locator('pre')
      .filter({ hasText: /relation "orders" does not exist/ })
  ).toBeVisible()
  await expect(
    failure.getByRole('button', { name: 'Back to queries' })
  ).toBeVisible()
  await shot(page, 'results-explain-failure')
})

/**
 * B-21 - the friendly sentence is the user's own words, with the driver
 * string left to the disclosure, and the failure offers a way back to the SQL.
 */
test('B-21 - an EXPLAIN failure keeps driver text out of its message', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [{ events: [EXPLAIN_FAILURE], repeat: true }],
  })
  await prepare(page, [ORDERS])

  await page.goto(resultsUrl())
  const title = mainContent(page).getByText(/We couldn't run this query/)
  await expect(title).toBeVisible({ timeout: 20_000 })

  const body = await mainContent(page).innerText()
  expect(body).not.toContain('PostgreSQL EXPLAIN failed')
  await expect(
    mainContent(page).getByRole('button', { name: 'Edit query' })
  ).toBeVisible()
})

/** B-21 - the way back to the SQL runs the corrected query. */
test('B-21 - editing the failed query measures the corrected one', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [{ events: [EXPLAIN_FAILURE] }, { events: successEvents }],
  })
  await prepare(page, [ORDERS])

  await page.goto(resultsUrl())
  const main = mainContent(page)
  await expect(main.getByText(/We couldn't run this query/)).toBeVisible({
    timeout: 20_000,
  })

  await main.getByRole('button', { name: 'Edit query' }).click()
  const editor = main.locator('.cm-content')
  await expect(editor).toBeVisible()
  await editor.click()
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type('SELECT id FROM public.orders')
  await main.getByRole('button', { name: 'Analyze', exact: true }).click()

  await expect(main.getByText(/We couldn't run this query/)).toHaveCount(0, {
    timeout: 20_000,
  })
  await expect(page).toHaveURL(/public\.orders/)
})
