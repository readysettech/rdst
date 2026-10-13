import {
  acceptBrowserError,
  clearTargets,
  expect,
  mockPrerequisites,
  setBackendFixtures,
  test,
} from './fixtures'

test('adds the first database from Home and persists it', async ({
  page,
  browserErrors,
}) => {
  // A database with no report yet answers its latest report with a 404.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
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

  // "Add and monitor" tests before it saves, so the connection the first run
  // stores is one that answered.
  await page.route('**/api/configure/targets/e2e-db/test', (route) =>
    route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: [
        'event: connection_test',
        `data: ${JSON.stringify({
          target_name: 'e2e-db',
          status: 'success',
          server_version: 'PostgreSQL 15',
          privileges: { writable: false },
        })}`,
        '',
        'event: success',
        'data: {"message":"Connection test complete"}',
        '',
      ].join('\n'),
    })
  )

  // The retired first-run page lands on Home with the dialog open.
  await page.goto('/onboarding')
  await expect(page).toHaveURL(/\/\?add=1/)
  const dialog = page.getByRole('dialog', { name: 'Add a database' })
  await expect(dialog).toBeVisible()

  // The database is named after its database name.
  await dialog
    .locator('[name="connection_string"]')
    .fill('postgresql://e2e:test-password@127.0.0.1:5432/e2e-db')
  await expect(dialog.locator('[name="host"]')).toHaveValue('127.0.0.1')
  // Adding opens the new database's page on the turn-on dialog: its checks and the
  // EXPLAIN ANALYZE warning come before monitoring turns on.
  await mockPrerequisites(page, 'e2e-db')
  const enabled = page.waitForRequest(
    (request) =>
      request.method() === 'PUT' &&
      request.url().endsWith('/api/monitor/targets/e2e-db/settings') &&
      request.postDataJSON()?.enabled === true
  )
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()
  await expect(page).toHaveURL(/\/database/)
  await expect(dialog).toHaveCount(0)
  const enable = page.getByRole('dialog', { name: 'Monitor e2e-db' })
  await expect(enable.getByTestId('explain-analyze-warning')).toBeVisible()
  await enable.getByRole('button', { name: 'Turn on monitoring' }).click()
  await enabled

  // The sidebar lists the persisted database beside Home.
  const listed = page
    .getByRole('navigation', { name: 'Databases' })
    .getByRole('link', { name: /^e2e-db/ })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(listed).toBeVisible()

  // The first database becomes the default.
  const targets = await page.request.get('/api/configure/targets')
  expect(targets.ok()).toBe(true)
  await expect(targets.json()).resolves.toMatchObject({
    default_target: 'e2e-db',
    targets: [{ name: 'e2e-db', is_default: true }],
  })

  await page.reload()
  await expect(listed).toBeVisible()
})
