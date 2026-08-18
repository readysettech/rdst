import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { useStableQueryList } from './useStableQueryList'

function query(hash: string, isNew = false): QueryRegistryEntry {
  return { hash, sql: `select '${hash}'`, is_new: isNew } as QueryRegistryEntry
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

  it('splits the pending count by lifecycle state', () => {
    const { result, rerender } = renderHook(
      ({ queries }) =>
        useStableQueryList({ queries, signature: 'all', isLoading: false }),
      { initialProps: { queries: [query('existing')] } }
    )

    rerender({
      queries: [query('fresh', true), query('duplicate'), query('existing')],
    })

    expect(result.current.pendingCount).toBe(2)
    expect(result.current.pendingNewCount).toBe(1)
    expect(result.current.pendingUpdatedCount).toBe(1)

    act(() => result.current.revealPending())

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'fresh',
      'duplicate',
      'existing',
    ])
    expect(result.current.pendingCount).toBe(0)
  })

  it('counts a pending row without lifecycle-new status as updated', () => {
    const { result, rerender } = renderHook(
      ({ queries }) =>
        useStableQueryList({ queries, signature: 'all', isLoading: false }),
      { initialProps: { queries: [query('existing')] } }
    )

    rerender({ queries: [query('duplicate'), query('existing')] })

    expect(result.current.pendingNewCount).toBe(0)
    expect(result.current.pendingUpdatedCount).toBe(1)

    act(() => result.current.revealPending())

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'duplicate',
      'existing',
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

  it('adopts the full settled list after a signature change served from placeholder rows', () => {
    const { result, rerender } = renderHook(
      ({ queries, signature, isPlaceholder }) =>
        useStableQueryList({
          queries,
          signature,
          isLoading: false,
          isPlaceholder,
        }),
      {
        initialProps: {
          queries: [query('one')],
          signature: 'saved',
          isPlaceholder: false,
        },
      }
    )

    // The view changes while the previous request's rows are still shown as
    // a placeholder for continuity.
    rerender({
      queries: [query('one')],
      signature: 'all',
      isPlaceholder: true,
    })
    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'one',
    ])
    expect(result.current.pendingCount).toBe(0)

    // The settled rows for the new signature render in full; they were
    // revealed by the user's own view change, not discovered.
    rerender({
      queries: [query('two'), query('three'), query('one')],
      signature: 'all',
      isPlaceholder: false,
    })
    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'two',
      'three',
      'one',
    ])
    expect(result.current.pendingCount).toBe(0)
  })

  it('resets the reveal gate on a signature change with settled rows', () => {
    const { result, rerender } = renderHook(
      ({ queries, signature }) =>
        useStableQueryList({ queries, signature, isLoading: false }),
      { initialProps: { queries: [query('one')], signature: 'saved' } }
    )

    rerender({
      queries: [query('two'), query('one')],
      signature: 'all',
    })

    expect(result.current.visibleQueries.map((entry) => entry.hash)).toEqual([
      'two',
      'one',
    ])
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
