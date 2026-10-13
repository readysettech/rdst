import { clearTargets, expect, test } from '../../fixtures'
import { signInWithEmail } from '../sign-in'
import { base, engine, eventually, hook, ok, openDatabase, target } from './cim'

// Shapes that don't run in the steady load: the burst-only aggregate, the
// new heavy query and the history-only stopped query.
const OFF_IN_STEADY = new Set(['burst_only', 'new_heavy', 'stopped'])

test('ci-shop is monitored with two days of history', async ({
  page,
}, testInfo) => {
  test.setTimeout(9 * 60_000)
  expect((await page.request.post('/api/account/logout')).ok()).toBe(true)
  await clearTargets(page.request)
  const signedIn = await signInWithEmail(
    page,
    `cim-${engine === 'mysql' ? 'my' : 'pg'}`
  )
  const request = signedIn.request
  const database = await hook.setup(request)

  await ok(
    request.post('/api/configure/targets', {
      data: {
        name: target,
        target: {
          engine,
          host: database.host,
          port: database.port,
          database: database.database,
          user: database.user,
          password_env: database.password_env,
        },
      },
    })
  )
  await ok(request.put('/api/configure/default', { data: { name: target } }))
  await ok(request.post('/api/init/complete'))

  await openDatabase(signedIn)
  await signedIn
    .getByRole('button', { name: 'Turn on monitoring' })
    .first()
    .click()
  const dialog = signedIn.getByRole('dialog')
  await expect(
    dialog.getByRole('heading', { name: `Monitor ${target}` })
  ).toBeVisible()
  // Deep analysis runs EXPLAIN ANALYZE by default, under its warning; the switches
  // sit behind Advanced.
  await expect(dialog.getByTestId('explain-analyze-warning')).toBeVisible()
  await dialog.getByRole('button', { name: 'Advanced' }).click()
  await expect(
    dialog.getByRole('switch', { name: 'Analyze automatically' })
  ).toBeChecked()
  await expect(
    dialog.getByRole('switch', { name: 'Measure real timings' })
  ).toBeChecked()
  const turnOn = dialog.getByRole('button', { name: 'Turn on monitoring' })
  await expect(turnOn).toBeEnabled({ timeout: 30_000 })
  await turnOn.click()
  await expect(signedIn.getByRole('tab', { name: 'Report' })).toBeVisible()
  // The bar says the database is being monitored beside its radar.
  await expect(signedIn.locator('[data-page-bar]')).toContainText(
    'Actively monitoring'
  )
  // Attention checks run when a scenario asks for one; a real nightly run
  // would race the one the history builds for yesterday. The top 10 get their
  // first deep analysis at turn-on; no newcomers are analyzed after that.
  // Pushes must not depend on the hour the suite runs: equal quiet hours mean
  // no quiet window.
  const settings = await ok<{
    enabled: boolean
    deep_analysis: boolean
    ai_monitoring: string
  }>(
    request.put(`${base}/settings`, {
      data: {
        attention_every_min: 60,
        nightly_report: false,
        push_daily_cap: 20,
        newcomer_daily_cap: 0,
        quiet_hours: { from_hour: 0, to_hour: 0 },
      },
    })
  )
  expect(settings).toMatchObject({
    enabled: true,
    deep_analysis: true,
    ai_monitoring: 'on',
  })

  // Every steady shape has been read at least twice, so it has measured calls.
  const steady = await eventually(
    testInfo,
    'steady-shapes',
    () => hook.shapes(request),
    (shapes) =>
      shapes
        .filter((shape) => !OFF_IN_STEADY.has(shape.role))
        .every((shape) => shape.observed && shape.calls > 0),
    {
      timeout: 120_000,
      diagnose: async () => ({
        missing: (await hook.shapes(request))
          .filter((shape) => !OFF_IN_STEADY.has(shape.role))
          .filter((shape) => !(shape.observed && shape.calls > 0))
          .map((shape) => shape.name),
        stats: await hook.stats(request),
      }),
    }
  )
  // Nothing runs the burst-only, new heavy and stopped shapes yet.
  expect(
    steady
      .filter((shape) => OFF_IN_STEADY.has(shape.role) && shape.calls > 0)
      .map((shape) => shape.name)
  ).toEqual([])

  const history = await hook.history(request)
  console.log(
    `ci-shop ${engine}: history ${history.minute_rows} minute rows, replays ` +
      `${JSON.stringify(history.replays)}, ${history.elapsed_s}s`
  )
  expect(history.minute_rows).toBeGreaterThan(100_000)
  // Yesterday's nightly report is finished, and its Deep Analyze runs wait at the front of
  // the queue.
  expect(history.nightly).toMatchObject({ state: 'done' })
  expect(history.nightly.report_jobs).toBeGreaterThan(0)
  // Both past bursts went through the real attention check, and the second
  // one repeats the first.
  expect(history.replays.map((replay) => replay.outcome)).toEqual([
    'filed',
    'filed',
  ])
  // Code settled the first burst's recovery, so the second repeats it.
  expect(history.burst_items).toEqual([
    expect.objectContaining({ occurrences: 2 }),
  ])
})
