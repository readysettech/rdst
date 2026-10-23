import type { Page } from '@playwright/test'
import {
  mockSignedOut,
  onboardingTour,
  optInToOnboardingTour,
  PRIVACY_DOC_URL,
  PRIVACY_POLICY_URL,
} from './firstRun'
import { clearTargets, expect, setBackendFixtures, test } from './fixtures'

const TITLES = [
  'Connect your database',
  'RDST watches in the background',
  'Fixes land in your Inbox',
  'Ask your coding agent what RDST found',
]
/** The longest scene runs 10 s; the clock gets some slack on a busy runner. */
const SCENE_TIMEOUT = 15_000

async function expectStep(page: Page, step: number, timeout?: number) {
  const tour = onboardingTour(page)
  await expect(tour.getByText(`Step ${step} of 4`)).toBeVisible({ timeout })
  await expect(
    tour.getByRole('heading', { name: TITLES[step - 1] })
  ).toBeVisible()
  await expect(
    tour.getByRole('button', { name: `Step ${step}: ${TITLES[step - 1]}` })
  ).toHaveAttribute('aria-current', 'step')
}

async function openTour(page: Page) {
  await page.goto('/')
  await expect(onboardingTour(page)).toBeVisible()
  await expectStep(page, 1)
}

test.beforeEach(async ({ page }) => {
  setBackendFixtures()
  await clearTargets(page.request)
  await mockSignedOut(page)
  await optInToOnboardingTour(page)
})

test.describe('with motion', () => {
  test.use({ contextOptions: { reducedMotion: 'no-preference' } })

  test('each scene plays and moves on to the next when it ends', async ({
    page,
  }) => {
    await openTour(page)
    await expect(
      onboardingTour(page).getByRole('button', { name: 'Pause' })
    ).toBeVisible()
    await expectStep(page, 2, SCENE_TIMEOUT)
    await expectStep(page, 3, SCENE_TIMEOUT)
  })

  test('Pause holds the scene and Play picks it up again', async ({ page }) => {
    test.setTimeout(60_000)
    await openTour(page)
    const tour = onboardingTour(page)
    await tour.getByRole('button', { name: 'Pause' }).click()
    await expect(tour.getByRole('button', { name: 'Play' })).toBeVisible()
    await page.waitForTimeout(12_000)
    await expectStep(page, 1)

    await tour.getByRole('button', { name: 'Play' }).click()
    await expect(tour.getByRole('button', { name: 'Pause' })).toBeVisible()
    // The pointer over the scene holds it too; move it away.
    await page.mouse.move(0, 0)
    await expectStep(page, 2, SCENE_TIMEOUT)
  })
})

test('Next, Back, the step bars and the arrow keys page through the tour', async ({
  page,
}) => {
  await openTour(page)
  const tour = onboardingTour(page)
  const next = tour.getByTestId('onboarding-next')
  // The keyboard starts on Next, and the first step has nothing to go back to.
  await expect(next).toBeFocused()
  await expect(
    tour.getByRole('button', { name: 'Back', exact: true })
  ).toHaveCount(0)

  await next.click()
  await expectStep(page, 2)
  await tour.getByRole('button', { name: 'Back', exact: true }).click()
  await expectStep(page, 1)

  await next.focus()
  await page.keyboard.press('ArrowRight')
  await expectStep(page, 2)
  await page.keyboard.press('ArrowRight')
  await expectStep(page, 3)
  await page.keyboard.press('ArrowLeft')
  await expectStep(page, 2)

  // The last step trades Skip and Next for the way in.
  await tour.getByRole('button', { name: `Step 4: ${TITLES[3]}` }).click()
  await expectStep(page, 4)
  await expect(tour.getByRole('button', { name: 'Skip' })).toHaveCount(0)
  await page.keyboard.press('ArrowRight')
  await expectStep(page, 4)
  await expect(next).toHaveText('Add a database')
  await next.click()
  await expect(tour).toHaveCount(0)
  await expect(
    page.getByRole('dialog', { name: 'Find your database' })
  ).toBeVisible()
})

