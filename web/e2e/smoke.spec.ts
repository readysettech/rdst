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
  await expect(
    page.getByRole('heading', { name: 'Welcome to RDST' })
  ).toBeVisible()
  await expect(page.getByText('What do you want to do?')).toBeVisible()
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
