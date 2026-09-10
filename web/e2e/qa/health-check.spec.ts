/**
 * Health Check (/audit): the preflight checklist, a capture run, the saved run
 * report, the email dialog, the fleet audit, and the Reports run history.
 *
 * Consolidated from the recorded audit flows. Every test asserts the behaviour
 * the fix delivers; a finding still open is declared `test.fixme` so the debt
 * stays visible without reddening the suite.
 */
import type { Page } from '@playwright/test'
import { acceptBrowserError, expect, mainContent, test } from '../fixtures'
import {
  DESKTOP,
  member,
  mockCaptureRun,
  mockFleetTargets,
  mockGatedCaptureStart,
  prepareAuditPage,
  runEvents,
  savedCaptureRun,
  selectTarget,
  shot,
} from './_helpers'

test.use({ viewport: DESKTOP })

/** A 404 on the run registry while a stream is being followed is expected. */
const NOT_FOUND =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

/** The missing-run scenario's own loader failure, as the router logs it. */
const RUN_LOAD_404 = 'Failed to fetch audit run: 404'

/**
 * Take the run loader's 404 for the missing-run scenario, and nothing else.
 * The router logs the Error itself, so the recorded text carries a bundle
 * stack and is matched by its message rather than accepted up front.
 */
function acceptRunLoadFailure(browserErrors: string[]) {
  const unrelated = browserErrors.filter(
    (error) => !error.includes(RUN_LOAD_404)
  )
  expect(
    unrelated,
    `Unexpected browser errors:\n${unrelated.join('\n')}`
  ).toEqual([])
  browserErrors.splice(0, browserErrors.length)
}

const CAPTURE_EVENTS = [
  { type: 'status', phase: 'config', message: 'Resolving target e2e-guard' },
  {
    type: 'connected',
    target_name: 'e2e-guard',
    db_engine: 'postgresql',
    source: 'pg_stat_statements',
  },
  { type: 'status', phase: 'capture', message: 'Watching live query traffic' },
  {
    type: 'capture_progress',
    elapsed_seconds: 12,
    total_seconds: 60,
    unique_queries: 14,
    total_executions: 3200,
    cache_hit_ratio: 0.94,
    active_connections: 8,
    tps: 260,
  },
]

const FLEET = [
  member('aurora-writer', { group: 'prod' }),
  member('aurora-reader', { group: 'prod' }),
  member('analytics', { group: 'analytics' }),
]

function targetResult(name: string) {
  return {
    target_name: name,
    engine: 'postgresql',
    instance_class: 'db.r6g.large',
    metrics: { active_connections: 12, max_connections: 100 },
    sizing: {
      verdict: 'oversized',
      current_monthly_cost_usd: 200,
      suggested_instance_class: 'db.r6g.medium',
      suggested_monthly_cost_usd: 40,
      potential_savings_usd: 160,
    },
    cache_opportunity: { score: 71, level: 'high' },
  }
}

async function mockFleetRun(
  page: Page,
  events: Array<Record<string, unknown>>,
  { runId = 'fleet_run_1', end = 'done' as string | null } = {}
) {
  await page.route('**/api/fleet/audit', (route) =>
    route.fulfill({ json: { run_id: runId, reused: false } })
  )
  await page.route(`**/api/runs/${runId}/events*`, (route) =>
    route
      .fulfill({
        headers: { 'content-type': 'text/event-stream' },
        body: runEvents(events, { end }),
      })
      .catch(() => undefined)
  )
}

