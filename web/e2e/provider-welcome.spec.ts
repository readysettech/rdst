import { test as base, expect, mainContent } from './fixtures'

// Other product tests assume AI is configured. Exercise the real startup
// boundary instead of installing that shared prerequisite mock.
const test = base.extend({
  aiAccess: async ({ page }, use) => {
    void page
    await use(false)
  },
})

for (const source of ['missing', 'trial'] as const) {
  test(`shows the provider choice in place of Home for ${source} credentials`, async ({
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
    // The gate stands in for Home inside the app shell.
    const main = mainContent(page)
    const welcome = main.getByText(
      'Set up AI to get fixes and the daily report.'
    )
    await expect(welcome).toBeVisible()
    await expect(main.getByText('Hosted by Readyset')).toBeVisible()
    await expect(
      main.getByRole('button', { name: 'Sign in', exact: true })
    ).toBeVisible()
    await expect(main.getByText('Use my own key')).toBeVisible()
    await expect(
      main.getByRole('button', { name: 'Add key', exact: true })
    ).toBeVisible()
    await expect(main.getByRole('region', { name: 'Today' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /skip/i })).toHaveCount(0)
    await page.reload()
    await expect(welcome).toBeVisible()
    await expect(main.getByRole('region', { name: 'Today' })).toHaveCount(0)
  })
}
