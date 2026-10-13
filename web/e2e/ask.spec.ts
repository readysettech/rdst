import {
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from './fixtures'

type AskEvent = {
  event: string
  data: Record<string, unknown>
}

function serviceEvents(events: AskEvent[]) {
  return events.map(({ data }) => data)
}

test('asks a question and renders the answer first with the post-validation SQL behind a disclosure', async ({
  page,
}) => {
  setBackendFixtures({
    ask: [
      {
        events: serviceEvents([
          {
            event: 'status',
            data: {
              type: 'status',
              phase: 'schema',
              message: 'Loading schema',
            },
          },
          {
            event: 'schema_loaded',
            data: {
              type: 'schema_loaded',
              source: 'semantic',
              table_count: 2,
              tables: ['customers', 'orders'],
              target: 'e2e-guard',
            },
          },
          {
            event: 'status',
            data: {
              type: 'status',
              phase: 'generate',
              message: 'Generating SQL',
            },
          },
          {
            event: 'sql_generated',
            data: {
              type: 'sql_generated',
              sql: 'SELECT customer, SUM(total) AS revenue FROM orders GROUP BY customer',
              explanation: 'Aggregates order totals by customer.',
            },
          },
          {
            event: 'result',
            data: {
              type: 'result',
              success: true,
              // Post-validation SQL: the LIMIT the backend injected is part of
              // the query that actually ran, and limit_added says so.
              sql: 'SELECT customer, SUM(total) AS revenue FROM orders GROUP BY customer LIMIT 100',
              columns: ['customer', 'revenue'],
              rows: [
                ['Ada', 4200],
                ['Grace', 3100],
              ],
              row_count: 2,
              execution_time_ms: 12.4,
              llm_calls: 1,
              total_tokens: 240,
              query_hash: 'ask-query-1',
              query_tag: 'top customers',
              limit_added: true,
            },
          },
        ]),
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
  // Ask preflights target reachability before POST /api/ask.
  await mockConnectivityOk(page)
  const requests: Record<string, unknown>[] = []
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/ask'
    ) {
      requests.push(request.postDataJSON() as Record<string, unknown>)
    }
  })

  await page.goto('/ask')
  await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()

  const question = 'Who are our highest revenue customers?'
  await page.getByRole('textbox', { name: 'Question' }).fill(question)
  await page.getByRole('button', { name: 'Ask', exact: true }).click()

  // Answer-first: the status line stamps provenance from the stream's own
  // target, then the explanation and the table.
  const status = page.getByTestId('ask-status')
  await expect(status).toContainText('Answered from e2e-guard')
  await expect(status).toContainText('2 rows')
  await expect(
    page.getByText('Aggregates order totals by customer.')
  ).toBeVisible()
  const table = page.getByTestId('ask-table')
  await expect(table).toContainText('customer')
  await expect(page.getByTestId('ask-row').first()).toContainText('Ada')
  expect(requests).toEqual([{ question, target: 'e2e-guard' }])

  // The SQL is folded by default; opening it shows the POST-VALIDATION query
  // (with the injected LIMIT) and the disclosure names the added LIMIT.
  await expect(page.getByTestId('ask-sql')).toHaveCount(0)
  const disclosure = page.getByRole('button', { name: /SQL that ran/ })
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await expect(disclosure).toContainText(/LIMIT \d+ added/)
  await disclosure.click()
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByTestId('ask-sql')).toContainText('LIMIT 100')

  // The silent auto-save is surfaced with the returned registry label.
  await expect(status).toContainText('saved as top customers')

  // The question stays in the box after the answer.
  await expect(page.getByRole('textbox', { name: 'Question' })).toHaveValue(
    question
  )
})

test('answers a clarification and resumes the original ask session', async ({
  page,
}) => {
  setBackendFixtures({
    ask: [
      {
        events: serviceEvents([
          {
            event: 'clarification_needed',
            data: {
              type: 'clarification_needed',
              session_id: 'ask-session-42',
              interpretations: [
                {
                  id: 1,
                  description: 'Revenue before refunds',
                  assumptions: ['Use gross order totals'],
                  likelihood: 0.8,
                },
              ],
              questions: [
                {
                  id: 'revenue_definition',
                  question: 'Which revenue definition: choose one',
                  options: ['Gross revenue', 'Net revenue'],
                },
              ],
            },
          },
        ]),
      },
    ],
    ask_resume: [
      {
        events: serviceEvents([
          {
            event: 'sql_generated',
            data: {
              type: 'sql_generated',
              sql: 'SELECT SUM(total) AS gross_revenue FROM orders',
              explanation: 'Uses gross order totals.',
            },
          },
          {
            event: 'result',
            data: {
              type: 'result',
              success: true,
              sql: 'SELECT SUM(total) AS gross_revenue FROM orders',
              columns: ['gross_revenue'],
              rows: [[7300]],
              row_count: 1,
              execution_time_ms: 4.8,
              llm_calls: 2,
              total_tokens: 380,
            },
          },
        ]),
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
  // Ask preflights target reachability before POST /api/ask.
  await mockConnectivityOk(page)
  const requests: Record<string, unknown>[] = []
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/ask'
    ) {
      requests.push(request.postDataJSON() as Record<string, unknown>)
    }
  })

  await page.goto('/ask')
  const question = 'How much revenue did we make?'
  await page.getByRole('textbox', { name: 'Question' }).fill(question)
  await page.getByRole('button', { name: 'Ask', exact: true }).click()

  // The original question stays in the box above the choice.
  await expect(page.getByRole('textbox', { name: 'Question' })).toHaveValue(
    question
  )
  // The clarifying question renders as written, colon and all.
  await expect(
    page.getByText('Which revenue definition: choose one', { exact: true })
  ).toBeVisible()
  await page.getByRole('radio', { name: /Gross revenue/ }).check()
  // The submit button names the outcome it runs.
  await page.getByRole('button', { name: 'Run with gross revenue' }).click()

  await expect(page.getByTestId('ask-row').first()).toContainText('7,300')
  expect(requests).toEqual([
    { question, target: 'e2e-guard' },
    {
      question,
      target: 'e2e-guard',
      session_id: 'ask-session-42',
      clarification_answers: { revenue_definition: 'Gross revenue' },
    },
  ])
})

test('a streamed Ask failure keeps the question and Try again re-runs it', async ({
  page,
}) => {
  setBackendFixtures({
    ask: [
      {
        events: serviceEvents([
          {
            event: 'error',
            data: {
              type: 'error',
              message: 'The SQL generator is temporarily unavailable',
              phase: 'generate',
            },
          },
        ]),
      },
      // The true retry issues a second POST; it fails again in this fixture.
      {
        events: serviceEvents([
          {
            event: 'error',
            data: {
              type: 'error',
              message: 'The SQL generator is temporarily unavailable',
              phase: 'generate',
            },
          },
        ]),
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
  // Ask preflights target reachability before POST /api/ask.
  await mockConnectivityOk(page)
  const requests: Record<string, unknown>[] = []
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/ask'
    ) {
      requests.push(request.postDataJSON() as Record<string, unknown>)
    }
  })

  await page.goto('/ask')
  const question = 'Show recent orders'
  await page.getByRole('textbox', { name: 'Question' }).fill(question)
  await page.getByRole('button', { name: 'Ask', exact: true }).click()

  const failure = page.getByTestId('ask-failure')
  await expect(failure).toContainText(
    'The SQL generator is temporarily unavailable'
  )

  // TRUE retry: the SAME question is re-run — the input is never wiped.
  // The retry renders the same error text as the first attempt, so waiting on
  // it cannot distinguish the attempts; synchronize on the second POST itself.
  const retryRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/ask'
  )
  await page.getByRole('button', { name: 'Try again' }).click()
  await retryRequest
  await expect(failure).toContainText(
    'The SQL generator is temporarily unavailable'
  )
  expect(requests).toEqual([
    { question, target: 'e2e-guard' },
    { question, target: 'e2e-guard' },
  ])

  // A failure routes back to the composer with the question intact, so the
  // recovery is "Edit question" rather than a fresh, empty box.
  await page.getByRole('button', { name: 'Edit question' }).click()
  await expect(page.getByRole('textbox', { name: 'Question' })).toHaveValue(
    question
  )
  await expect(
    page.getByRole('button', { name: 'Ask', exact: true })
  ).toBeEnabled()
})

test('a model context limit is shown as a schema-size error', async ({
  page,
}) => {
  setBackendFixtures({
    ask: [
      {
        events: serviceEvents([
          {
            event: 'error',
            data: {
              type: 'error',
              code: 'ANTHROPIC_CONTEXT_WINDOW_EXCEEDED',
              category: 'model-limit',
              message:
                "The complete database schema could not fit within Anthropic's model context window. RDST tried both complete lossless schema formats. RDST did not truncate the schema. Request ID: req_schema.",
              phase: 'generate',
            },
          },
        ]),
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await page.goto('/ask')
  await page
    .getByRole('textbox', { name: 'Question' })
    .fill('Show recent orders')
  await page.getByRole('button', { name: 'Ask', exact: true }).click()

  const failure = page.getByTestId('ask-failure')
  await expect(failure).toContainText(
    'The complete database schema could not fit'
  )
  // A schema that does not fit is neither a credential problem nor something a
  // retry of the same question can fix.
  await expect(failure.getByRole('link', { name: 'Sign in' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0)
  await expect(
    page.getByRole('button', { name: 'Edit question' })
  ).toBeVisible()
})