test.describe('Run', () => {
  /** E-01 - a run whose event stream closes without a terminal frame settles. */
  test('a stream that closes silently settles into a retry', async ({
    page,
    browserErrors,
  }) => {
    acceptBrowserError(browserErrors, NOT_FOUND)
    await mockCaptureRun(page, CAPTURE_EVENTS, { end: null })
    await prepareAuditPage(page)
    await selectTarget(page)
    await page.getByRole('button', { name: 'Run health check' }).click()

    await expect(page.getByText('Capture failed')).toBeVisible({
      timeout: 15_000,
    })
    // The session is released: nothing still claims a live run and the
    // launcher can start another without a reload.
    await expect(
      mainContent(page).getByText(/Running on e2e-guard/)
    ).toHaveCount(0)
    await expect(
      page.getByRole('button', { name: 'Run health check' })
    ).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
    await shot(page, 'run-stream-lost-settled')
  })

  /**
   * E-07, E-08 - a run in flight leaves one place to watch it and one place to
   * stop it, and its progress track fills the card it sits in.
   */
  test('the capture card owns the progress and the only Cancel', async ({
    page,
  }) => {
    const release = await mockGatedCaptureStart(page)
    await prepareAuditPage(page)
    await selectTarget(page)
    await page.getByRole('button', { name: 'Run health check' }).click()
    await expect(page.getByText('Connecting to the database')).toBeVisible()

    await expect(
      page.getByText(/A health check is running\. Its progress and Cancel/)
    ).toBeVisible()
    await expect(
      mainContent(page).getByRole('button', { name: 'Run health check' })
    ).toHaveCount(0)
    await expect(
      mainContent(page).getByRole('button', { name: 'Cancel' })
    ).toHaveCount(1)

    const bar = mainContent(page).getByRole('progressbar')
    await expect(bar).toBeVisible()
    // A phase with no countable end keeps a track without claiming a number.
    expect(await bar.getAttribute('aria-valuenow')).toBeNull()
    const geometry = await bar.evaluate((el) => ({
      width: el.getBoundingClientRect().width,
      parentWidth: el.parentElement?.getBoundingClientRect().width ?? 0,
    }))
    expect(geometry.width).toBeGreaterThan(geometry.parentWidth * 0.9)
    await shot(page, 'run-capture-in-flight')

    release()
  })

  /**
   * E-14 - a connection failure drops the checklist that just reported the
   * database reachable, instead of contradicting itself on one screen.
   */
  test('a connection failure retires the checklist that passed', async ({
    page,
  }) => {
    await mockCaptureRun(page, [
      { type: 'status', phase: 'config', message: 'Resolving target' },
      {
        type: 'error',
        message: 'connection to server at "database.external.test" failed',
        phase: 'connect',
      },
    ])
    await prepareAuditPage(page)
    await selectTarget(page)
    await page.getByRole('button', { name: 'Check requirements' }).click()
    await expect(
      mainContent(page).getByText('Database reachable')
    ).toBeVisible()

    await page.getByRole('button', { name: 'Run health check' }).click()
    await expect(page.getByText('Capture failed')).toBeVisible({
      timeout: 15_000,
    })
    await expect(mainContent(page).getByText('Database reachable')).toHaveCount(
      0
    )
    await expect(page.getByTestId('preflight-checklist')).toHaveCount(0)
  })

  /** E-09 - a settled, empty inventory offers a way to connect a database. */
  test('with no targets the picker offers the way to add one', async ({
    page,
  }) => {
    await page.route('**/api/fleet/targets*', (route) =>
      route.fulfill({ json: { members: [], groups: [], count: 0 } })
    )
    await prepareAuditPage(page)

    await expect(page.getByText('No targets yet')).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Add a target' })
    ).toBeVisible()
    // No empty picker chrome: the disabled bulk toggle is gone with the list.
    await expect(
      mainContent(page).getByRole('button', { name: 'Clear all' })
    ).toHaveCount(0)
    await expect(
      mainContent(page).getByRole('button', { name: 'Select all' })
    ).toHaveCount(0)
    await expect(
      page.getByRole('button', { name: 'Run health check' })
    ).toBeDisabled()
  })
})

