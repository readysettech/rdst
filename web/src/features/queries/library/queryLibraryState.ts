/**
 * Lifecycle statuses the server computes for a query. The user's own mark is
 * not one of them: it is the independent `starred` boolean below, so a
 * shortlist can be asked for alongside any status.
 */
export const QUERY_LIBRARY_VIEWS = [
  'all',
  'new',
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

/**
 * The analyze drawer's two panes. A bare `?analyze=<hash>` opens Analyze, so
 * every link that predates the tabs behaves exactly as it did.
 */
export const ANALYZE_DRAWER_TABS = ['overview', 'analyze'] as const

export type AnalyzeDrawerTab = (typeof ANALYZE_DRAWER_TABS)[number]

export type QueryLibrarySearch = {
  view?: QueryLibraryView
  q?: string
  source?: QueryLibrarySource
  params?: QueryLibraryParameterFilter
  activity?: QueryLibraryActivityWindow
  impact?: QueryLibraryImpactFilter
  sort?: QueryLibrarySort
  /**
   * The user's own mark, independent of `view`: "my shortlist, not yet
   * analysed" is `?view=needs-analysis&starred=1`.
   */
  starred?: boolean
  action?: QueryLibraryAction
  hash?: string
  run?: string
  /**
   * Registry hash of the query whose analysis is open in the drawer. The
   * drawer is URL-owned, so opening, closing and switching analyses are all
   * history entries. [RDST UX plan, A5]
   */
  analyze?: string
  /** A stored analysis to show read-only; absent means measure the query. */
  analysisId?: string
  /**
   * Measure the query even though stored analyses exist. Without it a drawer
   * opened on a query that was analyzed before shows that analysis rather
   * than silently spending another run.
   */
  rerun?: boolean
  /** Which drawer pane is showing. Absent means Analyze. */
  tab?: AnalyzeDrawerTab
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
 * An explicit add is a context change: an added query is starred, so show the
 * starred shortlist in newest-first order and keep the new card identifiable
 * after the dialog closes.
 */
export function addedQuerySearchPatch(
  hash?: string | null
): Partial<QueryLibrarySearch> {
  return {
    ...CLEARED_QUERY_LIBRARY_FILTERS,
    view: undefined,
    starred: true,
    sort: 'newest',
    hash: hash || undefined,
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

function optionalFlag(value: unknown) {
  return value === true || value === 'true' || value === '1' || value === 1
    ? true
    : undefined
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
    // The mark used to be a value of `view`; a link written then still asks
    // for the same rows, now as the independent boolean.
    starred:
      optionalFlag(search.starred) ??
      (legacyView === 'saved' ? true : undefined),
    action: includes(['add'] as const, search.action)
      ? search.action
      : undefined,
    hash: optionalString(search.hash),
    run: optionalString(search.run),
    analyze: optionalString(search.analyze),
    analysisId: optionalString(search.analysisId),
    rerun: optionalFlag(search.rerun),
    tab: includes(ANALYZE_DRAWER_TABS, search.tab) ? search.tab : undefined,
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
    starred: search.starred === true,
  } satisfies {
    view: QueryLibraryView
    searchTerm: string
    source: QueryLibrarySource
    params: QueryLibraryParameterFilter
    activity: QueryLibraryActivityWindow
    impact: QueryLibraryImpactFilter
    sort: QueryLibrarySort
    starred: boolean
  }
}
