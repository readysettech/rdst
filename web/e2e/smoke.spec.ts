import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

test('loads the production application and backend', async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })

  const health = await page.request.get('/health')
  expect(health.ok()).toBe(true)
  await expect(health.json()).resolves.toEqual({ status: 'ok' })

  await page.goto('/')
  // A configured target renders the connected home and its next actions.
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(page.getByText('Run a health check')).toBeVisible()
})

test('serves client-side routes directly', async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/query-registry')
  // /query-registry redirects into the Queries workspace's Saved view.
  await expect(
    page.getByRole('heading', { name: 'Queries', exact: true })
  ).toBeVisible()
  await expect(page).toHaveURL(/\/queries\?.*view=saved/)
})
