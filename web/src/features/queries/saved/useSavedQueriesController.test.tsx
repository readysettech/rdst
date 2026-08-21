import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { useSavedQueriesController } from './useSavedQueriesController'

const mocks = vi.hoisted(() => ({
  markReviewed: vi.fn(),
  useQueryRegistry: vi.fn(),
  navigate: vi.fn(),
  startCacheTestRun: vi.fn(),
  starQuery: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo' }),
}))
vi.mock('../../../lib/backgroundRuns', () => ({
  dismissBackgroundRun: vi.fn(),
  startCacheTestRun: mocks.startCacheTestRun,
  useBackgroundRuns: () => [],
}))
vi.mock('../../../lib/useQueryRegistry', () => ({
  useQueryRegistry: mocks.useQueryRegistry,
  useStarQuery: () => ({ mutate: mocks.starQuery }),
}))

function entry(hash: string): QueryRegistryEntry {
  return {
    hash,
    sql: 'SELECT * FROM users',
    tag: 'Users',
    source: 'top-historical',
    target: 'demo',
    frequency: 1,
    last_analyzed: '',
    is_new: true,
  }
}

const ownRegistry = {
  queries: [] as QueryRegistryEntry[],
  total: 0,
  offset: 0,
  resetPagination: vi.fn(),
  addMutation: { mutate: vi.fn(), isPending: false },
  updateSqlMutation: { mutate: vi.fn(), isPending: false },
  importMutation: {
    mutate: vi.fn(),
    isPending: false,
    data: undefined,
    reset: vi.fn(),
  },
  markReviewedMutation: {
    mutate: mocks.markReviewed,
    mutateAsync: vi.fn(),
  },
  updateTag: vi.fn(),
  removeQuery: vi.fn(),
}

function list(queries: QueryRegistryEntry[]) {
  return {
    queries,
    total: queries.length,
    isLoading: queries.length === 0,
    isFetching: queries.length === 0,
    listError: null,
    refetch: vi.fn().mockResolvedValue(undefined),
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  mocks.useQueryRegistry.mockReturnValue(ownRegistry)
})

afterEach(() => {
  vi.useRealTimers()
  document.body.replaceChildren()
})

describe('useSavedQueriesController deep links', () => {
  it('waits for a paged query before reviewing, scrolling, and consuming the link', () => {
    const onDeepLinkConsumed = vi.fn()
    const linked = entry('far-page-hash')
    const { rerender } = renderHook(
      ({ queries }) =>
        useSavedQueriesController({
          deepLinkHash: linked.hash,
          onDeepLinkConsumed,
          list: list(queries),
        }),
      { initialProps: { queries: [] as QueryRegistryEntry[] } }
    )

    act(() => vi.advanceTimersByTime(5_000))
    expect(mocks.markReviewed).not.toHaveBeenCalled()
    expect(onDeepLinkConsumed).not.toHaveBeenCalled()

    const card = document.createElement('div')
    card.dataset.queryHash = linked.hash
    card.scrollIntoView = vi.fn()
    document.body.append(card)

    rerender({ queries: [linked] })
    expect(mocks.markReviewed).toHaveBeenCalledWith(
      { hash: linked.hash, target: 'demo' },
      expect.any(Object)
    )

    act(() => vi.advanceTimersByTime(1_700))
    expect(card.scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'center',
    })
    expect(onDeepLinkConsumed).toHaveBeenCalledWith(linked.hash)
  })

  it('releases the link when an SQL edit rewrites the linked hash', () => {
    const onDeepLinkConsumed = vi.fn()
    const linked = entry('linked-hash')
    ownRegistry.updateSqlMutation.mutate.mockImplementation(
      (_variables, options) =>
        (
          options as {
            onSuccess: (result: {
              success: boolean
              hash: string
              hash_changed: boolean
            }) => void
          }
        ).onSuccess({
          success: true,
          hash: 'rewritten-hash',
          hash_changed: true,
        })
    )
    const { result } = renderHook(() =>
      useSavedQueriesController({
        deepLinkHash: linked.hash,
        onDeepLinkConsumed,
        list: list([linked]),
      })
    )

    act(() => {
      result.current.rowActions.startEditSql(linked.hash, 'SELECT 1')
    })
    act(() => {
      result.current.rowActions.saveSql(linked.hash)
    })

    // The read model narrows to the linked hash while the link is pending;
    // rewriting that hash must hand the link back so the list can show the
    // query under its new identity.
    expect(onDeepLinkConsumed).toHaveBeenCalledWith(linked.hash)
    expect(result.current.rowState.highlightedHash).toBe('rewritten-hash')
  })
})

describe('useSavedQueriesController inline comparison', () => {
  it('starts the run and expands the card without navigating', async () => {
    mocks.startCacheTestRun.mockResolvedValue('run-1')
    const target = entry('inline-hash')
    const { result } = renderHook(() =>
      useSavedQueriesController({ list: list([target]) })
    )

    await act(async () => {
      result.current.rowActions.cacheQuery(target.hash, target.sql)
      await Promise.resolve()
    })

    expect(mocks.startCacheTestRun).toHaveBeenCalledWith(
      expect.objectContaining({ query_hash: target.hash })
    )
    expect(result.current.rowState.expandedHash).toBe(target.hash)
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('leaves URL state untouched when the run fails to start', async () => {
    mocks.startCacheTestRun.mockResolvedValue(null)
    const target = entry('inline-hash')
    const { result } = renderHook(() =>
      useSavedQueriesController({ list: list([target]) })
    )

    await act(async () => {
      result.current.rowActions.cacheQuery(target.hash, target.sql)
      await Promise.resolve()
    })

    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})
