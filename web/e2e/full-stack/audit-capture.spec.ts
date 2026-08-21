import { clearTargets, expect, test } from '../fixtures'

/**
 * Runs a real capture against the live Postgres and holds the capture
 * window to its schedule. Guards the incident class where slow collector
 * queries silently stretched a 60-second window to minutes: the complete
 * event must record an honest duration and per-phase timings.
 *
 * The run starts through the production API rather than the Run button:
 * the button is AI-gated (no key in this environment), and its request
 * body is already asserted by the mocked browser suite; the progress
 * card rendering is asserted by the RunProgress component tests.
 */
test('capture window closes on schedule and reports phase timings', async ({
  page,
}) => {
  test.setTimeout(240_000)
  const host = process.env.RDST_E2E_DB_HOST ?? '127.0.0.1'
  const port = Number(process.env.RDST_E2E_DB_PORT ?? '15432')

  await clearTargets(page.request)

  await page.goto('/onboarding')
  await expect(
    page.getByRole('heading', { name: 'Start with Readyset' })
  ).toBeVisible()
  await page.locator('[name="name"]').fill('postgres-e2e')
  await page.locator('[name="host"]').fill(host)
  await page.locator('[name="port"]').fill(String(port))
  await page.locator('[name="database"]').fill('rdst_test')
  await page.locator('[name="user"]').fill('rdst_test')
  await page
    .locator('[name="password"]')
    .fill(process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password')
  await page.getByRole('button', { name: 'Test & connect' }).click()
  await expect(page).toHaveURL('/')

  const started = await page.request.post('/api/audit/capture', {
    data: {
      target: 'postgres-e2e',
      duration: 12,
      analysis: false,
      readyset: false,
    },
  })
  expect(started.ok()).toBe(true)
  const { run_id: runId } = (await started.json()) as { run_id: string }

  // The saved-events replay carries the complete event with phase timings.
  let complete: {
    summary: {
      // The saved-run id ("audit_<target>_<ts>"), distinct from the
      // registry run id the POST returned.
      run_id: string
      duration_seconds: number
      phase_timings_ms: Record<string, number>
      total_duration_ms: number
    }
  } | null = null
  await expect(async () => {
    const events = await page.request.get(
      `/api/runs/${runId}/events?after_seq=0`
    )
    expect(events.ok()).toBe(true)
    const payloads = (await events.text())
      .split('\n')
      .filter((line) => line.startsWith('data: '))
      .map((line) => JSON.parse(line.slice(6)) as Record<string, unknown>)
    const found = payloads.find(
      (payload) =>
        typeof payload.summary === 'object' &&
        payload.summary !== null &&
        'phase_timings_ms' in (payload.summary as Record<string, unknown>)
    )
    expect(found).toBeTruthy()
    complete = found as typeof complete
  }).toPass({ timeout: 150_000, intervals: [3_000] })

  const summary = complete!.summary
  // Report the observed capture duration, not merely the requested value.
  // A loaded event loop can resume the two-second sampling sleep late, but
  // the window must remain tightly bounded rather than regressing to minutes.
  expect(summary.duration_seconds).toBeGreaterThanOrEqual(12)
  expect(summary.duration_seconds).toBeLessThan(20)
  const timings = summary.phase_timings_ms
  expect(Object.keys(timings)).toEqual(
    expect.arrayContaining(['metrics_audit', 'connect', 'capture'])
  )
  // The window must close on schedule - the incident stretched 60s to 442s.
  expect(timings.capture).toBeGreaterThanOrEqual(11_000)
  expect(timings.capture).toBeLessThan(30_000)
  expect(summary.total_duration_ms).toBeGreaterThan(timings.capture)

  // The finished run lands in the saved-run history the Reports tab reads.
  const runs = await page.request.get('/api/audit/runs?target=postgres-e2e')
  expect(runs.ok()).toBe(true)
  const { runs: savedRuns } = (await runs.json()) as {
    runs: Array<{ run_id: string; duration_seconds: number }>
  }
  const saved = savedRuns.find((run) => run.run_id === summary.run_id)
  expect(saved).toBeTruthy()
  expect(saved!.duration_seconds).toBe(summary.duration_seconds)
})
