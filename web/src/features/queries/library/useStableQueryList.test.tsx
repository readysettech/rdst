import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { useStableQueryList } from './useStableQueryList'

function query(hash: string): QueryRegistryEntry {
  return { hash, sql: `select '${hash}'` } as QueryRegistryEntry
}

describe('useStableQueryList', () => {
  it('holds newly discovered rows until they are explicitly revealed', () => {
    const { result, rerender } = renderHook(
      ({ queries }) =>
        useStableQueryList({ queries, signature: 'all', isLoading: false }),
      { initialProps: { queries: [query('one')] } }
    )

    rerender({ queries: [query('two'), query('one')] })

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'one',
    ])
    expect(result.current.pendingCount).toBe(1)

    act(() => result.current.revealPending())

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'two',
      'one',
    ])
  })

  it('keeps the row key and position stable when SQL editing changes its hash', () => {
    const { result, rerender } = renderHook(
      ({ queries, hashAliases }) =>
        useStableQueryList({
          queries,
          signature: 'all',
          isLoading: false,
          hashAliases,
        }),
      {
        initialProps: {
          queries: [query('old'), query('sibling')],
          hashAliases: {} as Record<string, string>,
        },
      }
    )

    rerender({
      queries: [query('new'), query('sibling')],
      hashAliases: { old: 'new' },
    })

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'new',
      'sibling',
    ])
    expect(result.current.keyForHash('new')).toBe('old')
    expect(result.current.pendingCount).toBe(0)
  })

  it('adopts the latest sorted order while explicitly revealing a query', () => {
    const { result, rerender } = renderHook(
      ({ queries, revealHash }) =>
        useStableQueryList({
          queries,
          signature: 'saved-newest',
          isLoading: false,
          revealHash,
        }),
      {
        initialProps: {
          queries: [query('old')],
          revealHash: undefined as string | undefined,
        },
      }
    )

    rerender({
      queries: [query('added'), query('old')],
      revealHash: 'added',
    })

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'added',
      'old',
    ])

    rerender({
      queries: [query('added'), query('old')],
      revealHash: undefined,
    })

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'added',
      'old',
    ])
  })
})
