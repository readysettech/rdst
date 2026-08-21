import { describe, expect, it } from 'vitest'
import { storedAnalysisResults } from './analysisPayload'
import type { StoredAnalysis } from './api'

function stored(analysis: StoredAnalysis['analysis']): StoredAnalysis {
  return {
    hash: 'query-hash',
    analysis_id: 'an-1',
    created_at: '2026-08-21T06:00:00Z',
    target: 'imdb',
    overall_rating: 'good',
    efficiency_score: 82,
    analysis,
  }
}

describe('storedAnalysisResults', () => {
  it('replays a stored display payload as the results view own complete event', () => {
    const replayed = storedAnalysisResults(
      stored({
        display_payload: {
          explain_results: {
            success: true,
            database_engine: 'postgresql',
            execution_time_ms: 49.44,
            rows_examined: 81_169,
            rows_returned: 6,
            cost_estimate: 33_384.48,
          },
          llm_analysis: {
            success: true,
            performance_assessment: {
              overall_rating: 'good',
              efficiency_score: 82,
              primary_concerns: [],
            },
          },
          readyset_cacheability: {
            checked: true,
            cacheable: true,
            confidence: 'high',
            method: 'readyset_explain',
            explanation: 'Readyset verified this query.',
          },
        },
      })
    )

    expect(replayed.hasBody).toBe(true)
    expect(replayed.results?.type).toBe('complete')
    expect(replayed.results?.success).toBe(true)
    // The hash the record was read under wins, so the viewer's chat and
    // caching handoffs address the right query.
    expect(replayed.results?.query_hash).toBe('query-hash')
    expect(replayed.results?.explain_results?.execution_time_ms).toBe(49.44)
    expect(replayed.readysetCacheability?.cacheable).toBe(true)
  })

  it('normalizes rewrite testing the same way the live stream does', () => {
    const replayed = storedAnalysisResults(
      stored({
        display_payload: {
          rewrite_testing: {
            success: true,
            rewrite_results: [{ success: true }],
          } as never,
        },
      })
    )

    expect(replayed.rewriteTesting?.tested).toBe(true)
  })

  it('reports no body for analyses stored before results were kept', () => {
    expect(storedAnalysisResults(stored({ display_payload: {} })).hasBody).toBe(
      false
    )
    expect(storedAnalysisResults(stored({})).hasBody).toBe(false)
    expect(
      storedAnalysisResults(stored({ display_payload: null })).results
    ).toBeUndefined()
  })
})
