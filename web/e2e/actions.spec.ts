import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

// Actions and the index plan come from the real action engine over seeded facts.
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
  await mockPrerequisites(page, 'e2e-guard')
})

test('the database page opens on actions, one tab per kind of change', async ({
  page,
}) => {
  await page.goto('/database?target=e2e-guard')
  await expect(page.getByRole('tab', { name: /^Actions/ })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  const kinds = page.getByRole('tablist', { name: 'Kinds of change' })
  // The four kinds RDST checks always show, each saying where it stands.
  await expect(kinds.getByRole('tab')).toHaveCount(4)
  await expect(kinds).toContainText('1 to add, 1 to drop')
  await expect(kinds).toContainText('Server settings')
  await expect(kinds).toContainText('Maintenance')
  await expect(kinds).toContainText('Query rewrites')
  // Indexes opens on its explanation; the demo sits in the nav.
  await expect(page.getByTestId('action-story')).toBeVisible()
  await expect(
    page.locator('#app-sidebar').getByRole('link', { name: 'Readyset Caching' })
  ).toBeVisible()
})

test('a setting opens on its steps and SQL, and a dismissed one can come back', async ({
  page,
}) => {
  await page.goto('/database?target=e2e-guard&view=actions&section=settings')
  const setting = page
    .getByTestId('action-card')
    .filter({ hasText: 'Raise work_mem' })
  // A tab with one change opens on how to do it.
  await expect(setting.getByTestId('action-detail')).toContainText(
    "ALTER SYSTEM SET work_mem = '16MB';"
  )
  const dismissed = page.waitForResponse(
    (response) =>
      /\/actions\/\d+\/dismiss$/.test(response.url()) && response.ok()
  )
  await setting.getByRole('button', { name: 'Dismiss' }).click()
  await dismissed
  await expect(page.getByTestId('actions-all-clear')).toContainText(
    'Nothing to change'
  )
  const aside = page.getByTestId('actions-set-aside')
  await aside.getByRole('button', { name: 'Set aside (1)' }).click()
  await expect(aside).toContainText('Raise work_mem')
  await aside.getByRole('button', { name: 'Bring back' }).click()
  await expect(
    page.getByTestId('action-card').filter({ hasText: 'Raise work_mem' })
  ).toBeVisible()
})

test('the indexes tab explains the plan, lists each change and rolls it back', async ({
  page,
}) => {
  // Links to the old Indexes tab open it here.
  await page.goto('/database?target=e2e-guard&view=indexes')
  await expect(page.getByTestId('action-story')).toContainText(
    /Add 1 index on orders to speed up/
  )
  const add = page.getByTestId('index-plan-add')
  await expect(add).toContainText('orders')
  await expect(add).toContainText('customer_id')
  await add.getByRole('button').first().click()
  await expect(add).toContainText(
    'CREATE INDEX CONCURRENTLY idx_orders_customer_id ON orders (customer_id);'
  )
  await expect(page.getByTestId('index-plan-drop')).toContainText('status')
  await page.getByRole('button', { name: 'Show the SQL' }).click()
  await expect(page.getByTestId('apply-plan')).toContainText(
    'DROP INDEX CONCURRENTLY public.idx_orders_customer_id;'
  )
})
