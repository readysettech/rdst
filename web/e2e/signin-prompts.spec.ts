import type { Locator, Page, TestInfo } from '@playwright/test'
import { mockAnonymousAccess } from './accountless'
import {
  configureTestTarget,
  expect,
  mainContent,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from './fixtures'

const SOFT_PROMPT =
  'Sign in to keep your monitoring history and get alerts by email.'
const BUDGET_PROMPT = 'Sign in to keep AI monitoring running'
const SOFT_HIDDEN_KEY = 'rdst_signin_soft_hidden_at'
const THREE_DAYS_MS = 3 * 24 * 60 * 60 * 1000
const MODES = ['dark', 'light'] as const

async function setColorMode(page: Page, mode: (typeof MODES)[number]) {
  await page.evaluate((value) => {
    document.documentElement.dataset.colorMode = value
  }, mode)
}

/**
 * The prompt renders in both color modes, and its text takes each mode's colors. A
 * screenshot of each is attached to the report.
 */
async function expectInBothModes(
  page: Page,
  prompt: Locator,
  text: Locator,
  testInfo: TestInfo,
  name: string
) {
  const colors: string[] = []
  for (const mode of MODES) {
    await setColorMode(page, mode)
    await expect(prompt).toBeVisible()
    await expect(text).toBeVisible()
    colors.push(
      await text.evaluate((element) => getComputedStyle(element).color)
    )
    await testInfo.attach(`${name}-${mode}`, {
      body: await prompt.screenshot(),
      contentType: 'image/png',
    })
  }
  expect(colors[0], 'text color follows the color mode').not.toBe(colors[1])
  await setColorMode(page, 'dark')
}

test.beforeEach(async ({ page }) => {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
})

test('the soft prompt after a week is dismissible and comes back after three days', async ({
  page,
}, testInfo) => {
  await mockAnonymousAccess(page, 'soft', { days_remaining: 22 })
  await page.goto('/')

  const strip = page.getByTestId('signin-soft')
  await expect(strip).toContainText(SOFT_PROMPT)
  await expectInBothModes(
    page,
    strip,
    strip.getByText(SOFT_PROMPT),
    testInfo,
    'soft'
  )
  // It asks; it never blocks the page.
  await expect(page.getByTestId('signin-required')).toHaveCount(0)

  await strip.getByRole('button', { name: 'Sign in' }).click()
  const signIn = page.getByRole('dialog', { name: 'Sign in to Readyset' })
  await expect(signIn).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(signIn).toHaveCount(0)

  await strip.getByRole('button', { name: 'Not now' }).click()
  await expect(strip).toHaveCount(0)
  const hiddenAt = Number(
    await page.evaluate((key) => localStorage.getItem(key), SOFT_HIDDEN_KEY)
  )
  expect(hiddenAt).toBeGreaterThan(Date.now() - 60_000)

  // Still hidden on the next launch.
  await page.reload()
  await expect(mainContent(page)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(page.getByTestId('signin-soft')).toHaveCount(0)

  // Back once three days have passed since it was dismissed.
  await page.evaluate(([key, at]) => localStorage.setItem(key, at), [
    SOFT_HIDDEN_KEY,
    String(Date.now() - THREE_DAYS_MS - 60_000),
  ] as const)
  await page.reload()
  await expect(page.getByTestId('signin-soft')).toContainText(SOFT_PROMPT)
})

test('the budget prompt shows how much free AI is used, in the Inbox', async ({
  page,
}, testInfo) => {
  await mockAnonymousAccess(page, 'budget', { percent_used: 84.4 })
  await page.goto('/inbox')

  const card = page.getByTestId('signin-budget')
  await expect(card.getByRole('heading', { name: BUDGET_PROMPT })).toBeVisible()
  await expect(card).toContainText(
    "You've used 84% of the free AI on this computer."
  )
  await expectInBothModes(
    page,
    card,
    card.getByRole('heading', { name: BUDGET_PROMPT }),
    testInfo,
    'budget'
  )
  // A prominent ask, not a block, and not the soft line.
  await expect(page.getByTestId('signin-required')).toHaveCount(0)
  await expect(page.getByTestId('signin-soft')).toHaveCount(0)

  await card.getByRole('button', { name: 'Sign in' }).click()
  await expect(
    page.getByRole('dialog', { name: 'Sign in to Readyset' })
  ).toBeVisible()
})

test('the required prompt blocks hosted AI and offers an own key instead', async ({
  page,
}, testInfo) => {
  await mockAnonymousAccess(page, 'required', {
    percent_used: 40,
    days_remaining: 0,
  })

  // AI pages give way to the sign-in step.
  for (const path of ['/', '/ask']) {
    await page.goto(path)
    const step = page.getByTestId('signin-required')
    await expect(step).toBeVisible()
    await expect(
      step.getByRole('heading', { name: BUDGET_PROMPT })
    ).toBeVisible()
    await expect(step).toContainText(
      'Your 30 days of free AI on this computer are up.'
    )
  }
  const step = page.getByTestId('signin-required')
  await expectInBothModes(
    page,
    step,
    step.getByRole('heading', { name: BUDGET_PROMPT }),
    testInfo,
    'required'
  )

  await step.getByTestId('signin-required-action').click()
  const signIn = page.getByRole('dialog', { name: 'Sign in to Readyset' })
  await expect(signIn).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(signIn).toHaveCount(0)

  // The escape hatch: the user's own Anthropic key.
  await step
    .getByRole('button', { name: 'Use my own Anthropic key instead' })
    .click()
  await expect(
    page.getByRole('dialog', { name: 'Update Anthropic API key' })
  ).toBeVisible()
  await page.keyboard.press('Escape')

  // Local monitoring stays open behind the block.
  await page.goto('/inbox')
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible()
  await expect(page.getByTestId('signin-required')).toHaveCount(0)
})
