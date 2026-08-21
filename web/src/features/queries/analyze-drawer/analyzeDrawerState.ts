/**
 * URL state for the analyze drawer (RDST UX plan, A5).
 *
 * The drawer is owned by `/queries?analyze=<hash>`, so opening, closing and
 * switching between stored analyses are ordinary history entries — back closes
 * the drawer instead of leaving the page, and a reload reopens it. `/results`
 * keeps its own deep links; `drawerResultsSearch` is the bridge between them,
 * both for rendering the drawer and for its "Open full view" escape hatch.
 */

import type { QueryRegistryEntry } from '../../../lib/api'
import type {
  AnalyzeDrawerTab,
  QueryLibrarySearch,
} from '../library/queryLibraryState'
import type { ResultsSearch } from '../results/types'

export type { AnalyzeDrawerTab } from '../library/queryLibraryState'

export interface AnalyzeDrawerLink {
  hash: string
  /** A stored analysis to show read-only; absent means measure the query. */
  analysisId?: string
  /** Measure the query even though stored analyses exist. */
  rerun?: boolean
  /** Which pane to open on. Absent means Analyze, as it always did. */
  tab?: AnalyzeDrawerTab
}

/** The pane a link is asking for, resolved. */
export function analyzeDrawerTab(link: {
  tab?: AnalyzeDrawerTab
}): AnalyzeDrawerTab {
  return link.tab === 'overview' ? 'overview' : 'analyze'
}

/** The open drawer described by a Query Library URL, if any. */
export function analyzeDrawerLink(
  search: QueryLibrarySearch
): AnalyzeDrawerLink | null {
  if (!search.analyze) return null
  return {
    hash: search.analyze,
    analysisId: search.analysisId,
    rerun: search.rerun,
    tab: analyzeDrawerTab(search),
  }
}

/** Search patch that opens the drawer, or closes it when given no link. */
export function analyzeDrawerPatch(
  link: AnalyzeDrawerLink | null
): Partial<QueryLibrarySearch> {
  if (!link) {
    return {
      analyze: undefined,
      analysisId: undefined,
      rerun: undefined,
      tab: undefined,
    }
  }
  return {
    analyze: link.hash,
    analysisId: link.analysisId,
    rerun: link.rerun ? true : undefined,
    // Analyze is the default, so only Overview needs saying.
    tab: analyzeDrawerTab(link) === 'overview' ? 'overview' : undefined,
  }
}

/** The filters to restore when the analysis is left for the full page. */
export function analyzeReturnSearch(
  search: QueryLibrarySearch
): QueryLibrarySearch {
  return {
    view: search.view,
    q: search.q,
    source: search.source,
    params: search.params,
    activity: search.activity,
    impact: search.impact,
    sort: search.sort,
  }
}

function capturedParameters(entry: QueryRegistryEntry) {
  const captured = entry.most_recent_params
  if (!captured || Object.keys(captured).length === 0) return undefined
  return JSON.stringify(captured)
}

/**
 * The `/results` search the drawer is showing. `analysisId` is resolved by the
 * caller: the URL's own id, or the latest stored analysis when the drawer was
 * opened on a query rather than on a specific run.
 */
export function drawerResultsSearch({
  entry,
  hash,
  analysisId,
  returnSearch,
  sqlOverride,
  target,
}: {
  entry: QueryRegistryEntry
  hash: string
  analysisId?: string
  returnSearch: QueryLibrarySearch
  /** Parameter values the user substituted in this drawer session. */
  sqlOverride?: string
  target?: string | null
}): ResultsSearch {
  return {
    query: (sqlOverride ?? entry.sql).trim(),
    target: entry.target || target || undefined,
    fast: false,
    params: capturedParameters(entry),
    returnSearch: JSON.stringify(returnSearch),
    origin: 'query-library',
    hash,
    analysisId,
  }
}
