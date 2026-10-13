import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

/** A database's row in Settings: the innermost element holding its name and menu. */
function databaseRow(page: Page, name: string) {
  return page
    .getByRole('region', { name: 'Databases' })
    .locator('div')
    .filter({ has: page.getByText(name, { exact: true }) })
    .filter({
      has: page.getByRole('button', {
        name: `More actions for ${name}`,
        exact: true,
      }),
    })
    .last()
}

test('adds, updates, defaults, and deletes a database target', async ({
  page,
  browserErrors,
}) => {
  // The new database's page asks for its latest report, which it doesn't have yet.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/configure')
  await expect(page.getByRole('heading', { name: 'Databases' })).toBeVisible()

  const seedRow = databaseRow(page, 'e2e-guard')
  await expect(seedRow).toBeVisible()

  await page.getByRole('button', { name: 'Add a database' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add a database' })
  await expect(dialog.getByRole('button', { name: 'Test' })).toBeVisible()
  await expect(dialog.locator('[name="read_only"]')).toHaveCount(0)
  await expect(dialog.locator('[name="password_env"]')).toHaveCount(0)

  // The database is named after its database name.
  await dialog.locator('[name="host"]').fill('db.internal')
  await dialog.locator('[name="database"]').fill('primary-db')
  await dialog.locator('[name="user"]').fill('rdst_e2e')
  await dialog.locator('[name="password"]').fill('test-password')

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
          privileges: { writable: false },
        })}`,
        '',
        'event: success',
        'data: {"message":"Connection test complete"}',
        '',
      ].join('\n'),
    })
  })

  await dialog.getByRole('button', { name: 'Test' }).click()
  await expect(dialog.getByText('Connected to PostgreSQL 16.3.')).toBeVisible()
  expect(connectionTestBodies).toHaveLength(1)
  expect(connectionTestBodies[0].target).not.toHaveProperty('password_env')
  expect(connectionTestBodies[0].target).toMatchObject({
    read_only: false,
    host: 'db.internal',
    database: 'primary-db',
    user: 'rdst_e2e',
  })

  // Adding tests again, saves and opens the database's page on the turn-on dialog.
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()
  await expect(page).toHaveURL(/\/database/)
  await page.goto('/configure')

  const targetRow = databaseRow(page, 'primary-db')
  await expect(targetRow).toBeVisible()
  await expect(targetRow.getByText('default', { exact: true })).toHaveCount(0)

  // Row management actions live in the row's overflow menu: Set default, Edit,
  // Remove.
  await targetRow.getByRole('button', { name: /More actions/ }).click()
  await page.getByRole('menuitem', { name: 'Set default' }).click()
  await expect(targetRow.getByText('default', { exact: true })).toBeVisible()

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
  await page.getByRole('menuitem', { name: 'Edit', exact: true }).click()
  // Editing opens the add dialog on the saved fields, with no providers or string.
  const editDialog = page.getByRole('dialog', { name: 'Edit database' })
  await expect(editDialog.locator('[name="name"]')).toHaveValue('primary-db')
  await expect(editDialog.locator('[name="connection_string"]')).toHaveCount(0)
  await editDialog.locator('[name="host"]').fill('db-updated.internal')
  await editDialog.locator('[name="database"]').fill('application_v2')
  await editDialog.getByRole('button', { name: 'Save changes' }).click()
  await expect(editDialog).toHaveCount(0)

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

  // Removing asks for confirmation in the shared styled ConfirmDialog.
  await targetRow.getByRole('button', { name: /More actions/ }).click()
  await page.getByRole('menuitem', { name: 'Remove' }).click()
  await page.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(targetRow).toHaveCount(0)
  await expect(seedRow).toBeVisible()

  const targets = await page.request.get('/api/configure/targets')
  expect(targets.ok()).toBe(true)
  await expect(targets.json()).resolves.toMatchObject({
    default_target: null,
    targets: [{ name: 'e2e-guard', is_default: false }],
  })
})
