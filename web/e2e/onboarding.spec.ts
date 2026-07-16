import {
  awaitEmailGateClosed,
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

  await page.goto('/onboarding')
  await expect(page.getByText('Step 1 of 4')).toBeVisible()
  await awaitEmailGateClosed(page)
  await page.getByRole('button', { name: 'Get Started' }).click()

  await page.getByRole('button', { name: 'Add Your First Target' }).click()
  await page.locator('[name="name"]').fill('e2e-db')
  await page.locator('[name="host"]').fill('127.0.0.1')
  await page.locator('[name="database"]').fill('app')
  await page.locator('[name="user"]').fill('e2e')
  await page.locator('[name="password_env"]').fill('TEST_DB_PASSWORD')
  await page.getByRole('button', { name: 'Add Target' }).click()

  await expect(page.getByText('1 target configured')).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()

  await expect(page.getByText('All Passed')).toBeVisible()
  await expect(page.getByText('Connected', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()

  await expect(page.getByText("You're All Set!", { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Start Using RDST' }).click()

  await expect(page).toHaveURL('/')
  await expect(page.getByText('What do you want to do?')).toBeVisible()

  const targets = await page.request.get('/api/configure/targets')
  expect(targets.ok()).toBe(true)
  await expect(targets.json()).resolves.toMatchObject({
    default_target: null,
    targets: [{ name: 'e2e-db', is_default: false }],
  })

  await page.reload()
  await expect(page.getByText('What do you want to do?')).toBeVisible()
})
