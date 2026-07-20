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
  // The adaptive home greets with a welcome header (rendered as plain text,
  // not a heading element) plus job cards (a target is configured, so the
  // launcher branch renders).
  await expect(page.getByText('Welcome to RDST', { exact: true })).toBeVisible()
  await expect(
    page.getByText('speed up the queries running on your database')
  ).toBeVisible()
})

test('serves client-side routes directly', async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/query-registry')
  await expect(
    page.getByRole('heading', { name: 'Queries' })
  ).toBeVisible()
  await expect(page).toHaveURL(/\/query-registry$/)
})
