import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

test('saving an Anthropic key immediately resumes a parked bootstrap', async ({
  page,
}) => {
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
  // The settings drawer keeps its disabled submit button mounted while closed.
  // Select the page-level trigger explicitly so Playwright strict mode does not
  // confuse it with the hidden form action.
  await page
    .getByRole('button', { name: 'Add connection', exact: true })
    .first()
    .click()
  await page.getByRole('tab', { name: 'Manual setup' }).click()
  await page.locator('[name="name"]').fill('needs-key-db')
  await page.locator('[name="host"]').fill('database.external.test')
  await page.locator('[name="database"]').fill('application')
  await page.locator('[name="user"]').fill('rdst_e2e')
  await page.locator('[name="password"]').fill('test-password')

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
  await page.getByRole('button', { name: 'Add connection' }).last().click()

  const jobsTrigger = page.getByTestId('jobs-trigger')
  await expect(jobsTrigger).toContainText('Setting up needs-key-db')
  await expect(jobsTrigger).toContainText(
    'Add an Anthropic key or start a free trial.'
  )
  await expect(page.getByTestId('job-warning-icon')).toBeVisible()
  await jobsTrigger.click()

  const parkedJob = page.locator(
    '[data-testid^="background-run-bootstrap_needs-key-db_"]'
  )
  await expect(parkedJob).toHaveCount(1)
  await expect(page.getByText('1 job needs attention')).toBeVisible()
  await expect(parkedJob).toContainText(
    'Add an Anthropic key or start a free trial.'
  )
  await parkedJob.click()

  await expect(
    page.getByRole('heading', { name: 'Start free trial' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Add AI key' }).click()

  await expect(
    page.getByRole('heading', { name: 'Update Anthropic API key' })
  ).toBeVisible()
  await page
    .getByRole('textbox', { name: 'Anthropic API Key', exact: true })
    .fill('sk-ant-e2e-test')
  await page.getByRole('button', { name: 'Save secrets' }).click()

  // The fixture has no polling path: reaching completion proves that
  // /api/env/set asked RunRegistry to wake the parked needs_key handle.
  await expect(jobsTrigger).toContainText('needs-key-db is ready')
})
