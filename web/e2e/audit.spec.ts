import type { Page } from '@playwright/test'
import {
  configureTestTarget,
  expect,
  mockAiKeyReady,
  mockConnectivityOk,
  test,
} from './fixtures'

/**
 * Start a health check whose capture stream stays open until the returned
 * `release` is called, so the single-run lock is observable mid-run.
 */
async function startGatedCapture(page: Page): Promise<() => void> {
  let release: () => void = () => undefined
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/audit/capture', async (route) => {
    await gate
    await route
      .fulfill({ headers: { 'content-type': 'text/event-stream' }, body: '' })
      .catch(() => undefined)
  })
  await prepareAuditPage(page)
  await page
    .getByRole('checkbox', { name: 'Select e2e-guard' })
    .click({ force: true })
  await page.getByRole('button', { name: 'Check requirements' }).click()
  await page.getByRole('button', { name: 'Run health check' }).click()
  return release
}

async function prepareAuditPage(
  page: Page,
  requirements: Record<string, unknown> = {}
) {
  await configureTestTarget(page, { hasPassword: true })
  await mockAiKeyReady(page)
  await mockConnectivityOk(page)
  await page.route('**/api/audit/requirements*', (route) =>
    route.fulfill({
      json: {
        target: 'e2e-guard',
        engine: 'postgresql',
        query_stats: 'ok',
        detail: 'Query statistics are available',
        remediation: null,
        docker_available: true,
        ...requirements,
      },
    })
  )
  await page.goto('/audit')
  await expect(
    page.getByRole('heading', { name: 'Health Check' })
  ).toBeVisible()
}

test('starts with no target selected and offers only capture durations', async ({
  page,
}) => {
  await prepareAuditPage(page)

  await expect(
    page.getByRole('checkbox', { name: 'Select e2e-guard' })
  ).not.toBeChecked()
  await expect(
    page.getByRole('button', { name: 'Run health check' })
  ).toBeDisabled()
  await expect(
    page.getByText('Select one or more targets to check', { exact: true })
  ).toBeVisible()
  await expect(page.getByRole('combobox')).toContainText('1 minute')

  await page.getByRole('combobox').click()
  for (const label of [
    '30 seconds',
    '1 minute',
    '5 minutes',
    '15 minutes',
    '1 hour',
  ]) {
    await expect(
      page.getByRole('option', { name: label, exact: true })
    ).toBeVisible()
  }
  await expect(page.getByRole('option', { name: 'Instant' })).toHaveCount(0)
})

