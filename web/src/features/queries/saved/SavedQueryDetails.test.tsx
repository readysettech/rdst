import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import type { CacheRunResult } from '../../../types/cache'
import { SavedQueryDetails } from './SavedQueryDetails'

afterEach(cleanup)

const entry: QueryRegistryEntry = {
  hash: 'abc123456789',
  sql: 'SELECT 1',
  tag: 'Health check',
  target: 'imdb',
  source: 'manual',
  frequency: 1,
  last_analyzed: '2026-07-23T08:00:00Z',
}

function cacheTestRun(result?: CacheRunResult): BackgroundRunState {
  return {
    runId: 'cache_test_imdb_legacy',
    kind: 'cache_test',
    target: 'imdb',
    stage: 'complete',
    status: 'done',
    message: 'Performance test complete',
    lastSeq: 4,
    current: 100,
    total: 100,
    hasWarnings: false,
    queryHash: entry.hash,
    result,
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
  improvement_pct: 1900,
  winner: 'readyset',
}

describe('SavedQueryDetails', () => {
  it('renders query metadata without a linked performance run', () => {
    render(
      <SavedQueryDetails
        entry={entry}
        cacheTestRun={undefined}
        onDismissRun={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('abc12345')).toBeTruthy()
    expect(screen.queryByText(/Performance result unavailable/)).toBeNull()
  })

  it('can hide metadata when the surrounding detail rail owns it', () => {
    render(
      <SavedQueryDetails
        entry={entry}
        cacheTestRun={undefined}
        onDismissRun={vi.fn()}
        onClose={vi.fn()}
        showMetadata={false}
      />
    )

    expect(screen.queryByText('abc12345')).toBeNull()
  })

  it('keeps details usable when a stored performance result is incomplete', () => {
    const incompleteResult = {
      speedup_mean: 20,
      winner: 'readyset',
    } as unknown as CacheRunResult

    render(
      <SavedQueryDetails
        entry={entry}
        cacheTestRun={cacheTestRun(incompleteResult)}
        onDismissRun={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(
      screen.getAllByText(
        'Performance result unavailable. Run the test again to refresh it.'
      )
    ).not.toHaveLength(0)
    expect(screen.getByText('abc12345')).toBeTruthy()
  })

  it('separates deleting a test result from closing details', () => {
    const onDismissRun = vi.fn()
    const onClose = vi.fn()

    render(
      <SavedQueryDetails
        entry={entry}
        cacheTestRun={cacheTestRun(completeResult)}
        onDismissRun={onDismissRun}
        onClose={onClose}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Delete result' }))
    expect(onDismissRun).toHaveBeenCalledWith('cache_test_imdb_legacy')
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Close details' }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(onDismissRun).toHaveBeenCalledOnce()
  })
})
