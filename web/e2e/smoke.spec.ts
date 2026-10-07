import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

test('loads the production application and backend', async ({
  page,
  browserErrors,
}) => {
  // The Database page reads a checkup that doesn't exist yet; the API answers 404.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })

  const health = await page.request.get('/health')
  expect(health.ok()).toBe(true)
  await expect(health.json()).resolves.toEqual({ status: 'ok' })

  await page.goto('/')
  // One configured database: Home is its Database page.
  await expect(page).toHaveURL(/\/database/)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
})

test('serves client-side routes directly', async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/query-registry')
  // /query-registry redirects into the Queries workspace, which is what the
  // saved list became, and applies no filter of its own. [C-81]
  await expect(
    page.getByRole('heading', { name: 'Queries', exact: true })
  ).toBeVisible()
  await expect(page).toHaveURL(/\/queries$/)
})
