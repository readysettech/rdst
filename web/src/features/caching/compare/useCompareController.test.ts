import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

const mocks = vi.hoisted(() => ({
  useQueryRegistry: vi.fn(),
  queueSandboxPrewarm: vi.fn().mockResolvedValue(undefined),
  fetchSandboxDiagnostics: vi
    .fn()
    .mockResolvedValue({ docker_installed: true, docker_running: true }),
}))

vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo' }),
}))
vi.mock('../../../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isResolved: true,
    isLocked: false,
    targetName: 'demo',
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  }),
}))
vi.mock('../../../lib/useTargetConnectivityGate', () => ({
  useTargetConnectivityGate: () => ({
    isChecking: false,
    failure: null,
    ensureReachable: vi.fn().mockResolvedValue(true),
    reset: vi.fn(),
  }),
}))
vi.mock('../../../lib/useQueryRegistry', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/useQueryRegistry')>()),
  useQueryRegistry: mocks.useQueryRegistry,
}))
vi.mock('../../../lib/backgroundRuns', () => ({
  useBackgroundRuns: () => [],
}))
vi.mock('../../../lib/useAutoSuggestParameters', () => ({
  useAutoSuggestParameters: () => undefined,
}))
vi.mock('../sandbox', () => ({
  fetchSandboxDiagnostics: mocks.fetchSandboxDiagnostics,
  queueSandboxPrewarm: mocks.queueSandboxPrewarm,
}))
vi.mock('./compareRuns', () => ({
  cancelCompareBatch: vi.fn(),
  clearActiveCompareBatch: vi.fn(),
  compareBatchSnapshot: vi.fn(),
  deriveCompareOutcomeReport: vi.fn(),
  forgetCompareBatch: vi.fn(),
  latestCompareBatch: () => null,
  listCompareBatches: () => [],
  selectCompareBatch: vi.fn(),
  settleCompareBatch: vi.fn(),
  startCompareBatch: vi.fn(),
  updateCompareBatchLoad: vi.fn(),
}))

import {
  DEFAULT_COMPARE_DURATION,
  initialCompareConcurrency,
  MAX_COMPARE_CONCURRENCY,
  useCompareController,
} from './useCompareController'

function entry(
  hash: string,
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    hash,
    sql: `SELECT * FROM t_${hash}`,
    tag: '',
    source: 'top-historical',
    target: 'demo',
    frequency: 1,
    last_analyzed: '',
    is_new: false,
    ...overrides,
  }
}

function registry(queries: QueryRegistryEntry[]) {
  return {
    queries,
    isLoading: false,
    isFetching: false,
    total: queries.length,
    listError: null,
    refetch: vi.fn(),
    limit: 250,
    offset: 0,
    setLimit: vi.fn(),
    setOffset: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    resetPagination: vi.fn(),
    addQuery: vi.fn(),
    addMutation: { mutate: vi.fn(), isPending: false },
    removeQuery: vi.fn(),
    markReviewedMutation: { mutate: vi.fn(), mutateAsync: vi.fn() },
    updateTag: vi.fn(),
    updateSqlMutation: { mutate: vi.fn(), isPending: false },
    importMutation: { mutate: vi.fn(), isPending: false, data: undefined },
  }
}

function renderController() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  return renderHook(() => useCompareController(), { wrapper })
}

describe('Compare picker query ordering', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('leads with the highest-impact query rather than the registry API order', () => {
    // Last-analyzed/API order deliberately puts the low-impact query first,
    // so a passthrough (rather than an impact sort) would fail this.
    const low = entry('low-impact', {
      avg_duration_ms: 5,
      observation_count: 1,
    })
    const high = entry('high-impact', {
      avg_duration_ms: 500,
      observation_count: 100,
    })
    mocks.useQueryRegistry.mockReturnValue(registry([low, high]))

    const { result } = renderController()

    expect(result.current.queries.map((q) => q.hash)).toEqual([
      'high-impact',
      'low-impact',
    ])
  })
})

describe('safe Compare load profile', () => {
  it.each([
    [0, 2],
    [1, 2],
    [2, 2],
    [3, 3],
    [4, 4],
    [8, 4],
  ])('allocates at least one worker across %i queries', (queries, clients) => {
    expect(initialCompareConcurrency(queries)).toBe(clients)
  })

  it('keeps the automatic run inside the measured device-safe envelope', () => {
    expect(MAX_COMPARE_CONCURRENCY).toBe(4)
    expect(DEFAULT_COMPARE_DURATION).toBe(30)
  })
})

describe('sandbox prewarm on Compare setup', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('stays quiet on a machine that cannot run a sandbox', async () => {
    mocks.useQueryRegistry.mockReturnValue(registry([]))
    mocks.fetchSandboxDiagnostics.mockResolvedValueOnce({
      docker_installed: false,
      docker_running: false,
    })

    renderController()

    // Without Docker the page offers "Check again" instead of a run, so asking
    // the backend to warm a sandbox can only produce a failed request.
    await vi.waitFor(() =>
      expect(mocks.fetchSandboxDiagnostics).toHaveBeenCalled()
    )
    expect(mocks.queueSandboxPrewarm).not.toHaveBeenCalled()
  })

  it('queues a prewarm for the current target as soon as Docker is ready', async () => {
    mocks.useQueryRegistry.mockReturnValue(registry([]))

    const { rerender } = renderController()

    await vi.waitFor(() =>
      expect(mocks.queueSandboxPrewarm).toHaveBeenCalledTimes(1)
    )
    expect(mocks.queueSandboxPrewarm).toHaveBeenCalledWith('demo')

    // A re-render for the same target (e.g. the registry refetching) must
    // not re-queue the prewarm -- the whole point is a single head start.
    rerender()
    expect(mocks.queueSandboxPrewarm).toHaveBeenCalledTimes(1)
  })
})
