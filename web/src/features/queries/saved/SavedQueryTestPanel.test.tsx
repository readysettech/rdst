import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import type { CacheRunResult } from '../../../types/cache'
import { reportsCacheTestRun, SavedQueryTestPanel } from './SavedQueryTestPanel'

afterEach(cleanup)

function run(overrides: Partial<BackgroundRunState> = {}): BackgroundRunState {
  return {
    runId: 'cache_test_imdb_1',
    kind: 'cache_test',
    target: 'imdb',
    stage: 'complete',
    status: 'done',
    message: 'Performance test complete',
    lastSeq: 4,
    current: 100,
    total: 100,
    hasWarnings: false,
    queryHash: 'abc123456789',
    ...overrides,
  }
}

const completeResult: CacheRunResult = {
  success: true,
  query: 'SELECT 1',
  iterations: 15,
  origin_stats: {
    mean: 20,
    median: 19,
    min: 15,
    max: 30,
    p50: 19,
    p95: 28,
    p99: 30,
  },
  cache_stats: {
    mean: 1,
    median: 1,
    min: 0.5,
    max: 2,
    p50: 1,
    p95: 1.8,
    p99: 2,
  },
  speedup_mean: 20,
  speedup_median: 19,
  improvement_pct: 1_900,
  winner: 'readyset',
}

describe('reportsCacheTestRun', () => {
  it('reports a genuinely live run regardless of whether it was just seen', () => {
    expect(reportsCacheTestRun(run({ status: 'running' }))).toBe(true)
    expect(reportsCacheTestRun(run({ status: 'reconnecting' }))).toBe(true)
  })

  it('reports a terminal run only once this tab watched it get there', () => {
    expect(reportsCacheTestRun(run({ status: 'failed', seenLive: true }))).toBe(
      true
    )
    expect(reportsCacheTestRun(run({ status: 'done', seenLive: true }))).toBe(
      true
    )
  })

  it('stays quiet for a completed run reattached from a past session', () => {
    // Persisted storage never carries `seenLive`, so a result from before
    // this tab existed reads as quiet rather than reopening on every reload.
    expect(reportsCacheTestRun(run({ status: 'done' }))).toBe(false)
    expect(reportsCacheTestRun(run({ status: 'failed' }))).toBe(false)
    expect(reportsCacheTestRun(run({ status: 'partial' }))).toBe(false)
  })

  it('stays quiet without a run, once acknowledged, or once cancelled', () => {
    expect(reportsCacheTestRun(undefined)).toBe(false)
    expect(reportsCacheTestRun(run({ hidden: true, seenLive: true }))).toBe(
      false
    )
    expect(
      reportsCacheTestRun(run({ status: 'cancelled', seenLive: true }))
    ).toBe(false)
  })
})

describe('SavedQueryTestPanel', () => {
  it('reports progress while the test runs', () => {
    render(
      <SavedQueryTestPanel
        run={run({ status: 'running', message: 'Measuring upstream' })}
        onDismissRun={vi.fn()}
      />
    )

    expect(screen.getByText('Testing in the background')).toBeTruthy()
    expect(screen.getByText('Measuring upstream')).toBeTruthy()
  })

  it('says so when a finished run carries no usable result', () => {
    render(
      <SavedQueryTestPanel
        run={run({ result: { speedup_mean: 20 } as unknown as CacheRunResult })}
        onDismissRun={vi.fn()}
      />
    )

    expect(
      screen.getAllByText(
        'Performance result unavailable. Run the test again to refresh it.'
      )
    ).not.toHaveLength(0)
  })

  it('separates deleting the result from stopping the report', () => {
    const onDismissRun = vi.fn()
    const onClose = vi.fn()

    render(
      <SavedQueryTestPanel
        run={run({ result: completeResult })}
        onDismissRun={onDismissRun}
        onClose={onClose}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Delete result' }))
    expect(onDismissRun).toHaveBeenCalledWith('cache_test_imdb_1')
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Close details' }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(onDismissRun).toHaveBeenCalledOnce()
  })
})
