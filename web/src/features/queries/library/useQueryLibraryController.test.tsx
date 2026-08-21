import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import type { QueryLibrarySearch } from './queryLibraryState'
import { useQueryLibraryController } from './useQueryLibraryController'

/** The callbacks the library hands the saved-queries controller. */
interface SavedQueriesOptions {
  onQueryAdded?: (hash?: string | null) => void
  onRevealLinked?: (hash: string) => void
}

const mocks = vi.hoisted(() => {
  const readModel = {
    queries: [] as QueryRegistryEntry[],
    total: 0,
    facetCounts: null as Record<string, Record<string, number>> | null,
    pageCount: 1,
    isLoading: false,
    isFetching: false,
    isPlaceholder: false,
    listError: null as string | null,
    refetch: vi.fn(),
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
  }
  const markReviewed = vi.fn()
  return {
    readModel,
    markReviewed,
    navigate: vi.fn(),
    useQueryRegistryReadModel: vi.fn(() => readModel),
    useSavedQueriesController: vi.fn((_options: SavedQueriesOptions) => ({
      target: 'demo',
      registry: { queries: readModel.queries, isLoading: false },
      rowState: { hashAliases: {} },
      rowActions: {
        isCached: () => false,
        markAllReviewed: vi.fn(),
        markReviewed,
      },
      addDialog: { openDialog: vi.fn(), closeDialog: vi.fn() },
    })),
  }
})

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo' }),
}))
vi.mock('../../../lib/useQueryDiscovery', () => ({
  useQueryDiscoverySnapshot: () => ({
    data: { state: 'watching', updated_at: '' },
  }),
}))
vi.mock('../../../lib/useQueryRegistry', () => ({
  useQueryRegistryReadModel: mocks.useQueryRegistryReadModel,
}))
vi.mock('../saved/useSavedQueriesController', () => ({
  useSavedQueriesController: mocks.useSavedQueriesController,
}))

function entry(hash: string, isNew = false): QueryRegistryEntry {
  return {
    hash,
    sql: `SELECT * FROM ${hash}`,
    tag: '',
    source: 'manual',
    target: 'demo',
    frequency: 0,
    last_analyzed: '',
    is_new: isNew,
  }
}

beforeEach(() => {
  mocks.navigate.mockClear()
  mocks.useQueryRegistryReadModel.mockClear()
  mocks.useSavedQueriesController.mockClear()
  mocks.readModel.queries = []
  mocks.readModel.total = 0
  mocks.readModel.facetCounts = null
  mocks.readModel.hasNextPage = false
  mocks.readModel.isPlaceholder = false
  mocks.readModel.fetchNextPage.mockClear()
  mocks.markReviewed.mockClear()
})

