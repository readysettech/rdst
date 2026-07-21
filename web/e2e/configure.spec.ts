import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

test('adds, updates, defaults, and deletes a database target', async ({
  page,
}) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/configure')
  await expect(
    page.getByRole('heading', { name: 'Database connections' })
  ).toBeVisible()

  const seedRow = page
    .getByTestId('target-row')
    .filter({ has: page.getByText('e2e-guard', { exact: true }) })
  await expect(seedRow).toBeVisible()

  await page.getByRole('button', { name: 'Add Target' }).click()
  await page.locator('[name="name"]').fill('primary-db')
  await page.locator('[name="host"]').fill('db.internal')
  await page.locator('[name="database"]').fill('application')
  await page.locator('[name="user"]').fill('rdst_e2e')
  await page.locator('[name="password"]').fill('test-password')
  await page.locator('[name="password_env"]').fill('TEST_DB_PASSWORD')
  await page.getByRole('button', { name: 'Add Target' }).click()

  let targetRow = page
    .getByTestId('target-row')
    .filter({ has: page.getByText('primary-db', { exact: true }) })
  await expect(targetRow).toBeVisible()
  await expect(targetRow.getByText('Default', { exact: true })).toHaveCount(0)

  // Row management actions live in the ⋯ overflow menu now [C-09 configure
  // migration]: Set as default / Edit connection / Delete….
  await targetRow.getByRole('button', { name: /More actions/ }).click()
  await page.getByRole('menuitem', { name: 'Set as default' }).click()
  await expect(targetRow.getByText('Default', { exact: true })).toBeVisible()

  await expect
    .poll(async () => {
      const response = await page.request.get('/api/configure/targets')
      const body = (await response.json()) as {
        default_target: string | null
        targets: { name: string; is_default: boolean }[]
      }
      return {
        default_target: body.default_target,
        primary: body.targets.find(({ name }) => name === 'primary-db'),
      }
    })
    .toMatchObject({
      default_target: 'primary-db',
      primary: { name: 'primary-db', is_default: true },
    })

  await targetRow.getByRole('button', { name: /More actions/ }).click()
  await page.getByRole('menuitem', { name: 'Edit connection' }).click()
  await expect(page.getByText('Edit Target', { exact: true })).toBeVisible()
  await expect(page.locator('[name="name"]')).toBeDisabled()
  await page.locator('[name="host"]').fill('db-updated.internal')
  await page.locator('[name="database"]').fill('application_v2')
  await page.getByRole('button', { name: 'Update Target' }).click()

  targetRow = page
    .getByTestId('target-row')
    .filter({ has: page.getByText('primary-db', { exact: true }) })
  await expect(targetRow).toBeVisible()
  await expect
    .poll(async () => {
      const response = await page.request.get(
        '/api/configure/targets/primary-db'
      )
      return response.json()
    })
    .toMatchObject({
      target_name: 'primary-db',
      host: 'db-updated.internal',
      database: 'application_v2',
      is_default: true,
    })

  // Native confirm() replaced by the shared styled ConfirmDialog [C-09].
  await targetRow.getByRole('button', { name: /More actions/ }).click()
  await page.getByRole('menuitem', { name: 'Delete…' }).click()
  await page.getByRole('button', { name: 'Delete connection' }).click()
  await expect(targetRow).toHaveCount(0)
  await expect(seedRow).toBeVisible()

  const targets = await page.request.get('/api/configure/targets')
  expect(targets.ok()).toBe(true)
  await expect(targets.json()).resolves.toMatchObject({
    default_target: null,
    targets: [{ name: 'e2e-guard', is_default: false }],
  })
})
