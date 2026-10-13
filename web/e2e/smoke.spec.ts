import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mainContent,
  setBackendFixtures,
  test,
} from './fixtures'

test('loads the production application and backend', async ({
  page,
  browserErrors,
}) => {
  // A checkup that doesn't exist yet reads as a 404 from the API.
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
  // One configured database: the sidebar lists it and Home's sentence is about it
  // (the strip across databases needs two or more).
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(
    page
      .getByRole('navigation', { name: 'Databases' })
      .getByRole('link', { name: /^e2e-guard/ })
  ).toBeVisible()
  await expect(
    mainContent(page).getByText('e2e-guard: not monitored.')
  ).toBeVisible()
  await expect(
    mainContent(page).getByRole('region', { name: 'Across your databases' })
  ).toHaveCount(0)
})

test('serves client-side routes directly', async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/query-registry')
  // /query-registry redirects to the Database page's query list.
  await expect(page).toHaveURL(/\/database/)
})
