import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { FullAuditReportView } from '../components/audit/report/AuditReportView'
import { FleetSnapshotView } from '../components/audit/report/FleetSnapshotView'
import { VerdictCard } from '../components/audit/report/VerdictCard'
import {
  INNER_REPORT_TAB_DESCRIPTIONS,
  INNER_REPORT_TABS,
} from '../lib/auditReportLocation'
import type { AuditReport } from '../types/audit'

afterEach(() => cleanup())
beforeEach(() => window.history.replaceState({}, '', '/audit/runs/test'))

function selectInnerTab(name: string) {
  fireEvent.click(
    screen.getByRole('tab', {
      name,
    })
  )
}

function openDetailDisclosures(names: string[]) {
  for (const name of names) {
    const disclosure = screen.getByRole('button', { name })
    if (disclosure.getAttribute('aria-expanded') !== 'true') {
      fireEvent.click(disclosure)
    }
  }
}

const INNER_TAB_NAMES = INNER_REPORT_TABS.map((tab) => tab.label)

const INNER_TAB_DESCRIPTIONS = INNER_REPORT_TABS.map(
  (tab) => [tab.label, INNER_REPORT_TAB_DESCRIPTIONS[tab.id]] as const
)

/** Content each inner tab must still show after the tabs have been walked. */
const TAB_CONTENT: Array<[string, string[], boolean]> = [
  [
    'Detailed Analysis',
    [
      'Vacuum & Bloat',
      'public.orders',
      'Indexes',
      'orders_old_idx',
      'orders_a',
      'Connection Detail',
      'UPDATE orders',
      'Configuration Audit',
      'shared_buffers',
      'Replication',
      'analytics',
      'Connection pressure',
      'q1: Good cache candidate',
      'WARN',
    ],
    false,
  ],
  ['Sizing', ['CloudWatch CPU', 'db.r6g.medium'], true],
  ['Savings', ['$400.00/mo', '$650.00/mo', 'Cost Breakdown'], true],
  ['Queries', ['Queries (1)'], true],
  ['Next Steps', ['Tune the pool'], true],
]

/** Every inner tab's honest empty state, keyed by tab label. */
const EMPTY_TAB_MESSAGES: Array<[string, string]> = [
  ['Queries', 'No query data was collected for this run.'],
  ['Sizing', 'No sizing data was collected for this run.'],
  ['Savings', 'No savings or cost data was collected for this run.'],
  [
    'Detailed Analysis',
    'No detailed analysis data was collected for this run.',
  ],
  ['Next Steps', 'No recommended next steps were generated for this run.'],
]

