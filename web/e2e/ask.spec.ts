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
  await page
    .getByPlaceholder(
      'For example: Which customers placed the most orders this month?'
    )
    .fill(question)
  // The composer submit is the only button named "Ask" — the view switcher's
  // "Ask" segment is a tab, so it never collides on the button role.
  await page.getByRole('button', { name: 'Get answer' }).click()

  // Answer-first: the results table leads, with provenance stamped from the
  // stream's own target and the plain-English explanation beside it.
  await expect(page.getByText('Answer', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Answered from e2e-guard via semantic layer')
  ).toBeVisible()
  await expect(
    page.getByText('Aggregates order totals by customer.')
  ).toBeVisible()
  await expect(
    page.getByRole('columnheader', { name: 'customer' })
  ).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Ada' })).toBeVisible()
  await expect(page.getByText('2 rows', { exact: true })).toBeVisible()
  expect(requests).toEqual([{ question, target: 'e2e-guard' }])

  // The SQL is collapsed by default; toggling reveals the POST-VALIDATION
  // query (with the injected LIMIT) plus the backend's LIMIT-added note.
  await expect(page.locator('.cm-content')).toHaveCount(0)
  const disclosure = page.getByRole('button', {
    name: /Show the SQL that ran/,
  })
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await disclosure.click()
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('.cm-content')).toContainText('LIMIT 100')
  await expect(
    page.getByText('was added to keep the result set bounded')
  ).toBeVisible()

  // The silent auto-save is surfaced with the returned registry label.
  await expect(page.getByText('Saved as top customers')).toBeVisible()

  // Only "Ask another" clears the input.
  await page.getByRole('button', { name: 'Ask another' }).click()
  await expect(
    page.getByPlaceholder(
      'For example: Which customers placed the most orders this month?'
    )
  ).toHaveValue('')
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
  await page
    .getByPlaceholder(
      'For example: Which customers placed the most orders this month?'
    )
    .fill(question)
  // The composer submit is the only button named "Ask" — the view switcher's
  // "Ask" segment is a tab, so it never collides on the button role.
  await page.getByRole('button', { name: 'Get answer' }).click()

  // Calm clarification heading with the original question echoed above the
  // options — never dropped.
  await expect(
    page.getByText('One quick question', { exact: true })
  ).toBeVisible()
  await expect(page.getByText(`You asked: ${question}`)).toBeVisible()
  await expect(
    page.getByText('Which revenue definition', { exact: true })
  ).toBeVisible()
  await page.getByRole('radio', { name: 'Gross revenue' }).check()
  // The submit button says the outcome ("Get answer"), not a step advance.
  await page.getByRole('button', { name: 'Get answer' }).click()

  await expect(page.getByRole('cell', { name: '7300' })).toBeVisible()
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
  await page
    .getByPlaceholder(
      'For example: Which customers placed the most orders this month?'
    )
    .fill(question)
  // The composer submit is the only button named "Ask" — the view switcher's
  // "Ask" segment is a tab, so it never collides on the button role.
  await page.getByRole('button', { name: 'Get answer' }).click()

  await expect(
    page.getByText("Couldn't generate SQL", { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('The SQL generator is temporarily unavailable', {
      exact: true,
    })
  ).toBeVisible()
  await expect(
    page.getByText('Failed while: Generating SQL', { exact: true })
  ).toBeVisible()

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
  await expect(
    page.getByText("Couldn't generate SQL", { exact: true })
  ).toBeVisible()
  expect(requests).toEqual([
    { question, target: 'e2e-guard' },
    { question, target: 'e2e-guard' },
  ])

  // Only "Ask another" clears the box and returns to a clean input.
  await page.getByRole('button', { name: 'Ask another' }).click()
  await expect(
    page.getByPlaceholder(
      'For example: Which customers placed the most orders this month?'
    )
  ).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Get answer' })).toBeDisabled()
})