describe('useQueryLibraryController read model wiring', () => {
  it('maps URL search state onto read-model request params', () => {
    renderHook(() =>
      useQueryLibraryController({
        search: {
          q: 'orders',
          view: 'new',
          source: 'observed',
          params: 'values-ready',
          activity: '24h',
          impact: '1m',
          sort: 'newest',
        },
      })
    )

    expect(mocks.useQueryRegistryReadModel).toHaveBeenCalledWith(
      {
        search: 'orders',
        view: 'new',
        source: 'observed',
        params: 'values-ready',
        activity: '24h',
        impact: '1m',
        sort: 'newest',
        starred: false,
      },
      'demo'
    )
  })

  it('requests defaults when the URL carries no filters', () => {
    renderHook(() => useQueryLibraryController({ search: {} }))

    expect(mocks.useQueryRegistryReadModel).toHaveBeenCalledWith(
      {
        search: '',
        view: 'all',
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
        sort: 'highest-impact',
        starred: false,
      },
      'demo'
    )
  })

  it('uses a transient deep-link hash to fetch a query beyond the loaded page', () => {
    renderHook(() =>
      useQueryLibraryController({
        search: { q: 'orders', view: 'new', hash: 'far-page-hash' },
      })
    )

    expect(mocks.useQueryRegistryReadModel).toHaveBeenCalledWith(
      expect.objectContaining({
        search: 'far-page-hash',
        view: 'new',
      }),
      'demo'
    )
    expect(mocks.useSavedQueriesController).toHaveBeenCalledWith(
      expect.objectContaining({ deepLinkHash: 'far-page-hash' })
    )
  })

  it('sources facet counts and total from the server response', () => {
    mocks.readModel.facetCounts = {
      view: { all: 12, new: 3 },
      source: { observed: 9 },
      params: {},
      activity: {},
      impact: {},
    }
    mocks.readModel.total = 12

    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    const { facetCounts } = result.current.library.selection
    expect(facetCounts.view.all).toBe(12)
    expect(facetCounts.view.new).toBe(3)
    expect(facetCounts.source.observed).toBe(9)
    expect(facetCounts.source.manual).toBe(0)
    expect(result.current.library.total).toBe(12)
  })

  it('feeds the read-model rows into the saved-queries controller', () => {
    mocks.readModel.queries = [entry('a'), entry('b')]
    mocks.readModel.total = 2

    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    expect(mocks.useSavedQueriesController).toHaveBeenCalledWith(
      expect.objectContaining({
        list: expect.objectContaining({
          queries: mocks.readModel.queries,
          total: 2,
        }),
      })
    )
    expect(
      result.current.library.visibleQueries.map((query) => query.hash)
    ).toEqual(['a', 'b'])
  })

  it('resets the reveal gate when the view changes', () => {
    mocks.readModel.queries = [entry('a')]

    const { result, rerender } = renderHook(
      ({ search }: { search: QueryLibrarySearch }) =>
        useQueryLibraryController({ search }),
      { initialProps: { search: { view: 'new' } as QueryLibrarySearch } }
    )
    expect(
      result.current.library.visibleQueries.map((query) => query.hash)
    ).toEqual(['a'])

    mocks.readModel.queries = [entry('a'), entry('b'), entry('c')]
    rerender({ search: {} })

    expect(
      result.current.library.visibleQueries.map((query) => query.hash)
    ).toEqual(['a', 'b', 'c'])
    expect(result.current.library.pendingCount).toBe(0)
  })

  it('does not count placeholder rows shown during a view change as discovery', () => {
    mocks.readModel.queries = [entry('a')]

    const { result, rerender } = renderHook(
      ({ search }: { search: QueryLibrarySearch }) =>
        useQueryLibraryController({ search }),
      { initialProps: { search: { view: 'new' } as QueryLibrarySearch } }
    )

    // The view changes while the read model still serves the previous key's
    // rows as a placeholder.
    mocks.readModel.isPlaceholder = true
    rerender({ search: {} })
    expect(result.current.library.pendingCount).toBe(0)

    // The settled result for the new view replaces the placeholder wholesale.
    mocks.readModel.isPlaceholder = false
    mocks.readModel.queries = [entry('a'), entry('b'), entry('c')]
    rerender({ search: {} })

    expect(
      result.current.library.visibleQueries.map((query) => query.hash)
    ).toEqual(['a', 'b', 'c'])
    expect(result.current.library.pendingCount).toBe(0)
  })

  it('reveals a pending non-new row in place without changing filters', () => {
    mocks.readModel.queries = [entry('a')]

    const { result, rerender } = renderHook(
      ({ search }: { search: QueryLibrarySearch }) =>
        useQueryLibraryController({ search }),
      { initialProps: { search: {} as QueryLibrarySearch } }
    )

    // A duplicate identity arrives: no lifecycle-new status, so the New
    // view would not show it.
    mocks.readModel.queries = [entry('duplicate'), entry('a')]
    rerender({ search: {} })

    expect(result.current.library.pendingCount).toBe(1)
    expect(result.current.library.pendingNewCount).toBe(0)
    expect(result.current.library.pendingUpdatedCount).toBe(1)

    act(() => result.current.library.revealPending())

    expect(
      result.current.library.visibleQueries.map((query) => query.hash)
    ).toEqual(['duplicate', 'a'])
    expect(mocks.useQueryRegistryReadModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ view: 'all' }),
      'demo'
    )
  })

  it('reveals a pending lifecycle-new row under the current filter', () => {
    mocks.readModel.queries = [entry('a')]

    const { result, rerender } = renderHook(
      ({ search }: { search: QueryLibrarySearch }) =>
        useQueryLibraryController({ search }),
      { initialProps: { search: { view: 'all' } as QueryLibrarySearch } }
    )

    mocks.readModel.queries = [entry('fresh', true), entry('a')]
    rerender({ search: { view: 'all' } })

    expect(result.current.library.pendingNewCount).toBe(1)
    expect(result.current.library.pendingUpdatedCount).toBe(0)

    act(() => result.current.library.revealPending())

    expect(
      result.current.library.visibleQueries.map((query) => query.hash)
    ).toEqual(['fresh', 'a'])
    expect(mocks.useQueryRegistryReadModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ view: 'all' }),
      'demo'
    )
  })

  it('exposes the paging affordance', () => {
    mocks.readModel.hasNextPage = true

    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    expect(result.current.library.hasNextPage).toBe(true)
    result.current.library.loadMore()
    expect(mocks.readModel.fetchNextPage).toHaveBeenCalledOnce()
  })
})

