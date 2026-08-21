import { useQuery } from '@tanstack/react-query'
import { fetchQueryRegistryReadModel } from '../../lib/api'

export const topQueryHashQueryKey = (target: string) =>
  ['setupTopQuery', target] as const

/**
 * The query the setup guide's "Analyze a query" step should open: the one with
 * the most database time behind it, which is the library's own default order.
 * Read only while the open panel actually needs it.
 */
export function useTopQueryHash(
  target: string | undefined,
  enabled: boolean
): string | undefined {
  const { data } = useQuery({
    queryKey: topQueryHashQueryKey(target ?? ''),
    queryFn: () =>
      fetchQueryRegistryReadModel({
        target,
        view: 'all',
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
        sort: 'highest-impact',
        limit: 1,
      }),
    enabled,
    staleTime: 60 * 1000,
    retry: false,
  })

  return data?.queries[0]?.hash
}
