/**
 * Keyboard and screen-reader regressions: what a caret lands on and how it is
 * marked, what a name announces, how the outline reads, whether the text meets
 * AA, and how the dialogs behave once they are open.
 */
import type { Page } from '@playwright/test'
import {
  configureTestTarget,
  expect,
  mainContent,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  contrastRows,
  DESKTOP,
  focusWalk,
  ROUTES,
  settle,
  shot,
} from './_helpers'

/** Elements whose accessible name is its own visible label, said twice. */
const DOUBLED_NAMES = `(() => {
  const norm = (s) => (s ?? '').replace(/\\s+/g, ' ').trim();
  const doubled = [];
  const controls = [
    ...document.querySelectorAll('a[href],button,[role="tab"],[role="menuitem"]'),
  ].filter((el) => el.offsetParent !== null);
  for (const el of controls) {
    const name = norm(el.getAttribute('aria-label') ?? el.textContent);
    if (!name) continue;
    const half = name.length / 2;
    if (name.length % 2 === 0 && name.slice(0, half) === name.slice(half)) {
      doubled.push(name);
    } else if (/^(.+) \\1$/.test(name)) {
      doubled.push(name);
    }
  }
  return [...new Set(doubled)];
})()`

async function setup(page: Page) {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.setViewportSize(DESKTOP)
}

/**
 * MG-04, MG-05, F-43 - every keyboard stop shows a focus indicator. The walk
 * settles at each stop because the sidebar ring animates in over ~450ms, so an
 * instant sample reads the start of the transition rather than the ring.
 */
test('a11y: every keyboard stop is marked', async ({ page }) => {
  await setup(page)
  const unringed: string[] = []
  for (const path of ['/', '/guards', '/audit']) {
    await page.goto(path)
    await settle(page, 1200)
    const stops = await focusWalk(page, 14)
    expect(stops.length, `${path} has keyboard stops`).toBeGreaterThan(4)
    unringed.push(
      ...stops.filter((s) => !s.ring).map((s) => `${path} ${s.tag}:${s.label}`)
    )
  }
  expect(unringed).toEqual([])
  await shot(page, 'a11y/focus-walk-last-stop')
})

/** MG-06 - a control's label is announced once, not once per icon and copy. */
test('a11y: accessible names are said once', async ({ page }) => {
  await setup(page)
  const doubled: string[] = []
  for (const path of ['/', '/queries', '/audit', '/guards']) {
    await page.goto(path)
    await settle(page, 1200)
    const names = (await page.evaluate(DOUBLED_NAMES)) as string[]
    doubled.push(...names.map((name) => `${path}: ${name}`))
  }
  expect(doubled).toEqual([])
})

/** F-29 - each route names itself with exactly one level-1 heading. */
test('a11y: every route has one page heading', async ({ page }) => {
  await setup(page)
  const withoutOne: string[] = []
  for (const route of ROUTES.filter(
    (r) => !['account-login', 'results', 'configure-connections'].includes(r.id)
  )) {
    await page.goto(route.path)
    await settle(page, 1200)
    const count = await mainContent(page).locator('h1').count()
    if (count !== 1) withoutOne.push(`${route.id}: ${count}`)
  }
  expect(withoutOne).toEqual([])
})

/** F-47 - every colour pair carrying text meets the AA ratio for its size. */
test('a11y: text meets AA on the theme the app ships with', async ({
  page,
}) => {
  await setup(page)
  const failing: string[] = []
  for (const path of ['/', '/queries', '/cache', '/guards']) {
    await page.goto(path)
    await settle(page, 1200)
    const rows = await contrastRows(page, path)
    failing.push(
      ...rows
        .filter((row) => !row.pass)
        .map((row) => `${path} ${row.ratio}:1 "${row.sample}"`)
    )
  }
  expect(failing).toEqual([])
})

/**
 * F-44, F-45 - the sign-in dialog opens on the safe field rather than on a
 * one-Enter trip into a third-party redirect, and Escape leaves it.
 */
test('a11y: the sign-in dialog opens safely and closes on Escape', async ({
  page,
}) => {
  await setup(page)
  await page.goto('/account-login')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(page.getByLabel('Email address')).toBeFocused()
  await expect(
    dialog.getByRole('button', { name: 'Continue with Google' })
  ).not.toBeFocused()

  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(new URL(page.url()).pathname).toBe('/')
})

/**
 * F-46 - a target row is one checkbox: a labelled outer control never wraps a
 * second, unlabelled one.
 */
test('a11y: a selectable target row is a single checkbox', async ({ page }) => {
  await setup(page)
  await page.goto('/audit')
  const row = page.getByRole('checkbox', { name: 'Select e2e-guard' })
  await expect(row).toHaveCount(1)
  const nested = await page.evaluate(() => {
    const boxes = [
      ...document.querySelectorAll('[role="checkbox"],input[type="checkbox"]'),
    ]
    return boxes
      .filter((box) =>
        boxes.some((other) => other !== box && other.contains(box))
      )
      .map((box) => box.getAttribute('aria-label') ?? box.tagName.toLowerCase())
  })
  expect(nested).toEqual([])
})

/** F-32 - the active nav item says "you are here" beyond colour. */
test('a11y: the active nav item carries aria-current', async ({ page }) => {
  await setup(page)
  for (const [path, label] of [
    ['/queries', 'Queries'],
    ['/ask', 'Ask'],
    ['/audit', 'Health check'],
  ] as const) {
    await page.goto(path)
    const current = page.locator('nav [aria-current="page"]')
    await expect(current).toHaveCount(1)
    await expect(current).toHaveText(label)
  }
})
