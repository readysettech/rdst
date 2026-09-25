// Scrolling the Database page with the pointer parked over the query list. Rows slide
// under a still pointer, and the hover they'd pick up redraws the charts; none of that
// may move the page. Scrolling goes one way only, and only as far as the wheel says.
import type { Page } from '@playwright/test'
import {
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

const VIEWPORT = 'main [data-radix-scroll-area-viewport]'

test.use({ viewport: { width: 1280, height: 640 } })
// Seeding history and sampling both scroll directions share the test deadline.
test.describe.configure({ timeout: 90_000 })

test.beforeEach(async ({ page }) => {
  await configureTestTarget(page, { hasPassword: true })
  const seeded = await page.request.post('/api/testing/monitor/seed')
  expect(seeded.ok()).toBe(true)
  await mockPrerequisites(page, 'e2e-guard')
})

const scrollTop = (page: Page) =>
  page.evaluate(
    (selector) => document.querySelector(selector)?.scrollTop ?? -1,
    VIEWPORT
  )

const raisedRow = (page: Page) =>
  page.evaluate(() => document.querySelector('[data-raised]')?.id ?? null)

/** Samples the page's scroll position every frame until stopped. */
async function startSampling(page: Page) {
  await page.evaluate((selector) => {
    const scroller = document.querySelector(selector)
    const state = window as Window & { scrollSamples?: number[] }
    state.scrollSamples = []
    const tick = () => {
      if (!state.scrollSamples) return
      state.scrollSamples.push(scroller?.scrollTop ?? -1)
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }, VIEWPORT)
}

async function stopSampling(page: Page) {
  return page.evaluate(() => {
    const state = window as Window & { scrollSamples?: number[] }
    const samples = state.scrollSamples ?? []
    state.scrollSamples = undefined
    return samples
  })
}

/** Direction changes in a series of scroll positions. */
function reversals(samples: number[]) {
  let count = 0
  let last = 0
  for (let index = 1; index < samples.length; index++) {
    const step = samples[index] - samples[index - 1]
    if (!step) continue
    if (last && Math.sign(step) !== Math.sign(last)) count++
    last = step
  }
  return count
}

async function openTallPage(page: Page) {
  await page.goto('/database?target=e2e-guard&range=1h')
  const row = page.locator('[id="query-orders-e2e"]')
  await expect(row).toBeVisible()
  await expect(page.getByTestId('load-chart')).toBeVisible()
  // An open row makes the page tall enough to scroll the hero away.
  await row.getByRole('button', { name: 'Details' }).click()
  await expect(page.getByTestId('query-detail')).toBeVisible()
  // Opening the row brings it into view; let that settle before starting at the top.
  await page.waitForTimeout(1200)
  await page.evaluate((selector) => {
    const scroller = document.querySelector(selector)
    if (scroller) scroller.scrollTop = 0
  }, VIEWPORT)
  await expect.poll(() => scrollTop(page)).toBe(0)
}

test('scrolling with the pointer over the rows moves the page one way, smoothly', async ({
  page,
}) => {
  await openTallPage(page)
  const max = await page.evaluate((selector) => {
    const scroller = document.querySelector(selector)
    return scroller ? scroller.scrollHeight - scroller.clientHeight : 0
  }, VIEWPORT)
  expect(max).toBeGreaterThan(200)

  const list = await page.getByTestId('query-section').boundingBox()
  if (!list) throw new Error('query section has no box')
  await page.mouse.move(list.x + list.width / 2, 420)
  const raisedBefore = await raisedRow(page)

  for (const direction of [1, -1]) {
    await startSampling(page)
    for (let step = 0; step < 40; step++) {
      await page.mouse.wheel(0, direction * 12)
      await page.waitForTimeout(30)
    }
    await page.waitForTimeout(400)
    const samples = await stopSampling(page)
    expect(samples.length).toBeGreaterThan(10)
    expect(reversals(samples), samples.join(',')).toBe(0)
    // Once the wheel stops, the page stays where it is.
    const settled = await scrollTop(page)
    await page.waitForTimeout(300)
    expect(await scrollTop(page)).toBe(settled)
  }

  // The parked pointer never moved, so the rows sliding under it changed no hover.
  expect(await raisedRow(page)).toBe(raisedBefore)
})

test('hovering rows and gaps never scrolls the page, wherever it stands', async ({
  page,
}) => {
  await openTallPage(page)
  // Charts redraw on hover, so none of them may anchor the page's scroll position.
  const anchors = await page.evaluate(() =>
    [...document.querySelectorAll('.mk-chart')].map(
      (chart) => getComputedStyle(chart).overflowAnchor
    )
  )
  expect(anchors.length).toBeGreaterThan(0)
  expect(new Set(anchors)).toEqual(new Set(['none']))
  const section = await page.getByTestId('query-section').boundingBox()
  if (!section) throw new Error('query section has no box')
  const x = section.x + section.width / 2
  const max = await page.evaluate((selector) => {
    const scroller = document.querySelector(selector)
    return scroller ? scroller.scrollHeight - scroller.clientHeight : 0
  }, VIEWPORT)
  // Every position the hero passes through on its way out, where a redrawn chart is
  // the first thing on screen.
  for (let top = 0; top <= Math.min(max, 700); top += 12) {
    await page.evaluate(
      ([selector, value]) => {
        const scroller = document.querySelector(selector as string)
        if (scroller) scroller.scrollTop = value as number
      },
      [VIEWPORT, top]
    )
    const start = await scrollTop(page)
    await startSampling(page)
    for (const y of [200, 330, 460, 590, 120]) {
      await page.mouse.move(x, y)
      await page.mouse.move(x + 2, y + 1)
    }
    await page.waitForTimeout(60)
    const samples = await stopSampling(page)
    expect(
      samples.every((value) => value === start),
      `scrollTop ${start} moved on hover: ${samples.join(',')}`
    ).toBe(true)
  }
})
