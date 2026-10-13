import { describe, expect, it } from 'vitest'
import type { CompleteEvent } from '@/lib/api'
import { CA_KEYS, CA_TARGET, caFixtures } from '@/lib/monitor/fixturesCa'
import type { DeepAnalysisDigest } from '@/lib/monitor/types'
import {
  paramSourceText,
  planLines,
  plannerSetup,
  rewriteRange,
  scanWord,
  speedup,
  stepForStage,
  viewFromDigest,
  viewFromResults,
} from './analysisView'

const digest = caFixtures('ca-database').queryAnalysis(
  CA_TARGET,
  CA_KEYS.postsByOwneruserid
) as DeepAnalysisDigest

describe('viewFromDigest', () => {
  it('says where the parameter values came from in words', () => {
    expect(paramSourceText('sampled')).toBe('from sampled values')
    expect(paramSourceText('table_sampled')).toBe('from table samples')
    expect(paramSourceText('none')).toBe('no values')
    expect(paramSourceText('sampled from table data')).toBe(
      'sampled from table data'
    )
    expect(paramSourceText(null)).toBeNull()
    expect(
      viewFromDigest({ ...digest, param_source: 'table_sampled' }).param_source
    ).toBe('from table samples')
  })

  it('passes the contract fields through', () => {
    const view = viewFromDigest(digest)
    expect(view.score).toBe(72)
    expect(view.verdict?.lead).toBe('One index removes the scan and the sort.')
    expect(view.fix_detail?.cost_after).toBe(41)
    expect(view.plan_findings).toHaveLength(3)
    expect(view.rewrites.map((rewrite) => rewrite.outcome)).toEqual([
      'no_change',
      'no_change',
    ])
    expect(view.run?.id).toBe('412')
    expect(view.params).toHaveLength(2)
  })

  it('falls back to the digest header when the view fields are absent', () => {
    const header: DeepAnalysisDigest = {
      query_key: 'k',
      analysis_ref: null,
      analyzed_at: null,
      mode: 'explain',
      mode_label: null,
      rating: 'fair',
      efficiency_score: 55,
      plan_summary: {
        text: '',
        root_node: 'Limit',
        execution_ms: null,
        rows_examined: 100,
        rows_returned: 5,
      },
      findings: ['Sequential scan on orders'],
      fix: {
        kind: 'index',
        title: 'Add index orders (customer_id)',
        sql: 'CREATE INDEX ON orders (customer_id)',
        why: 'Serves the filter.',
        expected_gain: null,
      },
      other_fixes: [],
      headline: 'Needs an index on orders',
    }
    const view = viewFromDigest(header)
    expect(view.score).toBe(55)
    expect(view.verdict).toEqual({
      lead: 'Needs an index on orders.',
      rest: 'Serves the filter.',
    })
    expect(view.fix_detail?.sql).toBe('CREATE INDEX ON orders (customer_id)')
    expect(view.plan_findings[0].title).toBe('Sequential scan on orders')
    expect(view.rows_read).toBe(100)
  })
})

