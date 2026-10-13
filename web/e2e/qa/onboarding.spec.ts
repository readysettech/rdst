/**
 * First run: the connect flow, the AI prerequisite and the demo entry point.
 *
 * Consolidated from the pack A first-run/demo/AI-gate flows. Every test asserts
 * the behaviour the product ships; the doc comment above each one names the
 * finding it protects.
 */
import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  clearTargets,
  configureTestTarget,
  expect,
  mainContent,
  mockAiKeyReady,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, ROUTES, shot } from './_helpers'

/** The console line a fake-backend 404 leaves behind on the demo surfaces. */
const RESOURCE_404 =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

/** One connection-test stream, in the shape the configure route serializes. */
function connectionTestBody(payload: Record<string, unknown>) {
  return [
    'event: connection_test',
    `data: ${JSON.stringify(payload)}`,
    '',
    'event: success',
    'data: {"message":"Connection test complete"}',
    '',
  ].join('\n')
}

/** Report no AI provider at all: no Anthropic key and no Readyset account. */
async function blockAiProvider(page: Page) {
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: false,
        requirements: [
          {
            kind: 'anthropic_api_key',
            satisfied: false,
            source: null,
            selected_provider: null,
            readyset_account_connected: false,
            anthropic_key_configured: false,
            target: null,
            accepted_names: ['ANTHROPIC_API_KEY'],
          },
        ],
      },
    })
  )
}

/** The add-database dialog names the database after its database name. */
async function fillConnectForm(page: Page, name: string) {
  await page.locator('[name="host"]').fill('192.0.2.10')
  await page.locator('[name="database"]').fill(name)
  await page.locator('[name="user"]').fill('rdst_e2e')
  await page.locator('[name="password"]').fill('test-password')
}

/** A-01 - the first-run primary tests the connection before it saves a target. */
test('the first-run primary tests the connection before it saves a target', async ({
  page,
  browserErrors,
}) => {
  // The new database's page asks for its latest report, which it doesn't have yet.
  acceptBrowserError(browserErrors, RESOURCE_404)
  await page.setViewportSize(DESKTOP)
  await clearTargets(page.request)
  setBackendFixtures()

  let reachable = false
  const attempts: string[] = []
  await page.route('**/api/configure/targets/*/test', (route) => {
    attempts.push(new URL(route.request().url()).pathname)
    return route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: connectionTestBody(
        reachable
          ? {
              target_name: 'qa-connect',
              status: 'success',
              server_version: 'PostgreSQL 16.3',
              privileges: { writable: false, evidence: 'read-only role' },
            }
          : {
              target_name: 'qa-connect',
              status: 'failed',
              category: 'database',
              code: 'TARGET_CONNECT_FAILED',
              message: 'could not connect to server: Connection refused',
            }
      ),
    })
  })

  await page.goto('/?add=1')
  const dialog = page.getByRole('dialog', { name: 'Add a database' })
  await expect(dialog).toBeVisible()
  await fillConnectForm(page, 'qa-connect')

  // An unreachable database is reported and nothing is written.
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()
  await expect(
    dialog.getByText('could not connect to server: Connection refused').first()
  ).toBeVisible()
  expect(attempts).toEqual(['/api/configure/targets/qa-connect/test'])
  await expect(page).toHaveURL(/\?add=1/)
  const before = await (await page.request.get('/api/configure/targets')).json()
  expect(before.targets).toEqual([])

  // A reachable one is saved, promoted to default, and its Database page
  // opens on the turn-on dialog.
  reachable = true
  await dialog.getByRole('button', { name: 'Add and monitor' }).click()
  await expect(page).toHaveURL(/\/database/)
  expect(attempts).toHaveLength(2)
  const after = await (await page.request.get('/api/configure/targets')).json()
  expect(after.default_target).toBe('qa-connect')
})

/** The AI gate's lead line, standing in for the page inside the app shell. */
const AI_GATE = 'Set up AI to get fixes and the daily report.'

/** Routes that stay open during AI setup: local monitoring and target setup. */
const OPEN_DURING_AI_SETUP = ['database', 'configure', 'configure-connections']

/** MG-01 - fresh installs choose AI access before opening a page. */
test('every route shows the sign-in chooser until an AI provider is configured', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(browserErrors, RESOURCE_404)
  await page.setViewportSize(DESKTOP)
  await clearTargets(page.request)
  setBackendFixtures()
  await blockAiProvider(page)

  // The sign-in callback stays reachable so account setup can complete.
  for (const route of ROUTES.filter((route) => route.id !== 'account-login')) {
    await page.goto(route.path)
    const main = mainContent(page)
    await expect(main).toBeVisible()
    if (OPEN_DURING_AI_SETUP.includes(route.id)) {
      await expect(page.getByText(AI_GATE, { exact: true })).toHaveCount(0)
      continue
    }
    await expect(main.getByText(AI_GATE, { exact: true })).toBeVisible()
    await expect(
      main.getByRole('button', { name: 'Sign in', exact: true })
    ).toBeVisible()
    await expect(
      main.getByRole('button', { name: 'Add key', exact: true })
    ).toBeVisible()
  }

  await mockAiKeyReady(page)
  for (const [path, heading] of [
    ['/', 'Home'],
    ['/demo', 'Readyset Platform'],
  ]) {
    await page.goto(path)
    await expect(
      page.getByRole('heading', { name: heading, exact: true })
    ).toBeVisible()
    await expect(page.getByText(AI_GATE, { exact: true })).toHaveCount(0)
  }
})

/**
 * A-05 - an unknown disk figure renders words rather than `NaN`.
 * A-32 - the demo names what blocks it, and what passed once it can start.
 */
test('the demo preflight names what blocks it and never prints NaN', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(browserErrors, RESOURCE_404)
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })

  let checks: Record<string, unknown> = {
    docker_installed: true,
    docker_running: true,
    images_present: false,
    missing_images: ['orders'],
    download_mb: 0,
    disk_space_ok: false,
    disk_free_gb: 2,
    amd64_emulation: 'not_applicable',
  }
  await page.route('**/api/demo/preflight*', (route) =>
    route.fulfill({ json: checks })
  )

  const main = mainContent(page)
  await page.goto('/demo')
  await expect(
    main.getByText('The demo needs about 2 GB of free disk.')
  ).toBeVisible()
  await expect(main).not.toContainText('NaN')
  await expect(
    page.getByRole('button', { name: 'Start the demo' })
  ).toBeDisabled()

  checks = {
    ...checks,
    images_present: true,
    disk_space_ok: true,
    disk_required_gb: 6,
  }
  await page.reload()
  await expect(main).toContainText(
    'Docker is ready and the images are downloaded.'
  )
  await expect(
    page.getByRole('button', { name: 'Start the demo' })
  ).toBeEnabled()
  await shot(page, 'onboarding-demo-preflight')
})

/** A-13 - the value proposition is printed once, by the sign-in page. */
test('the value proposition is printed once, on the sign-in page', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  await clearTargets(page.request)
  setBackendFixtures()
  const line = page.getByText('Find slow queries. Prove the fix.', {
    exact: true,
  })

  await page.goto('/account-login')
  await expect(line).toHaveCount(1)
  // Home and the add-database dialog carry no competing description.
  await page.goto('/onboarding')
  await expect(
    page.getByRole('dialog', { name: 'Add a database' })
  ).toBeVisible()
  await expect(line).toHaveCount(0)
})
