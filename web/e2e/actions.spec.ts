import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

// The global Actions page over the real action engine and seeded facts: on e2e-guard, five
// index adds, one setting and one maintenance task in the plan; a verified change, an undone
// one and two dismissed ones in Applied.
test.describe.configure({ timeout: 90_000 })

test.beforeEach(async ({ page, browserErrors }) => {
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await configureTestTarget(page, { hasPassword: true })
  expect((await page.request.post('/api/testing/monitor/seed')).ok()).toBe(true)
  expect(
    (await page.request.post('/api/testing/monitor/seed-actions')).ok()
  ).toBe(true)
  expect(
    (
      await page.request.put('/api/monitor/targets/e2e-guard/settings', {
        data: { enabled: true },
      })
    ).ok()
  ).toBe(true)
  await mockPrerequisites(page, 'e2e-guard')
})

test('groups every change by category, then by priority, and narrows to a linked kind', async ({
  page,
}) => {
  await page.goto('/actions')
  await expect(
    page.getByRole('heading', { name: 'Actions', exact: true })
  ).toBeVisible()
  await expect(
    page.locator('[data-page-bar]').getByText('7 to do', { exact: true })
  ).toBeVisible()
  await expect(page.getByTestId('actions-lead')).toContainText(
    'Start with the index on orders: one change speeds up 1 query'
  )
  await expect(page.getByTestId('plan-row')).toHaveCount(7)
  const groups = page.getByTestId('plan-group')
  await expect(groups.locator('h2')).toHaveText([
    'Indexes',
    'Server settings',
    'Maintenance',
    'Query rewrites',
  ])
  await expect(groups.nth(0)).toContainText('5 to add')
  await expect(groups.nth(1)).toContainText('1 to set')
  await expect(groups.nth(2)).toContainText('1 to run')
  await expect(groups.nth(3)).toContainText('Nothing to change')
  await expect(groups.nth(0).getByTestId('plan-verb').first()).toHaveText('Add')
  // One script per database: Copy all SQL waits for one to be picked.
  await expect(page.getByRole('button', { name: 'Copy all SQL' })).toHaveCount(
    0
  )

  await page
    .getByRole('radiogroup', { name: 'Group by' })
    .getByRole('radio', { name: 'Priority' })
    .click()
  await expect(page).toHaveURL(/[?&]group=priority(&|$)/)
  await expect(groups.locator('h2')).toHaveText(['Do first', 'Then', 'Later'])

  await page.goto('/actions?kind=setting')
  await expect(page.getByTestId('plan-row')).toHaveCount(1)
  await expect(page.getByTestId('plan-row')).toContainText('work_mem')
  await expect(page.getByTestId('plan-verb')).toHaveText('Set')
  await page.getByRole('button', { name: 'Setting, clear' }).click()
  await expect(page).not.toHaveURL(/[?&]kind=/)
  await expect(page.getByTestId('plan-row')).toHaveCount(7)
})

