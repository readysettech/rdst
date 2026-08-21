import { cleanup, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import type { QueryRegistryEntry } from '../../../lib/api'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import type { CacheRunResult } from '../../../types/cache'
import { AnalyzeDrawerOverview } from './AnalyzeDrawerOverview'

const mocks = vi.hoisted(() => ({
  localRun: undefined as BackgroundRunState | undefined,
}))

vi.mock('../results/useStoredAnalysis', () => ({
  useAnalysisHistory: () => ({ entries: [], isLoading: false }),
}))
vi.mock('../../../lib/backgroundRuns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/backgroundRuns')>()),
  useCacheTestRunForQuery: () => mocks.localRun,
}))

const entry = {
  hash: 'h1',
  sql: 'SELECT * FROM orders',
  tag: 'Orders lookup',
  source: 'observed',
  target: 'demo',
  frequency: 12,
  observation_count: 1_200,
  avg_duration_ms: 180,
  max_duration_ms: 420,
  last_analyzed: '',
} as QueryRegistryEntry

const localResult: CacheRunResult = {
  success: true,
  query: 'SELECT * FROM orders',
  iterations: 15,
  origin_stats: {
    mean: 180,
    median: 175,
    min: 150,
    max: 260,
    p50: 175,
    p95: 240,
    p99: 260,
  },
  cache_stats: { mean: 2, median: 2, min: 1, max: 5, p50: 2, p95: 4, p99: 5 },
  speedup_mean: 90,
  speedup_median: 87.5,
  improvement_pct: 8_900,
  winner: 'readyset',
}

function localRun(result?: CacheRunResult): BackgroundRunState {
  return {
    runId: 'cache_test_demo_1',
    kind: 'cache_test',
    target: 'demo',
    stage: 'complete',
    status: 'done',
    message: 'Performance test complete',
    lastSeq: 5,
    current: 15,
    total: 15,
    hasWarnings: false,
    queryHash: 'h1',
    result,
  }
}

function renderOverview(row: QueryRegistryEntry = entry) {
  return renderWithClient(
    <AnalyzeDrawerOverview
      entry={row}
      target="demo"
      onOpenAnalysis={vi.fn()}
      onAnalyzeAgain={vi.fn()}
    />
  )
}

afterEach(() => {
  cleanup()
  mocks.localRun = undefined
})

describe('AnalyzeDrawerOverview', () => {
  it('leads with the workload behind the query', () => {
    renderOverview()

    expect(screen.getByText('Evidence')).toBeTruthy()
    expect(screen.getByText('Observed runs')).toBeTruthy()
    expect(screen.getByText('1,200')).toBeTruthy()
    expect(screen.getByText('Avg latency')).toBeTruthy()
    expect(screen.getByText('Max latency')).toBeTruthy()
  })

  it('shows the full latency profile when this browser measured one', () => {
    mocks.localRun = localRun(localResult)
    renderOverview({
      ...entry,
      last_compare: {
        status: 'ok',
        at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
        readyset_ms: 4,
        origin_ms: 180,
      },
    } as QueryRegistryEntry)

    expect(screen.getByText('Comparison')).toBeTruthy()
    expect(screen.getByText('faster with Readyset')).toBeTruthy()
    expect(screen.getAllByText('P95')).toHaveLength(2)
    expect(screen.getByText('15 samples each')).toBeTruthy()
    // The device-local profile replaces the server's coarser record; the two
    // never stack up as if they were separate comparisons.
    expect(screen.queryByText('45.0x faster')).toBeNull()
  })

  it('falls back to the recorded outcome, with the time it was recorded', () => {
    renderOverview({
      ...entry,
      last_compare: {
        status: 'ok',
        at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
        readyset_ms: 4,
        origin_ms: 180,
      },
    } as QueryRegistryEntry)

    expect(screen.getByText('45.0x faster')).toBeTruthy()
    expect(screen.getByText('3 hours ago')).toBeTruthy()
    expect(screen.queryByText('P95')).toBeNull()
  })

  it('omits the comparison entirely when nothing has been compared', () => {
    renderOverview()

    expect(screen.queryByText('Comparison')).toBeNull()
  })

  it('keeps a partial local result from posing as a profile', () => {
    mocks.localRun = localRun({ speedup_mean: 90 } as unknown as CacheRunResult)
    renderOverview()

    expect(screen.queryByText('Comparison')).toBeNull()
  })
})
