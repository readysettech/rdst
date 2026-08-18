import { useEffect, useState } from 'react'
import { fetchParameterSuggestions, type ParameterSuggestionsResponse } from './api'

/**
 * Loads value suggestions for a templated query while the parameter dialog is
 * open. Suggestions come from the database (captured statement, sampled
 * column values); a failed lookup just yields no suggestions.
 */
export function useParameterSuggestions(
  query: string | null,
  target: string | null | undefined,
  queryHash?: string | null,
  enabled = true
): { suggestions: ParameterSuggestionsResponse | null; loading: boolean } {
  const [suggestions, setSuggestions] = useState<ParameterSuggestionsResponse | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!enabled || !query || !target) {
      setSuggestions(null)
      return
    }
    let cancelled = false
    setLoading(true)
    fetchParameterSuggestions(query, target, queryHash)
      .then((result) => {
        if (!cancelled) setSuggestions(result)
      })
      .catch(() => {
        if (!cancelled) setSuggestions(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [enabled, query, target, queryHash])

  return { suggestions, loading }
}
