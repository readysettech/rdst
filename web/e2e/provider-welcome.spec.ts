import type { Page } from '@playwright/test'
import { mockSignedOut } from './firstRun'
import { test as base, clearTargets, expect, mainContent } from './fixtures'

// Other product tests assume AI is configured. Exercise the real startup
// boundary instead of installing that shared prerequisite mock.
const test = base.extend({
  aiAccess: async ({ page }, use) => {
    void page
    await use(false)
  },
})

const SETUP_HEADING = 'Set up AI'

async function mockCredentials(page: Page, source: 'missing' | 'trial') {
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: false,
        telemetry_enabled: false,
        requirements: [
          {
            kind: 'anthropic_api_key',
            accepted_names: ['ANTHROPIC_API_KEY'],
            target: null,
            source,
            // An old backend reporting a satisfied trial must not bypass setup.
            satisfied: source === 'trial',
          },
        ],
      },
    })
  )
}

for (const source of ['missing', 'trial'] as const) {
  test(`a signed-out install opens Home for ${source} credentials`, async ({
    page,
  }) => {
    // A fresh install: Home offers the first database, and sign-in waits for monitoring.
    await clearTargets(page.request)
    await mockSignedOut(page)
    await mockCredentials(page, source)
    await page.goto('/')
    const main = mainContent(page)
    await expect(
      main.getByRole('button', { name: 'Add a database' }).first()
    ).toBeVisible()
    await expect(main.getByText(SETUP_HEADING)).toHaveCount(0)
    await expect(
      main.getByText('Sign in there for free AI.', { exact: false })
    ).toBeVisible()
  })
}

test('AI pages offer sign-in or an own key while signed out', async ({
  page,
}) => {
  await mockSignedOut(page)
  await page.goto('/ask')
  const main = mainContent(page)
  await expect(main.getByText(SETUP_HEADING)).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Sign in', exact: true })
  ).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Use my own Anthropic key instead' })
  ).toBeVisible()
  await expect(page.getByRole('button', { name: /skip/i })).toHaveCount(0)
  await page.reload()
  await expect(main.getByText(SETUP_HEADING)).toBeVisible()
})

test('settings offers the provider choice to a signed-out install', async ({
  page,
}) => {
  await mockSignedOut(page)
  await page.goto('/configure?section=ai')
  // The deep link opens the own-key dialog, which also offers Readyset sign-in.
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText(/Anthropic/).first()).toBeVisible()
  await dialog
    .getByRole('button', { name: /close|cancel/i })
    .first()
    .click()
  await expect(
    mainContent(page).getByRole('button', { name: 'Sign in', exact: true })
  ).toBeVisible()
})
