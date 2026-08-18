import { useNavigate } from '@tanstack/react-router'
import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from 'react'
import { useTarget } from '../../../hooks/useTarget'
import { useQueryDiscoverySnapshot } from '../../../lib/useQueryDiscovery'
import { useQueryRegistryReadModel } from '../../../lib/useQueryRegistry'
import { useSavedQueriesController } from '../saved/useSavedQueriesController'
import {
  QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES,
  type QueryLibraryDisplayMode,
  type QueryLibraryDisplayProperty,
} from './queryLibraryDisplay'
import {
  isQueryLibraryFiltered,
  normalizeQueryLibraryFacetCounts,
  type QueryLibrarySelection,
} from './queryLibrarySelectors'
import {
  addedQuerySearchPatch,
  type QueryLibrarySearch,
  resolvedQueryLibraryState,
} from './queryLibraryState'
import { useStableQueryList } from './useStableQueryList'

export function useQueryLibraryController({
  search,
}: {
  search: QueryLibrarySearch
}) {
  const navigate = useNavigate()
  const [optimisticSearch, setOptimisticSearch] = useState(search)
  const optimisticSearchRef = useRef(search)
  const navigationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [navigationPending, startTransition] = useTransition()

  const cancelPendingSearchNavigation = useCallback(() => {
    if (!navigationTimerRef.current) return
    clearTimeout(navigationTimerRef.current)
    navigationTimerRef.current = null
  }, [])

  const updateSearch = useCallback(
    (patch: Partial<QueryLibrarySearch>) => {
      const nextSearch = { ...optimisticSearchRef.current, ...patch }
      optimisticSearchRef.current = nextSearch
      setOptimisticSearch(nextSearch)
      if (navigationTimerRef.current) clearTimeout(navigationTimerRef.current)
      navigationTimerRef.current = setTimeout(() => {
        navigationTimerRef.current = null
        startTransition(() => {
          void navigate({
            to: '/queries',
            search: nextSearch,
            replace: true,
          })
        })
      }, 160)
    },
    [navigate]
  )

  const consumeTransientDeepLink = useCallback(
    (hash: string) => {
      if (optimisticSearchRef.current.hash !== hash) return
      updateSearch({ hash: undefined })
    },
    [updateSearch]
  )

  const state = useMemo(
    () => resolvedQueryLibraryState(optimisticSearch),
    [
      optimisticSearch.activity,
      optimisticSearch.impact,
      optimisticSearch.params,
      optimisticSearch.q,
      optimisticSearch.sort,
      optimisticSearch.source,
      optimisticSearch.view,
    ]
  )
  const deferredState = useDeferredValue(state)
  const { target } = useTarget()
  // Deep links must be able to reveal a query that is not in the currently
  // loaded page. Temporarily narrow the server-side read model to the exact
  // hash; once the link is consumed the user's normal text search resumes.
  const readModelSearch =
    optimisticSearch.hash?.trim() || deferredState.searchTerm
  const readModel = useQueryRegistryReadModel(
    {
      search: readModelSearch,
      view: deferredState.view,
      source: deferredState.source,
      params: deferredState.params,
      activity: deferredState.activity,
      impact: deferredState.impact,
      sort: deferredState.sort,
    },
    target
  )
  const base = useSavedQueriesController({
    deepLinkHash: search.hash,
    deepLinkRunId: search.run,
    onQueryAdded: (hash) => updateSearch(addedQuerySearchPatch(hash)),
    onDeepLinkConsumed: search.run ? undefined : consumeTransientDeepLink,
    list: {
      queries: readModel.queries,
      total: readModel.total,
      isLoading: readModel.isLoading,
      isFetching: readModel.isFetching,
      listError: readModel.listError,
      refetch: readModel.refetch,
    },
  })
  const discovery = useQueryDiscoverySnapshot(target).data
  const [properties, setProperties] = useState<QueryLibraryDisplayProperty[]>(
    QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES
  )
  const [displayMode, setDisplayModeState] =
    useState<QueryLibraryDisplayMode>('card-1')
  const deferredDisplayMode = useDeferredValue(displayMode)
  const deferredProperties = useDeferredValue(properties)

  // Filtering, sorting, and facet counts come from the read model; the client
  // only presents the loaded rows.
  const selection = useMemo<QueryLibrarySelection>(() => {
    const facetCounts = normalizeQueryLibraryFacetCounts(readModel.facetCounts)
    return {
      queries: readModel.queries,
      counts: facetCounts.view,
      sourceCounts: facetCounts.source,
      facetCounts,
      isFiltered: isQueryLibraryFiltered(deferredState),
    }
  }, [deferredState, readModel.facetCounts, readModel.queries])
  // Loading another page is an explicit list extension: the page count is part
  // of the stable-list signature so appended rows render immediately.
  const signature = [
    target ?? '',
    deferredState.view,
    readModelSearch,
    deferredState.source,
    deferredState.params,
    deferredState.activity,
    deferredState.impact,
    deferredState.sort,
    String(readModel.pageCount),
  ].join('\u0000')
  const stableList = useStableQueryList({
    queries: selection.queries,
    signature,
    isLoading: base.registry.isLoading,
    isPlaceholder: readModel.isPlaceholder,
    hashAliases: base.rowState.hashAliases,
    revealHash: search.hash,
  })
  const newVisibleHashes = stableList.visibleQueries
    .filter((query) => query.is_new)
    .map((query) => query.hash)

  useEffect(
    () => () => {
      if (navigationTimerRef.current) {
        clearTimeout(navigationTimerRef.current)
      }
    },
    []
  )

  useEffect(() => {
    optimisticSearchRef.current = search
    setOptimisticSearch(search)
  }, [
    search.action,
    search.activity,
    search.hash,
    search.impact,
    search.params,
    search.q,
    search.run,
    search.sort,
    search.source,
    search.view,
  ])

  useEffect(() => {
    if (search.action === 'add') base.addDialog.openDialog()
  }, [base.addDialog.openDialog, search.action])

  const analyzeFromLibrary = (
    sql: string,
    queryTarget?: string,
    mostRecentParams?: Record<string, unknown>
  ) => {
    // Search and filter changes are reflected in the URL after a short delay so
    // the library stays responsive while typing. Do not let that queued
    // navigation pull the user back to Queries after they choose Analyze.
    cancelPendingSearchNavigation()
    const current = optimisticSearchRef.current
    const returnSearch: QueryLibrarySearch = {
      view: current.view,
      q: current.q,
      source: current.source,
      params: current.params,
      activity: current.activity,
      impact: current.impact,
      sort: current.sort,
    }

    void navigate({
      to: '/results',
      search: {
        query: sql.trim(),
        target: queryTarget || undefined,
        params:
          mostRecentParams && Object.keys(mostRecentParams).length > 0
            ? JSON.stringify(mostRecentParams)
            : undefined,
        returnSearch: JSON.stringify(returnSearch),
      },
    })
  }

  return {
    ...base,
    rowActions: {
      ...base.rowActions,
      analyze: analyzeFromLibrary,
    },
    library: {
      ...state,
      action: search.action,
      selection,
      ...stableList,
      newVisibleCount: newVisibleHashes.length,
      total: readModel.total,
      hasNextPage: readModel.hasNextPage,
      isLoadingMore: readModel.isFetchingNextPage,
      loadMore: () => void readModel.fetchNextPage(),
      discovery,
      freshness: readModel.freshness,
      displayMode,
      renderDisplayMode: deferredDisplayMode,
      isPending:
        navigationPending ||
        state !== deferredState ||
        displayMode !== deferredDisplayMode ||
        properties !== deferredProperties,
      setDisplayMode: setDisplayModeState,
      properties,
      renderProperties: deferredProperties,
      setView: (view: typeof state.view) => updateSearch({ view }),
      setSearch: (value: string) => updateSearch({ q: value || undefined }),
      setSource: (source: typeof state.source) => updateSearch({ source }),
      setParams: (params: typeof state.params) => updateSearch({ params }),
      setActivity: (activity: typeof state.activity) =>
        updateSearch({ activity }),
      setImpact: (impact: typeof state.impact) => updateSearch({ impact }),
      setSort: (sort: typeof state.sort) => updateSearch({ sort }),
      toggleProperty: (property: QueryLibraryDisplayProperty) =>
        setProperties((current) =>
          current.includes(property)
            ? current.filter((value) => value !== property)
            : [...current, property]
        ),
      clearFilterSelections: () =>
        updateSearch({
          view: undefined,
          source: undefined,
          params: undefined,
          activity: undefined,
          impact: undefined,
        }),
      clearAdvancedFilters: () =>
        updateSearch({
          source: undefined,
          params: undefined,
          activity: undefined,
          impact: undefined,
        }),
      clearFilters: () =>
        updateSearch({
          view: undefined,
          q: undefined,
          source: undefined,
          params: undefined,
          activity: undefined,
          impact: undefined,
          sort: undefined,
        }),
      markAllReviewed: () => base.rowActions.markAllReviewed(newVisibleHashes),
      // Pending rows were served by the active filter's read model, so
      // adopting them in place always shows them; the user's filters,
      // search, and sort stay untouched.
      revealPending: stableList.revealPending,
    },
    addDialog: {
      ...base.addDialog,
      openDialog: () => {
        base.addDialog.openDialog()
        updateSearch({ action: 'add' })
      },
      closeDialog: () => {
        base.addDialog.closeDialog()
        updateSearch({ action: undefined })
      },
    },
  }
}

export type QueryLibraryController = ReturnType<
  typeof useQueryLibraryController
>
