/**
 * First run: the connect flow, the AI prerequisite, the demo entry point and
 * the setup guide.
 *
 * Consolidated from the pack A first-run/demo/AI-gate flows and the W2C setup
 * guide verification. Every test asserts the behaviour the product ships; the
 * doc comment above each one names the finding it protects.
 */
import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  clearTargets,
  configureTestTarget,
  expect,
  mainContent,
  mockAiKeyReady,
  mockPrerequisites,
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

async function fillConnectForm(page: Page, name: string) {
  await page.locator('[name="name"]').fill(name)
  await page.locator('[name="host"]').fill('192.0.2.10')
  await page.locator('[name="database"]').fill('application')
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

  await page.goto('/onboarding')
  await expect(
    page.getByRole('heading', { name: 'Start with Readyset' })
  ).toBeVisible()
  await fillConnectForm(page, 'qa-connect')

  // An unreachable database is reported and nothing is written.
  await page.getByRole('button', { name: 'Test & connect' }).click()
  await expect(mainContent(page).getByText('Connection failed')).toBeVisible()
  expect(attempts).toEqual(['/api/configure/targets/qa-connect/test'])
  await expect(page).toHaveURL(/\/onboarding/)
  const before = await (await page.request.get('/api/configure/targets')).json()
  expect(before.targets).toEqual([])

  // A reachable one is saved, promoted to default, and the next step is the
  // turn-on dialog on its Database page.
  reachable = true
  await mockPrerequisites(page, 'qa-connect')
  await page.getByRole('button', { name: 'Test & connect' }).click()
  await expect(page).toHaveURL(/\/database\?panel=enable/)
  const turnOn = page.getByRole('dialog', {
    name: 'Turn on monitoring for qa-connect',
  })
  await expect(
    turnOn.getByTestId('prereq-checker').getByRole('heading', {
      name: 'Ready to monitor',
    })
  ).toBeVisible()
  expect(attempts).toHaveLength(2)
  const after = await (await page.request.get('/api/configure/targets')).json()
  expect(after.default_target).toBe('qa-connect')
})

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

  // The sign-in callback stays reachable so account setup can complete, and
  // target setup stays open for local monitoring.
  for (const route of ROUTES.filter((route) => route.id !== 'account-login')) {
    await page.goto(route.path)
    if (route.id.startsWith('configure')) {
      await expect(mainContent(page)).toBeVisible()
      await expect(
        page.getByRole('heading', {
          name: 'Choose how RDST uses AI',
          exact: true,
        })
      ).toHaveCount(0)
      continue
    }
    await expect(
      page.getByRole('heading', {
        name: 'Choose how RDST uses AI',
        exact: true,
      })
    ).toBeVisible()
    await expect(
      page.getByTestId('ai-access-sign-in').getByRole('button', {
        name: 'Sign up or sign in',
        exact: true,
      })
    ).toBeVisible()
    await expect(
      page.getByTestId('ai-access-own-key').getByRole('button', {
        name: 'Add Anthropic key',
        exact: true,
      })
    ).toBeVisible()
    await expect(mainContent(page)).toHaveCount(0)
  }

  await mockAiKeyReady(page)
  for (const [path, heading] of [
    ['/onboarding', 'Start with Readyset'],
    ['/demo', 'See Readyset Platform in action'],
  ]) {
    await page.goto(path)
    await expect(
      page.getByRole('heading', { name: heading, exact: true })
    ).toBeVisible()
    await expect(
      page.getByRole('heading', {
        name: 'Choose how RDST uses AI',
        exact: true,
      })
    ).toHaveCount(0)
  }
})

/**
 * A-05 - an unknown disk figure renders words rather than `NaN`.
 * A-32 - the checklist names every requirement, the passing ones included.
 */
test('the demo checklist names every requirement and never prints NaN', async ({
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
  await expect(main.getByText('Docker is running')).toBeVisible()
  await expect(
    main.getByText('The demo needs more available disk space.')
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
  await expect(main.getByText('Docker is running')).toBeVisible()
  await expect(main.getByText('Container images downloaded')).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Start the demo' })
  ).toBeEnabled()
  await shot(page, 'onboarding-demo-preflight')
})

/**
 * MG-12 - the setup guide is sidebar chrome, so it cannot occlude the page it
 * introduces.
 * MG-03 - and the page keeps the clicks aimed at its own controls.
 */
test('the setup guide lives in the sidebar and never covers the page', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')

  const block = page.locator('#app-sidebar').getByTestId('setup-steps')
  await expect(block).toBeVisible()
  await expect(block).toContainText('Setup')
  await expect(block).toContainText('1 of 2')

  const geometry = await page.evaluate(() => {
    const aside = document.querySelector('#app-sidebar') as HTMLElement
    const steps = document.querySelector(
      '[data-testid="setup-steps"]'
    ) as HTMLElement
    const corner = document.elementFromPoint(
      window.innerWidth - 40,
      window.innerHeight - 40
    )
    return {
      asideRight: aside.getBoundingClientRect().right,
      stepsRight: steps.getBoundingClientRect().right,
      cornerOwner: corner?.closest('[data-testid="setup-steps"]')
        ? 'setup'
        : 'page',
    }
  })
  expect(geometry.stepsRight).toBeLessThanOrEqual(geometry.asideRight + 1)
  expect(geometry.cornerOwner).toBe('page')
  await shot(page, 'onboarding-setup-steps')
})

/** MG-03 - the guide can be hidden, the choice sticks, and the sidebar brings it back. */
test('hiding the setup guide persists and the sidebar brings it back', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')

  await expect(page.getByTestId('setup-steps')).toBeVisible()
  await page.getByTestId('setup-steps-dismiss').click()
  await expect(page.getByTestId('setup-steps')).toHaveCount(0)
  await expect(page.getByTestId('setup-guide-help-entry')).toBeVisible()

  await page.reload()
  await expect(page.getByTestId('setup-steps')).toHaveCount(0)
  await page.getByTestId('setup-guide-help-entry').click()
  await expect(page.getByTestId('setup-steps')).toBeVisible()
})

/** A-29 - the connect page carries one identity mark and one content left edge. */
test('the connect page carries one identity mark and one left edge', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  await clearTargets(page.request)
  setBackendFixtures()
  await page.goto('/onboarding')

  const main = mainContent(page)
  await expect(
    page.getByRole('heading', { name: 'Start with Readyset' })
  ).toBeVisible()
  // The shell's breadcrumb owns the site identity; the page adds none.
  await expect(main.getByText('RDST', { exact: true })).toHaveCount(0)
  await expect(
    page.locator('header').getByRole('link', { name: 'RDST' })
  ).toBeVisible()

  const heading = await page
    .getByRole('heading', { name: 'Start with Readyset' })
    .boundingBox()
  const form = await main.locator('form').first().boundingBox()
  expect(Math.abs((heading?.x ?? 0) - (form?.x ?? -1))).toBeLessThanOrEqual(1)
})

/** A-13 - the value proposition is printed once per screen, by the sidebar. */
test('the value proposition is printed once per screen', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await clearTargets(page.request)
  setBackendFixtures()

  for (const path of ['/', '/onboarding']) {
    await page.goto(path)
    await expect(
      page.getByText('Find slow queries. Prove the fix.', { exact: true })
    ).toHaveCount(1)
  }
})
