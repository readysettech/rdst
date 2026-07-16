import { awaitEmailGateClosed, clearTargets, expect, test } from '../fixtures'

test('configures Postgres and introspects its schema through the UI', async ({
  page,
}) => {
  const host = process.env.RDST_E2E_DB_HOST ?? '127.0.0.1'
  const port = Number(process.env.RDST_E2E_DB_PORT ?? '15432')

  await clearTargets(page.request)

  await page.goto('/onboarding')
  await awaitEmailGateClosed(page)
  await page.getByRole('button', { name: 'Get Started' }).click()
  await page.getByRole('button', { name: 'Add Your First Target' }).click()
  await page.locator('[name="name"]').fill('postgres-e2e')
  await page.locator('[name="host"]').fill(host)
  await page.locator('[name="port"]').fill(String(port))
  await page.locator('[name="database"]').fill('rdst_test')
  await page.locator('[name="user"]').fill('rdst_test')
  await page.locator('[name="password_env"]').fill('RDST_E2E_DB_PASSWORD')
  await page.getByRole('button', { name: 'Add Target' }).click()
  await expect(page.getByText('1 target configured')).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('All Passed')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('button', { name: 'Start Using RDST' }).click()

  await page.goto('/configure')
  const targetRow = page
    .getByTestId('target-row')
    .filter({ hasText: 'postgres-e2e' })
  await targetRow.getByRole('button', { name: 'Set Default' }).click()
  await expect(targetRow.getByText('Default', { exact: true })).toBeVisible()

  const schemaResponse = await page.request.get(
    '/api/schema?target=postgres-e2e'
  )
  expect(schemaResponse.ok()).toBe(true)
  await expect(schemaResponse.json()).resolves.toMatchObject({
    dialect: 'postgresql',
    tables: {
      title_basics: expect.arrayContaining(['tconst', 'primarytitle']),
      title_ratings: expect.arrayContaining(['tconst', 'averagerating']),
    },
  })

  await page.goto('/schema')
  await expect(
    page.getByRole('heading', { name: 'Semantic Layer' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Initialize Schema' }).click()

  await expect(
    page.getByRole('button').filter({ hasText: 'title_basics' })
  ).toBeVisible({ timeout: 30_000 })
  await expect(
    page.getByRole('button').filter({ hasText: 'title_ratings' })
  ).toBeVisible()
})
