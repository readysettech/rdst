import { configureTestTarget, expect, test } from './fixtures'

// The demo was deliberately ungated (rdst-dma.3): no email wall before the
// user sees value. This spec guards that decision: the old signup dialog must
// not reappear on the home page or on the Readyset Platform page.
test('demo page opens without an email gate', async ({ page }) => {
  const gate = page.getByRole('dialog', {
    name: 'Enter your email to start the demo',
  })
  // hasPassword: the adaptive home probes the target on '/', and a
  // passwordless target answers 423 (locked), tripping the browser-error
  // fixture.
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(gate).toHaveCount(0)

  await page.goto('/demo')
  await expect(
    page.getByRole('heading', { level: 1, name: 'Readyset Platform' })
  ).toBeVisible()
  await expect(gate).toHaveCount(0)
})
