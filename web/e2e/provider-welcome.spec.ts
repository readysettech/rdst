import { test as base, expect } from './fixtures'

// Other product tests assume AI is configured. Exercise the real startup
// boundary instead of installing that shared prerequisite mock.
const test = base.extend({
  aiAccess: async ({ page }, use) => {
    void page
    await use(false)
  },
})

for (const source of ['missing', 'trial'] as const) {
  test(`shows the full-screen provider choice for ${source} credentials`, async ({
    page,
  }) => {
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
    await page.goto('/')
    const welcome = page.getByRole('heading', {
      name: 'Choose how RDST uses AI',
    })
    await expect(welcome).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Sign up or sign in', exact: true })
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Add Anthropic key', exact: true })
    ).toBeVisible()
    // The welcome replaces the app rather than adding an inline notice.
    await expect(page.locator('#main-content')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /skip/i })).toHaveCount(0)
    await page.reload()
    await expect(welcome).toBeVisible()
    await expect(page.locator('#main-content')).toHaveCount(0)
  })
}
