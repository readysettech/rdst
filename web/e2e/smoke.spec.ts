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
  // The design-system home replaced the welcome copy with a job launcher:
  // hero heading plus job cards (a target is configured, so the launcher
  // branch renders).
  await expect(
    page.getByRole('heading', {
      name: 'Understand, diagnose, and speed up your database',
    })
  ).toBeVisible()
  await expect(page.getByText('Run a health check')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Ask a question' })).toBeVisible()
})

test('serves client-side routes directly', async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/query-registry')
  await expect(
    page.getByRole('heading', { name: 'Saved Queries' })
  ).toBeVisible()
  await expect(page).toHaveURL(/\/query-registry$/)
})
