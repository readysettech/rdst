export const QUERY_LIBRARY_VIEWS = [
  'all',
  'new',
  'saved',
  'high-impact',
  'needs-analysis',
  'ready-to-cache',
  'cached',
] as const

export type QueryLibraryView = (typeof QUERY_LIBRARY_VIEWS)[number]

export const QUERY_LIBRARY_SORTS = [
  'highest-impact',
  'recently-observed',
  'newest',
  'most-frequent',
  'slowest-average',
  'recently-analyzed',
] as const

export type QueryLibrarySort = (typeof QUERY_LIBRARY_SORTS)[number]

export const QUERY_LIBRARY_SOURCES = [
  'all',
  'observed',
  'ask',
  'manual',
  'file',
  'scan',
] as const

export type QueryLibrarySource = (typeof QUERY_LIBRARY_SOURCES)[number]

export const QUERY_LIBRARY_PARAMETER_FILTERS = [
  'all',
  'without-parameters',
  'values-ready',
  'values-needed',
] as const

export type QueryLibraryParameterFilter =
  (typeof QUERY_LIBRARY_PARAMETER_FILTERS)[number]

export const QUERY_LIBRARY_ACTIVITY_WINDOWS = [
  'all',
  '1m',
  '1h',
  '8h',
  '24h',
  '7d',
  '30d',
] as const

export type QueryLibraryActivityWindow =
  (typeof QUERY_LIBRARY_ACTIVITY_WINDOWS)[number]

export const QUERY_LIBRARY_IMPACT_FILTERS = ['all', '1m', '10m', '1h'] as const

export type QueryLibraryImpactFilter =
  (typeof QUERY_LIBRARY_IMPACT_FILTERS)[number]

export type QueryLibraryFilterKey =
  | 'view'
  | 'source'
  | 'params'
  | 'activity'
  | 'impact'

export type QueryLibraryAction = 'add'

export type QueryLibrarySearch = {
  view?: QueryLibraryView
  q?: string
  source?: QueryLibrarySource
  params?: QueryLibraryParameterFilter
  activity?: QueryLibraryActivityWindow
  impact?: QueryLibraryImpactFilter
  sort?: QueryLibrarySort
  action?: QueryLibraryAction
  hash?: string
  run?: string
}

const CLEARED_QUERY_LIBRARY_FILTERS = {
  q: undefined,
  source: undefined,
  params: undefined,
  activity: undefined,
  impact: undefined,
  action: undefined,
  run: undefined,
} as const

/**
 * An explicit add is a context change: show the saved library in newest-first
 * order and keep the new card identifiable after the dialog closes.
 */
export function addedQuerySearchPatch(
  hash?: string | null
): Partial<QueryLibrarySearch> {
  return {
    ...CLEARED_QUERY_LIBRARY_FILTERS,
    view: 'saved',
    sort: 'newest',
    hash: hash || undefined,
  }
}

/**
 * Revealing a workload update must not leave a hidden search or facet active.
 * The New view becomes the user's visible, shareable review context.
 */
export function newQueriesSearchPatch(): Partial<QueryLibrarySearch> {
  return {
    ...CLEARED_QUERY_LIBRARY_FILTERS,
    view: 'new',
    sort: 'newest',
    hash: undefined,
  }
}

function includes<T extends string>(
  values: readonly T[],
  value: unknown
): value is T {
  return typeof value === 'string' && values.includes(value as T)
}

function optionalString(value: unknown) {
  return typeof value === 'string' && value.trim() ? value : undefined
}

/**
 * Keep old query workspace URLs useful while the three-pane UI is retired.
 * The compatibility values are interpreted here rather than leaking legacy
 * concepts into the Query Library controller.
 */
export function parseQueryLibrarySearch(
  search: Record<string, unknown>
): QueryLibrarySearch {
  const legacyView = search.view
  const view = includes(QUERY_LIBRARY_VIEWS, legacyView)
    ? legacyView
    : legacyView === 'saved'
      ? 'saved'
      : legacyView === 'slow'
        ? 'high-impact'
        : undefined
  return {
    view,
    q: optionalString(search.q),
    source: includes(QUERY_LIBRARY_SOURCES, search.source)
      ? search.source
      : undefined,
    params: includes(QUERY_LIBRARY_PARAMETER_FILTERS, search.params)
      ? search.params
      : undefined,
    activity: includes(QUERY_LIBRARY_ACTIVITY_WINDOWS, search.activity)
      ? search.activity
      : undefined,
    impact: includes(QUERY_LIBRARY_IMPACT_FILTERS, search.impact)
      ? search.impact
      : undefined,
    sort: includes(QUERY_LIBRARY_SORTS, search.sort) ? search.sort : undefined,
    action: includes(['add'] as const, search.action)
      ? search.action
      : undefined,
    hash: optionalString(search.hash),
    run: optionalString(search.run),
  }
}

export function resolvedQueryLibraryState(search: QueryLibrarySearch) {
  return {
    view: search.view ?? 'all',
    searchTerm: search.q ?? '',
    source: search.source ?? 'all',
    params: search.params ?? 'all',
    activity: search.activity ?? 'all',
    impact: search.impact ?? 'all',
    sort: search.sort ?? 'highest-impact',
  } satisfies {
    view: QueryLibraryView
    searchTerm: string
    source: QueryLibrarySource
    params: QueryLibraryParameterFilter
    activity: QueryLibraryActivityWindow
    impact: QueryLibraryImpactFilter
    sort: QueryLibrarySort
  }
}
