import { filterQueriesBySearch } from '../../../lib/queryIdentity'
import { isNotCacheable, queryImpactMs } from '../../../lib/queryImpact'
import {
  detectParameters,
  resolveInitialValue,
} from '../../../lib/sqlParameters'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import type {
  QueryLibraryActivityWindow,
  QueryLibraryImpactFilter,
  QueryLibraryParameterFilter,
  QueryLibrarySort,
  QueryLibrarySource,
  QueryLibraryView,
} from './queryLibraryState'
import {
  QUERY_LIBRARY_ACTIVITY_WINDOWS,
  QUERY_LIBRARY_IMPACT_FILTERS,
  QUERY_LIBRARY_PARAMETER_FILTERS,
  QUERY_LIBRARY_SOURCES,
  QUERY_LIBRARY_VIEWS,
} from './queryLibraryState'

const OBSERVED_SOURCES = new Set(['top', 'top-historical', 'audit'])
const ASK_SOURCES = new Set(['ask', 'prompt'])
const MANUAL_SOURCES = new Set(['manual', 'web'])

export const QUERY_LIBRARY_VIEW_LABELS: Record<QueryLibraryView, string> = {
  all: 'All queries',
  new: 'New',
  saved: 'Saved',
  'high-impact': 'High impact',
  'needs-analysis': 'Needs analysis',
  'ready-to-cache': 'Ready to cache',
  cached: 'Cached',
}

export const QUERY_LIBRARY_SORT_LABELS: Record<QueryLibrarySort, string> = {
  'highest-impact': 'Highest impact',
  'recently-observed': 'Recently observed',
  newest: 'Newest',
  'most-frequent': 'Most frequent',
  'slowest-average': 'Slowest average',
  'recently-analyzed': 'Recently analyzed',
}

export const QUERY_LIBRARY_SOURCE_LABELS: Record<QueryLibrarySource, string> = {
  all: 'All sources',
  observed: 'Observed',
  ask: 'Ask',
  manual: 'Manual',
  file: 'File import',
  scan: 'Code scan',
}

export const QUERY_LIBRARY_PARAMETER_LABELS: Record<
  QueryLibraryParameterFilter,
  string
> = {
  all: 'All parameter states',
  'without-parameters': 'No parameters',
  'values-ready': 'Values ready',
  'values-needed': 'Values needed',
}

export const QUERY_LIBRARY_ACTIVITY_LABELS: Record<
  QueryLibraryActivityWindow,
  string
> = {
  all: 'Any activity',
  '1m': 'Past minute',
  '1h': 'Past hour',
  '8h': 'Past 8 hours',
  '24h': 'Past 24 hours',
  '7d': 'Past 7 days',
  '30d': 'Past 30 days',
}

export const QUERY_LIBRARY_IMPACT_LABELS: Record<
  QueryLibraryImpactFilter,
  string
> = {
  all: 'Any database time',
  '1m': 'At least 1 minute',
  '10m': 'At least 10 minutes',
  '1h': 'At least 1 hour',
}

function entrySources(entry: QueryRegistryEntry) {
  return new Set([...(entry.sources ?? []), entry.source].filter(Boolean))
}

function sourceMatches(entry: QueryRegistryEntry) {
  const sources = entrySources(entry)
  return {
    all: true,
    observed: [...sources].some((value) => OBSERVED_SOURCES.has(value)),
    ask: [...sources].some((value) => ASK_SOURCES.has(value)),
    manual: [...sources].some((value) => MANUAL_SOURCES.has(value)),
    file: sources.has('file'),
    scan: sources.has('scan'),
  } satisfies Record<QueryLibrarySource, boolean>
}

export function matchesQuerySource(
  entry: QueryRegistryEntry,
  source: QueryLibrarySource
) {
  return sourceMatches(entry)[source]
}

function parameterMatches(entry: QueryRegistryEntry) {
  const parameters = detectParameters(entry.original_sql || entry.sql)
  const hasParameters = parameters.length > 0
  const valuesReady =
    hasParameters &&
    parameters.every(
      (parameter) =>
        resolveInitialValue(parameter, entry.most_recent_params).trim() !== ''
    )

  return {
    all: true,
    'without-parameters': !hasParameters,
    'values-ready': valuesReady,
    'values-needed': hasParameters && !valuesReady,
  } satisfies Record<QueryLibraryParameterFilter, boolean>
}

export function matchesParameterReadiness(
  entry: QueryRegistryEntry,
  filter: QueryLibraryParameterFilter
) {
  return parameterMatches(entry)[filter]
}

const ACTIVITY_WINDOW_MS: Record<
  Exclude<QueryLibraryActivityWindow, 'all'>,
  number
