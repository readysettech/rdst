import {
  KEYSERVICE_STUB_URL,
  keyserviceRequests,
  onboardingTour,
  optInToOnboardingTour,
} from '../accountless'
import {
  acceptBrowserError,
  clearTargets,
  continuePastSessionOnlyPassword,
  expect,
  mainContent,
  test,
} from '../fixtures'

// A database with no report yet answers report/latest with 404, which the browser logs.
const NO_REPORT_404 =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

test('a new install with no key and no account goes from the tour to monitoring', async ({
  page,
  browserErrors,
}) => {
  test.setTimeout(180_000)
  acceptBrowserError(browserErrors, NO_REPORT_404)
  // The real AI gate decides, not the suite's stand-in Anthropic key.
  await page.unroute('**/api/env/requirements')
  await clearTargets(page.request)
  await optInToOnboardingTour(page)
  const visited: string[] = []
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) visited.push(new URL(frame.url()).pathname)
  })

  await page.goto('/')

  // The tour comes first and plays on its own.
  const tour = onboardingTour(page)
  await expect(tour).toBeVisible()
  await expect(tour.getByText('Step 1 of 4')).toBeVisible()
  await expect(
    tour.getByRole('heading', { name: 'Connect your database' })
  ).toBeVisible()
  await expect(tour.getByText('Step 2 of 4')).toBeVisible({ timeout: 15_000 })
  await expect(
    tour.getByRole('heading', { name: 'RDST watches in the background' })
  ).toBeVisible()

  // Skip goes straight to adding a database.
  await tour.getByRole('button', { name: 'Skip' }).click()
  await expect(tour).toHaveCount(0)
  const dialog = page.getByRole('dialog', { name: 'Add a database' })
  await expect(dialog).toBeVisible()

  await dialog
    .locator('[name="host"]')
    .fill(process.env.RDST_E2E_DB_HOST ?? '127.0.0.1')
  await dialog
    .locator('[name="port"]')
    .fill(process.env.RDST_E2E_DB_PORT ?? '15432')
  await dialog.locator('[name="database"]').fill('rdst_test')
  await dialog.locator('[name="user"]').fill('rdst_test')
  await dialog
    .locator('[name="password"]')
    .fill(process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password')
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()

  const enable = page.getByRole('dialog', { name: 'Monitor rdst_test' })
  await continuePastSessionOnlyPassword(page, enable)
  await expect(page).toHaveURL(/\/database/, { timeout: 60_000 })
  await expect(enable.getByTestId('explain-analyze-warning')).toBeVisible()
  const turnOn = enable.getByRole('button', { name: 'Turn on monitoring' })
  await expect(turnOn).toBeEnabled({ timeout: 60_000 })
  await turnOn.click()
  await expect(enable).toHaveCount(0)

  // Monitoring is on, with its AI slot held by the anonymous account.
  await expect(async () => {
    const settings = await page.request.get(
      '/api/monitor/targets/rdst_test/settings'
    )
    expect(settings.ok()).toBe(true)
    await expect(settings.json()).resolves.toMatchObject({
      enabled: true,
      ai_monitoring: 'on',
    })
  }).toPass({ timeout: 30_000 })

  const account = await page.request.get('/api/account/status')
  expect(account.ok()).toBe(true)
  await expect(account.json()).resolves.toMatchObject({
    signed_in: false,
    has_session: true,
    is_anonymous: true,
    signin_prompt: 'none',
  })

  // The AI pages open without a sign-in or key step.
  for (const path of ['/', '/ask']) {
    await page.goto(path)
    await expect(mainContent(page)).toBeVisible()
    await expect(page.getByText('Checking your AI setup')).toHaveCount(0, {
      timeout: 15_000,
    })
    await expect(page.getByTestId('ai-setup')).toHaveCount(0)
    await expect(page.getByTestId('signin-required')).toHaveCount(0)
  }
  expect(visited).not.toContain('/account-login')

  // The backend started an anonymous session on its own and never asked anyone to sign in.
  const calls = await keyserviceRequests(page.request)
  const paths = calls.map((call) => `${call.method} ${call.path}`)
  expect(paths).toContain('POST /account-auth/anonymous')
  expect(paths).toContain('POST /v1/monitor/databases/claim')
  expect(
    paths.filter(
      (path) =>
        path.includes('/account-auth/') &&
        !path.endsWith('/account-auth/anonymous') &&
        !path.endsWith('/account-auth/refresh')
    ),
    `sign-in calls to ${KEYSERVICE_STUB_URL}`
  ).toEqual([])

  await clearTargets(page.request)
})
