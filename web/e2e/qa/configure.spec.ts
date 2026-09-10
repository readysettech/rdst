/**
 * Settings: the targets section and its add/edit/delete surfaces, the AI
 * access panel, and account sign-in.
 *
 * Consolidated from the pack A configure/account flows, the W4B settings-form
 * verification and the W3B copy verification. Each test asserts the shipped
 * behaviour; the doc comment names the finding it protects.
 */
import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mainContent,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, PHONE, settle, shot } from './_helpers'

const RESOURCE_404 =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

/** A signed-in Readyset account supplying the included AI. */
async function mockReadysetAccount(page: Page, email = 'qa@readyset.io') {
  await page.route('**/api/account/status', (route) =>
    route.fulfill({
      json: { signed_in: true, email, analytics_account_id: 'acct_qa' },
    })
  )
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: false,
        requirements: [
          {
            kind: 'anthropic_api_key',
            satisfied: true,
            source: 'readyset_account',
            selected_provider: 'readyset',
            readyset_account_connected: true,
            anthropic_key_configured: false,
            target: null,
            accepted_names: ['ANTHROPIC_API_KEY'],
          },
        ],
      },
    })
  )
}

/** Open the add-target drawer on its manual form. */
async function openManualAddForm(page: Page) {
  await page.getByRole('button', { name: 'Add target' }).first().click()
  await page.getByRole('tab', { name: 'Manual setup' }).click()
  await expect(page.locator('#cfg-host')).toBeVisible()
}

/** MG-15 - the targets section names one concept with one noun. */
test('the targets section speaks one noun', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await page.goto('/configure')

  await expect(page.getByRole('heading', { name: 'Targets' })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Add target' }).first()
  ).toBeVisible()
  await expect(page.getByText('Database connections')).toHaveCount(0)
  await expect(page.getByText('Add connection')).toHaveCount(0)
})

/**
 * A-08 - the add-target drawer keeps its action row inside a 1280x800 window.
 * A-18 - and no field carries a required asterisk, so the marker is gone
 * rather than meaningless.
 */
test('the add-target drawer keeps its submit in view and marks no field required', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await page.goto('/configure')
  await openManualAddForm(page)

  const submit = page
    .getByRole('button', { name: 'Add target', exact: true })
    .last()
  const box = await submit.boundingBox()
  expect(box).not.toBeNull()
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(DESKTOP.height)

  const labels = await page.getByRole('dialog').locator('label').allInnerTexts()
  expect(labels.length).toBeGreaterThan(0)
  expect(labels.filter((label) => label.includes('*'))).toEqual([])
  await shot(page, 'configure-add-target')
})

/** A-08 - the connection form stacks to one column and stays usable at 390. */
test('the add-target form stacks to one column at 390', async ({ page }) => {
  await page.setViewportSize(PHONE)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await page.goto('/configure')
  await openManualAddForm(page)

  const rows = await page.evaluate(() => {
    const host = document.querySelector('#cfg-host') as HTMLElement
    const port = document.querySelector('#cfg-port') as HTMLElement
    return {
      hostTop: Math.round(host.getBoundingClientRect().top),
      portTop: Math.round(port.getBoundingClientRect().top),
    }
  })
  expect(rows.portTop).toBeGreaterThan(rows.hostTop)

  const box = await page
    .getByRole('button', { name: 'Add target', exact: true })
    .last()
    .boundingBox()
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(PHONE.height)
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(PHONE.width)
})

/**
 * A-24 - the delete confirmation states the consequence once and keeps one
 * verb from the title through to the button.
 */
test('the delete confirmation states the consequence once', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await page.goto('/configure')

  const row = page
    .getByTestId('target-row')
    .filter({ has: page.getByText('e2e-guard', { exact: true }) })
  await row.getByRole('button', { name: 'More actions for e2e-guard' }).click()
  await page.getByRole('menuitem', { name: /^Delete/ }).click()

  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByRole('heading', { name: /^Delete target/ })
  ).toBeVisible()
  const body = await dialog.innerText()
  // The target is named where the decision is made, and nowhere else.
  expect(body.match(/e2e-guard/g) ?? []).toHaveLength(1)
  // One verb: nothing in the dialog switches to "remove".
  expect(body).not.toMatch(/remove/i)
  await expect(
    dialog.getByRole('button', { name: 'Delete target' })
  ).toBeVisible()

  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(row).toBeVisible()
})

/** A-07 - every AI action stays inside the card and inside a 390px viewport. */
test('every AI action stays reachable at 390', async ({ page }) => {
  await page.setViewportSize(PHONE)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await mockReadysetAccount(page)
  await page.goto('/configure?panel=ai')

  const main = mainContent(page)
  await expect(main.getByText('Using the included AI')).toBeVisible()
  for (const label of ['Sign out', 'Switch Readyset account', 'Set key']) {
    const box = await main.getByRole('button', { name: label }).boundingBox()
    expect(box, `${label} is rendered`).not.toBeNull()
    expect(box?.x ?? 0).toBeGreaterThanOrEqual(0)
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(PHONE.width)
  }
  await shot(page, 'configure-ai-390')
})

/**
 * A-09 - the signed-in identity is the control: the sidebar email opens the
 * account menu, so signing out is not buried on one settings tab.
 */
test('the signed-in identity opens an account menu that offers sign out', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockReadysetAccount(page)
  await page.goto('/')

  await page.getByTestId('sidebar-account-email').click()
  await expect(
    page.getByRole('menuitem', { name: 'Manage AI access' })
  ).toBeVisible()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(
    page.getByRole('heading', { name: 'Sign out of Readyset?' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()
})

/** A-16 - the sign-in dialog answers an invalid address under the field. */
test('the sign-in dialog answers an invalid address under the field', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/account-login')

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const email = dialog.locator('input[type="email"]')
  await email.fill('not-an-email')
  await email.blur()

  await expect(dialog.getByText('Enter a valid email address.')).toBeVisible()
  await expect(
    dialog.getByRole('button', { name: 'Send sign-in link' })
  ).toBeDisabled()

  await email.fill('qa@readyset.io')
  await expect(
    dialog.getByRole('button', { name: 'Send sign-in link' })
  ).toBeEnabled()
})

/** A-15 - a dead sign-in callback shows one state, with a way to start over. */
test('a dead sign-in callback shows one state and a way to start over', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(browserErrors, RESOURCE_404)
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.route('**/api/account/login/*/context', (route) =>
    route.fulfill({ status: 404, json: { detail: 'Unknown sign-in' } })
  )
  await page.goto('/account-login?login_id=abc123')

  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByText('This sign-in link has expired or was already used.')
  ).toBeVisible()
  await expect(dialog.getByText('Sign-in could not be completed')).toBeVisible()
  await expect(
    dialog.getByText(/Completing your Readyset sign-in/)
  ).toHaveCount(0)

  await dialog.getByRole('button', { name: 'Start over' }).click()
  await expect(dialog.locator('input[type="email"]')).toBeVisible()
})

/**
 * A-11 - the AI status tile is a status indicator: a configuration that can
 * already answer wears the positive tokens, and warning is kept for one the
 * reader still has to finish.
 */
test('A-11 - a healthy AI configuration is painted with the positive tokens', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockReadysetAccount(page)
  await page.goto('/configure?panel=ai')

  const main = mainContent(page)
  await expect(main.getByText('Using the included AI')).toBeVisible()
  await settle(page)
  expect(await main.locator('[class*="warning"]').count()).toBe(0)
})
