import {
  acceptBrowserError,
  clearTargets,
  expect,
  mockPrerequisites,
  prerequisitesResult,
  setBackendFixtures,
  test,
} from './fixtures'

test('completes first-run onboarding and persists the target', async ({
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

  // "Test & connect" tests before it saves, so the connection the first run
  // stores is one that answered. A read-only role needs no further review.
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

  // The four-step wizard was replaced by the single, exitable ConnectPage:
  // one form, one primary action, straight back into the app.
  await page.goto('/onboarding')
  await expect(
    page.getByRole('heading', { name: 'Start with Readyset' })
  ).toBeVisible()

  await page.locator('[name="name"]').fill('e2e-db')
  await page.locator('[name="host"]').fill('127.0.0.1')
  await page.locator('[name="database"]').fill('app')
  await page.locator('[name="user"]').fill('e2e')
  await page.locator('[name="password"]').fill('test-password')
  // The check finds the extension missing until the user runs the fix in
  // another window; the page re-checks on its own and finishes.
  const prerequisites = await mockPrerequisites(
    page,
    'e2e-db',
    prerequisitesResult('e2e-db', { ready: false })
  )
  await page.getByRole('button', { name: 'Test & connect' }).click()

  // Connecting goes straight to the next step: the "Turn on monitoring" dialog
  // on the new database's page, which runs the prerequisites check itself.
  await expect(page).toHaveURL(/\/database\?panel=enable/)
  const dialog = page.getByRole('dialog', {
    name: 'Turn on monitoring for e2e-db',
  })
  await expect(dialog).toBeVisible()
  const checker = dialog.getByTestId('prereq-checker')
  await expect(checker).toHaveAttribute('data-phase', 'blocked')
  await expect(
    checker.locator('[data-check="pg_stat_statements"][data-status="missing"]')
  ).toBeVisible()
  await expect(
    checker.getByText('CREATE EXTENSION IF NOT EXISTS pg_stat_statements;')
  ).toBeVisible()
  prerequisites.answer(prerequisitesResult('e2e-db'))
  await expect(checker).toHaveAttribute('data-phase', 'ready', {
    timeout: 10_000,
  })
  await expect(
    dialog.getByRole('heading', { name: 'Ready to monitor' })
  ).toBeVisible()
  // Its button really turns monitoring on.
  const enabled = page.waitForRequest(
    (request) =>
      request.method() === 'PUT' &&
      request.url().endsWith('/api/monitor/targets/e2e-db/settings') &&
      request.postDataJSON()?.enabled === true
  )
  await dialog.getByRole('button', { name: /^Turn on/ }).click()
  await enabled
  await expect(dialog).toHaveCount(0)
  await expect(page).not.toHaveURL(/panel=enable/)

  // With one database Home opens its Database page, which proves the target persisted.
  await page.goto('/')
  await expect(page).toHaveURL(/\/database/)
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-db' })
  ).toBeVisible()

  // ConnectPage promotes the first target to default (addTarget →
  // setDefaultTarget → completeInit), unlike the retired wizard.
  const targets = await page.request.get('/api/configure/targets')
  expect(targets.ok()).toBe(true)
  await expect(targets.json()).resolves.toMatchObject({
    default_target: 'e2e-db',
    targets: [{ name: 'e2e-db', is_default: true }],
  })

  await page.reload()
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-db' })
  ).toBeVisible()
})
