import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { QueryLibraryList } from './QueryLibraryList'
import { normalizeQueryLibraryFacetCounts } from './queryLibrarySelectors'
import type { QueryLibraryController } from './useQueryLibraryController'

vi.mock('../saved/SavedQueryRow', () => ({
  SavedQueryRow: ({ entry }: { entry: QueryRegistryEntry }) => (
    <div data-testid="query-row">{entry.hash}</div>
  ),
}))
vi.mock('./QueryLibraryNewQueriesCard', () => ({
  QueryLibraryNewQueriesCard: () => null,
}))

function entry(hash: string): QueryRegistryEntry {
  return {
    hash,
    sql: `SELECT * FROM ${hash}`,
    tag: '',
    source: 'manual',
    target: 'demo',
    frequency: 0,
    last_analyzed: '',
  }
}

function makeController({
  hasNextPage,
  loadMore = vi.fn(),
}: {
  hasNextPage: boolean
  loadMore?: () => void
}) {
  const queries = [entry('a'), entry('b')]
  return {
    target: 'demo',
    registry: {
      isLoading: false,
      listError: null,
      refetch: vi.fn(),
    },
    rowState: {},
    rowActions: {},
    addDialog: { openDialog: vi.fn() },
    library: {
      view: 'all',
      source: 'all',
      params: 'all',
      activity: 'all',
      impact: 'all',
      searchTerm: '',
      selection: {
        queries,
        facetCounts: normalizeQueryLibraryFacetCounts(null),
        isFiltered: false,
      },
      visibleQueries: queries,
      keyForHash: (hash: string) => hash,
      renderDisplayMode: 'card-1',
      renderProperties: [],
      isPending: false,
      hasNextPage,
      isLoadingMore: false,
      loadMore,
      setView: vi.fn(),
      setSource: vi.fn(),
      setParams: vi.fn(),
      setActivity: vi.fn(),
      setImpact: vi.fn(),
      setSearch: vi.fn(),
      clearFilters: vi.fn(),
    },
  } as unknown as QueryLibraryController
}

afterEach(cleanup)

describe('QueryLibraryList paging', () => {
  it('offers Load more while the server reports another page', () => {
    const loadMore = vi.fn()
    render(
      <QueryLibraryList
        controller={makeController({ hasNextPage: true, loadMore })}
      />
    )

    expect(screen.getAllByTestId('query-row')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(loadMore).toHaveBeenCalledOnce()
  })

  it('omits Load more on the final page', () => {
    render(
      <QueryLibraryList controller={makeController({ hasNextPage: false })} />
    )

    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
  })
})
