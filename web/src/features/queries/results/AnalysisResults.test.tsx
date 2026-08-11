import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CompleteEvent } from '../../../lib/api'
import { AnalysisResults } from './AnalysisResults'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('AnalysisResults', () => {
  it('shows honest stage progress without a synthetic percentage', () => {
    vi.useFakeTimers()
    render(
      <AnalysisResults
        state="analyzing"
        progress={{
          type: 'progress',
          stage: 'checking_readyset',
          percent: 85,
          message: 'Checking Readyset cacheability...',
        }}
      />
    )

    act(() => {
      vi.advanceTimersByTime(120)
    })

    expect(screen.getByText('Analysis in progress')).toBeTruthy()
    expect(screen.getByText('Step 4 of 4')).toBeTruthy()
    expect(screen.getAllByText('Checking Readyset fit').length).toBeGreaterThan(
      0
    )
    expect(screen.queryByText('85%')).toBeNull()
  })

  it('renders a prominent score, measured evidence, and one suggested action', () => {
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      query_hash: 'abc123',
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
          overall_rating: 'fair',
          efficiency_score: 65,
          primary_concerns: ['Scans more rows than it returns.'],
        },
        index_recommendations: [
          {
            table: 'posts',
            columns: ['posttypeid'],
            index_type: 'btree',
            estimated_impact: 'high',
            rationale: 'Avoids scanning unrelated rows.',
            sql: 'create index on posts(posttypeid)',
            caveats: [],
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
    }

    render(<AnalysisResults state="complete" results={results} target="demo" />)

    expect(
      screen.getByLabelText('Performance score 65 out of 100')
    ).toBeTruthy()
    expect(screen.getAllByText('Fair performance').length).toBeGreaterThan(0)
    expect(screen.getByText('81,169 → 6')).toBeTruthy()
    expect(screen.getByText('Key finding')).toBeTruthy()
    expect(screen.getByText('Recommended next step')).toBeTruthy()
    expect(screen.getByText('Suggested')).toBeTruthy()
    expect(screen.getByText('Copy SQL')).toBeTruthy()
    expect(screen.getByText('Readyset compatibility')).toBeTruthy()
    expect(screen.getByText('Readyset can cache this query')).toBeTruthy()
  })

  it('renders the verified cache setup state without an index recommendation', () => {
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      explain_results: {
        success: true,
        database_engine: 'postgresql',
        execution_time_ms: 12.5,
        rows_examined: 20,
        rows_returned: 20,
        cost_estimate: 18,
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
    }

    render(
      <AnalysisResults
        state="complete"
        results={results}
        onSetUpCaching={() => undefined}
      />
    )

    expect(screen.getByText('Verified action')).toBeTruthy()
    expect(screen.getAllByText('Ready for cache setup')).toHaveLength(2)
    expect(screen.getByText('Set up caching…')).toBeTruthy()
    expect(screen.getByText('Ready to set up')).toBeTruthy()
  })

  it('renders distinct unverified and measured-only states', () => {
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      explain_results: {
        success: true,
        database_engine: 'postgresql',
        execution_time_ms: 12.5,
        rows_examined: 20,
        rows_returned: 20,
        cost_estimate: 18,
      },
      llm_analysis: {
        success: false,
      },
      readyset_cacheability: {
        checked: false,
        cacheable: false,
        confidence: 'unknown',
        method: 'readyset_unavailable',
        explanation: 'Readyset connection timed out.',
        detail: 'connection refused on 127.0.0.1',
        issues: [],
        warnings: [],
      },
    }

    render(<AnalysisResults state="complete" results={results} />)

    expect(screen.getByText('No score')).toBeTruthy()
    expect(screen.getByText('Model assessment unavailable')).toBeTruthy()
    expect(screen.getByText('Verification status')).toBeTruthy()
    expect(screen.getByText('Verification needs attention')).toBeTruthy()
    expect(screen.getByText('Verification incomplete')).toBeTruthy()
  })

  it('keeps Readyset blockers compact and visible', () => {
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      explain_results: {
        success: true,
        database_engine: 'postgresql',
        execution_time_ms: 12.5,
        rows_examined: 20,
        rows_returned: 20,
        cost_estimate: 18,
      },
      readyset_cacheability: {
        checked: true,
        cacheable: false,
        confidence: 'high',
        method: 'readyset_explain',
        explanation: 'This query uses an unsupported construct.',
        issues: ['Window functions are not currently supported.'],
        warnings: [],
      },
    }

    render(<AnalysisResults state="complete" results={results} />)

    expect(screen.getByText('1 blocker')).toBeTruthy()
    expect(screen.getByText('Changes required')).toBeTruthy()
    expect(
      screen.getByText('Window functions are not currently supported.')
    ).toBeTruthy()
    expect(screen.getByText('No caching action')).toBeTruthy()
  })

  it('presents static screening as evidence rather than verification', () => {
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      explain_results: {
        success: true,
        database_engine: 'postgresql',
        execution_time_ms: 12.5,
        rows_examined: 20,
        rows_returned: 20,
        cost_estimate: 18,
      },
      readyset_cacheability: {
        checked: true,
        cacheable: true,
        confidence: 'high',
        method: 'static_analysis',
        explanation: 'This query appears cacheable by Readyset.',
        issues: [],
        warnings: [],
      },
    }

    render(<AnalysisResults state="complete" results={results} />)

    expect(screen.getByText('No obvious Readyset blockers')).toBeTruthy()
    expect(screen.getAllByText('Static check').length).toBeGreaterThan(0)
    expect(screen.getByText('Readyset verification required')).toBeTruthy()
    expect(screen.getByText('Static check only')).toBeTruthy()
    expect(screen.queryByText('Verification incomplete')).toBeNull()
  })
})
