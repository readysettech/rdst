import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

const mocks = vi.hoisted(() => ({
  useQueryRegistry: vi.fn(),
}))

vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo' }),
}))
vi.mock('../../../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({
    data: { targets: [{ name: 'demo' }, { name: 'other' }] },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}))
vi.mock('../../../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: (target: string | null) => ({
    isResolved: true,
    isLocked: false,
    targetName: target,
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
vi.mock('../../../lib/useAutoSuggestParameters', () => ({
  useAutoSuggestParameters: () => undefined,
}))
vi.mock('../../../lib/useQueryRegistry', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/useQueryRegistry')>()),
  useQueryRegistry: mocks.useQueryRegistry,
}))
vi.mock('../../../lib/api', () => ({
  fetchSchema: vi.fn().mockResolvedValue({ tables: {} }),
  fetchTargets: vi.fn().mockResolvedValue([]),
  updateQueryParameters: vi.fn(),
}))
vi.mock('../../../lib/sse', () => ({
  useBenchmark: () => ({
    start: vi.fn(),
    stop: vi.fn(),
    state: 'idle',
    stage: undefined,
    message: undefined,
    progress: undefined,
    timeline: [],
    request: undefined,
    status: undefined,
    error: undefined,
    reset: vi.fn(),
  }),
}))

import { useLoadTestController } from './useLoadTestController'

function entry(
  hash: string,
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    hash,
    sql: `SELECT * FROM t_${hash}`,
    tag: '',
    source: 'top',
    target: 'demo',
    frequency: 1,
    last_analyzed: '',
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
  return renderHook(() => useLoadTestController({}), { wrapper })
}

describe('Load Test picker target scoping', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('asks the registry for queries scoped to the selected destination target', () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))

    renderController()

    // Mirrors Compare's useQueryRegistry(limit, target) scoping: the picker
    // must not fetch the unscoped, cross-target registry.
    expect(mocks.useQueryRegistry).toHaveBeenLastCalledWith(undefined, 'demo')
  })

  it('re-scopes the picker when the destination database changes', () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))

    const { result } = renderController()

    act(() => {
      result.current.handleDestinationChange('other')
    })

    expect(mocks.useQueryRegistry).toHaveBeenLastCalledWith(undefined, 'other')
  })

  it('surfaces an empty query list for a target with no queries', () => {
    mocks.useQueryRegistry.mockReturnValue(registry([]))

    const { result } = renderController()

    expect(result.current.queries).toEqual([])
    expect(result.current.filteredQueries).toEqual([])
  })
})
