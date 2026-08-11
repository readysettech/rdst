import { DataResponse } from '../../../components/data-response/DataResponse'
import {
  QueryListEmptyState,
  QueryListErrorState,
  QueryListSkeleton,
} from '../../../components/query-list-state'
import { SavedQueryRow } from '../saved/SavedQueryRow'
import { QueryLibraryNewQueriesCard } from './QueryLibraryNewQueriesCard'
import {
  QUERY_LIBRARY_ACTIVITY_LABELS,
  QUERY_LIBRARY_IMPACT_LABELS,
  QUERY_LIBRARY_PARAMETER_LABELS,
  QUERY_LIBRARY_SOURCE_LABELS,
  QUERY_LIBRARY_VIEW_LABELS,
} from './queryLibrarySelectors'
import type { QueryLibraryController } from './useQueryLibraryController'

export function QueryLibraryList({
  controller,
}: {
  controller: QueryLibraryController
}) {
  const { registry, library, rowState, rowActions } = controller
  const recoveryCandidates = [
    {
      active: library.view !== 'all',
      label: QUERY_LIBRARY_VIEW_LABELS[library.view],
      count: library.selection.facetCounts.view.all,
      clear: () => library.setView('all'),
    },
    {
      active: library.source !== 'all',
      label: QUERY_LIBRARY_SOURCE_LABELS[library.source],
      count: library.selection.facetCounts.source.all,
      clear: () => library.setSource('all'),
    },
    {
      active: library.params !== 'all',
      label: QUERY_LIBRARY_PARAMETER_LABELS[library.params],
      count: library.selection.facetCounts.params.all,
      clear: () => library.setParams('all'),
    },
    {
      active: library.activity !== 'all',
      label: QUERY_LIBRARY_ACTIVITY_LABELS[library.activity],
      count: library.selection.facetCounts.activity.all,
      clear: () => library.setActivity('all'),
    },
    {
      active: library.impact !== 'all',
      label: QUERY_LIBRARY_IMPACT_LABELS[library.impact],
      count: library.selection.facetCounts.impact.all,
      clear: () => library.setImpact('all'),
    },
  ]
    .filter((candidate) => candidate.active && candidate.count > 0)
    .sort((left, right) => right.count - left.count)
  const recovery = recoveryCandidates[0]
  const hasSearch = library.searchTerm.trim().length > 0
  const filteredEmptyTitle = hasSearch
    ? 'No queries match this search'
    : 'No queries match these filters'
  const filteredEmptyBody = recovery
    ? `Remove “${recovery.label}” to show ${recovery.count} ${recovery.count === 1 ? 'query' : 'queries'}.`
    : hasSearch
      ? 'Clear the search to return to the current query view.'
      : 'Clear the active filters to return to the full query library.'
  const filteredEmptyAction = recovery
    ? {
        label: `Remove ${recovery.label}`,
        icon: 'close' as const,
        onClick: recovery.clear,
      }
    : hasSearch
      ? {
          label: 'Clear search',
          icon: 'close' as const,
          onClick: () => library.setSearch(''),
        }
      : {
          label: 'Clear filters',
          icon: 'close' as const,
          onClick: library.clearFilters,
        }

  return (
    <div aria-busy={library.isPending}>
      <DataResponse
        data={library.visibleQueries}
        isLoading={registry.isLoading}
        error={registry.listError}
        renderLoading={<QueryListSkeleton />}
        renderError={(loadError) => (
          <QueryListErrorState
            title="Queries couldn't be loaded"
            message="Readyset couldn't load the query library."
            trustworthy="No query was changed."
            error={loadError}
            onRetry={() => void registry.refetch()}
          />
        )}
        renderEmpty={
          <QueryListEmptyState
            icon={library.selection.isFiltered ? 'search' : 'folder-file'}
            title={
              library.selection.isFiltered
                ? filteredEmptyTitle
                : 'No queries observed yet'
            }
            body={
              library.selection.isFiltered
                ? filteredEmptyBody
                : 'Readyset watches this database for queries. You can also add one manually.'
            }
            action={
              library.selection.isFiltered
                ? filteredEmptyAction
                : {
                    label: 'Add query',
                    icon: 'add',
                    onClick: controller.addDialog.openDialog,
                  }
            }
            secondaryAction={
              library.selection.isFiltered &&
              (recovery || (hasSearch && recoveryCandidates.length > 0))
                ? {
                    label: 'Clear all filters',
                    icon: 'filter',
                    onClick: library.clearFilters,
                  }
                : undefined
            }
          />
        }
      >
        {() => (
          <div className="space-y-3">
            <QueryLibraryNewQueriesCard controller={controller} />
            {library.visibleQueries.map((entry) => (
              <SavedQueryRow
                key={library.keyForHash(entry.hash)}
                entry={entry}
                target={controller.target}
                state={rowState}
                actions={rowActions}
                displayMode={library.renderDisplayMode}
                visibleProperties={library.renderProperties}
              />
            ))}
          </div>
        )}
      </DataResponse>
    </div>
  )
}
