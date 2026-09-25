// Pure list logic for the Database query list: search, filter, sort, counts, the
// significance gate on change, and the tail of text-less queries. Everything runs
// client-side over the one fetched page so each keystroke answers without a request.
import type { JevAssessmentSummary } from '@/lib/api'
import type { MonitorQuery } from '@/lib/monitor/types'
import { filterQueriesBySearch } from '@/lib/queryIdentity'
import { isStopped, relevantQueryKeys, windowEnd } from '../relevance'
import {
  CLASSIFICATION_FILTERS,
  CLASSIFICATION_LABELS,
  type ClassificationFilter,
  isClassificationFilter,
  matchesClassification,
} from './jev'
import {
  QUERY_STATUSES,
  type QueryStatus,
  type QueryStatusId,
  STATUS_LABELS,
} from './queryStatus'
import { rowScore, scoreValue } from './ScorePill'

/** One Status filter: the five statuses, analyzed, then the classification findings. */
export type QueryFilter =
  | 'all'
  | 'analyzed'
  | QueryStatusId
  | ClassificationFilter
export type QuerySort =
  | 'impact'
  | 'change'
  | 'qps'
  | 'latency'
  | 'score_low'
  | 'score_high'
export type DetailTab = 'performance' | 'analysis' | 'readyset'

export const QUERY_FILTERS: readonly QueryFilter[] = [
  'all',
  ...QUERY_STATUSES,
  'analyzed',
  ...CLASSIFICATION_FILTERS,
]
export const QUERY_SORTS: readonly QuerySort[] = [
  'impact',
  'change',
  'qps',
  'latency',
  'score_low',
  'score_high',
]
export const DETAIL_TABS: readonly DetailTab[] = [
  'performance',
  'analysis',
  'readyset',
]

export const FILTER_LABELS: Record<QueryFilter, string> = {
  all: 'All statuses',
  ...STATUS_LABELS,
  // The filter also holds statements RDST never analyzes.
  not_analyzed: 'Not analyzed',
  analyzed: 'Analyzed',
  ...CLASSIFICATION_LABELS,
}

export const SORT_LABELS: Record<QuerySort, string> = {
  impact: 'Impact',
  change: 'Biggest change',
  qps: 'Most frequent',
  latency: 'Slowest',
  score_low: 'Score, lowest first',
  score_high: 'Score, highest first',
}

export const SORT_HINTS: Record<QuerySort, string> = {
  impact: 'Database time in the range',
  change: 'Largest significant latency change',
  qps: 'Calls per second',
  latency: 'Mean latency per call',
  score_low: 'Deep analysis score; queries without one last',
  score_high: 'Deep analysis score; queries without one last',
}

// Filters from earlier links, and the retired Jev filter's values.
const LEGACY_FILTERS: Record<string, QueryFilter> = {
  new: 'all',
  muted: 'all',
}

/**
 * The filter from the URL. `jev` is the retired Jev filter: its findings map to the
 * same classification filter, its priority bands to no filter.
 */
export function parseFilter(value: unknown, jev?: unknown): QueryFilter {
  if (QUERY_FILTERS.includes(value as QueryFilter)) return value as QueryFilter
  if (typeof value === 'string' && value in LEGACY_FILTERS)
    return LEGACY_FILTERS[value]
  if (isClassificationFilter(jev)) return jev
  return 'all'
}

export function parseSort(value: unknown): QuerySort {
  return QUERY_SORTS.includes(value as QuerySort)
    ? (value as QuerySort)
    : 'impact'
}

/** `queries=key1,key2` from the URL: the keys, or null when absent or empty. */
export function parseQueryKeys(value: unknown): string[] | null {
  if (typeof value !== 'string') return null
  const keys = [
    ...new Set(
      value
        .split(',')
        .map((key) => key.trim())
        .filter(Boolean)
    ),
  ]
  return keys.length ? keys : null
}

export function parseTab(value: unknown): DetailTab {
  return DETAIL_TABS.includes(value as DetailTab)
    ? (value as DetailTab)
    : 'performance'
}

/** What the filter reads for each row: its Status and its classification. */
export interface RowFacts {
  status: QueryStatus
  classification: JevAssessmentSummary | null
  /** A deep analysis finished for the query, whatever its Status now. */
  analyzed?: boolean
}

