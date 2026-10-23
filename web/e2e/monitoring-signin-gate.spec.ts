import type { Page, Request } from '@playwright/test'
import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

// Monitoring's AI runs through a Readyset account, so turning monitoring on signed out
// asks for a sign-in right there. Turning on without AI stays possible, as the quieter way.

const TARGET = 'e2e-guard'
const AUTH_URL = 'https://supabase.e2e.test/auth/v1'

/** Answer account status as signed out until `signIn` flips it. */
async function mockAccount(page: Page) {
  let signedIn = false
  await page.route('**/api/account/status', (route) =>
    route.fulfill({
      json: signedIn
        ? { signed_in: true, has_session: true, email: 'new@example.com' }
        : { signed_in: false, has_session: false, detail: 'Not signed in' },
    })
  )
  return {
    signIn: () => {
      signedIn = true
    },
  }
}

/** Start a sign-in against a stand-in Supabase that accepts any magic-link request. */
async function mockSignInStart(page: Page) {
  await page.route('**/api/account/login', (route) =>
    route.fulfill({
      json: {
        login_id: 'login-e2e',
        state: 'state-e2e',
        auth_url: AUTH_URL,
        publishable_key: 'sb_publishable_e2e',
        callback_url: 'http://127.0.0.1/account-login?login_id=login-e2e',
        expires_in: 600,
      },
    })
  )
  await page.route('https://supabase.e2e.test/**', (route) =>
    route.fulfill({ json: {} })
  )
}

const turnOnRequest = (request: Request) =>
  request.method() === 'PUT' &&
  request.url().endsWith(`/api/monitor/targets/${TARGET}/settings`) &&
  request.postDataJSON()?.enabled === true

async function openTurnOn(page: Page) {
  await page.goto(`/database?target=${TARGET}`)
  await page.getByRole('button', { name: 'Turn on monitoring' }).click()
  const dialog = page.getByRole('dialog', { name: `Monitor ${TARGET}` })
  await expect(dialog).toBeVisible()
  return dialog
}

test.beforeEach(async ({ page, browserErrors }) => {
  // A database with no report yet answers its latest report with a 404.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await configureTestTarget(page, { hasPassword: true })
  await mockPrerequisites(page, TARGET)
})

test.afterEach(async ({ page }) => {
  await page.request.put(`/api/monitor/targets/${TARGET}/settings`, {
    data: { enabled: false },
  })
})

test('signed out, turning on needs a Readyset account, with no way around it', async ({
  page,
}) => {
  await mockAccount(page)
  const dialog = await openTurnOn(page)

  const gate = dialog.getByTestId('monitoring-signin-gate')
  await expect(gate).toContainText(
    'Monitoring runs on a free Readyset account.'
  )
  await expect(
    dialog.getByRole('button', { name: 'Turn on monitoring' })
  ).toHaveCount(0)
  await expect(
    dialog.getByRole('button', { name: 'Turn on without AI' })
  ).toHaveCount(0)
  await dialog
    .getByRole('button', { name: 'Get started with Readyset' })
    .click()
  const signUp = page.getByRole('dialog', { name: 'Get started with Readyset' })
  await expect(
    signUp.getByRole('button', { name: 'Sign up with Google' })
  ).toBeVisible()
})

test('signing in from the gate turns monitoring on once the sign-in finishes', async ({
  page,
}) => {
  const account = await mockAccount(page)
  await mockSignInStart(page)
  const dialog = await openTurnOn(page)

  let turnedOn = false
  page.on('request', (request) => {
    if (turnOnRequest(request)) turnedOn = true
  })
  await dialog
    .getByRole('button', { name: 'Get started with Readyset' })
    .click()
  const login = page.getByRole('dialog', { name: 'Get started with Readyset' })
  await expect(login).toBeVisible()
  await login.getByLabel('Email address').fill('new@example.com')
  await login.getByRole('button', { name: 'Email me a link' }).click()
  await expect(login.getByText('Check your email')).toBeVisible()
  expect(turnedOn).toBe(false)

  // The emailed link signs in from another tab; this one notices and turns monitoring on.
  const enabled = page.waitForRequest(turnOnRequest)
  account.signIn()
  await enabled
  await expect(login).toHaveCount(0)
  await expect(dialog).toHaveCount(0)
})

test('signed in, turning on asks for nothing more', async ({ page }) => {
  await page.route('**/api/account/status', (route) =>
    route.fulfill({
      json: { signed_in: true, has_session: true, email: 'e2e@example.com' },
    })
  )
  const dialog = await openTurnOn(page)
  await expect(dialog.getByTestId('monitoring-signin-gate')).toHaveCount(0)
  const enabled = page.waitForRequest(turnOnRequest)
  await dialog.getByRole('button', { name: 'Turn on monitoring' }).click()
  await enabled
})
