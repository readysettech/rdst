import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  clearTargets,
  expect,
  mockAiKeyReady,
  mockPrerequisites,
  setBackendFixtures,
  test,
} from './fixtures'

/** API requests per endpoint while `run` waits, on every page given. */
async function countRequests(pages: Page[], run: () => Promise<void>) {
  const counts = new Map<string, number>()
  const listener = (request: { url: () => string; method: () => string }) => {
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) return
    const key = `${request.method()} ${url.pathname}`
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  for (const page of pages) page.on('request', listener)
  await run()
  for (const page of pages) page.off('request', listener)
  return counts
}

// The owner's fresh install sent about 70 requests a second from the Database
// page after onboarding: a second tab, still holding the target list from before
// the database was added, kept resetting the selection the onboarding tab made,
// and every flip remounted the page. A settled Database page polls a few
// endpoints a few times a minute.
test('a Database page reached through onboarding stays calm', async ({
  page,
  browserErrors,
}) => {
  test.setTimeout(120_000)
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await clearTargets(page.request)
  setBackendFixtures({
    init_validate: [
      {
        events: [
          {
            type: 'complete',
            success: true,
            validation: {
              target_results: [
                { name: 'calm-db', success: true, version: 'PostgreSQL 15' },
              ],
              llm_result: { success: false, error: 'Not needed here' },
            },
          },
        ],
      },
    ],
  })
  await page.route('**/api/configure/targets/calm-db/test', (route) =>
    route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: [
        'event: connection_test',
        `data: ${JSON.stringify({
          target_name: 'calm-db',
          status: 'success',
          server_version: 'PostgreSQL 15',
          privileges: { writable: false },
        })}`,
        '',
        'event: success',
        'data: {"message":"Connection test complete"}',
        '',
      ].join('\n'),
    })
  )
  await mockPrerequisites(page, 'calm-db')

  // The tab the user started in stays open, with the empty target list it read
  // before the database existed.
  const other = await page.context().newPage()
  await mockAiKeyReady(other)
  await mockPrerequisites(other, 'calm-db')
  await other.goto('/')
  await expect(other.locator('#main-content')).toBeVisible()

  await page.goto('/onboarding')
  await page.locator('[name="name"]').fill('calm-db')
  await page.locator('[name="host"]').fill('127.0.0.1')
  await page.locator('[name="database"]').fill('app')
  await page.locator('[name="user"]').fill('calm')
  await page.locator('[name="password"]').fill('calm-password')
  await page.getByRole('button', { name: 'Test & connect' }).click()
  // Connecting opens the turn-on dialog on the new database's page.
  const dialog = page.getByRole('dialog', {
    name: 'Turn on monitoring for calm-db',
  })
  await expect(dialog).toBeVisible({ timeout: 15_000 })
  await expect(dialog.getByTestId('prereq-checker')).toHaveAttribute(
    'data-phase',
    'ready',
    { timeout: 15_000 }
  )
  await dialog.getByRole('button', { name: 'Not now' }).click()
  await expect(
    page.getByRole('heading', { level: 1, name: 'calm-db' })
  ).toBeVisible()

  const seconds = 15
  const counts = await countRequests([page, other], () =>
    page.waitForTimeout(seconds * 1000)
  )
  const total = [...counts.values()].reduce((sum, n) => sum + n, 0)
  const perMinute = (total * 60) / seconds
  const busiest = [...counts.entries()].sort((a, b) => b[1] - a[1])
  expect(
    perMinute,
    `API requests in ${seconds} s: ${JSON.stringify(busiest)}`
  ).toBeLessThan(150)
  // A database without a report yet asks for it once, not in a loop.
  expect(
    counts.get('GET /api/monitor/targets/calm-db/report/latest') ?? 0
  ).toBeLessThanOrEqual(2)
  // Both tabs follow the database the onboarding tab chose.
  await expect(
    page.getByRole('heading', { level: 1, name: 'calm-db' })
  ).toBeVisible()
  expect(
    await other.evaluate(() => localStorage.getItem('rdst_selected_target'))
  ).toBe('calm-db')
  await other.close()
})
