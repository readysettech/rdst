import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import type { CacheRunResult } from '../../../types/cache'
import { SavedQueryDetails } from './SavedQueryDetails'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function stubLatestAnalysis(
  analysis: {
    overall_rating?: string
    efficiency_score?: number | null
  } | null
) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () =>
      analysis
        ? {
            found: true,
            analysis: {
              analysis_id: 'a-1',
              analyzed_at: '2026-08-18T08:00:00Z',
              target: 'imdb',
              overall_rating: analysis.overall_rating ?? '',
              efficiency_score: analysis.efficiency_score ?? null,
            },
          }
        : { found: false, analysis: null },
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

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
    renderWithClient(
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
    renderWithClient(
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

    renderWithClient(
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

  it('shows the last analysis with its outcome and a view action for analyzed entries', async () => {
    const fetchMock = stubLatestAnalysis({
      overall_rating: 'good',
      efficiency_score: 82,
    })
    const onViewAnalysis = vi.fn()

    renderWithClient(
      <SavedQueryDetails
        entry={{
          ...entry,
          last_analyzed_at: '2026-08-18T08:00:00Z',
          analysis_count: 3,
        }}
        cacheTestRun={undefined}
        onDismissRun={vi.fn()}
        onClose={vi.fn()}
        onViewAnalysis={onViewAnalysis}
      />
    )

    expect(screen.getByText('Last analysis')).toBeTruthy()
    expect(screen.getByText(/3 analyses/)).toBeTruthy()
    expect(await screen.findByText('Good · 82/100')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/query-registry/abc123456789/analysis/latest'
    )

    fireEvent.click(screen.getByRole('button', { name: 'View analysis' }))
    expect(onViewAnalysis).toHaveBeenCalledOnce()
  })

  it('keeps the last-analysis row without an outcome when no summary is stored', async () => {
    const fetchMock = stubLatestAnalysis(null)

    renderWithClient(
      <SavedQueryDetails
        entry={{ ...entry, last_analyzed_at: '2026-08-18T08:00:00Z' }}
        cacheTestRun={undefined}
        onDismissRun={vi.fn()}
        onClose={vi.fn()}
        onViewAnalysis={vi.fn()}
      />
    )

    expect(screen.getByText('Last analysis')).toBeTruthy()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    expect(screen.queryByText(/\/100/)).toBeNull()
  })

  it('hides the last-analysis row for entries that were never analyzed', () => {
    const fetchMock = stubLatestAnalysis(null)

    renderWithClient(
      <SavedQueryDetails
        entry={entry}
        cacheTestRun={undefined}
        onDismissRun={vi.fn()}
        onClose={vi.fn()}
        onViewAnalysis={vi.fn()}
      />
    )

    expect(screen.queryByText('Last analysis')).toBeNull()
    expect(screen.queryByRole('button', { name: 'View analysis' })).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('separates deleting a test result from closing details', () => {
    const onDismissRun = vi.fn()
    const onClose = vi.fn()

    renderWithClient(
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
