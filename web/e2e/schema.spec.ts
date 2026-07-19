import {
  configureTestTarget,
  consumeBrowserError,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

// YAML-shape semantic layer definition. The fake init persists it through
// the real SemanticLayer parser and manager, so the status, details, and
// delete flows below run the production read paths against real disk state.
const semanticLayer = {
  tables: {
    customers: {
      description: 'Customer accounts and contact details',
      business_context: 'One row per customer account.',
      row_estimate: '1200',
      columns: {
        id: { type: 'bigint', description: 'Primary customer identifier' },
        email: {
          type: 'text',
          description: 'Primary contact email',
          is_pii: true,
        },
      },
      relationships: [
        {
          target_table: 'orders',
          relationship_type: 'one_to_many',
          join_pattern: 'customers.id = orders.customer_id',
        },
      ],
    },
  },
  terminology: {
    'active customer': {
      definition: 'A customer with an order in the last 30 days',
      sql_pattern: "last_order_at > NOW() - INTERVAL '30 days'",
      synonyms: ['recent customer'],
    },
  },
  metrics: {
    'customer revenue': {
      definition: 'Total completed order revenue',
      sql: 'SUM(orders.total)',
    },
  },
}

test('initializes, explores, refreshes, and deletes a semantic layer', async ({
  page,
}) => {
  setBackendFixtures({
    semantic_init: [
      {
        layer: semanticLayer,
        events: [
          {
            type: 'complete',
            operation: 'init',
            success: true,
            init_result: {
              success: true,
              target: 'e2e-guard',
              tables: 1,
              columns: 2,
              relationships: 1,
              enum_columns: [],
              path: '/tmp/e2e-guard-schema.yaml',
              error: null,
            },
          },
        ],
      },
    ],
    semantic_refresh: [
      {
        value: {
          ok: true,
          message: 'Discovered one updated table',
          data: {},
        },
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
  const initRequests: Record<string, unknown>[] = []
  const refreshRequests: Record<string, unknown>[] = []
  let deleteCount = 0
  page.on('request', (request) => {
    const { pathname } = new URL(request.url())
    if (
      request.method() === 'POST' &&
      pathname === '/api/semantic-layer/init'
    ) {
      initRequests.push(request.postDataJSON() as Record<string, unknown>)
    }
    if (
      request.method() === 'POST' &&
      pathname === '/api/semantic-layer/refresh'
    ) {
      refreshRequests.push(request.postDataJSON() as Record<string, unknown>)
    }
    if (request.method() === 'DELETE' && pathname === '/api/semantic-layer') {
      deleteCount += 1
    }
  })

  await page.goto('/schema')
  await expect(
    page.getByRole('heading', { name: 'Semantic Layer' })
  ).toBeVisible()
  await expect(
    page.getByText('Initialize Semantic Layer', { exact: true })
  ).toBeVisible()

  await page.getByRole('button', { name: 'Initialize Schema' }).click()

  await expect(
    page.getByText('Customer accounts and contact details')
  ).toBeVisible()
  expect(initRequests).toEqual([
    {
      target: 'e2e-guard',
      enum_threshold: 20,
      force: false,
      sample_enums: true,
    },
  ])

  const customerTable = page
    .getByRole('button')
    .filter({ hasText: 'customers' })
  await expect(customerTable).toHaveCount(1)
  await customerTable.click()
  await expect(
    page.getByText('One row per customer account.', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Primary contact email', { exact: true })
  ).toBeVisible()
  await expect(page.getByText('PII', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect(
    page.getByText('Schema refreshed', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Discovered one updated table', { exact: true })
  ).toBeVisible()
  expect(refreshRequests).toEqual([{ target: 'e2e-guard' }])

  await page.getByRole('button', { name: 'Delete Semantic Layer' }).click()
  await page.getByRole('button', { name: 'Yes, Delete' }).click()
  await expect(
    page.getByText('Initialize Semantic Layer', { exact: true })
  ).toBeVisible()
  expect(deleteCount).toBe(1)
})

test('surfaces a semantic-layer status failure', async ({
  page,
  browserErrors,
}) => {
  setBackendFixtures({
    semantic_status: [
      {
        error: {
          status: 503,
          detail: 'database introspection unavailable',
        },
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/schema')
  // C-01's shared error handler (register_error_handlers) wraps every HTTP
  // failure in the {code, message, detail} envelope, so the 503 body is no
  // longer the bare {detail} FastAPI shape. The schema surface still renders
  // the raw `Error: HTTP <status>: <body>` line, so the visible text now
  // carries the full envelope.
  await expect(
    page.getByText(
      'Error: HTTP 503: {"code":"service_unavailable","message":"database introspection unavailable","detail":"database introspection unavailable"}',
      {
        exact: true,
      }
    )
  ).toBeVisible()
  consumeBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 503 (Service Unavailable)'
  )
})
