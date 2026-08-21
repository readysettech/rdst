import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry, QueryRegistryReadModelPage } from './api'
import { queryRegistryQueryKey, useStarQuery } from './useQueryRegistry'

const mocks = vi.hoisted(() => ({ setQueryStarred: vi.fn() }))

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  setQueryStarred: mocks.setQueryStarred,
}))

function row(hash: string, starred = false): QueryRegistryEntry {
  return {
    hash,
    sql: `SELECT * FROM ${hash}`,
    tag: '',
    source: 'manual',
    target: 'demo',
    frequency: 0,
    last_analyzed: '',
    starred,
  }
}

function page(hashes: string[]): QueryRegistryReadModelPage {
  return {
    queries: hashes.map((hash) => row(hash)),
    facet_counts: {
      view: {},
      source: {},
      params: {},
      activity: {},
      impact: {},
    },
    next_cursor: null,
    total: hashes.length,
    freshness: null,
    error: null,
  }
}

let queryClient: QueryClient

const listKey = [...queryRegistryQueryKey('demo'), 'read-model'] as const

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function cachedStar(hash: string) {
  const data = queryClient.getQueryData<{
    pages: QueryRegistryReadModelPage[]
  }>(listKey)
  return data?.pages[0].queries.find((entry) => entry.hash === hash)?.starred
}

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  queryClient.setQueryData(listKey, {
    pages: [page(['a', 'b'])],
    pageParams: [null],
  })
  mocks.setQueryStarred.mockReset()
})

describe('useStarQuery', () => {
  it('flips the cached row before the request answers', async () => {
    let resolve: (value: unknown) => void = () => undefined
    mocks.setQueryStarred.mockReturnValue(
      new Promise((done) => {
        resolve = done
      })
    )
    const { result } = renderHook(() => useStarQuery('demo'), { wrapper })

    act(() => result.current.mutate({ hash: 'a', starred: true }))

    await waitFor(() => expect(cachedStar('a')).toBe(true))
    expect(cachedStar('b')).toBe(false)
    expect(mocks.setQueryStarred).toHaveBeenCalledWith('a', true, 'demo')

    act(() =>
      resolve({ hash: 'a', target: 'demo', starred: true, starred_at: 'now' })
    )
    await waitFor(() => expect(result.current.isPending).toBe(false))
  })

  it('puts the rows back exactly as they were when the request fails', async () => {
    mocks.setQueryStarred.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useStarQuery('demo'), { wrapper })

    act(() => result.current.mutate({ hash: 'a', starred: true }))

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(cachedStar('a')).toBe(false)
    expect(cachedStar('b')).toBe(false)
  })
})
