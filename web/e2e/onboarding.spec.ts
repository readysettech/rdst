import {
  clearTargets,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

test('completes first-run onboarding and persists the target', async ({
  page,
}) => {
  await clearTargets(page.request)
  setBackendFixtures({
    init_validate: [
      {
        events: [
          {
            type: 'complete',
            success: true,
            validation: {
              target_results: [
                {
                  name: 'e2e-db',
                  success: true,
                  version: 'PostgreSQL 15',
                },
              ],
              llm_result: {
                success: false,
                error: 'Optional in frontend integration tests',
              },
            },
          },
        ],
      },
    ],
  })

  // The four-step wizard was replaced by the single, exitable ConnectPage:
  // one form, one primary action, straight back into the app.
  await page.goto('/onboarding')
  await expect(
    page.getByRole('heading', { name: 'Connect your database' })
  ).toBeVisible()

  await page.locator('[name="name"]').fill('e2e-db')
  await page.locator('[name="host"]').fill('127.0.0.1')
  await page.locator('[name="database"]').fill('app')
  await page.locator('[name="user"]').fill('e2e')
  await page.locator('[name="password"]').fill('test-password')
  await page.locator('[name="password_env"]').fill('TEST_DB_PASSWORD')
  await page.getByRole('button', { name: 'Test & connect' }).click()

  // Landing on the design-system home with the job launcher proves the
  // target persisted (the launcher only renders when targets exist).
  await expect(page).toHaveURL('/')
  await expect(page.getByText('Run a health check')).toBeVisible()

  // ConnectPage promotes the first target to default (addTarget →
  // setDefaultTarget → completeInit), unlike the retired wizard.
  const targets = await page.request.get('/api/configure/targets')
  expect(targets.ok()).toBe(true)
  await expect(targets.json()).resolves.toMatchObject({
    default_target: 'e2e-db',
    targets: [{ name: 'e2e-db', is_default: true }],
  })

  await page.reload()
  await expect(page.getByText('Run a health check')).toBeVisible()
})
