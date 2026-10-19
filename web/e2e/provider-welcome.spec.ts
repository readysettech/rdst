import type { Page } from '@playwright/test'
import { mockAnonymousAccess } from './accountless'
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
  test(`free anonymous AI opens Home for ${source} credentials`, async ({
    page,
  }) => {
    // A fresh install: Home offers the first database.
    await clearTargets(page.request)
    await mockAnonymousAccess(page, 'none')
    await mockCredentials(page, source)
    await page.goto('/')
    const main = mainContent(page)
    // Anonymous access counts as AI access: Home, not a provider choice.
    await expect(
      main.getByRole('button', { name: 'Add a database' }).first()
    ).toBeVisible()
    await expect(main.getByText(SETUP_HEADING)).toHaveCount(0)
    await expect(main.getByTestId('signin-required')).toHaveCount(0)
  })
}

test('an ended free allowance stands in for Home with sign-in or an own key', async ({
  page,
}) => {
  await mockAnonymousAccess(page, 'required')
  await page.goto('/')
  const main = mainContent(page)
  const step = main.getByTestId('signin-required')
  await expect(step).toBeVisible()
  await expect(
    step.getByRole('button', { name: 'Sign in', exact: true })
  ).toBeVisible()
  await expect(
    step.getByRole('button', { name: 'Use my own Anthropic key instead' })
  ).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Add a database' })
  ).toHaveCount(0)
  await expect(page.getByRole('button', { name: /skip/i })).toHaveCount(0)
  await page.reload()
  await expect(step).toBeVisible()
})

test('a backend without free hosted AI still shows the provider choice', async ({
  page,
}) => {
  await mockCredentials(page, 'missing')
  // An older backend answers account status without a sign-in prompt state.
  await page.route('**/api/account/status', (route) =>
    route.fulfill({ json: { signed_in: false } })
  )
  await page.goto('/')
  const main = mainContent(page)
  await expect(main.getByText(SETUP_HEADING)).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Sign in', exact: true })
  ).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Use my own Anthropic key instead' })
  ).toBeVisible()
})

test('settings offers the provider choice to an anonymous install', async ({
  page,
}) => {
  await mockAnonymousAccess(page, 'none')
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
