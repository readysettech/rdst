/**
 * The layout floor: the desktop app's stated minimum (1024x720) and phone
 * width (390x844). Nothing is painted over, cut off, or pushed past the
 * viewport, and the action rows wrap instead of overflowing.
 */
import type { Locator, Page } from '@playwright/test'
import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  type Box,
  boxesOf,
  clipped,
  layoutProblems,
  NARROW,
  overlaps,
  PHONE,
  settle,
  shot,
  widestOffenders,
} from './_helpers'

const PII_GUARD = {
  name: 'pii-guard',
  description: 'Masks PII and blocks unbounded reads',
  intent: 'Protect customer data in analytical queries',
  derived: false,
  masking: { '*.email': 'email' },
  restrictions: {
    denied_columns: ['users.password'],
    allowed_tables: ['users', 'orders'],
    required_filters: { orders: ['tenant_id'] },
  },
  guards: {
    require_where: true,
    require_limit: false,
    no_select_star: true,
    max_tables: 2,
    cost_limit: null,
    max_estimated_rows: null,
  },
  limits: { max_rows: 1000, timeout_seconds: 30 },
}

async function setup(page: Page) {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
}

async function documentOverflow(page: Page) {
  return page.evaluate(() => {
    const de = document.documentElement
    return de.scrollWidth - de.clientWidth
  })
}

/**
 * F-48 - the Ask header keeps its title, database filter and the line naming
 * the database it asks on separate boxes at phone width instead of painting
 * them over each other.
 */
test('viewports: the Ask header does not overprint at 390', async ({
  page,
}) => {
  await setup(page)
  await page.setViewportSize(PHONE)
  await page.goto('/ask')
  const title = page.getByRole('heading', { name: 'Ask', level: 1 })
  await title.waitFor()
  await settle(page, 800)

  const header = page.locator('#main-content header').first()
  const filter = header.getByRole('button', { name: /^Database: / })
  const asks = header.getByText(/^Asks /)
  await expect(filter).toBeVisible()
  expect(await clipped(title), 'title clipped').toBe(false)
  expect(await clipped(filter), 'database filter clipped').toBe(false)

  const box = async (locator: Locator): Promise<Box> => {
    const rect = await locator.boundingBox()
    expect(rect).not.toBeNull()
    return { text: '', x: rect!.x, y: rect!.y, w: rect!.width, h: rect!.height }
  }
  const titleBox = await box(title)
  expect(
    overlaps(titleBox, await box(filter)),
    'database filter over the title'
  ).toBe(false)
  if (await asks.isVisible())
    expect(
      overlaps(await box(asks), await box(filter)),
      'database filter over the line naming the database'
    ).toBe(false)
  expect(await widestOffenders(page)).toEqual([])
  expect(await documentOverflow(page)).toBe(0)
})

/**
 * F-09 - the guard row's rule tags sit beside the guard name at both supported
 * widths instead of painting over it.
 */
test('viewports: the guard row keeps its name readable', async ({ page }) => {
  await setup(page)
  const existing = (await (await page.request.get('/api/guards')).json()) as {
    guards: { name: string }[]
  }
  for (const guard of existing.guards) {
    await page.request.delete(`/api/guards/${guard.name}`)
  }
  expect(
    (await page.request.post('/api/guards', { data: PII_GUARD })).ok()
  ).toBe(true)

  for (const [label, size] of [
    ['1024', NARROW],
    ['390', PHONE],
  ] as const) {
    await page.setViewportSize(size)
    await page.goto('/guards')
    await expect(
      page.getByRole('button').filter({ hasText: 'pii-guard' })
    ).toBeVisible()
    await settle(page, 900)
    const problems = await layoutProblems(page)
    expect(
      problems.filter((p) => p.kind === 'occluded'),
      `guards occluded at ${label}`
    ).toEqual([])
    expect(
      problems.filter((p) => p.kind === 'offscreen'),
      `guards offscreen at ${label}`
    ).toEqual([])
  }
  await shot(page, 'viewports/guards-390')
})

/**
 * Content wider than the viewport that reaches the viewport edge.
 *
 * The generic probe reports every over-wide box, which includes the decorative
 * layers a card deliberately paints larger than itself: on Home the demo
 * card's animated backdrop draws gradient blobs of around 1100px inside a
 * `pointer-events-none overflow-hidden` frame that is itself 656px wide. They
 * are clipped well inside the viewport, carry no text and take no pointer, so
 * an element whose clipping ancestor fits the viewport is not lost content.
 */
async function lostContent(page: Page) {
  return page.evaluate(() => {
    const main = document.querySelector('#main-content') ?? document.body
    const limit = document.documentElement.clientWidth
    const clippedInside = (el: Element) => {
      let parent = el.parentElement
      while (parent) {
        const rect = parent.getBoundingClientRect()
        if (
          getComputedStyle(parent).overflowX !== 'visible' &&
          rect.left >= -2 &&
          rect.right <= limit + 2
        ) {
          return true
        }
        parent = parent.parentElement
      }
      return false
    }
    return [...main.querySelectorAll('*')]
      .filter((el) => (el as HTMLElement).offsetWidth > limit + 2)
      .filter((el) => !clippedInside(el))
      .slice(0, 8)
      .map((el) => ({
        tag: el.tagName.toLowerCase(),
        width: (el as HTMLElement).offsetWidth,
      }))
  })
}

/**
 * The desktop floor never scrolls sideways, and nothing inside the content
 * column is drawn wider than the viewport that has to hold it.
 */
test('viewports: no route overflows horizontally at 1024', async ({ page }) => {
  await setup(page)
  await page.setViewportSize(NARROW)
  const offenders: string[] = []
  for (const path of [
    '/',
    '/database',
    '/ask',
    '/cache',
    '/configure',
    '/guards',
  ]) {
    await page.goto(path)
    await settle(page, 1200)
    const overflow = await documentOverflow(page)
    if (overflow !== 0) offenders.push(`${path}: document ${overflow}px`)
    offenders.push(
      ...(await lostContent(page)).map(
        (wide) => `${path}: ${wide.tag} ${wide.width}px`
      )
    )
  }
  expect(offenders).toEqual([])
})

/**
 * MG-03 - the setup guide lives in the chrome, so it never covers a control in
 * the content column at any supported width.
 */
test('viewports: the setup guide never covers a control', async ({ page }) => {
  await setup(page)
  const covered: string[] = []
  for (const [label, size] of [
    ['1024', NARROW],
    ['390', PHONE],
  ] as const) {
    await page.setViewportSize(size)
    for (const path of ['/', '/cache', '/guards']) {
      await page.goto(path)
      await settle(page, 1100)
      const guide = await boxesOf(page, '[aria-label="Setup guide"]')
      if (guide.length === 0) continue
      const controls = await boxesOf(
        page,
        '#main-content button, #main-content a, #main-content input, #main-content textarea, #main-content select'
      )
      covered.push(
        ...controls
          .filter((control) => guide.some((box) => overlaps(box, control)))
          .map((control) => `${label} ${path}: ${control.text}`)
      )
    }
  }
  expect(covered).toEqual([])
})