describe('Queries Display: default and persistence', () => {
  it('defaults to Card 2 when no Display preference is saved', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    expect(result.current.library.displayMode).toBe('card-2')
  })

  it('persists a Display mode change and restores it on the next mount', () => {
    const { result, unmount } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() => result.current.library.setDisplayMode('card-1'))
    expect(
      JSON.parse(localStorage.getItem('rdst-queries-display') ?? '{}').mode
    ).toBe('card-1')
    unmount()

    const { result: reMounted } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )
    expect(reMounted.current.library.displayMode).toBe('card-1')
  })

  it('persists visible-property toggles independently of Filter state', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() => result.current.library.toggleProperty('activity'))
    const stored = JSON.parse(
      localStorage.getItem('rdst-queries-display') ?? '{}'
    )
    expect(stored.properties).toContain('activity')
    // Filter selections stay URL-owned; Display persistence must not touch
    // the URL search state.
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('falls back to Card 2 when a saved preference names the hidden rows view', () => {
    localStorage.setItem(
      'rdst-queries-display',
      JSON.stringify({ mode: 'rows', properties: ['source'] })
    )

    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    expect(result.current.library.displayMode).toBe('card-2')
  })
})

describe('analyze drawer state is URL-owned', () => {
  it('opens over the library instead of navigating away', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: { view: 'new' } })
    )

    act(() =>
      result.current.rowActions.analyze('SELECT 1', 'demo', undefined, {
        hash: 'h1',
      })
    )

    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/queries',
      search: expect.objectContaining({
        view: 'new',
        analyze: 'h1',
        rerun: true,
      }),
      replace: false,
    })
    expect(result.current.analyzeDrawer.link).toEqual({
      hash: 'h1',
      analysisId: undefined,
      rerun: true,
      tab: 'analyze',
    })
  })

  it('opens a stored analysis read-only', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() =>
      result.current.rowActions.analyze('SELECT 1', 'demo', undefined, {
        stored: { hash: 'h1', analysisId: 'a2' },
      })
    )

    expect(result.current.analyzeDrawer.link).toEqual({
      hash: 'h1',
      analysisId: 'a2',
      rerun: undefined,
      tab: 'analyze',
    })
    expect(mocks.navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        search: expect.objectContaining({ analyze: 'h1', analysisId: 'a2' }),
      })
    )
  })

  it('switches analyses and closes through the same URL', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: { analyze: 'h1' } })
    )

    act(() =>
      result.current.analyzeDrawer.open({ hash: 'h1', analysisId: 'a3' })
    )
    expect(result.current.analyzeDrawer.link).toEqual({
      hash: 'h1',
      analysisId: 'a3',
      rerun: undefined,
      tab: 'analyze',
    })

    act(() => result.current.analyzeDrawer.close())
    expect(result.current.analyzeDrawer.link).toBeNull()
    expect(mocks.navigate).toHaveBeenLastCalledWith({
      to: '/queries',
      search: expect.objectContaining({
        analyze: undefined,
        analysisId: undefined,
        rerun: undefined,
      }),
      replace: false,
    })
  })

  it('writes the drawer immediately, dropping the queued filter write', () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() =>
        useQueryLibraryController({ search: {} })
      )

      act(() => result.current.library.setSearch('ord'))
      expect(mocks.navigate).not.toHaveBeenCalled()

      act(() =>
        result.current.rowActions.analyze('SELECT 1', 'demo', undefined, {
          hash: 'h1',
        })
      )
      expect(mocks.navigate).toHaveBeenCalledTimes(1)

      act(() => vi.runAllTimers())

      // The debounced search write never lands on top of the drawer; its text
      // rides along in the one URL the drawer wrote.
      expect(mocks.navigate).toHaveBeenCalledTimes(1)
      expect(mocks.navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          search: expect.objectContaining({ q: 'ord', analyze: 'h1' }),
        })
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the full page for queries the registry does not know', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() => result.current.rowActions.analyze('SELECT 1', 'demo'))

    expect(result.current.analyzeDrawer.link).toBeNull()
    expect(mocks.navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/results',
        search: expect.objectContaining({ query: 'SELECT 1' }),
      })
    )
  })
})

