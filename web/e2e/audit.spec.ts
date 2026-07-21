import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

const runId = 'audit_e2e_guard_20260715_120000'

const auditReport = {
  target_name: 'e2e-guard',
  engine: 'postgresql',
  host: 'db.internal',
  instance_class: 'db.r6g.large',
  audited_at: '2026-07-15T12:00:00Z',
  metrics: {
    max_connections: 100,
    active_connections: 82,
    idle_connections: 18,
    connection_utilization_pct: 82,
    cache_hit_rate: 97.4,
    database_size_mb: 2048,
    server_version: 'PostgreSQL 16.3',
    tracked_query_count: 42,
    uptime_seconds: 432000,
    read_pct: 91,
    write_pct: 9,
    storage_allocated_gb: 100,
    storage_used_pct: 73,
    storage_type: 'gp3',
  },
  sizing: {
    verdict: 'oversized',
    explanation: 'Memory usage is consistently below the provisioned tier.',
    suggested_instance_class: 'db.r6g.medium',
    potential_savings_usd: 210,
  },
  cache_opportunity: {
    score: 88,
    level: 'high',
    explanation: 'Repeated read-heavy queries are strong cache candidates.',
  },
  top_queries: [
    {
      query_hash: 'slow-query-001',
      query_text: 'SELECT * FROM orders WHERE customer_id = $1',
      calls: 3200,
      total_time_ms: 48000,
      avg_time_ms: 15,
      pct_total_time: 31.5,
    },
  ],
  health_analysis: {
    health_score: 58,
    health_label: 'Needs attention',
    health_score_rationale:
      'Connection pressure and repeated reads are the primary risks.',
    executive_summary:
      'The database is stable but has clear capacity and caching opportunities.',
    findings: [
      {
        severity: 'warn',
        title: 'Connection pool is nearing capacity',
        body: 'Peak utilization leaves little room for traffic bursts.',
      },
    ],
    recommended_actions: [
      {
        rank: 1,
        title: 'Reduce connection pressure',
        body: 'Tune pool limits and investigate idle sessions.',
      },
    ],
  },
}

const successfulAuditEvents = [
  {
    event: 'status',
    data: {
      type: 'status',
      phase: 'connect',
      message: 'Auditing e2e-guard...',
    },
  },
  {
    event: 'target_start',
    data: {
      type: 'target_start',
      target_name: 'e2e-guard',
      index: 0,
      total: 1,
    },
  },
  {
    event: 'status',
    data: {
      type: 'status',
      phase: 'collect',
      message: 'Collecting database metrics...',
    },
  },
  {
    event: 'metrics_collected',
    data: {
      type: 'metrics_collected',
      target_name: 'e2e-guard',
      metrics: auditReport.metrics,
    },
  },
  {
    event: 'status',
    data: {
      type: 'status',
      phase: 'insights',
      message: 'Analyzing health data...',
    },
  },
  {
    event: 'snapshot_saved',
    data: {
      type: 'snapshot_saved',
      snapshot_id: runId,
      path: `/tmp/${runId}.json`,
    },
  },
  {
    event: 'target_complete',
    data: {
      type: 'target_complete',
      target_name: 'e2e-guard',
      result: auditReport,
      index: 0,
      total: 1,
    },
  },
  {
    event: 'complete',
    data: { type: 'complete', success: true, snapshot_id: runId },
  },
]

async function prepareAuditPage(
  page: Parameters<typeof configureTestTarget>[0]
) {
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/audit')
  await expect(
    page.getByRole('heading', { name: 'Health Check' })
  ).toBeVisible()
  await expect(
    page.getByText('Full audit of "e2e-guard"', { exact: false })
  ).toBeVisible()
}

test('runs a streamed health check and reopens it from history', async ({
  page,
}) => {
  setBackendFixtures({
    audit: [{ events: successfulAuditEvents.map(({ data }) => data) }],
  })
  await prepareAuditPage(page)

  const auditRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/audit'
  )
  await page.getByRole('button', { name: 'Run Audit' }).click()

  const request = await auditRequest
  expect(request.postDataJSON()).toEqual({
    insights: true,
    target: 'e2e-guard',
  })
  await expect(
    page.getByText('Starting audit...', { exact: true })
  ).toBeVisible()

  await expect(page.getByText('Latest audit', { exact: true })).toBeVisible()
  // C-09 redesigned the report: the AI findings section is titled "AI Analysis"
  // (the score itself moved into the Verdict hero).
  await expect(
    page.getByRole('paragraph').filter({ hasText: /^AI Analysis$/ })
  ).toBeVisible()
  await expect(page.getByText('58', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Connection pool is nearing capacity', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Recommended Actions', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Reduce connection pressure', { exact: true })
  ).toBeVisible()
  // "Oversized" now renders twice (Verdict hero tag + Sizing card tag).
  await expect(
    page.getByText('Oversized', { exact: true }).first()
  ).toBeVisible()
  await expect(
    page.getByText('Repeated read-heavy queries are strong cache candidates.', {
      exact: true,
    })
  ).toBeVisible()
  // The "Top Queries (N)" table moved behind the collapsed Details disclosure;
  // open it to keep the exact-count assertion.
  await page.getByRole('button', { name: /overview metrics/ }).click()
  await expect(
    page.getByRole('paragraph').filter({ hasText: /^Top Queries \(1\)$/ })
  ).toBeVisible()

  // Past runs now render as links to their own detail route
  // (/audit/runs/$runId), not the old inline history buttons. [FIX-2]
  const pastRun = page.getByRole('link').filter({ hasText: runId })
  await expect(pastRun).toHaveCount(1)
  await pastRun.click()

  // The detail page names the run (kind heading + bare runId + "Saved · <date>")
  // and reuses the /audit report body — the old "Saved run: <id>" string is gone.
  await expect(page.getByRole('heading', { name: 'Quick audit' })).toBeVisible()
  await expect(page.getByTestId('run-detail-id')).toHaveText(runId)
  await expect(
    page.getByText('Connection pool is nearing capacity', { exact: true })
  ).toBeVisible()
})

test('shows a streamed audit failure and retries successfully', async ({
  page,
}) => {
  setBackendFixtures({
    audit: [
      {
        events: [
          {
            type: 'target_error',
            target_name: 'e2e-guard',
            error: 'Database connection refused during health check',
            index: 0,
            total: 1,
          },
          { type: 'complete', success: false },
        ],
      },
      { events: successfulAuditEvents.map(({ data }) => data) },
    ],
  })
  await prepareAuditPage(page)

  let auditCalls = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/audit'
    ) {
      auditCalls += 1
    }
  })

  await page.getByRole('button', { name: 'Run Audit' }).click()
  await expect(
    page.getByText('Database connection refused during health check', {
      exact: true,
    })
  ).toBeVisible()

  await page.getByRole('button', { name: 'Run Audit' }).click()
  await expect(page.getByText('Latest audit', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('paragraph').filter({ hasText: /^AI Analysis$/ })
  ).toBeVisible()
  expect(auditCalls).toBe(2)
})
