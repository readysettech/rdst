import { useQueries } from '@tanstack/react-query'
import {
  fetchParameterSuggestions,
  type ParameterSuggestionsResponse,
} from './api'

export interface AutoSuggestQuery {
  /** Query registry hash; keys the cache and the persisted suggestion. */
  hash: string
  sql: string
  target: string
}

/**
 * Backend value suggestions (pg_stats MCVs, bounded DISTINCT sampling) for a
 * batch of templated queries, fetched in the background and cached by
 * (target, hash) so reselecting a query never re-queries the database within
 * the stale window. Used by multi-query setup screens (Compare, Load test)
 * to have suggestions ready before the user opens a parameter dialog.
 */
export function useAutoParameterSuggestions(
  queries: AutoSuggestQuery[]
): Map<string, ParameterSuggestionsResponse> {
  const results = useQueries({
    queries: queries.map((query) => ({
      queryKey: ['parameter-suggestions', query.target, query.hash],
      queryFn: () =>
        fetchParameterSuggestions(query.sql, query.target, query.hash),
      staleTime: 5 * 60_000,
      gcTime: 30 * 60_000,
      retry: false,
    })),
  })
  const suggestions = new Map<string, ParameterSuggestionsResponse>()
  queries.forEach((query, index) => {
    const data = results[index]?.data
    if (data) suggestions.set(query.hash, data)
  })
  return suggestions
}