test.describe('Report', () => {
  const DEGRADED = 'capture_degraded_1'
  const SAVED = 'capture_saved_1'
  const EMAILED = 'capture_email_1'
  const SUMMARY = 'Connection headroom is fine; two queries dominate.'

  /**
   * E-52, E-53 - the skip notices travel with the saved run, and a report with
   * no analysis says so rather than reporting a clean bill of health.
   */
  test('a run with no analysis carries its skip notices and says so', async ({
    page,
  }) => {
    await page.route(`**/api/audit/runs/${DEGRADED}`, (route) =>
      route.fulfill({
        json: {
          ...savedCaptureRun(DEGRADED),
          health_analysis: null,
          analysis_error: 'The model provider returned 503',
          readyset_notice:
            'Readyset comparison skipped: docker is not available',
        },
      })
    )
    await page.goto(`/audit/runs/${DEGRADED}`)
    await expect(
      page.getByRole('heading', { name: 'e2e-guard' }).first()
    ).toBeVisible()

    await expect(page.getByText('Analysis skipped')).toBeVisible()
    await expect(
      page.getByText('The model provider returned 503').first()
    ).toBeVisible()
    await expect(page.getByText('Readyset benchmark skipped')).toBeVisible()
    await expect(
      page.getByText('Readyset comparison skipped: docker is not available')
    ).toBeVisible()

    await expect(page.getByText('AI analysis unavailable')).toBeVisible()
    await expect(
      page.getByText(
        'Findings were not generated for this run: The model provider returned 503'
      )
    ).toBeVisible()
    await expect(
      page.getByText('No critical findings were identified.')
    ).toHaveCount(0)
    await shot(page, 'report-analysis-unavailable')
  })

  /** E-04 - the report's section titles are real headings below the h1. */
  test('the report sections are headings, not paragraphs', async ({ page }) => {
    await page.route(`**/api/audit/runs/${SAVED}`, (route) =>
      route.fulfill({ json: savedCaptureRun(SAVED) })
    )
    await page.goto(`/audit/runs/${SAVED}`)

    await expect(
      page.getByRole('heading', { level: 1, name: 'e2e-guard' })
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { level: 2, name: 'Verdict' })
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { level: 2, name: 'Top findings' })
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { level: 3, name: 'Cache opportunity' })
    ).toBeVisible()
  })

  /** E-10 - the report states the summary sentence once. */
  test('the summary sentence is printed once', async ({ page }) => {
    await page.route(`**/api/audit/runs/${SAVED}`, (route) =>
      route.fulfill({ json: savedCaptureRun(SAVED) })
    )
    await page.goto(`/audit/runs/${SAVED}`)
    await expect(page.getByText(SUMMARY)).toBeVisible()

    const occurrences = await mainContent(page).evaluate(
      (main, sentence) =>
        (main as HTMLElement).innerText.split(sentence).length - 1,
      SUMMARY
    )
    expect(occurrences).toBe(1)
    // The verdict already carries the sentence, so no second card repeats it.
    await expect(
      page.getByRole('heading', { name: 'Executive summary' })
    ).toHaveCount(0)
  })

  /** E-25 - a run that is no longer stored explains itself and offers a way back. */
  test('a missing run explains itself and routes back to Reports', async ({
    page,
    browserErrors,
  }) => {
    acceptBrowserError(browserErrors, NOT_FOUND)
    await page.route('**/api/audit/runs/missing_run', (route) =>
      route.fulfill({ status: 404, json: { detail: 'Run not found' } })
    )
    await page.route('**/api/fleet/snapshots/missing_run', (route) =>
      route.fulfill({ status: 404, json: { detail: 'Snapshot not found' } })
    )
    await page.goto('/audit/runs/missing_run')

    await expect(page.getByText('This run is no longer saved')).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page.getByText('Your other saved reports are unaffected.')
    ).toBeVisible()
    // The raw client string stays behind the technical-details expander.
    await expect(
      page.getByText('Failed to fetch audit run: 404')
    ).not.toBeVisible()
    await expect(page.getByText('Technical details')).toBeVisible()

    await page.getByRole('button', { name: 'Back to Reports' }).click()
    await expect(page).toHaveURL(/\/audit\?tab=history#history/)

    acceptRunLoadFailure(browserErrors)
  })

  /**
   * E-11, E-27 - the email field carries a label, its error lands at the field,
   * and the dialog title is one visible heading.
   */
  test('the email dialog labels its field and reports errors at it', async ({
    page,
  }) => {
    await page.route(`**/api/audit/runs/${EMAILED}`, (route) =>
      route.fulfill({ json: savedCaptureRun(EMAILED) })
    )
    await page.route('**/api/settings/email', (route) =>
      route.fulfill({ json: { email: null, verified: false } })
    )
    await page.goto(`/audit/runs/${EMAILED}`)
    await page.getByRole('button', { name: 'Email me this report' }).click()

    const dialog = page.getByRole('dialog')
    await expect(
      dialog.getByRole('heading', { name: 'Email me this report' })
    ).toHaveCount(1)

    const field = page.getByLabel('Email address')
    await expect(field).toBeVisible()
    await field.fill('not-an-email')
    await page.getByRole('button', { name: 'Send report' }).click()
    await expect(dialog.getByText('Enter a valid email address.')).toBeVisible()
    // The error replaces the helper line rather than stacking above the field.
    await expect(dialog.getByText(/First time on this address/)).toHaveCount(0)

    const wiring = await dialog.evaluate((root) => {
      const input = root.querySelector('input') as HTMLElement
      const error = Array.from(root.querySelectorAll('*')).find(
        (node) =>
          node.children.length === 0 &&
          node.textContent?.trim() === 'Enter a valid email address.'
      ) as HTMLElement | undefined
      return {
        inputTop: input.getBoundingClientRect().top,
        errorTop: error?.getBoundingClientRect().top ?? -1,
        invalid: input.getAttribute('aria-invalid'),
        describedBy: input.getAttribute('aria-describedby'),
      }
    })
    expect(wiring.errorTop).toBeGreaterThan(wiring.inputTop)
    expect(wiring.invalid).toBe('true')
    expect(wiring.describedBy).toBeTruthy()
    await shot(page, 'report-email-invalid-address')
  })

  /**
   * E-26 - a failed send reports the server's own reason. openapi-fetch has
   * already read the body, so the reason is taken from its parsed error rather
   * than from a response that can only yield a status.
   */
  test('E-26 - a failed send names the reason, not a status code', async ({
    page,
    browserErrors,
  }) => {
    await page.route(`**/api/audit/runs/${EMAILED}`, (route) =>
      route.fulfill({ json: savedCaptureRun(EMAILED) })
    )
    await page.route('**/api/settings/email', (route) =>
      route.fulfill({ json: { email: 'ada@example.com', verified: true } })
    )
    await page.route(`**/api/audit/runs/${EMAILED}/email`, (route) =>
      route.fulfill({
        status: 502,
        json: { detail: 'The report service is unavailable' },
      })
    )
    // The 502 is the point of the flow, so the browser logging it is expected.
    acceptBrowserError(
      browserErrors,
      'Failed to load resource: the server responded with a status of 502 (Bad Gateway)'
    )

    await page.goto(`/audit/runs/${EMAILED}`)
    await page.getByRole('button', { name: 'Email me this report' }).click()
    await page.getByRole('button', { name: 'Send report' }).click()

    const dialog = page.getByRole('dialog')
    await expect(
      dialog.getByText('The report service is unavailable')
    ).toBeVisible()
    await expect(dialog.getByText(/Failed to email report: \d+/)).toHaveCount(0)
    // The send stays available, so the reader can try again in place.
    await expect(
      dialog.getByRole('button', { name: 'Send report' })
    ).toBeEnabled()
  })

  /**
   * E-39 - "Email me this report" wears the report's own glyph; an
   * open-in-new arrow would promise a new tab rather than a send.
   */
  test('E-39 - the email action does not carry an open-in-new icon', async ({
    page,
  }) => {
    await page.route(`**/api/audit/runs/${SAVED}`, (route) =>
      route.fulfill({ json: savedCaptureRun(SAVED) })
    )
    await page.goto(`/audit/runs/${SAVED}`)
    const icon = page
      .getByRole('button', { name: 'Email me this report' })
      .locator('use')
    expect(await icon.getAttribute('href')).not.toContain('arrow-up-right')
  })
})

