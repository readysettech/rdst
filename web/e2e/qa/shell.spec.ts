/**
 * The app shell: the sidebar on the page tone with the canvas beside it, the
 * database list, the legacy-route redirects and the Home layout.
 *
 * Consolidated from the pack A navigation flow and the W4B shell/home
 * verification, then moved onto the Continuous Analysis shell. Each test
 * asserts the shipped behaviour; the doc comment names the finding it protects.
 */
import {
  acceptBrowserError,
  clearTargets,
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, NARROW, REDIRECTS, readySandbox, shot } from './_helpers'

/** Every nav destination, with the page title it must agree with. */
const NAV = [
  { path: '/', label: 'Home', heading: 'Home' },
  { path: '/inbox', label: 'Inbox', heading: 'Inbox' },
  { path: '/actions', label: 'Actions', heading: 'Actions' },
  { path: '/ask', label: 'Ask', heading: 'Ask' },
  { path: '/agent-activity', label: 'Activity', heading: 'Activity' },
  { path: '/configure', label: 'Settings', heading: 'Settings' },
  { path: '/help', label: 'Help', heading: 'Help' },
] as const

/** A nav link's name: its label, with the Inbox badge or a quiet count after it. */
function navLink(page: import('@playwright/test').Page, label: string) {
  return page
    .locator('#app-sidebar')
    .getByRole('link', { name: new RegExp(`^${label}( ?\\d+)?$`) })
}

/** The test database's item under the Databases caption, named first. */
function databaseLink(page: import('@playwright/test').Page) {
  return page
    .getByRole('navigation', { name: 'Databases' })
    .getByRole('link', { name: /^e2e-guard/ })
}

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

/**
 * A-23 / MG-07 - the page a nav item leads to is named with the words the user
 * clicked.
 * F-53 - and plain navigation to each of them leaves the console clean, which
 * the `browserErrors` fixture asserts for every test in this file.
 */
test('every nav destination resolves, marks itself current, and keeps its name', async ({
  page,
  browserErrors,
}) => {
  // This fresh target has no daily report; the API returns 404 for that empty state.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await page.setViewportSize(DESKTOP)
  shellFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)

  for (const item of NAV) {
    await page.goto(item.path)
    await expect(navLink(page, item.label)).toHaveAttribute(
      'aria-current',
      'page'
    )
    await expect(
      page.getByRole('heading', { level: 1, name: item.heading })
    ).toBeVisible()
  }

  // A database's page is current under the Databases caption, named by the database.
  await databaseLink(page).click()
  await expect(page).toHaveURL(/\/database/)
  await expect(databaseLink(page)).toHaveAttribute('aria-current', 'page')
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()

  // The Readyset Platform card leads to its page.
  await page
    .locator('#app-sidebar')
    .getByRole('link', { name: /^Readyset Platform/ })
    .click()
  await expect(
    page.getByRole('heading', { level: 1, name: 'Readyset Platform' })
  ).toBeVisible()
})

/**
 * The shell is the sidebar flush on the page tone and one rounded canvas
 * beside it: no header bar above the page, the primary group of four, one item
 * per database, the Platform card, and the secondary group with the account
 * line at the bottom.
 */
test('the shell is a sidebar and a canvas, with the two nav groups', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()

  const primary = page.getByRole('navigation', { name: 'Primary' })
  await expect(primary.getByRole('link')).toHaveText([
    'Home',
    /^Inbox\d*$/,
    /^Actions\d*$/,
    'Ask',
  ])
  await expect(databaseLink(page)).toBeVisible()
  await expect(
    page
      .locator('#app-sidebar')
      .getByRole('link', { name: /^Readyset Platform/ })
  ).toBeVisible()
  const secondary = page.getByRole('navigation', { name: 'Secondary' })
  await expect(secondary.getByRole('link', { name: /^Activity/ })).toBeVisible()
  await expect(secondary.getByRole('link', { name: 'Settings' })).toBeVisible()
  await expect(secondary.getByRole('link', { name: 'Help' })).toBeVisible()
  // Signed out, the account line is the way to the sign-in page.
  await expect(page.getByTestId('sidebar-sign-in')).toHaveText('Sign in')

  // The page bar sits inside the canvas; nothing stands above it.
  await expect(page.locator('#main-content [data-page-bar]')).toBeVisible()
  await expect(page.locator('body > div > header')).toHaveCount(0)
  const geometry = await page.evaluate(() => {
    const sidebar = document
      .querySelector('#app-sidebar')!
      .getBoundingClientRect()
    const main = document
      .querySelector('#main-content')!
      .getBoundingClientRect()
    return {
      sidebarLeft: sidebar.left,
      gap: main.left - sidebar.right,
      inset: main.top,
      radius: getComputedStyle(document.querySelector('#main-content')!)
        .borderTopLeftRadius,
    }
  })
  expect(geometry.sidebarLeft).toBe(0)
  expect(geometry.gap).toBeLessThanOrEqual(1)
  expect(geometry.inset).toBe(12)
  expect(geometry.radius).not.toBe('0px')
  await shot(page, 'shell-home')
})

/**
 * F-54 - the retired URLs keep working, and /top carries the ordering the
 * bookmark asked for.
 */
test('legacy routes land on their current homes', async ({ page }) => {
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
})

/**
 * A-20 - an install with no database is named in the user's words: the
 * Databases caption stands over an empty list, with the way to add one beside it.
 */
test('an install with no database is named in plain words', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await clearTargets(page.request)
  await page.goto('/')

  const sidebar = page.locator('#app-sidebar')
  await expect(sidebar.getByText('Databases', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('navigation', { name: 'Databases' }).getByRole('link')
  ).toHaveCount(0)
  await expect(
    sidebar.getByRole('button', { name: 'Add database' })
  ).toBeVisible()
  await expect(sidebar.getByText(/targets/i)).toHaveCount(0)
})

/**
 * D-17 - at the stated 1024x720 minimum the content column stays wide enough
 * to read and nothing scrolls sideways.
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
 * A-25 - the sidebar carries the way on to adding a database, so a
 * single-database install has a next step beside its one database.
 */
test('A-25 - the sidebar offers a way to add a database', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/')

  await page
    .locator('#app-sidebar')
    .getByRole('button', { name: 'Add database' })
    .click()
  await expect(
    page.getByRole('dialog', { name: 'Add a database' })
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
