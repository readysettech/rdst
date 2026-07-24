import { configureTestTarget, expect, test } from './fixtures'

test('explains why a queued benchmark has not started', async ({ page }) => {
  await configureTestTarget(page, { hasPassword: true })

  await page.addInitScript(() => {
    localStorage.setItem(
      'rdst_background_runs',
      JSON.stringify([
        {
          runId: 'load-test-e2e-queued',
          kind: 'load_test',
          target: 'e2e-guard',
          stage: 'queued',
          status: 'running',
          message: 'Waiting for an isolated measurement slot...',
          lastSeq: 0,
          current: null,
          total: null,
          hasWarnings: false,
          loadRequest: {
            queries: ['query-e2e-queued'],
            target: 'e2e-guard',
            mode: 'interval',
            interval_ms: 100,
            concurrency: 1,
            duration_seconds: 30,
          },
        },
      ])
    )
  })

  await page.route(/\/api\/runs\/load-test-e2e-queued$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'load-test-e2e-queued',
        kind: 'load_test',
        target: 'e2e-guard',
        status: 'running',
        last_seq: 0,
        metadata: {
          request: {
            queries: ['query-e2e-queued'],
            target: 'e2e-guard',
            mode: 'interval',
            interval_ms: 100,
            concurrency: 1,
            duration_seconds: 30,
          },
        },
      }),
    })
  })
  await page.route(
    /\/api\/runs\/load-test-e2e-queued\/events/,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'event: progress\n' +
          'data: {"seq":1,"stage":"queued","message":"Waiting for an isolated measurement slot..."}\n\n',
      })
    }
  )

  await page.goto('/benchmark')

  await expect(page.getByText('Waiting', { exact: true }).first()).toBeVisible()
  await expect(
    page.getByRole('heading', {
      name: 'Waiting for another performance test',
    })
  ).toBeVisible()
  await expect(
    page.getByText(/runs performance measurements one at a time/i)
  ).toBeVisible()
  await expect(
    page.getByText(/30s test timer begins only after/i)
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: /Cancel queued test/ })
  ).toBeVisible()
  await expect(page.getByText('Running...', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Total Executions')).toHaveCount(0)
})
