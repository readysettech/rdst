import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

const mocks = vi.hoisted(() => ({
  useQueryRegistry: vi.fn(),
  fetchSandboxDiagnostics: vi.fn(),
  start: vi.fn(),
  targets: [{ name: 'demo' }, { name: 'other' }],
  fetchTargets: vi.fn().mockResolvedValue([]),
}))

vi.mock('../sandbox', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../sandbox')>()),
  fetchSandboxDiagnostics: mocks.fetchSandboxDiagnostics,
}))

vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo' }),
}))
vi.mock('../../../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({
    data: { targets: mocks.targets },
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
  fetchTargets: mocks.fetchTargets,
  updateQueryParameters: vi.fn(),
}))
vi.mock('../../../lib/sse', () => ({
  useBenchmark: () => ({
    start: mocks.start,
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

function renderController(
  options: Parameters<typeof useLoadTestController>[0] = {}
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  return renderHook(() => useLoadTestController(options), { wrapper })
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

describe('Load Test Readyset lane', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('arms the Readyset lane by default, so the run proves the fix', () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))
    mocks.fetchSandboxDiagnostics.mockResolvedValue({
      docker_installed: true,
      docker_running: true,
    })

    const { result } = renderController()

    expect(result.current.comparative).toBe(true)
    expect(result.current.confirmIncludesReadyset).toBe(true)
  })

  it('falls back to origin only where no sandbox can exist (D-11)', async () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))
    mocks.fetchSandboxDiagnostics.mockResolvedValue({
      docker_installed: false,
      docker_running: false,
    })

    const { result } = renderController()

    await waitFor(() => expect(result.current.dockerUnavailable).toBe(true))
    expect(result.current.comparative).toBe(false)
    // Nothing to warn about in the confirm dialog: the run is origin-only.
    expect(result.current.confirmIncludesReadyset).toBe(false)
  })

  it('follows the switch when a capacity-only run turns the lane off', () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))
    mocks.fetchSandboxDiagnostics.mockResolvedValue({
      docker_installed: true,
      docker_running: true,
    })

    const { result } = renderController()

    act(() => {
      result.current.setComparative(false)
    })
    expect(result.current.comparative).toBe(false)
    // Nothing to warn about in the confirm dialog: the run is origin-only.
    expect(result.current.confirmIncludesReadyset).toBe(false)

    act(() => {
      result.current.setComparative(true)
    })
    expect(result.current.confirmIncludesReadyset).toBe(true)
  })
})

describe('Merged benchmark run choices', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    mocks.targets = [{ name: 'demo' }, { name: 'other' }]
    mocks.fetchTargets.mockResolvedValue([])
  })

  it.each([
    ['source', ['origin']],
    ['both', ['origin', 'readyset']],
    ['readyset', ['readyset']],
  ] as const)('runs two selected queries with %s', async (choice, lanes) => {
    mocks.fetchTargets.mockResolvedValue([
      { name: 'demo' },
      { name: 'demo-cache' },
    ])
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1'), entry('q2')]))
    mocks.fetchSandboxDiagnostics.mockResolvedValue({
      docker_installed: true,
      docker_running: true,
    })
    const { result } = renderController({ keepFilteredSelection: true })
    act(() => {
      result.current.setSelectedQueries(['q1', 'q2'])
      result.current.setRunChoice(choice)
      result.current.setSearchTerm('t_q1')
    })
    await waitFor(() => expect(result.current.canStart).toBe(true))
    await act(async () => result.current.handleConfirmRun())
    expect(mocks.start).toHaveBeenCalledWith(
      expect.objectContaining({
        target: 'demo',
        queries: [{ identifier: 'q1' }, { identifier: 'q2' }],
        lanes,
      })
    )
    expect(result.current.destinationLock.targetName).toBe('demo')
  })

  it('runs Readyset only without a deployed cache when Docker works', async () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))
    mocks.fetchSandboxDiagnostics.mockResolvedValue({
      docker_installed: true,
      docker_running: true,
    })
    const { result } = renderController()
    act(() => {
      result.current.setSelectedQueries(['q1'])
      result.current.setRunChoice('readyset')
    })
    await waitFor(() => expect(result.current.canStart).toBe(true))
    await act(async () => result.current.handleConfirmRun())
    expect(result.current.destinationLock.targetName).toBe('demo')
    expect(mocks.start).toHaveBeenCalledWith(
      expect.objectContaining({ target: 'demo', lanes: ['readyset'] })
    )
  })

  it('runs a monitored query using its captured SQL and deduplicates saved queries', async () => {
    mocks.useQueryRegistry.mockReturnValue(registry([entry('q1')]))
    const { result } = renderController({
      extraQueries: [entry('q1'), entry('q2', { source: 'monitor-benchmark' })],
    })
    expect(result.current.queries).toHaveLength(2)
    act(() => {
      result.current.setSelectedQueries(['q2'])
      result.current.setRunChoice('source')
    })
    await waitFor(() => expect(result.current.canStart).toBe(true))
    await act(async () => result.current.handleConfirmRun())
    expect(mocks.start).toHaveBeenCalledWith(
      expect.objectContaining({
        queries: [{ identifier: 'q2', sql: 'SELECT * FROM t_q2' }],
        lanes: ['origin'],
      })
    )
  })
})
