import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryReadModelPage } from './api'
import { QueryRegistryCursorError } from './api'
import {
  QUERY_REGISTRY_READ_MODEL_PAGE_SIZE,
  type QueryRegistryReadModelSpec,
  queryRegistryQueryKey,
  useQueryRegistryReadModel,
} from './useQueryRegistry'

const mocks = vi.hoisted(() => ({
  fetchReadModel: vi.fn(),
}))

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  fetchQueryRegistryReadModel: mocks.fetchReadModel,
}))

function page(
  hashes: string[],
  nextCursor: string | null,
  total: number
): QueryRegistryReadModelPage {
  return {
    queries: hashes.map((hash) => ({
      hash,
      sql: `SELECT * FROM ${hash}`,
      tag: '',
      source: 'manual',
      target: 'demo',
      frequency: 0,
      last_analyzed: '',
    })),
    facet_counts: {
      view: { all: total },
      source: {},
      params: {},
      activity: {},
      impact: {},
    },
    next_cursor: nextCursor,
    total,
    freshness: null,
    error: null,
  }
}

function spec(
  overrides: Partial<QueryRegistryReadModelSpec> = {}
): QueryRegistryReadModelSpec {
  return {
    search: '',
    view: 'all',
    source: 'all',
    params: 'all',
    activity: 'all',
    impact: 'all',
    sort: 'highest-impact',
    starred: false,
    ...overrides,
  }
}

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  queryClient = new QueryClient()
  mocks.fetchReadModel.mockReset()
})

describe('useQueryRegistryReadModel', () => {
  it('fetches the first page and appends the next on demand', async () => {
    mocks.fetchReadModel.mockResolvedValueOnce(page(['a', 'b'], 'c1', 3))
    const { result } = renderHook(
      () => useQueryRegistryReadModel(spec(), 'demo'),
      { wrapper }
    )

    await waitFor(() => expect(result.current.queries).toHaveLength(2))
    expect(mocks.fetchReadModel).toHaveBeenCalledWith({
      target: 'demo',
      search: '',
      view: 'all',
      source: 'all',
      params: 'all',
      activity: 'all',
      impact: 'all',
      sort: 'highest-impact',
      starred: false,
      limit: QUERY_REGISTRY_READ_MODEL_PAGE_SIZE,
      cursor: undefined,
    })
    expect(result.current.total).toBe(3)
    expect(result.current.hasNextPage).toBe(true)

    mocks.fetchReadModel.mockResolvedValueOnce(page(['c'], null, 3))
    act(() => void result.current.fetchNextPage())

    await waitFor(() =>
      expect(result.current.queries.map((entry) => entry.hash)).toEqual([
        'a',
        'b',
        'c',
      ])
    )
    expect(mocks.fetchReadModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 'c1' })
    )
    expect(result.current.hasNextPage).toBe(false)
    expect(result.current.pageCount).toBe(2)
  })

  it('stores pages under the target-scoped registry key prefix', async () => {
    mocks.fetchReadModel.mockResolvedValueOnce(page(['a'], null, 1))
    const { result } = renderHook(
      () => useQueryRegistryReadModel(spec(), 'demo'),
      { wrapper }
    )

    await waitFor(() => expect(result.current.queries).toHaveLength(1))
    const stored = queryClient.getQueriesData({
      queryKey: queryRegistryQueryKey('demo'),
    })
    expect(stored).toHaveLength(1)
  })

  it('starts over without a cursor when the spec changes', async () => {
    mocks.fetchReadModel.mockResolvedValueOnce(page(['a', 'b'], 'c1', 3))
    const { result, rerender } = renderHook(
      ({ view }: { view: string }) =>
        useQueryRegistryReadModel(spec({ view }), 'demo'),
      { wrapper, initialProps: { view: 'all' } }
    )
    await waitFor(() => expect(result.current.queries).toHaveLength(2))
    mocks.fetchReadModel.mockResolvedValueOnce(page(['c'], 'c2', 3))
    act(() => void result.current.fetchNextPage())
    await waitFor(() => expect(result.current.pageCount).toBe(2))

    mocks.fetchReadModel.mockResolvedValueOnce(page(['n'], null, 1))
    rerender({ view: 'new' })

    await waitFor(() =>
      expect(result.current.queries.map((entry) => entry.hash)).toEqual(['n'])
    )
    expect(mocks.fetchReadModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ view: 'new', cursor: undefined })
    )
    expect(result.current.pageCount).toBe(1)
  })

  it('silently restarts from page one when the cursor is invalid', async () => {
    mocks.fetchReadModel.mockResolvedValueOnce(page(['a', 'b'], 'c1', 4))
    const { result } = renderHook(
      () => useQueryRegistryReadModel(spec(), 'demo'),
      { wrapper }
    )
    await waitFor(() => expect(result.current.queries).toHaveLength(2))

    mocks.fetchReadModel.mockRejectedValueOnce(new QueryRegistryCursorError())
    mocks.fetchReadModel.mockResolvedValueOnce(page(['a2', 'b2'], 'c2', 4))
    act(() => void result.current.fetchNextPage())

    await waitFor(() =>
      expect(result.current.queries.map((entry) => entry.hash)).toEqual([
        'a2',
        'b2',
      ])
    )
    expect(mocks.fetchReadModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: undefined })
    )
    expect(result.current.listError).toBeNull()
    expect(result.current.pageCount).toBe(1)
  })

  it('deduplicates rows that shift across page boundaries', async () => {
    mocks.fetchReadModel.mockResolvedValueOnce(page(['a', 'b'], 'c1', 3))
    const { result } = renderHook(
      () => useQueryRegistryReadModel(spec(), 'demo'),
      { wrapper }
    )
    await waitFor(() => expect(result.current.queries).toHaveLength(2))

    mocks.fetchReadModel.mockResolvedValueOnce(page(['b', 'c'], null, 3))
    act(() => void result.current.fetchNextPage())

    await waitFor(() =>
      expect(result.current.queries.map((entry) => entry.hash)).toEqual([
        'a',
        'b',
        'c',
      ])
    )
  })
})