describe('viewFromResults', () => {
  const results: CompleteEvent = {
    type: 'complete',
    success: true,
    analysis_id: 'an-3',
    explain_results: {
      success: true,
      database_engine: 'postgresql',
      plan_format: 'explain',
      execution_time_ms: 0,
      rows_examined: 1000,
      rows_returned: 10,
      cost_estimate: 250,
      explain_plan: { Plan: { 'Node Type': 'Seq Scan' } },
    },
    index_testing: {
      tested: true,
      results: [
        {
          index_sql: 'CREATE INDEX ON orders (customer_id)',
          planner_used_index: true,
          cost_before: 250,
          cost_after: 8,
        },
      ],
    },
    llm_analysis: {
      success: true,
      llm_model: 'claude',
      token_usage: {
        input: 1000,
        output: 500,
        total: 1500,
        estimated_cost_usd: 0,
      },
      performance_assessment: {
        overall_rating: 'fair',
        efficiency_score: 60,
        primary_concerns: ['Scans every row.', 'Sorts in memory.'],
      },
      index_recommendations: [
        {
          table: 'orders',
          columns: ['customer_id'],
          index_type: 'btree',
          sql: 'CREATE INDEX ON orders (customer_id)',
          rationale: 'Serves the filter. Keeps the sort.',
          estimated_impact: 'high',
        },
      ],
      optimization_opportunities: [
        {
          type: 'projection',
          priority: 'low',
          description: 'Select fewer columns',
        },
      ],
      rewrite_suggestions: [
        {
          rewritten_sql: 'select 2',
          explanation: 'Filter before sort',
          expected_improvement: 'small',
          optimization_type: 'rewrite',
          priority: 'low',
        },
      ],
    },
    readyset_cacheability: { checked: true, cacheable: true, issues: [] },
  }

  it('maps the payload into the view', () => {
    const view = viewFromResults(results)
    expect(view.score).toBe(60)
    expect(view.verdict).toEqual({
      lead: 'Serves the filter.',
      rest: 'Scans every row.',
    })
    expect(view.fix_detail).toMatchObject({
      sql: 'CREATE INDEX ON orders (customer_id)',
      cost_before: 250,
      cost_after: 8,
    })
    expect(view.plan_findings.map((finding) => finding.title)).toEqual([
      'Scans every row.',
      'Sorts in memory.',
    ])
    expect(view.rewrites).toEqual([
      {
        name: 'Filter before sort',
        before_ms: null,
        after_ms: null,
        runs: null,
        outcome: 'untested',
        sql: 'select 2',
      },
    ])
    expect(view.also[0].title).toBe('Select fewer columns')
    expect(view.readyset).toEqual({ blockers: [], verified: true })
    expect(view.run).toEqual({
      model: 'claude',
      tokens: 1500,
      seconds: null,
      id: 'an-3',
    })
    expect(view.rows_read).toBe(1000)
    expect(view.planner_cost).toBe(250)
    expect(view.param_source).toBeNull()
    expect(view.explain_text).toContain('Seq Scan')
  })

  it('grades tested rewrites by their measured change', () => {
    const view = viewFromResults(results, {
      tested: true,
      original_performance: { execution_time_ms: 100 },
      rewrite_results: [
        {
          sql: 'select 2',
          success: true,
          performance: { execution_time_ms: 40 },
          improvement: { overall: { improvement_pct: 60 } },
          suggestion_metadata: { explanation: 'Filter before sort' },
        },
        {
          sql: 'select 3',
          success: true,
          performance: { execution_time_ms: 101 },
          improvement: { overall: { improvement_pct: -1 } },
          suggestion_metadata: { explanation: 'Lateral join' },
        },
      ],
    })
    expect(view.rewrites.map((rewrite) => rewrite.outcome)).toEqual([
      'faster',
      'no_change',
    ])
    expect(rewriteRange(view.rewrites[0])).toBe('100.0 to 40.0 ms')
    expect(view.rewrites.map((rewrite) => rewrite.sql)).toEqual([
      'select 2',
      'select 3',
    ])
  })

  it('names the values a stored analysis ran with and where they came from', () => {
    const stored = {
      ...results,
      parameters: [{ name: '$1', value: '661' }],
      param_source: 'sampled',
    } as CompleteEvent
    const view = viewFromResults(stored)
    expect(view.params).toEqual([{ name: '$1', value: '661' }])
    expect(view.param_source).toBe('from sampled values')
    const estimated = viewFromResults({
      ...results,
      explain_results: results.explain_results && {
        ...results.explain_results,
        param_source: 'estimated',
      },
    })
    expect(estimated.param_source).toBe('with estimated values (plan only)')
  })

  it('shows the values typed for this run, and nothing for a query without any', () => {
    const typed = viewFromResults(results, undefined, undefined, { '1': 42 })
    expect(typed.params).toEqual([{ name: '1', value: '42' }])
    expect(typed.param_source).toBe('with your values')
    const measured = viewFromResults({
      ...results,
      explain_results: results.explain_results && {
        ...results.explain_results,
        plan_format: 'analyze',
      },
    })
    expect(measured.params).toEqual([])
    expect(measured.param_source).toBeNull()
  })

  it('does not count a static Readyset screen as verified', () => {
    for (const method of ['static_analysis', 'readyset_unavailable']) {
      const view = viewFromResults({
        ...results,
        readyset_cacheability: {
          checked: true,
          cacheable: true,
          issues: [],
          method,
        },
      })
      expect(view.readyset).toEqual({ blockers: [], verified: false })
    }
    const verified = viewFromResults({
      ...results,
      readyset_cacheability: {
        checked: true,
        cacheable: true,
        issues: [],
        method: 'readyset_container',
      },
    })
    expect(verified.readyset?.verified).toBe(true)
  })

  const channel = {
    table: 'orders',
    columns: ['channel'],
    index_type: 'btree',
    sql: 'CREATE INDEX idx_orders_channel ON orders (channel)',
    rationale: 'Speculative index on channel.',
    estimated_impact: 'high' as const,
  }
  const composite = {
    table: 'orders',
    columns: ['customer_id', 'status'],
    index_type: 'btree',
    sql: 'CREATE INDEX idx_orders_customer_status ON orders (customer_id, status)',
    rationale: 'Covers the customer and status filters.',
    estimated_impact: 'medium' as const,
  }
  const withIndexes = (usedComposite: boolean): CompleteEvent => ({
    ...results,
    llm_analysis: results.llm_analysis && {
      ...results.llm_analysis,
      index_recommendations: [channel, composite],
    },
    index_testing: {
      tested: true,
      results: [
        {
          index_sql: channel.sql,
          planner_used_index: false,
          cost_before: 49586,
          cost_after: 49586,
          cost_reduction_pct: 0,
        },
        {
          index_sql: composite.sql,
          planner_used_index: usedComposite,
          cost_before: 49586,
          cost_after: 35.8,
          cost_reduction_pct: 99.9,
        },
      ],
    },
  })

  it('promotes the index the planner uses, with its cost', () => {
    const view = viewFromResults(withIndexes(true))
    expect(view.fix_detail).toMatchObject({
      sql: composite.sql,
      cost_before: 49586,
      cost_after: 35.8,
    })
    expect(view.verdict?.lead).toBe('Covers the customer and status filters.')
  })

  it('keeps every other index under Also worth doing, with its SQL', () => {
    const view = viewFromResults(withIndexes(true))
    expect(view.also[0]).toEqual({
      title: 'Add an index on orders(channel)',
      detail: 'Speculative index on channel.',
      sql: channel.sql,
    })
    expect(view.also[1].title).toBe('Select fewer columns')
  })

  it('keeps the first index without a planner line when the planner uses none', () => {
    const view = viewFromResults(withIndexes(false))
    expect(view.fix_detail).toMatchObject({
      sql: channel.sql,
      cost_before: null,
      cost_after: null,
    })
  })
})

