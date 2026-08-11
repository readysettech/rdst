import { AnimatePresence } from '@rs/ui-new/motion'
import { DataResponse } from '../../../components/data-response/DataResponse'
import {
  QueryListEmptyState,
  QueryListErrorState,
  QueryListSkeleton,
} from '../../../components/query-list-state'
import { SavedQueriesHero } from './SavedQueriesHero'
import { SavedQueryRow } from './SavedQueryRow'
import type { SavedQueriesController } from './useSavedQueriesController'

export function SavedQueryList({
  controller,
}: {
  controller: SavedQueriesController
}) {
  const { registry, selection, rowState, rowActions } = controller

  return (
    <DataResponse
      data={selection.filteredQueries}
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
          icon={selection.isFiltered ? 'search' : 'folder-file'}
          title={
            selection.isFiltered ? 'No matching queries' : 'No queries yet'
          }
          body={
            selection.isFiltered
              ? 'Try clearing the search or source filter.'
              : 'Add a query or let Readyset discover your workload.'
          }
          action={
            selection.isFiltered
              ? {
                  label: 'Clear filters',
                  icon: 'close',
                  onClick: controller.clearFilters,
                }
              : {
                  label: 'Add your first query',
                  icon: 'add',
                  onClick: controller.addDialog.openDialog,
                }
          }
        />
      }
    >
      {() => (
        <>
          <SavedQueriesHero
            hero={selection.hero}
            heroWhy={controller.heroWhy}
            heroMoreCount={selection.heroMoreCount}
            caching={
              !!selection.hero && rowActions.cachingHash === selection.hero.hash
            }
            showTelemetryNudge={
              !controller.searchTerm.trim() &&
              !selection.hasMeasuredImpact &&
              selection.filteredQueries.length > 0
            }
            onCache={rowActions.cacheQuery}
            onOpenSlowQueries={controller.navigation.openSlowQueries}
          />

          <div className="space-y-3">
            <AnimatePresence mode="popLayout">
              {selection.displayedQueries.map((entry) => (
                <SavedQueryRow
                  key={entry.hash}
                  entry={entry}
                  target={controller.target}
                  state={rowState}
                  actions={rowActions}
                />
              ))}
            </AnimatePresence>
          </div>
        </>
      )}
    </DataResponse>
  )
}
