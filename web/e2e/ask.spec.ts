import {
  configureTestTarget,
  expect,
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

test('asks a question and renders generated SQL with query results', async ({
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
              source: 'semantic layer',
              table_count: 2,
              tables: ['customers', 'orders'],
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
              sql: 'SELECT customer, SUM(total) AS revenue FROM orders GROUP BY customer',
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
            },
          },
        ]),
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
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
  await expect(
    page.getByRole('heading', { name: 'Ask in Plain English' })
  ).toBeVisible()

  const question = 'Who are our highest revenue customers?'
  await page
    .getByPlaceholder('Ask a question about your data...')
    .fill(question)
  await page.getByRole('button', { name: 'Generate SQL' }).click()

  await expect(page.getByText('Generated SQL', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Aggregates order totals by customer.', { exact: true })
  ).toBeVisible()
  await expect(page.getByText('Query Results', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('columnheader', { name: 'customer' })
  ).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Ada' })).toBeVisible()
  await expect(page.getByText('2 rows', { exact: true })).toBeVisible()
  expect(requests).toEqual([{ question, target: 'e2e-guard' }])

  await page.getByRole('button', { name: 'Ask another question' }).click()
  await expect(
    page.getByPlaceholder('Ask a question about your data...')
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
    .getByPlaceholder('Ask a question about your data...')
    .fill(question)
  await page.getByRole('button', { name: 'Generate SQL' }).click()

  await expect(
    page.getByText(
      'I need some clarification to better understand your question.'
    )
  ).toBeVisible()
  await expect(
    page.getByText('Which revenue definition', { exact: true })
  ).toBeVisible()
  await page.getByRole('radio', { name: 'Gross revenue' }).check()
  await page.getByRole('button', { name: 'Generate SQL' }).click()

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

test('shows a streamed Ask failure and returns to a clean input', async ({
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
    ],
  })
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/ask')
  await page
    .getByPlaceholder('Ask a question about your data...')
    .fill('Show recent orders')
  await page.getByRole('button', { name: 'Generate SQL' }).click()

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

  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(
    page.getByPlaceholder('Ask a question about your data...')
  ).toHaveValue('')
  await expect(
    page.getByRole('button', { name: 'Generate SQL' })
  ).toBeDisabled()
})
