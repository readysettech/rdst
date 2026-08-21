import { useQuery } from '@tanstack/react-query'
import {
  fetchQueryRegistryReadModel,
  type QueryRegistryEntry,
} from '../../../lib/api'
import { queryRegistryQueryKey } from '../../../lib/useQueryRegistry'

// A registry read like any other, so it sits under the registry prefix:
// discovery refreshes and row mutations reach it without knowing about it.
export const analyzeDrawerEntryQueryKey = (
  hash: string,
  target: string | null
) => [...queryRegistryQueryKey(target), 'drawer-entry', hash] as const

/**
 * The query a drawer link points at. Rows already loaded by the library answer
 * immediately; a link that arrives from Home, the setup guide or a reload is
 * resolved by narrowing the read model to that one hash, the same way the
 * library's own deep links reveal a query outside the current page.
 */
export function useAnalyzeDrawerEntry({
  hash,
  loaded,
  target,
}: {
  hash: string
  loaded: QueryRegistryEntry[]
  target?: string | null
}) {
  const listed = loaded.find((entry) => entry.hash === hash) ?? null
  const needsFetch = Boolean(hash) && !listed

  const lookup = useQuery({
    queryKey: analyzeDrawerEntryQueryKey(hash, target ?? null),
    queryFn: () =>
      fetchQueryRegistryReadModel({
        target,
        search: hash,
        view: 'all',
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
        sort: 'highest-impact',
        limit: 1,
      }),
    enabled: needsFetch,
    staleTime: 60 * 1000,
    retry: false,
  })

  const fetched =
    lookup.data?.queries.find((entry) => entry.hash === hash) ?? null

  return {
    entry: listed ?? fetched,
    isLoading: needsFetch && lookup.isPending,
    error:
      lookup.error instanceof Error
        ? lookup.error.message
        : lookup.error
          ? 'Could not read this query.'
          : null,
  }
}