describe('plannerSetup', () => {
  it('names the install statement when hypopg is missing', () => {
    expect(
      plannerSetup({
        tested: false,
        skipped_reason: 'hypopg_not_installed',
        message: 'Enable hypopg.',
        install_sql: 'CREATE EXTENSION IF NOT EXISTS hypopg;',
      })
    ).toEqual({ installSql: 'CREATE EXTENSION IF NOT EXISTS hypopg;' })
    expect(plannerSetup({ tested: true, results: [] })).toBeNull()
    expect(
      plannerSetup({ tested: false, skipped_reason: 'hypopg_not_installed' })
    ).toBeNull()
    expect(plannerSetup(null)).toBeNull()
  })
})

describe('the words', () => {
  it('counts sequential scans and marks the hot node', () => {
    expect(scanWord('Seq Scan on posts')).toBe('one seq scan')
    expect(scanWord('Seq Scan on a\nSeq Scan on b')).toBe('two seq scans')
    expect(scanWord('Index Scan')).toBeNull()
    const lines = planLines('Limit\n  Seq Scan on posts\n  Seq Scan on users')
    expect(lines.map((line) => line.hot)).toEqual([false, true, false])
  })

  it('reads a benchmark as a speedup', () => {
    expect(speedup(65.4, 1.9)).toEqual({ value: '34x', unit: 'faster' })
    expect(speedup(10, 20)).toEqual({ value: '2.0x', unit: 'slower' })
    expect(speedup(null, 1)).toBeNull()
  })

  it('places a run stage on the four steps', () => {
    expect(stepForStage('executing_explain')).toBe(0)
    expect(stepForStage('collecting_schema')).toBe(1)
    expect(stepForStage('testing_rewrites')).toBe(2)
    expect(stepForStage('storing_results')).toBe(3)
  })
})