export type FactsOf = (query: MonitorQuery) => RowFacts

export function matchesFilter(facts: RowFacts, filter: QueryFilter) {
  if (filter === 'all') return true
  if (isClassificationFilter(filter))
    return matchesClassification(facts.classification, filter)
  // Needs attention counts every query with an open Inbox item, Watch included.
  if (filter === 'attention')
    return facts.status.id === 'attention' || facts.status.id === 'watch'
  if (filter === 'analyzed')
    return (
      facts.analyzed ??
      (facts.status.id === 'fix_ready' || facts.status.id === 'healthy')
    )
  return facts.status.id === filter
}

export type FilterCounts = Record<QueryFilter, number>

export function countQueries(
  queries: MonitorQuery[],
  factsOf: FactsOf
): FilterCounts {
  const facts = queries.map(factsOf)
  return Object.fromEntries(
    QUERY_FILTERS.map((filter) => [
      filter,
      facts.filter((row) => matchesFilter(row, filter)).length,
    ])
  ) as FilterCounts
}

/** Search matches display name, SQL text, tables and a fingerprint prefix. */
export function searchQueries(queries: MonitorQuery[], search: string) {
  if (!search.trim()) return queries
  const entries = queries.map((query) => ({
    query,
    hash: query.key,
    sql: query.text,
    tag: query.display_name,
    question: query.tables.join(' '),
  }))
  return filterQueriesBySearch(entries, search).map((entry) => entry.query)
}

/** The change worth showing: significant by the backend or by the brief's rule. */
export function significantLatencyChange(query: MonitorQuery): number | null {
  const change = query.change.latency
  if (change === null) return null
  if (query.change.significant) return change
  if (Math.abs(change) >= 0.2 && query.db_time_share >= 0.01) return change
  return null
}

function changeMagnitude(query: MonitorQuery) {
  return Math.abs(significantLatencyChange(query) ?? 0)
}

/** The deep analysis score a row shows (0-100), or null when it has none. */
function analysisScore(query: MonitorQuery): number | null {
  const score = rowScore(query)
  return score ? scoreValue(score) : null
}

/** By score either way; queries without one always go last. */
const byScore = (direction: 1 | -1) => (a: MonitorQuery, b: MonitorQuery) => {
  const [left, right] = [analysisScore(a), analysisScore(b)]
  if (left == null || right == null)
    return (left == null ? 1 : 0) - (right == null ? 1 : 0)
  return direction * (left - right)
}

const SORTERS: Record<QuerySort, (a: MonitorQuery, b: MonitorQuery) => number> =
  {
    impact: (a, b) => b.db_time_ms - a.db_time_ms,
    change: (a, b) => changeMagnitude(b) - changeMagnitude(a),
    qps: (a, b) => b.qps - a.qps,
    latency: (a, b) => b.mean_ms - a.mean_ms,
    score_low: byScore(1),
    score_high: byScore(-1),
  }

export function sortQueries(queries: MonitorQuery[], sort: QuerySort) {
  const compare = SORTERS[sort]
  return [...queries].sort(
    (a, b) =>
      compare(a, b) || b.db_time_ms - a.db_time_ms || a.key.localeCompare(b.key)
  )
}

/** Rows too small to earn a card when their text is also missing. */
export const TEXTLESS_TAIL_SHARE = 0.001

export function isTextlessTail(query: MonitorQuery) {
  return (
    query.text_status === 'unavailable' &&
    query.db_time_share < TEXTLESS_TAIL_SHARE &&
    !query.state.includes('attention') &&
    !query.state.includes('stuck')
  )
}

/**
 * Rows that stay collapsed until asked for: queries outside the relevance gate,
 * and small queries that stopped running. Stuck queries always show.
 */
export function isQuiet(
  query: MonitorQuery,
  relevant: Set<string>,
  end: number
) {
  if (query.state.includes('stuck')) return false
  if (!relevant.has(query.key)) return true
  return query.db_time_share < 0.02 && isStopped(query, end)
}

export interface QueryListView {
  rows: MonitorQuery[]
  tail: MonitorQuery[]
  tailShare: number
  quiet: MonitorQuery[]
  quietShare: number
  quietStopped: number
  counts: FilterCounts
  searched: MonitorQuery[]
}