test.describe('Fleet', () => {
  /**
   * E-02, E-13 - a fleet run with a failed target stays on the run view, names
   * the target that failed, and reports the run in the past tense.
   */
  test('a failed target keeps the run view and is named', async ({ page }) => {
    const snapshotId = 'fleet_snapshot_partial'
    await mockFleetTargets(page, FLEET)
    await page.route(`**/api/audit/runs/${snapshotId}`, (route) =>
      route.fulfill({
        json: {
          snapshot_id: snapshotId,
          name: 'Fleet',
          created_at: '2026-09-08T10:00:00Z',
          targets_audited: 2,
          results: [targetResult('aurora-writer'), targetResult('analytics')],
        },
      })
    )
    await mockFleetRun(page, [
      { type: 'status', phase: 'config', message: 'Resolving 3 targets' },
      {
        type: 'target_start',
        target_name: 'aurora-writer',
        index: 0,
        total: 3,
      },
      {
        type: 'target_complete',
        target_name: 'aurora-writer',
        result: targetResult('aurora-writer'),
        index: 0,
        total: 3,
      },
      {
        type: 'target_start',
        target_name: 'aurora-reader',
        index: 1,
        total: 3,
      },
      {
        type: 'target_error',
        target_name: 'aurora-reader',
        error: 'connection to server at "aurora-reader.internal" failed',
        index: 1,
        total: 3,
      },
      { type: 'target_start', target_name: 'analytics', index: 2, total: 3 },
      {
        type: 'target_complete',
        target_name: 'analytics',
        result: targetResult('analytics'),
        index: 2,
        total: 3,
      },
      { type: 'snapshot_saved', snapshot_id: snapshotId, name: 'Fleet' },
      {
        type: 'complete',
        success: true,
        snapshot_id: snapshotId,
        summary: { targets_audited: 2, successes: 2, failures: 1 },
      },
    ])
    await prepareAuditPage(page)
    await page.getByRole('button', { name: 'Select all' }).click()
    await page.getByRole('button', { name: 'Run health check' }).click()

    await expect(
      page.getByText('aurora-reader could not be audited')
    ).toBeVisible({ timeout: 20_000 })
    // The report is offered, not forced: an unacknowledged failure is only
    // visible here.
    await expect(page).toHaveURL(/\/audit$/)
    await expect(
      page.getByText('connection to server at "aurora-reader.internal" failed')
    ).toBeVisible()
    await expect(page.getByText('2 audited, 1 failed')).toBeVisible()
    await expect(
      page.getByRole('link', { name: 'Open the report' })
    ).toBeVisible()

    // The finished run stops describing itself as running.
    await expect(
      mainContent(page).getByText(/Completed on 3 targets:/)
    ).toBeVisible()
    await expect(
      mainContent(page).getByText(/Running on 3 targets:/)
    ).toHaveCount(0)
    await shot(page, 'fleet-partial-failure')
  })

  /**
   * E-13, E-12 - a fleet run that never reached a target says so on every row
   * and states its scope in the same case the targets are configured with.
   */
  test('a failed fleet run reports what did not start', async ({
    page,
    browserErrors,
  }) => {
    acceptBrowserError(browserErrors, NOT_FOUND)
    await mockFleetTargets(page, FLEET)
    await mockFleetRun(page, [
      { type: 'status', phase: 'config', message: 'Resolving 3 targets' },
      {
        type: 'error',
        message: 'No reachable targets in the selection',
        phase: 'config',
      },
    ])
    await prepareAuditPage(page)
    await page.getByRole('button', { name: 'Select all' }).click()
    await page.getByRole('button', { name: 'Run health check' }).click()

    await expect(page.getByText('Fleet health check failed')).toBeVisible({
      timeout: 20_000,
    })
    // The Jobs sidebar chip repeats the same sentence; this finding is about
    // what the page itself reports.
    await expect(
      mainContent(page).getByText('No reachable targets in the selection')
    ).toBeVisible()
    await expect(
      page.getByText(
        'No target completed, so this run saved nothing; earlier reports are unaffected.'
      )
    ).toBeVisible()

    // A row that will never run is "Not started", not still "Queued".
    await expect(
      mainContent(page).getByText('Not started', { exact: true }).first()
    ).toBeVisible()
    await expect(
      mainContent(page).getByText('Queued', { exact: true })
    ).toHaveCount(0)

    const scope = mainContent(page).getByText(/Failed on 3 targets:/)
    await expect(scope).toContainText('aurora-writer')
    expect(
      await scope.evaluate((el) => getComputedStyle(el).textTransform)
    ).not.toBe('uppercase')
  })
})