test('opening Actions for a database closes its Changes ready event once', async ({
  page,
}) => {
  const opened = page.waitForResponse(
    (response) =>
      response
        .url()
        .endsWith('/api/monitor/targets/e2e-guard/actions/opened') &&
      response.request().method() === 'POST' &&
      response.ok()
  )
  await page.goto('/actions?db=e2e-guard')
  await opened
  await expect(page.getByText('7 of 7 to do')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Copy all SQL' })).toBeVisible()
})

test('a change opens in the side pane on its evidence and SQL, and a dismissed one comes back', async ({
  page,
}) => {
  await page.goto('/actions?db=e2e-guard')
  const orders = page
    .getByTestId('plan-row')
    .filter({ hasText: 'orders (customer_id)' })
  await orders
    .getByRole('button', { name: /Add orders \(customer_id\)/ })
    .click()
  await expect(page).toHaveURL(/[?&]action=\d+/)
  const detail = page
    .getByRole('region', { name: /Add an index on orders\(customer_id\)/ })
    .getByTestId('plan-detail')
  await expect(detail).toContainText('SELECT on orders by customer_id')
  await expect(detail.getByTestId('plan-sql')).toContainText(
    'CREATE INDEX CONCURRENTLY idx_orders_customer_id ON orders (customer_id);'
  )
  await expect(detail.getByTestId('plan-rollback-sql')).toContainText(
    'DROP INDEX CONCURRENTLY public.idx_orders_customer_id;'
  )
  await expect(
    detail.getByRole('link', { name: 'Open query' }).first()
  ).toHaveAttribute('href', /\/database\?target=e2e-guard&query=orders-e2e/)
  await expect(detail).toContainText('Builds without blocking writes.')

  await page
    .getByTestId('plan-verbs')
    .getByRole('button', { name: 'Dismiss' })
    .click()
  const popover = page.getByRole('dialog', { name: 'Dismiss' })
  await popover.getByLabel('Reason').fill('Orders moves to its own service')
  const dismissed = page.waitForResponse(
    (response) =>
      /\/actions\/\d+\/dismiss$/.test(response.url()) && response.ok()
  )
  await popover.getByRole('button', { name: 'Dismiss' }).click()
  await dismissed
  await expect(page.getByTestId('plan-row')).toHaveCount(6)

  const list = page.getByTestId('plan-dismissed')
  await list.getByRole('button', { name: 'Dismissed (3)' }).click()
  const row = list
    .getByTestId('closed-row')
    .filter({ hasText: 'Add an index on orders(customer_id)' })
  await expect(row).toContainText('Orders moves to its own service')
  await row.getByRole('button', { name: 'Bring back' }).click()
  await expect(page.getByTestId('plan-row')).toHaveCount(7)
})

test('a change set aside until a date waits under the plan', async ({
  page,
}) => {
  await page.goto('/actions?db=e2e-guard')
  const vacuum = page
    .getByTestId('plan-row')
    .filter({ hasText: '1 bloated table' })
  await vacuum.getByRole('button', { name: /Vacuum 1 bloated table/ }).click()
  await page
    .getByTestId('plan-verbs')
    .getByRole('button', { name: 'Set aside' })
    .click()
  const popover = page.getByRole('dialog', { name: 'Set aside' })
  await popover.getByLabel('Reason').fill('After the quarter closes')
  const aside = page.waitForResponse(
    (response) =>
      /\/actions\/\d+\/set_aside$/.test(response.url()) && response.ok()
  )
  await popover.getByRole('button', { name: 'Set aside' }).click()
  await aside
  const list = page.getByTestId('plan-set-aside')
  await expect(list).toContainText('Vacuum 1 bloated table')
  await expect(list).toContainText('After the quarter closes')
  await list.getByRole('button', { name: 'Set aside (1)' }).click()
  await list.getByRole('button', { name: 'Bring back' }).click()
  await expect(page.getByTestId('plan-row')).toHaveCount(7)
})

test('Applied shows the verified change with its undo, the undone one and the dismissed', async ({
  page,
}) => {
  await page.goto('/actions?scope=applied')
  await expect(page.getByTestId('applied-lead')).toContainText(
    'This week: 1 change applied'
  )
  const verified = page
    .getByTestId('applied-row')
    .filter({ hasText: 'Add an index on tags(count, tagname)' })
  await expect(verified).toHaveAttribute('data-status', 'verified')
  await expect(verified).toContainText('Verified')

  await page.getByRole('button', { name: 'Undone (1)' }).click()
  await expect(page.getByTestId('applied-undone')).toContainText(
    'It slowed the nightly import.'
  )
  await page.getByRole('button', { name: 'Dismissed (2)' }).click()
  await expect(page.getByTestId('applied-dismissed')).toContainText(
    'The host caps memory at 16 GB.'
  )

  await verified.getByRole('button', { name: 'Undo' }).click()
  const popover = page.getByRole('dialog', {
    name: /Undo Add an index on tags/,
  })
  await expect(popover.getByTestId('undo-sql')).toContainText(
    'DROP INDEX CONCURRENTLY public.idx_tags_count_tagname;'
  )
  const undid = page.waitForResponse(
    (response) =>
      /\/actions\/\d+\/undid_it$/.test(response.url()) && response.ok()
  )
  await popover.getByRole('button', { name: 'I undid this' }).click()
  await undid
  await expect(page.getByRole('button', { name: 'Undone (2)' })).toBeVisible()
})