> = {
  '1m': 60 * 1000,
  '1h': 60 * 60 * 1000,
  '8h': 8 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
}

function latestActivity(entry: QueryRegistryEntry) {
  return Math.max(
    timestamp(entry.last_observed_at),
    timestamp(entry.last_analyzed_at),
    timestamp(entry.last_compared_at),
    timestamp(entry.last_analyzed)
  )
}

export function matchesActivityWindow(
  entry: QueryRegistryEntry,
  window: QueryLibraryActivityWindow,
  now: number
) {
  if (window === 'all') return true
  const activity = latestActivity(entry)
  return activity > 0 && activity >= now - ACTIVITY_WINDOW_MS[window]
}

function activityMatches(entry: QueryRegistryEntry, now: number) {
  const activity = latestActivity(entry)
  return {
    all: true,
    '1m': activity > 0 && activity >= now - ACTIVITY_WINDOW_MS['1m'],
    '1h': activity > 0 && activity >= now - ACTIVITY_WINDOW_MS['1h'],
    '8h': activity > 0 && activity >= now - ACTIVITY_WINDOW_MS['8h'],
    '24h': activity > 0 && activity >= now - ACTIVITY_WINDOW_MS['24h'],
    '7d': activity > 0 && activity >= now - ACTIVITY_WINDOW_MS['7d'],
    '30d': activity > 0 && activity >= now - ACTIVITY_WINDOW_MS['30d'],
  } satisfies Record<QueryLibraryActivityWindow, boolean>
}

const IMPACT_THRESHOLD_MS: Record<QueryLibraryImpactFilter, number> = {
  all: 0,
  '1m': 60 * 1000,
  '10m': 10 * 60 * 1000,
  '1h': 60 * 60 * 1000,
}

export function matchesImpact(
  entry: QueryRegistryEntry,
  impact: QueryLibraryImpactFilter
) {
  return queryImpactMs(entry) >= IMPACT_THRESHOLD_MS[impact]
}

function impactMatches(entry: QueryRegistryEntry) {
  const impact = queryImpactMs(entry)
  return {
    all: true,
    '1m': impact >= IMPACT_THRESHOLD_MS['1m'],
    '10m': impact >= IMPACT_THRESHOLD_MS['10m'],
    '1h': impact >= IMPACT_THRESHOLD_MS['1h'],
  } satisfies Record<QueryLibraryImpactFilter, boolean>
}

function isLegacySavedEntry(entry: QueryRegistryEntry) {
  return (
    entry.saved_at === undefined &&
    entry.first_observed_at === undefined &&
    entry.last_analyzed_at === undefined &&
    entry.last_compared_at === undefined
  )
}

export function matchesQueryView(
  entry: QueryRegistryEntry,
  view: QueryLibraryView,
  isCached: (hash: string) => boolean
) {
  return viewMatches(entry, isCached)[view]
}

function viewMatches(
  entry: QueryRegistryEntry,
  _isCached: (hash: string) => boolean
) {
  // A completed quick comparison is evidence, not persistent cache inventory.
  // Keep the callback in the selector contract while callers migrate, but only
  // registry-backed Readyset queries belong in the Cached lifecycle view.
  const cached = Boolean(entry.readyset_query_id)
  return {
    all: true,
    new: Boolean(entry.is_new),
    saved: Boolean(entry.saved_at) || isLegacySavedEntry(entry),
    'high-impact': queryImpactMs(entry) > 0,
    'needs-analysis': !entry.last_analyzed_at,
    'ready-to-cache':
      !cached &&
      entry.readyset_supported === 'yes' &&
      !isNotCacheable(entry.readyset_supported),
    cached,
  } satisfies Record<QueryLibraryView, boolean>
}

function timestamp(value?: string | null) {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? 0 : parsed
}

function firstLibraryTimestamp(entry: QueryRegistryEntry) {
  return Math.max(
    timestamp(entry.saved_at),
    timestamp(entry.first_observed_at),
    timestamp(entry.first_analyzed)
  )
}

function numeric(value?: number | null) {
  return Number.isFinite(value) ? (value ?? 0) : 0
}

export function compareQueries(
  left: QueryRegistryEntry,
  right: QueryRegistryEntry,
  sort: QueryLibrarySort
) {
  let delta = 0
  switch (sort) {
    case 'highest-impact':
      delta = queryImpactMs(right) - queryImpactMs(left)
      break
    case 'recently-observed':
      delta =
        timestamp(right.last_observed_at) - timestamp(left.last_observed_at)
      break
    case 'newest':
      delta = firstLibraryTimestamp(right) - firstLibraryTimestamp(left)
      break
    case 'most-frequent':
      delta = numeric(right.frequency) - numeric(left.frequency)
      break
    case 'slowest-average':
      delta = numeric(right.avg_duration_ms) - numeric(left.avg_duration_ms)
      break
    case 'recently-analyzed':
      delta =
        timestamp(right.last_analyzed_at ?? right.last_analyzed) -
        timestamp(left.last_analyzed_at ?? left.last_analyzed)
      break
  }
  return delta || left.hash.localeCompare(right.hash)
}