test.describe('Reports', () => {
  function runSummary(index: number) {
    return {
      run_id: `capture_run_${index}`,
      target_name: 'e2e-guard',
      engine: 'postgresql',
      started_at: `2026-09-05T1${index}:05:00Z`,
      duration_seconds: 60,
      total_queries: 10 + index,
      has_analysis: index % 2 === 0,
    }
  }

  /**
   * E-17, E-24 - every row is named by its scope and time, and its open
   * affordance is visible before anyone hovers it.
   */
  test('every report row is named by its scope and time', async ({ page }) => {
    const runs = [0, 1, 2, 3, 4].map(runSummary)
    await page.route('**/api/audit/runs', (route) =>
      route.fulfill({ json: { runs, count: runs.length } })
    )
    await page.route('**/api/fleet/snapshots', (route) =>
      route.fulfill({
        json: {
          snapshots: [
            {
              snapshot_id: 'fleet_snap_1',
              name: 'Production fleet',
              created_at: '2026-09-05T08:00:00Z',
              targets_audited: 3,
              target_names: ['aurora-writer', 'aurora-reader', 'analytics'],
              duration_seconds: 60,
            },
          ],
        },
      })
    )
    // Registered last so it wins over the list patterns above.
    await page.route('**/api/audit/runs/capture_run_*', (route) =>
      route.fulfill({
        json: savedCaptureRun(
          new URL(route.request().url()).pathname.split('/').pop() as string
        ),
      })
    )
    await prepareAuditPage(page)
    await page.getByRole('tab', { name: 'Reports' }).click()
    await expect(
      page.getByRole('heading', { name: 'Reports (6)' })
    ).toBeVisible()

    const names = await page.evaluate(() =>
      Array.from(
        document.querySelectorAll('#main-content button[aria-label]')
      ).map((el) => el.getAttribute('aria-label') ?? '')
    )
    const rowNames = names.filter((name) => name.startsWith('Open the '))
    expect(rowNames).toHaveLength(6)
    expect(new Set(rowNames).size).toBe(rowNames.length)
    for (const name of rowNames) {
      expect(name).toMatch(/^Open the .+ health check from .+/)
    }
    expect(
      rowNames.some((name) =>
        name.includes('Production fleet fleet health check')
      )
    ).toBe(true)

    const chevronOpacity = await page
      .locator(
        '#main-content button[aria-label^="Open the "] svg[class*="opacity-"]'
      )
      .first()
      .evaluate((el) => Number(getComputedStyle(el).opacity))
    expect(chevronOpacity).toBeGreaterThan(0.2)

    await page
      .getByRole('button', { name: /^Open the e2e-guard health check from / })
      .first()
      .click()
    await expect(page).toHaveURL(/\/audit\/runs\/capture_run_\d/)
    await page.getByRole('link', { name: 'Back to Reports' }).click()
    await expect(page.getByRole('tab', { name: 'Reports' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    await shot(page, 'reports-named-rows')
  })
})
