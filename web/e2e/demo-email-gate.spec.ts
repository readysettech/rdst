import { configureTestTarget, expect, test } from './fixtures'

test('requires signup on the demo page only', async ({ page }) => {
  const gate = page.getByRole('dialog', { name: 'Tell us who you are' })
  await configureTestTarget(page)

  await page.goto('/')
  await expect(page.getByText('What do you want to do?')).toBeVisible()
  await expect(gate).toHaveCount(0)

  await page.goto('/demo')
  await expect(gate).toBeVisible()
})
