/**
 * Reduced motion on the Ask and Schema surfaces.
 *
 * The whole suite runs with `contextOptions: { reducedMotion: 'reduce' }`, so
 * `prefers-reduced-motion: reduce` is true in every test here.
 */
import type { Page } from '@playwright/test'
import {
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP } from './_helpers'

test.use({ viewport: DESKTOP })

const ASK_PLACEHOLDER =
  'For example: Which customers placed the most orders this month?'

const ANSWER_EVENTS = [
  {
    type: 'result',
    success: true,
    sql: 'SELECT customer, revenue FROM orders',
    columns: ['customer', 'revenue'],
    rows: Array.from({ length: 30 }, (_, index) => [
      `Customer ${index}`,
      index * 10,
    ]),
    row_count: 30,
    execution_time_ms: 4.1,
    llm_calls: 1,
    total_tokens: 40,
  },
]

const smallLayer = {
  tables: {
    customers: {
      description: 'Customer accounts',
      columns: { id: { type: 'bigint', description: 'Id' } },
      relationships: [],
    },
  },
  terminology: {},
  metrics: {},
}

/**
 * An entrance in flight writes its current values inline. Content that arrives
 * displaced reports the element and the offset it still carries, so a failure
 * names the animation that played.
 */
async function displaced(page: Page) {
  return page.evaluate(() => {
    const main = document.querySelector('#main-content') ?? document.body
    const found: { tag: string; text: string; transform: string }[] = []
    for (const element of [...main.querySelectorAll('[style]')]) {
      if (element.closest('[aria-hidden="true"]')) continue
      const transform = (element as HTMLElement).style.transform
      if (!transform) continue
      const offsets = [...transform.matchAll(/translate[XY3d]*\(([^)]*)\)/g)]
        .flatMap((match) => match[1].split(','))
        .map((part) => Number.parseFloat(part))
        .filter((value) => Number.isFinite(value))
      if (!offsets.some((value) => value !== 0)) continue
      found.push({
        tag: element.tagName.toLowerCase(),
        text: (element.textContent || '')
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 40),
        transform,
      })
    }
    return found
  })
}

/** Time, in ms, until nothing under `#main-content` is still fading in. */
async function fadeSettleMs(page: Page, budgetMs = 3000) {
  const start = Date.now()
  while (Date.now() - start < budgetMs) {
    const fading = await page.evaluate(
      () =>
        [...document.querySelectorAll('#main-content [style]')].filter(
          (element) => {
            const opacity = (element as HTMLElement).style.opacity
            return opacity !== '' && Number(opacity) < 0.99
          }
        ).length
    )
    if (fading === 0) return Date.now() - start
    await page.waitForTimeout(50)
  }
  return -1
}

/**
 * MG-13 (was C-13) - Ask entrances land in place under
 * prefers-reduced-motion.
 */
test('the Ask page and its answer arrive in place under reduced motion', async ({
  page,
}) => {
  setBackendFixtures({ ask: [{ events: ANSWER_EVENTS }] })
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)

  await page.goto('/ask')
  expect(
    await page.evaluate(
      () => matchMedia('(prefers-reduced-motion: reduce)').matches
    )
  ).toBe(true)

  await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()
  expect(await displaced(page)).toEqual([])
  const idleSettle = await fadeSettleMs(page)
  expect(idleSettle).toBeGreaterThanOrEqual(0)
  expect(idleSettle).toBeLessThan(1000)

  await page.getByPlaceholder(ASK_PLACEHOLDER).fill('Top customers')
  await page.getByRole('button', { name: 'Get answer' }).click()
  await expect(page.getByText('Answer', { exact: true })).toBeVisible()

  // Thirty result rows: none of them slides in, and none of them waits its
  // turn behind a per-row delay.
  expect(await displaced(page)).toEqual([])
  const answerSettle = await fadeSettleMs(page)
  expect(answerSettle).toBeGreaterThanOrEqual(0)
  expect(answerSettle).toBeLessThan(1000)
})

/** MG-13 (was C-13) - the Schema page enters without displacing its content. */
test('the Schema page arrives in place under reduced motion', async ({
  page,
}) => {
  setBackendFixtures({
    semantic_init: [
      {
        layer: smallLayer,
        events: [
          {
            type: 'complete',
            operation: 'init',
            success: true,
            init_result: {
              success: true,
              target: 'e2e-guard',
              tables: 1,
              columns: 1,
              relationships: 0,
              enum_columns: [],
              path: '/tmp/e2e-guard-schema.yaml',
              error: null,
            },
          },
        ],
      },
    ],
  })
  await configureTestTarget(page, { hasPassword: true })
  await page.request.delete('/api/semantic-layer?target=e2e-guard')

  await page.goto('/schema')
  await expect(page.getByRole('heading', { name: 'Schema' })).toBeVisible()
  expect(await displaced(page)).toEqual([])
  const settle = await fadeSettleMs(page)
  expect(settle).toBeGreaterThanOrEqual(0)
  expect(settle).toBeLessThan(1000)
})