const fullReport: AuditReport = {
  target_name: 'production',
  engine: 'postgresql',
  instance_class: 'db.r6g.large',
  metrics: {
    active_connections: 42,
    max_connections: 100,
    cache_hit_rate: 98.2,
    database_size_mb: 2048,
    read_pct: 91,
    write_pct: 9,
    server_version:
      'PostgreSQL 17.7 on aarch64-unknown-linux-gnu, compiled by gcc',
  },
  sizing: {
    verdict: 'oversized',
    current_monthly_cost_usd: 900,
    suggested_instance_class: 'db.r6g.medium',
    suggested_monthly_cost_usd: 500,
    potential_savings_usd: 400,
    readyset_projected_class: 'db.r6g.small',
    readyset_projected_cost_usd: 250,
    readyset_projected_savings_usd: 650,
  },
  cache_opportunity: { score: 88, level: 'high' },
  cloudwatch_cpu: { avg_cpu: 22, max_cpu: 71, min_cpu: 4, hours: 24 },
  top_queries: [
    {
      query_hash: 'q1',
      query_text: 'SELECT * FROM orders',
      calls: 20,
      avg_time_ms: 12,
      pct_total_time: 40,
    },
  ],
  health_report: {
    vacuum_bloat: {
      summary_status: 'warn',
      txid_age: 1200,
      txid_age_pct: 2,
      autovacuum_enabled: true,
      tables: [
        {
          schema: 'public',
          table: 'orders',
          live_tuples: 100,
          dead_tuples: 30,
          dead_ratio_pct: 23,
          status: 'warn',
        },
      ],
    },
    index_health: {
      total_indexes: 4,
      unused_indexes: [
        {
          table: 'orders',
          index: 'orders_old_idx',
          columns: ['old_id'],
          scans: 0,
        },
      ],
      duplicates: [
        {
          table: 'orders',
          redundant_index: 'orders_a',
          covered_by: 'orders_ab',
        },
      ],
    },
    connections: {
      total_connections: 42,
      max_connections: 100,
      by_state: { active: 12 },
      idle_in_transaction_count: 1,
      long_running_idle_in_tx: [
        {
          pid: 99,
          state: 'idle in transaction',
          query_preview: 'UPDATE orders',
        },
      ],
    },
    config_audit: {
      instance_ram_gb: 16,
      instance_vcpus: 4,
      settings: [
        {
          parameter: 'shared_buffers',
          current: '1 GB',
          recommended: '4 GB',
          status: 'warn',
          note: 'Increase it',
        },
      ],
    },
    replication: {
      is_replica: false,
      inactive_slots: 1,
      peers: [{ application_name: 'reader-1', state: 'streaming' }],
      slots: [{ slot_name: 'analytics', active: false, retained_bytes: 2048 }],
    },
  },
  health_analysis: {
    health_score: 72,
    executive_summary: 'Stable with tuning opportunities.',
    top_findings: [{ severity: 'warn', title: 'Bloat risk' }],
    findings: [{ severity: 'warn', title: 'Connection pressure' }],
    recommended_actions: [
      { rank: 1, title: 'Tune the pool', body: 'Reduce idle sessions.' },
    ],
    index_suggestions: [
      {
        sql: 'CREATE INDEX orders_customer_idx ON orders(customer_id)',
        reason: 'Frequent lookup',
      },
    ],
    query_commentary: [
      { query_hash: 'q1', observation: 'Good cache candidate' },
    ],
  },
}

/** The canonical report, with only what a test asserts on overridden. */
function auditReportFixture(overrides: Partial<AuditReport> = {}): AuditReport {
  return { ...fullReport, ...overrides }
}

function renderReport(overrides: Partial<AuditReport> = {}) {
  return render(
    <FullAuditReportView
      report={auditReportFixture(overrides)}
      aiCredentialInvalid={false}
    />
  )
}

type SnapshotDetail = ComponentProps<typeof FleetSnapshotView>['detail']

/** A fleet snapshot envelope around `results`; only the header is boilerplate. */
function fleetSnapshotDetail(
  results: AuditReport[],
  overrides: Partial<SnapshotDetail> = {}
): SnapshotDetail {
  return {
    snapshot_id: 'fleet_test',
    name: 'Fleet',
    created_at: '2026-07-22T20:10:00Z',
    targets_audited: results.length,
    results,
    ...overrides,
  } as SnapshotDetail
}

function renderFleet(
  results: AuditReport[],
  overrides: Partial<SnapshotDetail> = {}
) {
  return render(
    <FleetSnapshotView
      detail={fleetSnapshotDetail(results, overrides)}
      aiCredentialInvalid={false}
    />
  )
}