test('opens a saved report from Reports on the run route and returns to Reports', async ({
  page,
}) => {
  const runId = 'audit_e2e_guard_20260715_120000'
  const report = {
    target_name: 'e2e-guard',
    engine: 'postgresql',
    host: 'db.internal',
    audited_at: '2026-07-15T12:00:00Z',
    metrics: {
      active_connections: 8,
      max_connections: 100,
      tracked_query_count: 0,
    },
    sizing: {
      verdict: 'right_sized',
      explanation: 'Capacity matches the workload.',
    },
    cache_opportunity: { score: 44, level: 'medium' },
    health_analysis: {
      health_score: 91,
      health_label: 'Healthy',
      top_findings: [
        {
          severity: 'ok',
          title: 'Connection headroom is healthy',
          body: 'The pool has room for bursts.',
        },
      ],
    },
  }
  await page.route('**/api/audit/runs**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === `/api/audit/runs/${runId}`)
      return route.fulfill({ json: report })
    return route.fulfill({
      json: {
        runs: [
          {
            run_id: runId,
            target_name: 'e2e-guard',
            engine: 'postgresql',
            started_at: '2026-07-15T12:00:00Z',
            duration_seconds: 60,
            total_queries: 0,
            has_analysis: true,
          },
        ],
      },
    })
  })
  await page.route('**/api/fleet/snapshots', (route) =>
    route.fulfill({ json: { snapshots: [] } })
  )
  await prepareAuditPage(page)

  await page.getByRole('tab', { name: 'Reports' }).click()
  const savedReportRow = page.getByRole('button').filter({
    has: page.getByText('e2e-guard', { exact: true }),
    hasText: 'Health check · postgresql · 1m window',
  })
  await expect(savedReportRow).toBeVisible()
  await savedReportRow.click()
  await expect(page).toHaveURL(`/audit/runs/${runId}`)
  await expect(
    page.getByRole('heading', { name: 'e2e-guard' }).first()
  ).toBeVisible()
  await page.getByRole('tab', { name: 'Overview' }).click()
  await expect(
    page.getByText(
      'See the headline verdict and the findings that matter most for this database.',
      { exact: true }
    )
  ).toBeVisible()
  await expect(
    page.getByText('Connection headroom is healthy', { exact: true }).first()
  ).toBeVisible()

  await page.getByRole('link', { name: 'Back to Reports' }).click()
  await expect(page).toHaveURL(/\/audit\?tab=history#history/)
  await expect(page.getByRole('tab', { name: 'Reports' })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  await expect(savedReportRow).toBeVisible()
})

test('shows failed preflight remediation inline and never starts capture', async ({
  page,
}) => {
  let captureCalls = 0
  await page.route('**/api/audit/capture', (route) => {
    captureCalls += 1
    return route.abort()
  })
  await prepareAuditPage(page, {
    query_stats: 'missing',
    detail: 'pg_stat_statements is not available',
    remediation:
      "CREATE EXTENSION pg_stat_statements;\nALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';",
  })
  await page
    .getByRole('checkbox', { name: 'Select e2e-guard' })
    .click({ force: true })
  await page.getByRole('button', { name: 'Check requirements' }).click()

  await expect(page.getByText('How to fix this', { exact: true })).toBeVisible()
  await expect(
    page.getByText('CREATE EXTENSION pg_stat_statements;', { exact: false })
  ).toBeVisible()
  await expect(
    page.getByText('Instant is still available', { exact: false })
  ).toHaveCount(0)
  await expect(
    page.getByRole('button', { name: 'Run health check' })
  ).toBeDisabled()
  expect(captureCalls).toBe(0)
})

test('docker-unavailable blocks a live-capture run with remediation', async ({
  page,
}) => {
  let captureCalls = 0
  await page.route('**/api/audit/capture', (route) => {
    captureCalls += 1
    return route.abort()
  })
  await prepareAuditPage(page, { docker_available: false })
  await page
    .getByRole('checkbox', { name: 'Select e2e-guard' })
    .click({ force: true })
  await page.getByRole('button', { name: 'Check requirements' }).click()
  await expect(
    page.getByText(
      'Start Docker Desktop, then re-check. The run stays blocked until Docker is available.',
      { exact: true }
    )
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Run health check' })
  ).toBeDisabled()
  expect(captureCalls).toBe(0)
})

test('single-run lock disables Run until Cancel releases it', async ({
  page,
}) => {
  const release = await startGatedCapture(page)

  const runningInfo = page.getByRole('button', {
    name: 'A health check is already running',
  })
  await expect(runningInfo).toBeVisible()
  await runningInfo.hover()
  await expect(page.getByRole('tooltip')).toHaveText(
    'A health check is already running. Cancel it before starting another.'
  )
  await expect(
    page.getByRole('button', { name: 'Run health check' })
  ).toBeDisabled()
  await page.getByRole('button', { name: 'Cancel' }).last().click()
  await expect(
    page.getByRole('button', { name: 'Run health check' })
  ).toBeEnabled()
  release()
})

test('AWS preflight appears only for AWS targets and hard-gates when signed out', async ({
  page,
}) => {
  await page.route('**/api/fleet/targets*', (route) =>
    route.fulfill({
      json: {
        members: [
          {
            name: 'e2e-guard',
            engine: 'postgresql',
            host: 'db.example.us-east-1.rds.amazonaws.com',
            port: 5432,
            database: 'application',
            user: 'rdst_e2e',
            password_env: 'TEST_DB_PASSWORD',
            has_password: true,
            group: null,
            region: 'us-east-1',
            instance_class: 'db.r6g.large',
          },
        ],
        groups: [],
        count: 1,
      },
    })
  )
  await page.route('**/api/fleet/aws-status', (route) =>
    route.fulfill({
      json: {
        has_credentials: false,
        method: null,
        identity_arn: null,
        account: null,
        active_profile: null,
        available_profiles: ['dev'],
        region: null,
      },
    })
  )
  let captureCalls = 0
  await page.route('**/api/audit/capture', (route) => {
    captureCalls += 1
    return route.abort()
  })
  await prepareAuditPage(page)

  await expect(page.getByText('AWS session', { exact: true })).toHaveCount(0)
  await page
    .getByRole('checkbox', { name: 'Select e2e-guard' })
    .click({ force: true })
  await page.getByRole('button', { name: 'Check requirements' }).click()

  await expect(page.getByText('AWS session', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Sign in to continue', { exact: true })
  ).toBeVisible()
  await expect(page.getByTestId('aws-connection-panel')).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Run health check' })
  ).toBeDisabled()
  expect(captureCalls).toBe(0)
})

test('shows the selected target name while a capture is running', async ({
  page,
}) => {
  const release = await startGatedCapture(page)
  await expect(
    page.getByText('Running on e2e-guard', { exact: true })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).last().click()
  release()
})

test('opens a saved fleet report through its fleet and instance tab hierarchy', async ({
  page,
}) => {
  const snapshotId = 'fleet_saved_e2e'
  await configureTestTarget(page, { hasPassword: true })
  await page.route(`**/api/audit/runs/${snapshotId}`, (route) =>
    route.fulfill({
      json: {
        snapshot_id: snapshotId,
        name: 'Production fleet',
        created_at: '2026-07-22T20:10:00Z',
        targets_audited: 2,
        total_monthly_cost_usd: 400,
        potential_savings_usd: 160,
        avg_cache_opportunity: 72,
        fleet_insights: {
          health_score: 78,
          health_label: 'Healthy',
          executive_summary: 'The fleet is healthy with room to reduce spend.',
          top_findings: [
            {
              severity: 'warn',
              title: 'Readers are overprovisioned',
            },
          ],
        },
        results: [
          {
            target_name: 'aurora-writer',
            engine: 'postgresql',
            instance_class: 'db.r6g.large',
            metrics: {
              active_connections: 18,
              max_connections: 100,
            },
            sizing: {
              verdict: 'right_sized',
              current_monthly_cost_usd: 200,
              suggested_instance_class: 'db.r6g.large',
              suggested_monthly_cost_usd: 200,
              potential_savings_usd: 0,
            },
            cache_opportunity: { score: 60, level: 'medium' },
          },
          {
            target_name: 'aurora-reader',
            engine: 'postgresql',
            instance_class: 'db.r6g.large',
            metrics: {
              active_connections: 6,
              max_connections: 100,
            },
            sizing: {
              verdict: 'oversized',
              current_monthly_cost_usd: 200,
              suggested_instance_class: 'db.r6g.medium',
              suggested_monthly_cost_usd: 40,
              potential_savings_usd: 160,
            },
            cache_opportunity: { score: 84, level: 'high' },
            health_analysis: {
              health_score: 86,
              executive_summary: 'The reader has ample connection headroom.',
              top_findings: [
                {
                  severity: 'ok',
                  title: 'Reader connections have headroom',
                },
              ],
            },
            health_report: {
              config_audit: {
                settings: [
                  {
                    parameter: 'shared_buffers',
                    current: '1 GB',
                    recommended: '2 GB',
                    status: 'warn',
                  },
                ],
              },
            },
          },
        ],
      },
    })
  )

  await page.goto(`/audit/runs/${snapshotId}`)
  await expect(
    page.getByRole('heading', { name: 'Production fleet · 2 targets' })
  ).toBeVisible()

  const fleetTabs = page.getByRole('tablist', {
    name: 'Fleet report sections',
  })
  await expect(
    fleetTabs.getByRole('tab', { name: 'Fleet Summary' })
  ).toBeVisible()
  await expect(
    fleetTabs.getByRole('tab', { name: 'Fleet Savings' })
  ).toBeVisible()
  await expect(fleetTabs.getByRole('tab')).toHaveCount(2)

  const instanceTabs = page.getByRole('tablist', { name: 'Instances' })
  await expect(
    instanceTabs.getByRole('tab', { name: 'aurora-writer' })
  ).toBeVisible()
  await expect(
    instanceTabs.getByRole('tab', { name: 'aurora-reader' })
  ).toBeVisible()

  await fleetTabs.getByRole('tab', { name: 'Fleet Savings' }).click()
  await expect(
    page.getByText(
      'Review current and suggested monthly fleet costs, total potential savings, and the per-instance rollup.',
      { exact: true }
    )
  ).toBeVisible()
  await expect(
    page.getByText('$160.00/mo', { exact: true }).first()
  ).toBeVisible()
  await expect(page.getByText('Per-Node Sizing Rollup')).toBeVisible()

  await instanceTabs.getByRole('tab', { name: 'aurora-reader' }).click()
  await expect(page).toHaveURL(
    /fleetTab=target&target=aurora-reader&tab=overview/
  )
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  await expect(
    page.getByText(
      'See the headline verdict and the findings that matter most for this database.',
      { exact: true }
    )
  ).toBeVisible()
  await expect(
    page.getByText('Reader connections have headroom', { exact: true })
  ).toBeVisible()

  await page.getByRole('tab', { name: 'Detailed Analysis' }).click()
  await expect(
    page.getByText(
      'Inspect database health across configuration, memory and cache, vacuum and bloat, indexes, connections, and replication.',
      { exact: true }
    )
  ).toBeVisible()
  await expect(page.getByText('Configuration Audit')).toBeVisible()
  const databaseSettings = page.getByRole('button', {
    name: 'Database settings (1)',
  })
  await expect(databaseSettings).toHaveAttribute('aria-expanded', 'false')
  await databaseSettings.click()
  await expect(databaseSettings).toHaveAttribute('aria-expanded', 'true')
  await expect(
    page
      .locator('[data-report-tab="detailed-analysis"]')
      .getByText('shared_buffers', { exact: true })
  ).toBeVisible()
})
