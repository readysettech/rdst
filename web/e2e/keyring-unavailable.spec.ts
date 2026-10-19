import { mockAnonymousAccess } from './accountless'
import {
  acceptBrowserError,
  clearTargets,
  expect,
  mockPrerequisites,
  setBackendFixtures,
  test,
} from './fixtures'

const SESSION_ONLY =
  "Your OS keyring isn't available, so RDST keeps this password only until you close the app. You'll need to enter it again next time."

test('a password the OS keyring could not keep is called out before moving on', async ({
  page,
  browserErrors,
}) => {
  // The new database's page asks for its latest report, which it doesn't have yet.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await clearTargets(page.request)
  setBackendFixtures()
  await mockAnonymousAccess(page, 'none')
  // "Add and monitor" tests the connection before it saves.
  await page.route('**/api/configure/targets/keyring-db/test', (route) =>
    route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: [
        'event: connection_test',
        `data: ${JSON.stringify({
          target_name: 'keyring-db',
          status: 'success',
          server_version: 'PostgreSQL 16',
          privileges: { writable: false },
        })}`,
        '',
        'event: success',
        'data: {"message":"Connection test complete"}',
        '',
      ].join('\n'),
    })
  )
  await mockPrerequisites(page, 'keyring-db')

  await page.goto('/?add=1')
  const dialog = page.getByRole('dialog', { name: 'Add a database' })
  await expect(dialog).toBeVisible()
  await dialog
    .locator('[name="connection_string"]')
    .fill('postgresql://e2e:test-password@127.0.0.1:5432/keyring-db')

  // The suite's server runs without an OS keyring, so the real add reports the password
  // as kept for this session only.
  const added = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().endsWith('/api/configure/targets')
  )
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()
  const response = await added
  expect(response.ok()).toBe(true)
  await expect(response.json()).resolves.toMatchObject({
    success: true,
    target_name: 'keyring-db',
    persisted: false,
  })

  // The dialog stays put and says so before the database's page opens.
  const warning = dialog.getByTestId('password-session-only')
  await expect(warning).toContainText(SESSION_ONLY)
  await expect(page).not.toHaveURL(/\/database/)
  await expect(
    dialog.getByRole('button', { name: 'Add and monitor' })
  ).toHaveCount(0)

  await dialog.getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/\/database/)
  await expect(dialog).toHaveCount(0)
  await expect(
    page.getByRole('dialog', { name: 'Monitor keyring-db' })
  ).toBeVisible()

  // The database was saved; only its password is session-bound.
  const targets = await page.request.get('/api/configure/targets')
  await expect(targets.json()).resolves.toMatchObject({
    targets: [{ name: 'keyring-db' }],
  })
  await clearTargets(page.request)
})