describe('AuditReportView', () => {
  it('explains what each inner report tab contains', () => {
    renderReport()

    for (const [tab, description] of INNER_TAB_DESCRIPTIONS) {
      selectInnerTab(tab)
      expect(screen.getByText(description, { exact: true })).toBeTruthy()
    }
  })

  it('preserves persisted report content across the inner tabs', () => {
    renderReport()

    expect(
      screen
        .getByRole('tab', { name: 'Overview' })
        .getAttribute('aria-selected')
    ).toBe('true')
    expect(
      screen.getAllByText('Stable with tuning opportunities.').length
    ).toBeGreaterThan(0)
    expect(screen.getByText('Bloat risk')).toBeTruthy()

    // One render, walked tab by tab: this is the persistence claim, so the
    // assertion message names the tab rather than splitting into it.each.
    for (const [tab, texts, exact] of TAB_CONTENT) {
      selectInnerTab(tab)
      if (tab === 'Detailed Analysis') {
        openDetailDisclosures([
          'Table statistics (1)',
          'Unused indexes (1)',
          'Duplicate indexes (1)',
          'Long-running idle transactions (1)',
          'Database settings (1)',
          'Replication slots (1)',
        ])
      }
      for (const text of texts) {
        expect(
          screen.getAllByText(text, { exact }),
          `${tab} tab is missing ${text}`
        ).not.toHaveLength(0)
      }
    }

    selectInnerTab('Detailed Analysis')
    openDetailDisclosures(['Database settings (1)'])
    expect(
      screen.queryByRole('button', { name: /overview metrics/i })
    ).toBeNull()
    expect(screen.getByText('Notes', { exact: true })).toBeTruthy()
    expect(screen.getByText('PostgreSQL 17.7', { exact: true })).toBeTruthy()
    expect(screen.queryByText(/aarch64-unknown/)).toBeNull()
    expect(screen.getByText(/Table statistics \(1\)/)).toBeTruthy()
  })

  it('shows the stored non-auth AI error without a Configure link', () => {
    render(
      <VerdictCard
        report={{
          ...fullReport,
          health_analysis: { error: 'provider timed out' },
        }}
        aiCredentialInvalid
      />
    )
    expect(
      screen.getByText('AI analysis failed: provider timed out')
    ).toBeTruthy()
    expect(screen.queryByText('Configure')).toBeNull()
  })

  it('uses honest instance and zero-query copy', () => {
    renderReport({
      instance_class: null,
      metrics: { ...fullReport.metrics, tracked_query_count: 0 },
    })
    expect(
      screen.getAllByText('Not sure what instance this is running on', {
        exact: true,
      }).length
    ).toBeGreaterThan(0)
    selectInnerTab('Detailed Analysis')
    expect(
      screen.getByText(
        'No tracked queries because query statistics were unavailable during this check.',
        { exact: true }
      )
    ).toBeTruthy()
  })

  it('hides internal metadata tags and presents the database role as a badge', () => {
    renderReport({
      tags: ['aws-account:123456789012', 'role:reader', 'production'],
    })

    selectInnerTab('Detailed Analysis')
    expect(screen.queryByText(/aws-account:123456789012/)).toBeNull()
    expect(screen.queryByText(/role:reader/)).toBeNull()
    expect(screen.getByText('Role: reader', { exact: true })).toBeTruthy()
    expect(screen.getAllByText(/production/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('navigation')).toBeNull()
  })

  it('keeps the saved-detail header singular and explains a skipped benchmark', () => {
    renderReport({
      workload: { duration_seconds: 30, total_queries: 0, queries: [] },
    })

    expect(screen.queryByRole('heading', { name: /^production$/ })).toBeNull()
    selectInnerTab('Queries')
    expect(
      screen.getByText(
        'No live queries were captured during the audit window, so there was nothing to benchmark against Readyset. Re-run with live traffic on the database.'
      )
    ).toBeTruthy()
  })

  it('joins captured volume and cache results into one query row', () => {
    renderReport({
      workload: {
        duration_seconds: 30,
        total_queries: 20,
        queries: fullReport.top_queries,
        analysis: {
          index_recommendations: [
            {
              table: 'orders',
              columns: ['customer_id'],
              reason: 'Frequent customer lookup',
              estimated_impact: 'high',
              create_index_sql:
                'CREATE INDEX orders_customer_idx ON orders(customer_id)',
            },
            {
              table: 'products',
              reason: 'Aggregation on product ID',
              estimated_impact: 'moderate',
            },
          ],
        },
      },
      readyset_comparison: {
        queries_tested: 1,
        supported_count: 1,
        avg_speedup: 537,
        queries: [
          {
            query_hash: 'q1',
            query_text: 'SELECT * FROM orders',
            supported: true,
            upstream_ms: 588,
            readyset_ms: 1.1,
            speedup: 537,
          },
        ],
      },
    })

    selectInnerTab('Queries')
    expect(
      screen
        .getAllByText('Queries (1)', { exact: true })
        .filter((element) => element.tagName === 'P')
    ).toHaveLength(1)
    expect(
      screen
        .getByRole('tab', { name: /Captured \(live\)/ })
        .getAttribute('aria-selected')
    ).toBe('true')
    expect(screen.getByText('Cacheable', { exact: true })).toBeTruthy()
    const queryTable = screen
      .getByText('Calls', { exact: true })
      .closest('table') as HTMLTableElement
    expect(queryTable).toBeTruthy()
    expect(within(queryTable).getByText('Avg', { exact: true })).toBeTruthy()
    expect(within(queryTable).getByText('20', { exact: true })).toBeTruthy()
    expect(within(queryTable).getByText('12.0ms', { exact: true })).toBeTruthy()
    expect(screen.getByText('Upstream avg')).toBeTruthy()
    expect(screen.getByText('588.0ms')).toBeTruthy()
    expect(screen.getByText('Readyset avg')).toBeTruthy()
    expect(screen.getByText('1.1ms')).toBeTruthy()
    expect(screen.getByText('537x speedup')).toBeTruthy()
    expect(screen.queryByText(/Captured Queries/)).toBeNull()
    expect(screen.queryByText(/Historical Top Queries/)).toBeNull()
    expect(screen.queryByText(/Readyset Cache Comparison/)).toBeNull()
    expect(screen.queryByText(/Caching Candidates/)).toBeNull()

    selectInnerTab('Detailed Analysis')
    expect(document.body.textContent).toContain('orders_customer_idx')
    expect(document.body.textContent).toContain(
      'CREATE INDEX orders_customer_idx ON orders(customer_id)'
    )
    expect(
      screen
        .getAllByText('Indexes', { exact: true })
        .filter((element) => element.tagName === 'P')
    ).toHaveLength(1)
    expect(
      screen.getByText('Add an index on products(product_id)', { exact: true })
    ).toBeTruthy()
    expect(
      screen.getByText(/Exact CREATE INDEX DDL was not included/)
    ).toBeTruthy()
  })

  it('switches between captured and historical query sub-tabs', () => {
    renderReport({
      workload: {
        duration_seconds: 30,
        queries: [
          {
            query_hash: 'live-query',
            query_text: 'SELECT * FROM live_orders',
            calls: 12,
          },
        ],
      },
      readyset_comparison: {
        queries_tested: 1,
        supported_count: 0,
        queries: [
          {
            query_hash: 'live-query',
            query_text: 'SELECT * FROM live_orders',
            supported: false,
            reason: 'Uses an unsupported function',
          },
        ],
      },
    })

    selectInnerTab('Queries')
    const liveTab = screen.getByRole('tab', { name: /Captured \(live\)/ })
    const historicalTab = screen.getByRole('tab', {
      name: /Historical \(top by time\)/,
    })
    expect(liveTab.getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText('SELECT * FROM live_orders')).toBeTruthy()
    expect(screen.getByText('Not cacheable', { exact: true })).toBeTruthy()
    expect(screen.queryByText('Uses an unsupported function')).toBeNull()

    fireEvent.click(historicalTab)
    expect(historicalTab.getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText('SELECT * FROM orders')).toBeTruthy()
    expect(screen.queryByText('SELECT * FROM live_orders')).toBeNull()
  })

  it('explains a skipped benchmark when captured queries have no comparison', () => {
    renderReport({
      workload: {
        duration_seconds: 30,
        total_queries: 20,
        queries: fullReport.top_queries,
      },
      readyset_comparison: undefined,
    })

    selectInnerTab('Queries')
    expect(
      screen.getByText(
        'The live capture completed without a Readyset comparison. Start Docker Desktop, then re-run the health check to benchmark the captured workload.'
      )
    ).toBeTruthy()
  })

  it('links index recommendations to the correct query tab and badges priority metadata', () => {
    renderReport({
      top_queries: [
        {
          query_hash: 'f2d8abcd1234',
          query_text: 'SELECT * FROM historical_orders',
          calls: 40,
        },
      ],
      workload: {
        duration_seconds: 30,
        queries: [
          {
            query_hash: 'live-query',
            query_text: 'SELECT * FROM live_orders',
            calls: 12,
          },
        ],
        analysis: {
          index_recommendations: [
            {
              table: 'orders',
              columns: ['customer_id'],
              create_index_sql:
                'CREATE INDEX orders_customer_idx ON orders(customer_id)',
              reason: 'Avoid repeated scans',
              affected_queries: ['F2D8'],
            },
          ],
          optimization_priorities: [
            'Cache the hottest lookup - cache - low - high',
          ],
        },
      },
    })

    selectInnerTab('Queries')
    const capturedTab = screen.getByRole('tab', { name: /Captured \(live\)/ })
    expect(capturedTab.getAttribute('aria-selected')).toBe('true')
    selectInnerTab('Detailed Analysis')
    fireEvent.click(screen.getByRole('link', { name: 'F2D8' }))
    const historicalTab = screen.getByRole('tab', {
      name: /Historical \(top by time\)/,
    })
    expect(historicalTab.getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText('SELECT * FROM historical_orders')).toBeTruthy()
    expect(window.location.search).toContain('tab=queries')
    expect(window.location.search).toContain('queryTab=historical')

    selectInnerTab('Next Steps')
    expect(screen.getByText('Cache the hottest lookup')).toBeTruthy()
    expect(screen.getByText('cache', { exact: true })).toBeTruthy()
    expect(screen.getByText('low effort')).toBeTruthy()
    expect(screen.getByText('high impact')).toBeTruthy()
  })

  it('renders the unified query section in a fleet target inner tab', () => {
    renderFleet(
      [
        auditReportFixture({
          target_name: 'query-target',
          workload: { duration_seconds: 30, queries: fullReport.top_queries },
          readyset_comparison: {
            queries_tested: 1,
            supported_count: 1,
            queries: [
              {
                query_hash: 'q1',
                query_text: 'SELECT * FROM orders',
                supported: true,
                upstream_ms: 12,
                readyset_ms: 1,
                speedup: 12,
              },
            ],
          },
        }),
      ],
      { snapshot_id: 'fleet_queries' }
    )

    fireEvent.click(screen.getByRole('tab', { name: 'query-target' }))
    selectInnerTab('Queries')
    expect(
      screen
        .getAllByText('Queries (1)', { exact: true })
        .filter((element) => element.tagName === 'P')
    ).toHaveLength(1)
    expect(screen.queryByText(/Captured Queries/)).toBeNull()
    expect(screen.queryByText(/Readyset Cache Comparison/)).toBeNull()
  })

  it('separates fleet-wide tabs from the secondary instance tabs', () => {
    renderFleet([auditReportFixture({ target_name: 'Fleet Aurora 3' })], {
      snapshot_id: 'fleet_disclosures',
    })

    expect(screen.queryByText('Database Overview')).toBeNull()
    const outerTabs = screen.getByRole('tablist', {
      name: 'Fleet report sections',
    })
    expect(
      within(outerTabs).getByRole('tab', { name: 'Fleet Summary' })
    ).toBeTruthy()
    expect(
      within(outerTabs).getByRole('tab', { name: 'Fleet Savings' })
    ).toBeTruthy()
    expect(within(outerTabs).getAllByRole('tab')).toHaveLength(2)
    expect(
      within(outerTabs).queryByRole('tab', { name: 'Fleet Aurora 3' })
    ).toBeNull()
    expect(
      screen.getByText(
        'See the fleet-wide health verdict, audit coverage, and highest-priority findings across all instances.',
        { exact: true }
      )
    ).toBeTruthy()

    const instanceTabs = screen.getByRole('tablist', { name: 'Instances' })
    expect(screen.getByRole('heading', { name: 'Instances' })).toBeTruthy()
    fireEvent.click(
      within(instanceTabs).getByRole('tab', { name: 'Fleet Aurora 3' })
    )
    for (const tab of [
      'Overview',
      'Queries',
      'Sizing',
      'Savings',
      'Detailed Analysis',
      'Next Steps',
    ]) {
      expect(screen.getByRole('tab', { name: tab })).toBeTruthy()
    }
    expect(
      screen.getByText(
        'See the headline verdict and the findings that matter most for this database.',
        { exact: true }
      )
    ).toBeTruthy()
    selectInnerTab('Detailed Analysis')
    expect(screen.getAllByText('Database Overview').length).toBeGreaterThan(0)
    expect(window.location.search).toContain('fleetTab=target')
    expect(window.location.search).toContain('target=Fleet+Aurora+3')

    fireEvent.click(screen.getByRole('tab', { name: 'Fleet Savings' }))
    expect(
      screen.getByText(
        'Review current and suggested monthly fleet costs, total potential savings, and the per-instance rollup.',
        { exact: true }
      )
    ).toBeTruthy()
    expect(screen.getByRole('tablist', { name: 'Instances' })).toBeTruthy()
    expect(screen.getByText('Current cluster total')).toBeTruthy()
    expect(screen.getAllByText('Per-Node Sizing Rollup')).toHaveLength(1)
  })

  it('renders rounded fleet summary values, severity badges, and sizing totals', () => {
    renderFleet(
      [
        auditReportFixture({
          target_name: 'reader-1',
          sizing: {
            verdict: 'oversized',
            current_monthly_cost_usd: 100,
            suggested_monthly_cost_usd: 50,
            potential_savings_usd: 50,
          },
        }),
        auditReportFixture({
          target_name: 'reader-2',
          sizing: {
            verdict: 'oversized',
            current_monthly_cost_usd: 94.18,
            suggested_monthly_cost_usd: 46.36,
            potential_savings_usd: 47.82,
          },
        }),
      ],
      {
        snapshot_id: 'fleet_1',
        name: 'Production fleet',
        total_monthly_cost_usd: 194.18,
        potential_savings_usd: 97.82,
        avg_cache_opportunity: 71.66666666666667,
        fleet_insights: {
          health_score: 50,
          health_label: 'FAIR',
          executive_summary: 'A readable summary.',
          top_findings: [
            {
              severity: 'warn',
              title: 'Oversized nodes',
              body: 'Both nodes have excess capacity.',
            },
          ],
        },
      }
    )

    expect(screen.queryByText('Production fleet')).toBeNull()
    expect(screen.getByText('50/100', { exact: true })).toBeTruthy()
    expect(screen.getByText('72/100', { exact: true })).toBeTruthy()
    expect(screen.queryByText('71.66666666666667/100')).toBeNull()
    expect(screen.queryByText('$194.18/mo', { exact: true })).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Fleet Savings' }))
    expect(
      screen.getAllByText('$194.18/mo', { exact: true }).length
    ).toBeGreaterThan(0)
    expect(
      screen.getAllByText('$97.82/mo', { exact: true }).length
    ).toBeGreaterThan(0)
    expect(screen.getAllByText('Fleet Sizing Summary').length).toBeGreaterThan(
      0
    )
    expect(
      screen.getAllByText('reader-1', { exact: true }).length
    ).toBeGreaterThan(0)
  })

  it('defaults a single-target report to Overview', () => {
    renderReport()

    expect(
      screen
        .getByRole('tab', { name: 'Overview' })
        .getAttribute('aria-selected')
    ).toBe('true')
    expect(screen.getByText('Verdict')).toBeTruthy()
  })

  it('deep-links tab state and restores it with browser back', async () => {
    renderReport()

    selectInnerTab('Queries')
    expect(window.location.search).toContain('tab=queries')
    selectInnerTab('Savings')
    expect(window.location.search).toContain('tab=savings')

    window.history.back()
    await waitFor(() =>
      expect(
        screen
          .getByRole('tab', { name: 'Queries' })
          .getAttribute('aria-selected')
      ).toBe('true')
    )
  })

  function renderEmptyReport() {
    render(
      <FullAuditReportView
        report={{ target_name: 'empty-target' }}
        aiCredentialInvalid={false}
      />
    )
  }

  it('always renders every inner tab for an empty report', () => {
    renderEmptyReport()

    for (const tab of INNER_TAB_NAMES) {
      expect(screen.getByRole('tab', { name: tab })).toBeTruthy()
    }
  })

  it.each(
    EMPTY_TAB_MESSAGES
  )('states honestly that the %s tab has no data', (tab, message) => {
    renderEmptyReport()
    selectInnerTab(tab)
    expect(screen.getByText(message)).toBeTruthy()
  })
})
