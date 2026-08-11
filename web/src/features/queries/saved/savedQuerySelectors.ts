import { filterQueriesBySearch } from '../../../lib/queryIdentity'
import {
  byImpact,
  isHeroCandidate,
  queryImpactMs,
} from '../../../lib/queryImpact'
import { fillCapturedParams, hasParameters } from '../../../lib/sqlParameters'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

export type SourceVariant = 'informative' | 'rising' | 'positive' | 'neutral'

export type SavedQuerySource = {
  label: string
  variant: SourceVariant
}

const SOURCE_META: Record<string, SavedQuerySource> = {
  'top-historical': { label: 'Slow Queries', variant: 'informative' },
  top: { label: 'Slow Queries', variant: 'informative' },
  ask: { label: 'Ask', variant: 'rising' },
  prompt: { label: 'Ask', variant: 'rising' },
  cache: { label: 'Cache', variant: 'positive' },
  web: { label: 'Manual', variant: 'neutral' },
  manual: { label: 'Manual', variant: 'neutral' },
  file: { label: 'Manual', variant: 'neutral' },
}

export function getSourceMeta(source: string): SavedQuerySource {
  return SOURCE_META[source] ?? { label: 'Manual', variant: 'neutral' }
}

export { deriveQueryName } from '../../../lib/queryIdentity'

export function concreteSqlForTest(
  sql: string,
  stored: Record<string, unknown>
): string | null {
  const filled = fillCapturedParams(sql, stored)
  return hasParameters(filled) ? null : filled
}

export type SavedQuerySelection = {
  searchFiltered: QueryRegistryEntry[]
  sourceOptions: Array<{ label: string; count: number }>
  filteredQueries: QueryRegistryEntry[]
  displayedQueries: QueryRegistryEntry[]
  hero: QueryRegistryEntry | null
  heroMoreCount: number
  hasMeasuredImpact: boolean
  isFiltered: boolean
}

function hasLifecycleReadModel(entry: QueryRegistryEntry) {
  return (
    entry.saved_at !== undefined ||
    entry.first_observed_at !== undefined ||
    entry.last_analyzed_at !== undefined ||
    entry.last_compared_at !== undefined
  )
}

export function selectSavedQueries({
  queries,
  searchTerm,
  sourceFilter,
  isCached,
}: {
  queries: QueryRegistryEntry[]
  searchTerm: string
  sourceFilter: string
  isCached: (hash: string) => boolean
}): SavedQuerySelection {
  // During the T7A compatibility window the registry also contains automatic
  // observations. Keep the legacy Saved pane truthful until the unified
  // library replaces this adapter. Old test fixtures/API builds without the
  // additive lifecycle fields retain their historical behavior.
  const savedQueries = queries.filter(
    (entry) => !hasLifecycleReadModel(entry) || Boolean(entry.saved_at)
  )
  const term = searchTerm.trim().toLowerCase()
  const searchFiltered = filterQueriesBySearch(savedQueries, searchTerm)

  const sourceLabels = Array.from(
    new Set(savedQueries.map((entry) => getSourceMeta(entry.source).label))
  )
  const sourceOptions = sourceLabels.map((label) => ({
    label,
    count: searchFiltered.filter(
      (entry) => getSourceMeta(entry.source).label === label
    ).length,
  }))

  const sourceFiltered =
    sourceFilter === 'all'
      ? searchFiltered
      : searchFiltered.filter(
          (entry) => getSourceMeta(entry.source).label === sourceFilter
        )
  const filteredQueries = [...sourceFiltered].sort(byImpact)
  const heroCandidates = term
    ? []
    : filteredQueries.filter((entry) =>
        isHeroCandidate(entry, isCached(entry.hash))
      )
  const hero = heroCandidates[0] ?? null

  return {
    searchFiltered,
    sourceOptions,
    filteredQueries,
    displayedQueries: hero
      ? filteredQueries.filter((entry) => entry.hash !== hero.hash)
      : filteredQueries,
    hero,
    heroMoreCount: Math.max(0, heroCandidates.length - 1),
    hasMeasuredImpact: filteredQueries.some(
      (entry) => queryImpactMs(entry) > 0
    ),
    isFiltered: term.length > 0 || sourceFilter !== 'all',
  }
}