export function selectQueryList(
  queries: MonitorQuery[],
  {
    search,
    filter,
    sort,
    factsOf,
  }: { search: string; filter: QueryFilter; sort: QuerySort; factsOf: FactsOf }
): QueryListView {
  const searched = searchQueries(queries, search)
  const counts = countQueries(searched, factsOf)
  const visible = sortQueries(
    searched.filter((query) => matchesFilter(factsOf(query), filter)),
    sort
  )
  // A search is an explicit ask, so it shows every match as a card.
  const collapse = !search.trim()
  const relevant = relevantQueryKeys(queries)
  const end = windowEnd(queries)
  const tail = collapse ? visible.filter(isTextlessTail) : []
  const quietAll = collapse
    ? visible.filter(
        (query) =>
          !isTextlessTail(query) &&
          isQuiet(query, relevant, end) &&
          !matchesFilter(factsOf(query), 'attention')
      )
    : []
  // Never collapse everything: a filter that only matches quiet rows shows them.
  const quiet = quietAll.length + tail.length < visible.length ? quietAll : []
  const collapsed = new Set([...tail, ...quiet])
  const share = (list: MonitorQuery[]) =>
    list.reduce((sum, query) => sum + query.db_time_share, 0)
  return {
    rows: visible.filter((query) => !collapsed.has(query)),
    tail,
    tailShare: share(tail),
    quiet,
    quietShare: share(quiet),
    quietStopped: quiet.filter((query) => isStopped(query, end)).length,
    counts,
    searched,
  }
}

/**
 * The list the Database page shows: the top queries of the window first (the same set
 * the hero bands), everything else after, and the textless tail last. With no search,
 * filter or table picked, only the first `limit` rows show; "Show more" raises it.
 */
export function orderedRows(
  view: QueryListView,
  sort: QuerySort,
  table: string | null,
  keys: readonly string[] | null = null
): MonitorQuery[] {
  const inTable = (query: MonitorQuery) =>
    !table || query.tables.includes(table)
  const picked = keys ? new Set(keys) : null
  const inKeys = (query: MonitorQuery) => !picked || picked.has(query.key)
  return [
    ...sortQueries([...view.rows, ...view.quiet], sort),
    ...view.tail,
  ].filter((query) => inTable(query) && inKeys(query))
}

/** Tables the queries touch, most time first. */
export function tablesOf(queries: MonitorQuery[]): string[] {
  return tableCounts(queries).map(({ table }) => table)
}

/** Each table with how many queries touch it, by database time, heaviest first. */
export function tableCounts(
  queries: MonitorQuery[]
): { table: string; count: number }[] {
  const time = new Map<string, { ms: number; count: number }>()
  for (const query of queries)
    for (const table of new Set(query.tables)) {
      const entry = time.get(table) ?? { ms: 0, count: 0 }
      entry.ms += query.db_time_ms
      entry.count += 1
      time.set(table, entry)
    }
  return [...time.entries()]
    .sort((a, b) => b[1].ms - a[1].ms)
    .map(([table, { count }]) => ({ table, count }))
}

/** Empty-result copy that names the thing to remove, like the Queries page. */
export function recoveryCopy({
  filter,
  search,
  total,
  searchedCount,
}: {
  filter: QueryFilter
  search: string
  total: number
  searchedCount: number
}): { title: string; body: string; clear: 'filter' | 'search' } {
  const plural = (n: number) => `${n} ${n === 1 ? 'query' : 'queries'}`
  if (filter !== 'all' && searchedCount > 0) {
    const label = FILTER_LABELS[filter]
    const title =
      filter === 'attention'
        ? 'No queries need attention'
        : filter === 'not_analyzed'
          ? 'Every query is analyzed'
          : `No queries match '${label}'`
    const suffix = search.trim() ? ` matching "${search.trim()}"` : ''
    return {
      title: `${title}${suffix}`,
      body: `Remove '${label}' to show ${plural(searchedCount)}.`,
      clear: 'filter',
    }
  }
  return {
    title: `No queries match "${search.trim()}"`,
    body: `Clear the search to show ${plural(total)}.`,
    clear: 'search',
  }
}

/** The backend supplies the friendly name, including family disambiguation. */
export function queryName(query: MonitorQuery): string {
  return query.display_name || 'Query without text'
}
