import { describe, expect, it } from 'vitest'
import type { CompleteEvent, RewriteTesting } from '../../../lib/api'
import {
  getReadysetVerdict,
  getScoreTone,
  selectResultsViewModel,
} from './resultsSelectors'

function completeEvent(overrides: Partial<CompleteEvent> = {}): CompleteEvent {
  return {
    type: 'complete',
    success: true,
    explain_results: {
      success: true,
      database_engine: 'postgresql',
      execution_time_ms: 49.44,
      rows_examined: 81_169,
      rows_returned: 6,
      cost_estimate: 33_384.48,
    },
    ...overrides,
  }
}

describe('selectResultsViewModel', () => {
  it('keeps the score prominent while preferring a measured rewrite', () => {
    const testing: RewriteTesting = {
      tested: true,
      original_performance: {
        execution_time_ms: 50,
        rows_returned: 6,
      },
      rewrite_results: [
        {
          success: true,
          sql: 'select 1',
          performance: {
            execution_time_ms: 25,
            rows_returned: 6,
          },
          improvement: { overall: { improvement_pct: 50 } },
          suggestion_metadata: { explanation: 'Uses less database work.' },
        },
      ],
    }
    const results = completeEvent({
      llm_analysis: {
        success: true,
        performance_assessment: {
          overall_rating: 'fair',
          efficiency_score: 65,
          primary_concerns: ['Scans more rows than it returns.'],
        },
      },
    })

    const view = selectResultsViewModel(results, testing)

    expect(view.performance).toMatchObject({
      score: 65,
      rating: 'fair',
    })
    expect(view.nextStep).toMatchObject({
      kind: 'rewrite',
      evidence: 'Tested',
      sql: 'select 1',
    })
  })

  it('does not promote a rewrite that changes the result row count', () => {
    const testing: RewriteTesting = {
      tested: true,
      original_performance: {
        execution_time_ms: 50,
        rows_returned: 6,
      },
      best_rewrite: {
        success: true,
        sql: 'select 1',
        performance: {
          execution_time_ms: 10,
          rows_returned: 1,
        },
        improvement: { overall: { improvement_pct: 80 } },
        suggestion_metadata: { explanation: 'Changes the result.' },
      },
    }
    const results = completeEvent({
      llm_analysis: {
        success: true,
        index_recommendations: [
          {
            table: 'posts',
            columns: ['posttypeid'],
            index_type: 'btree',
            estimated_impact: 'high',
            rationale: 'Avoids the full scan.',
            sql: 'create index on posts(posttypeid)',
            caveats: ['Adds write overhead.'],
          },
        ],
      },
    })

    const view = selectResultsViewModel(results, testing)

    expect(view.nextStep).toMatchObject({
      kind: 'index',
      evidence: 'Suggested',
      caveats: ['Adds write overhead.'],
    })
  })

  it('suppresses an unknown zero score in partial results', () => {
    const results = completeEvent({
      llm_analysis: {
        success: false,
      },
    })

    const view = selectResultsViewModel(results)

    expect(view.performance).toBeUndefined()
    expect(view.hasModelAnalysis).toBe(false)
  })

  it('does not recommend cache setup for a query that is already cached', () => {
    const results = completeEvent({
      readyset_cacheability: {
        checked: true,
        cacheable: true,
        confidence: 'high',
        method: 'readyset_explain',
        explanation: 'Query is already cached in Readyset (query_id: q_123)',
        issues: [],
        warnings: [],
      },
    })

    const view = selectResultsViewModel(results)

    expect(view.nextStep).toMatchObject({
      kind: 'none',
      title: 'No cache change needed',
    })
  })

  it('promotes cache setup only after Readyset verifies an uncached query', () => {
    const results = completeEvent({
      readyset_cacheability: {
        checked: true,
        cacheable: true,
        confidence: 'high',
        method: 'readyset_explain',
        explanation: 'Readyset verified this query.',
        issues: [],
        warnings: [],
      },
    })

    const view = selectResultsViewModel(results)

    expect(view.nextStep).toMatchObject({
      kind: 'readyset',
      evidence: 'Verified',
    })
  })
})

describe('getScoreTone', () => {
  it('maps score ranges to semantic performance tones', () => {
    expect(getScoreTone(80)).toBe('positive')
    expect(getScoreTone(70)).toBe('informative')
    expect(getScoreTone(65)).toBe('warning')
    expect(getScoreTone(49)).toBe('negative')
    expect(getScoreTone()).toBe('informative')
  })
})

describe('getReadysetVerdict', () => {
  it('distinguishes an existing cache from a merely cacheable query', () => {
    const verdict = getReadysetVerdict({
      checked: true,
      cacheable: true,
      confidence: 'high',
      method: 'readyset_explain',
      explanation: 'Query is already cached in Readyset (query_id: q_123)',
      issues: [],
      warnings: [],
    })

    expect(verdict).toMatchObject({
      verified: true,
      cacheable: true,
      alreadyCached: true,
      title: 'Cached by Readyset',
      tag: 'Cached',
    })
  })

  it('keeps an unavailable check distinct from a not-cacheable verdict', () => {
    const verdict = getReadysetVerdict({
      checked: false,
      cacheable: false,
      confidence: 'unknown',
      method: 'readyset_unavailable',
      explanation: 'Readyset connection timed out.',
      detail: 'connection refused on 127.0.0.1',
      issues: [],
      warnings: [],
    })

    expect(verdict).toMatchObject({
      verified: false,
      cacheable: false,
      title: 'Cacheability not verified',
      tag: 'Not verified',
    })
    expect(verdict.technicalDetail).toContain('connection refused')
  })

  it('keeps a positive static screen distinct from Readyset verification', () => {
    const verdict = getReadysetVerdict({
      checked: true,
      cacheable: true,
      confidence: 'high',
      method: 'static_analysis',
      explanation: 'This query appears cacheable by Readyset.',
      issues: [],
      warnings: [],
    })

    expect(verdict).toMatchObject({
      verified: false,
      estimated: true,
      cacheable: false,
      title: 'No obvious Readyset blockers',
      tag: 'Static check',
    })
    expect(verdict.body).toContain('Verify this query with Readyset')
  })

  it('gives a not-cacheable result a useful fallback explanation', () => {
    const verdict = getReadysetVerdict({
      checked: true,
      cacheable: false,
      confidence: 'high',
      method: 'readyset_explain',
      issues: [],
      warnings: [],
    })

    expect(verdict).toMatchObject({
      verified: true,
      cacheable: false,
      body: 'The query needs a compatibility change before Readyset can cache it.',
    })
  })
})
