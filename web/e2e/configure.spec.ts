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

  await page.getByRole('button', { name: 'Add connection' }).click()
  await page.getByRole('tab', { name: 'Manual setup' }).click()
  await expect(
    page.getByRole('button', { name: /Connect via SSH jump host/ })
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Test connection' })
  ).toBeVisible()
  await expect(page.locator('[name="read_only"]')).toHaveCount(0)
  await expect(page.locator('[name="password_env"]')).toHaveCount(0)

  await page.getByRole('button', { name: /Connect via SSH jump host/ }).click()
  await expect(page.locator('[name="cfg-ssh-host"]')).toBeVisible()

  await page.locator('[name="name"]').fill('primary-db')
  await page.locator('[name="host"]').fill('db.internal')
  await page.locator('[name="database"]').fill('application')
  await page.locator('[name="user"]').fill('rdst_e2e')
  await page.locator('[name="password"]').fill('test-password')

  const connectionTestBodies: {
    target: Record<string, unknown>
  }[] = []
  await page.route('**/api/configure/targets/primary-db/test', (route) => {
    connectionTestBodies.push(
      route.request().postDataJSON() as {
        target: Record<string, unknown>
      }
    )
    return route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: [
        'event: connection_test',
        `data: ${JSON.stringify({
          target_name: 'primary-db',
          status: 'success',
          server_version: 'PostgreSQL 16.3',
          privileges: {
            writable: true,
            evidence: 'PostgreSQL role is a superuser.',
          },
        })}`,
        '',
        'event: success',
        'data: {"message":"Connection test complete"}',
        '',
      ].join('\n'),
    })
  })

  await page.getByRole('button', { name: 'Test connection' }).click()
  await expect(page.getByText('Connected · PostgreSQL 16.3')).toBeVisible()
  await expect(
    page.getByText('Read-only access highly recommended')
  ).toBeVisible()
  await expect(
    page.getByText(
      'This account has write access. RDST works with it, but a read-only account reduces the risk of unintended database changes.'
    )
  ).toBeVisible()
  expect(connectionTestBodies).toHaveLength(1)
  expect(connectionTestBodies[0].target).not.toHaveProperty('password_env')
  expect(connectionTestBodies[0].target).toMatchObject({
    read_only: false,
    host: 'db.internal',
    database: 'application',
    user: 'rdst_e2e',
  })
  await page
    .getByRole('button', { name: 'Add connection', exact: true })
    .last()
    .click()

  const writableAccountDialog = page.getByRole('dialog')
  await expect(
    writableAccountDialog.getByRole('heading', {
      name: 'Use this database account?',
    })
  ).toBeVisible()
  expect(connectionTestBodies).toHaveLength(1)
  await writableAccountDialog
    .getByRole('button', { name: 'Add connection' })
    .click()

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
  await expect(page.getByText('Edit connection', { exact: true })).toBeVisible()
  await expect(page.locator('[name="name"]')).toBeDisabled()
  await page.locator('[name="host"]').fill('db-updated.internal')
  await page.locator('[name="database"]').fill('application_v2')
  await page.getByRole('button', { name: 'Update connection' }).click()

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
