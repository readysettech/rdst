/**
 * The app shell: sidebar navigation, the mobile drawer, the target switcher,
 * the legacy-route redirects and the Home layout.
 *
 * Consolidated from the pack A navigation flow and the W4B shell/home
 * verification. Each test asserts the shipped behaviour; the doc comment names
 * the finding it protects.
 */
import {
  clearTargets,
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  DESKTOP,
  NARROW,
  PHONE,
  REDIRECTS,
  readySandbox,
  settle,
  shot,
} from './_helpers'

/** Every daily nav destination, with the page title it must agree with. */
const NAV = [
  { path: '/', label: 'Home' },
  { path: '/ask', label: 'Ask', heading: 'Ask' },
  { path: '/queries', label: 'Queries' },
  { path: '/cache', label: 'Benchmarks' },
  { path: '/audit', label: 'Health check', heading: 'Health check' },
  { path: '/schema', label: 'Schema', heading: 'Schema' },
  { path: '/configure', label: 'Settings', heading: 'Settings' },
] as const

/** Fixtures every workspace on the nav reads once on arrival. */
function shellFixtures() {
  setBackendFixtures({
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
    docker_runtime: [
      { value: { installed: true, running: true }, repeat: true },
    ],
  })
}

/**
 * MG-04 - the sidebar focus ring is painted the instant the caret arrives,
 * rather than fading in behind it over ~450ms.
 */
test('the sidebar focus ring is on screen the instant focus arrives', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()

  let ring: { boxShadow: string; label: string } | null = null
  for (let step = 0; step < 12 && !ring; step += 1) {
    await page.keyboard.press('Tab')
    ring = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      if (!el?.closest('#app-sidebar nav')) return null
      const style = getComputedStyle(el)
      return {
        boxShadow: style.boxShadow,
        label: (el.textContent ?? '').trim(),
      }
    })
  }

  expect(ring, 'a sidebar nav link is reachable by Tab').not.toBeNull()
  // Split on the commas between shadow layers, not the ones inside rgb().
  const layers = (ring?.boxShadow ?? 'none').split(/,(?![^(]*\))/)
  const painted = layers.filter(
    (layer) => layer.trim() !== 'none' && !/rgba\([^)]*,\s*0\)/.test(layer)
  )
  expect(painted.length).toBeGreaterThan(0)
})

/** A-26 - the mobile drawer carries a close control and keeps its footer inside. */
test('the mobile drawer carries a close control and keeps its footer inside', async ({
  page,
}) => {
  await page.setViewportSize(PHONE)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')

  const hamburger = page.getByRole('button', { name: 'Open navigation' })
  await hamburger.click()
  const drawer = page.locator('#app-sidebar')
  const close = drawer.getByRole('button', { name: 'Close navigation' })
  await expect(close).toBeVisible()

  await drawer.getByTestId('setup-steps-toggle').click()
  await settle(page, 600)
  const geometry = await page.evaluate(() => {
    const aside = document.querySelector('#app-sidebar') as HTMLElement
    const utilities = document.querySelector(
      '[data-testid="sidebar-footer-utilities"]'
    ) as HTMLElement
    return {
      asideBottom: aside.getBoundingClientRect().bottom,
      utilitiesBottom: utilities.getBoundingClientRect().bottom,
    }
  })
  expect(geometry.utilitiesBottom).toBeLessThanOrEqual(geometry.asideBottom + 1)
  await shot(page, 'shell-mobile-drawer')

  await close.click()
  await expect(hamburger).toHaveAttribute('aria-expanded', 'false')
})

/**
 * A-23 / MG-07 - the page a nav item leads to is named with the words the user
 * clicked.
 * F-53 - and plain navigation to each of them leaves the console clean, which
 * the `browserErrors` fixture asserts for every test in this file.
 */
test('every nav destination resolves, marks itself current, and keeps its name', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  shellFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)

  for (const item of NAV) {
    await page.goto(item.path)
    const link = page
      .locator('#app-sidebar')
      .getByRole('link', { name: item.label, exact: true })
    await expect(link).toHaveAttribute('aria-current', 'page')
    if ('heading' in item) {
      await expect(
        page.getByRole('heading', { level: 1, name: item.heading })
      ).toBeVisible()
    }
  }
})

/**
 * F-54 - the retired URLs keep working, and the two that land somewhere that
 * looks unrelated say what happened: /agents carries its hand-off to Ask and
 * /top carries the ordering the bookmark asked for.
 */
test('legacy routes land on their current homes and explain the move', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  shellFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)

  for (const route of REDIRECTS) {
    await page.goto(route.path)
    await expect
      .poll(() => new URL(page.url()).pathname)
      .toBe(route.lands ?? route.path)
  }

  await page.goto('/agents')
  await expect(page).toHaveURL(/from=agents/)
  await expect(page.getByText('Agents was retired')).toBeVisible()

  await page.goto('/top')
  await expect(page).toHaveURL(/sort=slowest-average/)
})

/** A-20 - an install with no database is named in the user's words. */
test('an install with no database is named in plain words', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await clearTargets(page.request)
  await page.goto('/')

  const sidebar = page.locator('#app-sidebar')
  await expect(sidebar.getByText('No targets', { exact: true })).toBeVisible()
  await expect(sidebar.getByText('No Targets', { exact: true })).toHaveCount(0)
})

/**
 * A-40 - the demo card's call to action sits on its row's footer baseline
 * rather than mid-card above a band of empty gradient.
 */
test('the demo card lands its call to action on the row baseline', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')

  const card = page.getByRole('link', { name: /Launch the demo/ })
  await expect(card).toBeVisible()
  const cardBox = await card.boundingBox()
  const ctaBox = await page
    .getByText('Launch the demo', { exact: true })
    .boundingBox()
  const cardBottom = (cardBox?.y ?? 0) + (cardBox?.height ?? 0)
  const ctaBottom = (ctaBox?.y ?? 0) + (ctaBox?.height ?? 0)
  expect(cardBottom - ctaBottom).toBeLessThan(60)
  await shot(page, 'shell-home-demo-card')
})

/**
 * D-17 - at the stated 1024x720 minimum the sidebar narrows so the content
 * column stays wide enough to read.
 */
test('the content column stays readable at the 1024 floor', async ({
  page,
}) => {
  await page.setViewportSize(NARROW)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()

  const width = await page.evaluate(
    () =>
      (
        document.querySelector('#main-content') as HTMLElement
      ).getBoundingClientRect().width
  )
  expect(width).toBeGreaterThanOrEqual(760)
  const overflows = await page.evaluate(
    () =>
      document.documentElement.scrollWidth >
      document.documentElement.clientWidth
  )
  expect(overflows).toBe(false)
})

/**
 * A-25 - the target switcher carries the way on to managing targets, so a
 * single-target install has a next step rather than a popover with one row.
 */
test('A-25 - the target switcher offers a way to manage targets', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')

  await page
    .locator('#app-sidebar')
    .getByRole('button', { name: /Switch target/ })
    .click()
  await expect(
    page.getByRole('menuitem', { name: /Manage targets|Add target/ })
  ).toBeVisible()
})

/**
 * A-12 - /dev-settings forwards to a Developer section a production build does
 * not render, and the arrival says so instead of landing silently on Targets.
 */
test('A-12 - /dev-settings says the developer tools are development-only', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/dev-settings')

  await expect(
    page.getByText(/developer (tools|settings).*development/i)
  ).toBeVisible()
})
