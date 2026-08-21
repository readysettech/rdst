import { useQuery } from '@tanstack/react-query'
import {
  fetchLatestAnalysis,
  type QueryAnalysisSummary,
} from '../../../lib/api'

/** Cache key for one query's latest stored analysis summary. */
export const latestAnalysisQueryKey = (hash: string) =>
  ['latestAnalysis', hash] as const

/**
 * Latest stored analysis summary for one query hash. Every analyzed card in
 * the library asks for its own, so the read goes through the shared query
 * cache: one request per query, shared by the card footer and the expanded
 * detail, and reused while the summary is fresh. The summary is enrichment, so
 * a missing record or a failed request simply yields null.
 */
export function useLatestAnalysisQuery(hash: string, enabled: boolean) {
  const { data, isPending, isFetched } = useQuery({
    queryKey: latestAnalysisQueryKey(hash),
    queryFn: () => fetchLatestAnalysis(hash),
    enabled: enabled && Boolean(hash),
    staleTime: 30 * 1000,
    retry: false,
  })

  const summary: QueryAnalysisSummary | null = data?.found
    ? data.analysis
    : null
  // A disabled query never settles, so "resolved" means the caller stopped
  // asking or the answer arrived — either way there is nothing left to wait on.
  return { summary, isResolved: !enabled || isFetched || !isPending }
}

export function useLatestAnalysis(hash: string, enabled: boolean) {
  return useLatestAnalysisQuery(hash, enabled).summary
}
