import { configureTestTarget, expect, test } from './fixtures'

test('requires signup on the demo page only', async ({ page }) => {
  // The gate collects only an email now; its dialog is labelled by the
  // collect-state heading.
  const gate = page.getByRole('dialog', {
    name: 'Enter your email to start the demo',
  })
  await configureTestTarget(page)

  await page.goto('/')
  await expect(page.getByText('What do you want to do?')).toBeVisible()
  await expect(gate).toHaveCount(0)

  await page.goto('/demo')
  await expect(gate).toBeVisible()
})
