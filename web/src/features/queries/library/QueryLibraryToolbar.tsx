import {
  QueryLibraryControlBar,
  type QueryLibraryFilters,
} from './QueryLibraryControlBar'
import type { QueryLibraryFilterKey } from './queryLibraryState'
import type { QueryLibraryController } from './useQueryLibraryController'

export function QueryLibraryToolbar({
  controller,
}: {
  controller: QueryLibraryController
}) {
  const { library } = controller
  const { selection } = library
  const visibleCount = library.visibleQueries.length
  const filters: QueryLibraryFilters = {
    view: library.view,
    source: library.source,
    params: library.params,
    activity: library.activity,
    impact: library.impact,
  }

  const setFilter = <Key extends QueryLibraryFilterKey>(
    key: Key,
    value: QueryLibraryFilters[Key]
  ) => {
    switch (key) {
      case 'view':
        library.setView(value as QueryLibraryFilters['view'])
        break
      case 'source':
        library.setSource(value as QueryLibraryFilters['source'])
        break
      case 'params':
        library.setParams(value as QueryLibraryFilters['params'])
        break
      case 'activity':
        library.setActivity(value as QueryLibraryFilters['activity'])
        break
      case 'impact':
        library.setImpact(value as QueryLibraryFilters['impact'])
        break
    }
  }

  const clearFilter = (key: QueryLibraryFilterKey) => {
    switch (key) {
      case 'view':
        library.setView('all')
        break
      case 'source':
        library.setSource('all')
        break
      case 'params':
        library.setParams('all')
        break
      case 'activity':
        library.setActivity('all')
        break
      case 'impact':
        library.setImpact('all')
        break
    }
  }

  return (
    <div className="space-y-4">
      <QueryLibraryControlBar
        idPrefix="query-library"
        searchTerm={library.searchTerm}
        onSearchChange={library.setSearch}
        filters={filters}
        onFilterChange={setFilter}
        onClearFilter={clearFilter}
        onClearFilters={library.clearFilterSelections}
        sort={library.sort}
        onSortChange={library.setSort}
        starred={library.starred}
        onStarredChange={library.setStarred}
        displayMode={library.displayMode}
        onDisplayModeChange={library.setDisplayMode}
        properties={library.properties}
        onToggleProperty={library.toggleProperty}
        selection={selection}
        resultCount={visibleCount}
        totalCount={library.total}
      />
    </div>
  )
}
