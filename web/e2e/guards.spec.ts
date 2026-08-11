import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

const piiGuard = {
  name: 'pii-guard',
  description: 'Masks PII and blocks unbounded reads',
  intent: 'Protect customer data in analytical queries',
  derived: false,
  masking: { '*.email': 'email' },
  restrictions: {
    denied_columns: ['users.password'],
    allowed_tables: ['users', 'orders'],
    required_filters: { orders: ['tenant_id'] },
  },
  guards: {
    require_where: true,
    require_limit: false,
    no_select_star: true,
    max_tables: 2,
    cost_limit: null,
    max_estimated_rows: null,
  },
  limits: { max_rows: 1000, timeout_seconds: 30 },
  created_at: '2026-07-15T12:00:00Z',
}

test('loads guard details, checks SQL, and creates a guard', async ({
  page,
}) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  const existing = (await (await page.request.get('/api/guards')).json()) as {
    guards: { name: string }[]
  }
  for (const guard of existing.guards) {
    expect((await page.request.delete(`/api/guards/${guard.name}`)).ok()).toBe(
      true
    )
  }
  expect(
    (await page.request.post('/api/guards', { data: piiGuard })).ok()
  ).toBe(true)
  const createRequests: Record<string, unknown>[] = []
  const checkRequests: Record<string, unknown>[] = []
  page.on('request', (request) => {
    const { pathname } = new URL(request.url())
    if (
      request.method() === 'POST' &&
      pathname === '/api/guards/pii-guard/check'
    ) {
      checkRequests.push(request.postDataJSON() as Record<string, unknown>)
    }
    if (request.method() === 'POST' && pathname === '/api/guards') {
      createRequests.push(request.postDataJSON() as Record<string, unknown>)
    }
  })

  await page.goto('/guards')
  await expect(
    page.getByRole('heading', { name: 'Query Guards' })
  ).toBeVisible()
  await expect(page.getByText('WHERE required', { exact: true })).toBeVisible()

  const piiRow = page.getByRole('button').filter({ hasText: 'pii-guard' })
  await expect(piiRow).toHaveCount(1)
  await piiRow.click()
  await expect(
    page.getByText('Protect customer data in analytical queries', {
      exact: true,
    })
  ).toBeVisible()
  await expect(page.getByText('users.password', { exact: true })).toBeVisible()
  await expect(page.getByText('tenant_id', { exact: true })).toBeVisible()

  await page.locator('[name="test-sql"]').fill('SELECT * FROM users')
  await page.getByRole('button', { name: 'Run check' }).click()
  await expect(
    page.getByText('BLOCKED by pii-guard', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Missing WHERE clause', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('SELECT * detected', { exact: true })
  ).toBeVisible()
  expect(checkRequests).toEqual([
    { sql: 'SELECT * FROM users', target: 'e2e-guard' },
  ])

  await page.getByRole('button', { name: 'New Guard' }).click()
  await page.locator('[name="guard-name"]').fill('reporting-guard')
  await page
    .locator('[name="guard-description"]')
    .fill('Requires bounded reporting queries')
  await page.getByRole('button', { name: 'Add pattern' }).click()
  await page.locator('[name="mask-pattern-0"]').fill('customers.email')
  // Advanced sections are collapsed-by-default disclosures now [C-09 guards
  // migration] — open each before filling the fields inside it.
  await page
    .getByRole('button', { name: /Table & column restrictions/ })
    .click()
  await page.locator('[name="denied-columns"]').fill('users.password')
  await page.locator('[name="allowed-tables"]').fill('customers\norders')
  await page.getByRole('button', { name: /Rules/ }).click()
  await page.getByRole('switch', { name: 'Require WHERE' }).click()
  await page.getByRole('button', { name: /Limits/ }).click()
  await page.locator('[name="max-rows"]').fill('500')
  await page.getByRole('button', { name: 'Create Guard' }).click()

  await expect(page.getByText('Guard created', { exact: true })).toBeVisible()
  const reportingRow = page
    .getByRole('button')
    .filter({ hasText: 'reporting-guard' })
  await expect(reportingRow).toHaveCount(1)
  await expect(reportingRow).toBeVisible()
  expect(createRequests).toEqual([
    {
      name: 'reporting-guard',
      description: 'Requires bounded reporting queries',
      intent: '',
      derived: false,
      masking: { 'customers.email': 'redact' },
      restrictions: {
        denied_columns: ['users.password'],
        allowed_tables: ['customers', 'orders'],
        required_filters: null,
      },
      guards: {
        require_where: true,
        require_limit: false,
        no_select_star: false,
        max_tables: null,
        cost_limit: null,
        max_estimated_rows: null,
      },
      limits: { max_rows: 500, timeout_seconds: 30 },
    },
  ])
})