describe('the star is an independent filter', () => {
  it('asks the read model for the shortlist alongside a status', () => {
    renderHook(() =>
      useQueryLibraryController({
        search: { view: 'needs-analysis', starred: true },
      })
    )

    expect(mocks.useQueryRegistryReadModel).toHaveBeenCalledWith(
      expect.objectContaining({ view: 'needs-analysis', starred: true }),
      'demo'
    )
  })

  it('writes the star into the URL and clears it with the filters', () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() =>
        useQueryLibraryController({ search: { view: 'new' } })
      )

      act(() => result.current.library.setStarred(true))
      act(() => vi.runAllTimers())
      expect(mocks.navigate).toHaveBeenLastCalledWith({
        to: '/queries',
        search: expect.objectContaining({ view: 'new', starred: true }),
        replace: true,
      })

      act(() => result.current.library.clearFilters())
      act(() => vi.runAllTimers())
      expect(mocks.navigate).toHaveBeenLastCalledWith({
        to: '/queries',
        search: expect.objectContaining({
          view: undefined,
          starred: undefined,
        }),
        replace: true,
      })
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('the drawer Overview is the card body entry point', () => {
  it('opens over the library on the Overview tab', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() => result.current.rowActions.openOverview('h1'))

    expect(result.current.analyzeDrawer.link).toEqual({
      hash: 'h1',
      analysisId: undefined,
      rerun: undefined,
      tab: 'overview',
    })
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/queries',
      search: expect.objectContaining({ analyze: 'h1', tab: 'overview' }),
      replace: false,
    })
  })

  it('drains the New mark the way expanding the card did', () => {
    mocks.readModel.queries = [entry('h1', true)]

    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )
    expect(mocks.markReviewed).not.toHaveBeenCalled()

    act(() => result.current.rowActions.openOverview('h1'))

    expect(mocks.markReviewed).toHaveBeenCalledWith('h1')
  })

  it('leaves the New mark alone when the link opens Analyze', () => {
    mocks.readModel.queries = [entry('h1', true)]

    renderHook(() => useQueryLibraryController({ search: { analyze: 'h1' } }))

    expect(mocks.markReviewed).not.toHaveBeenCalled()
  })
})

describe('what a revealed query is worth interrupting for', () => {
  function savedQueriesOptions(): SavedQueriesOptions {
    return mocks.useSavedQueriesController.mock.calls.at(-1)?.[0] ?? {}
  }

  it('opens a query linked from elsewhere in full', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() => savedQueriesOptions().onRevealLinked?.('h1'))

    expect(result.current.analyzeDrawer.link).toEqual({
      hash: 'h1',
      analysisId: undefined,
      rerun: undefined,
      tab: 'overview',
    })
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/queries',
      search: expect.objectContaining({ analyze: 'h1', tab: 'overview' }),
      replace: true,
    })
  })

  it('leaves the library in front of a query added right here', () => {
    const { result } = renderHook(() =>
      useQueryLibraryController({ search: {} })
    )

    act(() => {
      const options = savedQueriesOptions()
      options.onQueryAdded?.('h1')
      options.onRevealLinked?.('h1')
    })

    expect(result.current.analyzeDrawer.link).toBeNull()
  })
})
