// Hard rule (DESIGN_V4): wherever a deep analysis is embedded (the query row and the
// daily snapshot's query rows), it renders the full AnalysisResults with every section. The
// query row leads with the score and next step and keeps the rest one click away, in Details.
import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompleteEvent, RewriteTesting } from '@/lib/api'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { FIXTURE_NOW, reportFixture } from '@/lib/monitor/fixtures'
import type { MonitorQuery } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'
import { AnalysisTab } from '../queries/AnalysisTab'
import { DaySnapshot } from './DaySnapshot'

const INDEX_A = 'CREATE INDEX ON orders (customer_id, status)'
const INDEX_B = 'CREATE INDEX ON orders (placed_at)'

const results: CompleteEvent = {
  type: 'complete',
  success: true,
  query_hash: 'abc123',
  explain_results: {
    success: true,
    database_engine: 'postgresql',
    execution_time_ms: 49.4,
    rows_examined: 81_169,
    rows_returned: 6,
    cost_estimate: 33_384,
    explain_plan: {
      Plan: { 'Node Type': 'Seq Scan', 'Relation Name': 'orders' },
    },
  },
  llm_analysis: {
    success: true,
    performance_assessment: {
      overall_rating: 'fair',
      efficiency_score: 65,
      primary_concerns: ['Scans more rows than it returns.'],
    },
    index_recommendations: [
      {
        table: 'orders',
        columns: ['customer_id', 'status'],
        index_type: 'btree',
        estimated_impact: 'high',
        rationale: 'Filters on both columns.',
        sql: INDEX_A,
        caveats: [],
      },
      {
        table: 'orders',
        columns: ['placed_at'],
        index_type: 'btree',
        estimated_impact: 'low',
        rationale: 'Supports the sort.',
        sql: INDEX_B,
        caveats: [],
      },
    ],
    optimization_opportunities: [
      {
        description: 'Select only the columns the page shows.',
        priority: 'medium',
        type: 'projection',
      },
    ],
  },
  index_testing: {
    tested: true,
    method: 'hypopg',
    results: [
      {
        index_sql: INDEX_A,
        planner_used_index: true,
        scan_type: 'Bitmap Index Scan',
        cost_before: 49_586,
        cost_after: 35.8,
        cost_reduction_pct: 99.9,
      },
      {
        index_sql: INDEX_B,
        planner_used_index: false,
        cost_before: 49_586,
        cost_after: 49_586,
        cost_reduction_pct: 0,
      },
    ],
  },
  readyset_cacheability: {
    checked: true,
    cacheable: true,
    confidence: 'high',
    method: 'readyset_explain',
    explanation: 'Readyset verified this query.',
    issues: [],
    warnings: [],
  },
} as CompleteEvent

const rewriteTesting: RewriteTesting = {
  tested: true,
  original_performance: { execution_time_ms: 49.4, rows_returned: 6 },
  rewrite_results: [
    {
      success: true,
      sql: 'SELECT id FROM orders WHERE status = $1',
      performance: { execution_time_ms: 20, rows_returned: 6 },
      improvement: { overall: { improvement_pct: 59 } },
      suggestion_metadata: { explanation: 'Reads fewer columns.' },
    },
  ],
} as RewriteTesting

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return { ...actual, useNavigate: () => vi.fn() }
})

vi.mock('@/features/queries/results/useStoredAnalysis', () => ({
  useStoredAnalysis: () => ({
    isActive: true,
    isLoading: false,
    error: null,
    hasBody: true,
    results,
    rewriteTesting,
    readysetCacheability: results.readyset_cacheability,
    record: null,
    history: [],
  }),
}))

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})
afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

function expectEverySection() {
  expect(screen.getByLabelText('Performance score 65 out of 100')).toBeTruthy()
  expect(screen.getByRole('button', { name: /View EXPLAIN plan/ })).toBeTruthy()
  expect(screen.getAllByText('Tested optimizations').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Index recommendations').length).toBeGreaterThan(0)
  expect(screen.getAllByText(/hypopg check/).length).toBeGreaterThan(0)
  expect(
    screen.getAllByText(/Additional recommendations|More recommendations/)
      .length
  ).toBeGreaterThan(0)
  expect(screen.getAllByText(/Readyset/).length).toBeGreaterThan(0)
}

describe('embedded deep analysis keeps every section', () => {
  it('in the query row', async () => {
    const query = {
      key: 'abc123',
      text: 'SELECT * FROM orders WHERE customer_id = $1 AND status = $2',
      text_status: 'ok',
      tables: ['orders'],
      state: [],
      last_analysis: {
        state: 'done',
        finished_at: FIXTURE_NOW - 3600,
        analysis_ref: '/api/query-registry/abc123/analysis/an_1',
        mode: 'analyze',
        param_source: 'sampled',
        reason: null,
      },
    } as unknown as MonitorQuery
    renderWithClient(
      <AnalysisTab
        query={query}
        target="localdev"
        onCompare={() => undefined}
      />
    )
    expect(
      await screen.findByLabelText('Performance score 65 out of 100')
    ).toBeTruthy()
    expect(screen.queryByText('Tested optimizations')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Details/ }))
    expectEverySection()
    expect(
      screen.getByText('Measured with values from live traffic.')
    ).toBeTruthy()
  })

  it('in a daily snapshot row, expanding in place', async () => {
    const report = reportFixture('localdev', 'latest', FIXTURE_NOW)!
    const snapshot = report.report_json!.snapshot!
    renderWithClient(
      <DaySnapshot
        report={report}
        snapshot={snapshot}
        target="localdev"
        days={[]}
        onPick={() => undefined}
      />
    )
    const analyzed = snapshot.look_at.find((query) => query.analysis_ref)!
    const row = screen
      .getAllByTestId('look-at-row')
      .find((node) =>
        node.querySelector(`[data-query-key="${analyzed.query_key}"]`)
      ) as HTMLElement
    fireEvent.click(
      row.querySelector('[aria-label="Show the full analysis"]') as Element
    )
    expect(
      await screen.findByLabelText('Performance score 65 out of 100')
    ).toBeTruthy()
    expectEverySection()
  })
})