export type QueryLibrarySelection = {
  queries: QueryRegistryEntry[]
  counts: Record<QueryLibraryView, number>
  sourceCounts: Record<QueryLibrarySource, number>
  facetCounts: {
    view: Record<QueryLibraryView, number>
    source: Record<QueryLibrarySource, number>
    params: Record<QueryLibraryParameterFilter, number>
    activity: Record<QueryLibraryActivityWindow, number>
    impact: Record<QueryLibraryImpactFilter, number>
  }
  isFiltered: boolean
}

function emptyCounts<Value extends string>(values: readonly Value[]) {
  return Object.fromEntries(values.map((value) => [value, 0])) as Record<
    Value,
    number
  >
}

export function selectQueryLibrary({
  queries,
  view,
  searchTerm,
  source,
  params,
  activity,
  impact,
  sort,
  isCached,
  now = Date.now(),
}: {
  queries: QueryRegistryEntry[]
  view: QueryLibraryView
  searchTerm: string
  source: QueryLibrarySource
  params: QueryLibraryParameterFilter
  activity: QueryLibraryActivityWindow
  impact: QueryLibraryImpactFilter
  sort: QueryLibrarySort
  isCached: (hash: string) => boolean
  now?: number
}): QueryLibrarySelection {
  const searchFiltered = filterQueriesBySearch(queries, searchTerm)

  const facetCounts = {
    view: emptyCounts(QUERY_LIBRARY_VIEWS),
    source: emptyCounts(QUERY_LIBRARY_SOURCES),
    params: emptyCounts(QUERY_LIBRARY_PARAMETER_FILTERS),
    activity: emptyCounts(QUERY_LIBRARY_ACTIVITY_WINDOWS),
    impact: emptyCounts(QUERY_LIBRARY_IMPACT_FILTERS),
  }
  const selected: QueryRegistryEntry[] = []

  for (const entry of searchFiltered) {
    const views = viewMatches(entry, isCached)
    const sources = sourceMatches(entry)
    const parameters = parameterMatches(entry)
    const activities = activityMatches(entry, now)
    const impacts = impactMatches(entry)
    const matchesView = views[view]
    const matchesSource = sources[source]
    const matchesParams = parameters[params]
    const matchesActivity = activities[activity]
    const matchesSelectedImpact = impacts[impact]

    if (
      matchesSource &&
      matchesParams &&
      matchesActivity &&
      matchesSelectedImpact
    ) {
      for (const candidate of QUERY_LIBRARY_VIEWS) {
        if (views[candidate]) facetCounts.view[candidate] += 1
      }
    }
    if (
      matchesView &&
      matchesParams &&
      matchesActivity &&
      matchesSelectedImpact
    ) {
      for (const candidate of QUERY_LIBRARY_SOURCES) {
        if (sources[candidate]) facetCounts.source[candidate] += 1
      }
    }
    if (
      matchesView &&
      matchesSource &&
      matchesActivity &&
      matchesSelectedImpact
    ) {
      for (const candidate of QUERY_LIBRARY_PARAMETER_FILTERS) {
        if (parameters[candidate]) facetCounts.params[candidate] += 1
      }
    }
    if (
      matchesView &&
      matchesSource &&
      matchesParams &&
      matchesSelectedImpact
    ) {
      for (const candidate of QUERY_LIBRARY_ACTIVITY_WINDOWS) {
        if (activities[candidate]) facetCounts.activity[candidate] += 1
      }
    }
    if (matchesView && matchesSource && matchesParams && matchesActivity) {
      for (const candidate of QUERY_LIBRARY_IMPACT_FILTERS) {
        if (impacts[candidate]) facetCounts.impact[candidate] += 1
      }
    }

    if (
      matchesView &&
      matchesSource &&
      matchesParams &&
      matchesActivity &&
      matchesSelectedImpact
    ) {
      selected.push(entry)
    }
  }

  selected.sort((left, right) => compareQueries(left, right, sort))

  return {
    queries: selected,
    counts: facetCounts.view,
    sourceCounts: facetCounts.source,
    facetCounts,
    isFiltered:
      Boolean(searchTerm.trim()) ||
      view !== 'all' ||
      source !== 'all' ||
      params !== 'all' ||
      activity !== 'all' ||
      impact !== 'all',
  }
}