test('Skip opens Find your database and the tour stays done', async ({
  page,
}) => {
  await openTour(page)
  await onboardingTour(page).getByRole('button', { name: 'Skip' }).click()
  await expect(onboardingTour(page)).toHaveCount(0)
  await expect(page).toHaveURL(/\?add=agent/)
  const add = page.getByRole('dialog', { name: 'Find your database' })
  await expect(add.getByTestId('find-databases')).toBeVisible()
  // Connecting by hand stays one click away.
  await add.getByTestId('agent-connect-manually').click()
  await expect(
    page.getByRole('dialog', { name: 'Add a database' })
  ).toBeVisible()
  // The flag is keyed to the installation /api/status names.
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter(
        (key) =>
          key.startsWith('rdst_onboarding_tour_done:') &&
          localStorage.getItem(key) === '1'
      )
    )
  ).toEqual([expect.stringMatching(/^rdst_onboarding_tour_done:[0-9a-f]{16}$/)])

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(onboardingTour(page)).toHaveCount(0)
})

test('closing the tour leaves the user where they were', async ({ page }) => {
  await openTour(page)
  await page.keyboard.press('Escape')
  await expect(onboardingTour(page)).toHaveCount(0)
  await expect(
    page.getByRole('dialog', { name: 'Add a database' })
  ).toHaveCount(0)
})

test('skipping a resumed tour opens the add-database dialog once', async ({
  page,
}) => {
  await openTour(page)
  await onboardingTour(page).getByTestId('onboarding-next').click()
  await expectStep(page, 2)
  await page.keyboard.press('Escape')
  await expect(onboardingTour(page)).toHaveCount(0)

  await page.getByTestId('tour-resume').click()
  await expectStep(page, 2)
  await onboardingTour(page).getByRole('button', { name: 'Skip' }).click()
  const add = page.getByRole('dialog', { name: 'Find your database' })
  await expect(add).toBeVisible()

  // Home doesn't open it a second time in this session.
  await page.keyboard.press('Escape')
  await expect(add).toHaveCount(0)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
  await expect(add).toHaveCount(0)
})

test('with reduced motion each step holds its final frame until the user moves on', async ({
  page,
}) => {
  // The suite's browser context already asks for reduced motion.
  await openTour(page)
  const tour = onboardingTour(page)
  await expect(tour.getByRole('button', { name: 'Pause' })).toHaveCount(0)
  await page.waitForTimeout(12_000)
  await expectStep(page, 1)
  await tour.getByTestId('onboarding-next').click()
  await expectStep(page, 2)
})

test("the trust and privacy links point at Readyset's pages", async ({
  page,
}) => {
  await openTour(page)
  const tour = onboardingTour(page)
  const expectLink = async (name: string, href: string) => {
    const link = tour.getByRole('link', { name })
    await expect(link).toHaveAttribute('href', href)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(link).toHaveAttribute('rel', /noreferrer/)
  }
  await expectLink('How your data is handled', PRIVACY_DOC_URL)
  await expectLink('Privacy policy', PRIVACY_POLICY_URL)
  await expect(tour.getByTestId('authority-note')).toContainText(
    'RDST is made by Readyset.'
  )
  await expectLink(
    'Our story',
    'https://github.com/readysettech/readyset/blob/main/history.md'
  )

  // Every step carries the same links; who makes RDST is said on the first only.
  await tour.getByRole('button', { name: `Step 4: ${TITLES[3]}` }).click()
  await expectStep(page, 4)
  await expectLink('How your data is handled', PRIVACY_DOC_URL)
  await expectLink('Privacy policy', PRIVACY_POLICY_URL)
  await expect(tour.getByTestId('authority-note')).toHaveCount(0)
})
