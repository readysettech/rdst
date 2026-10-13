import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

test('saving an Anthropic key immediately resumes a parked bootstrap', async ({
  page,
  browserErrors,
}) => {
  // The new database's page asks for its latest report, which it doesn't have yet.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  setBackendFixtures({
    bootstrap: [
      {
        events: [
          {
            type: 'bootstrap_stage',
            stage: 'connection_test',
            status: 'done',
            message: 'Connection successful',
          },
          {
            type: 'bootstrap_stage',
            stage: 'structure',
            status: 'done',
            message: '3 tables found',
          },
          {
            type: 'bootstrap_stage',
            stage: 'profile',
            status: 'done',
            message: 'Profiled columns',
          },
          {
            type: 'needs_key',
            message: 'An Anthropic key unlocks AI schema descriptions.',
          },
          {
            type: 'bootstrap_stage',
            stage: 'annotate',
            status: 'started',
            message: 'Generating AI descriptions...',
          },
          {
            type: 'bootstrap_stage',
            stage: 'annotate',
            status: 'done',
            message: 'AI descriptions generated',
          },
        ],
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/configure')
  await page.getByRole('button', { name: 'Add a database' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add a database' })
  await dialog.locator('[name="host"]').fill('database.external.test')
  await dialog.locator('[name="database"]').fill('needs-key-db')
  await dialog.locator('[name="user"]').fill('rdst_e2e')
  await dialog.locator('[name="password"]').fill('test-password')

  await page.route('**/api/configure/targets/needs-key-db/test', (route) =>
    route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: [
        'event: connection_test',
        `data: ${JSON.stringify({
          target_name: 'needs-key-db',
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
  )
  const started = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/bootstrap'
  )
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()
  const { run_id: runId } = (await (await started).json()) as {
    run_id: string
  }
  const runStatus = async () => {
    const response = await page.request.get(`/api/bootstrap/runs/${runId}`)
    return ((await response.json()) as { status: string }).status
  }

  // Setup parks on the missing key while the new database's page opens.
  await expect(page).toHaveURL(/\/database/)
  await expect.poll(runStatus).toBe('needs_key')

  await page.goto('/configure?section=ai')

  await expect(
    page.getByRole('heading', { name: 'Update Anthropic API key' })
  ).toBeVisible()
  await page
    .getByRole('textbox', { name: 'Anthropic API key', exact: true })
    .fill('sk-ant-e2e-test')
  await page.getByRole('button', { name: 'Save secrets' }).click()

  // The fixture has no polling path: reaching completion proves that
  // /api/env/set asked RunRegistry to wake the parked needs_key handle.
  await expect.poll(runStatus).toBe('done')
})
