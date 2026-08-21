import type { QueryClient } from '@tanstack/react-query'
import { setAnalysisRunObserver } from '../../../lib/analysisRuns'
import { QUERY_REGISTRY_ROOT_KEY } from '../../../lib/useQueryRegistry'
import { latestAnalysisQueryKey } from '../saved/useLatestAnalysis'
import { analysisHistoryQueryKey } from './useStoredAnalysis'

/**
 * A run that finishes while nobody is watching still changes what the library
 * shows: the card footer's stored-analysis outcome, and that query's analysis
 * history. Closing the analyze drawer mid-run is exactly that case, so the
 * store reports finished runs here rather than to whichever view happens to be
 * mounted.
 */
export function installAnalysisRunInvalidation(queryClient: QueryClient): void {
  setAnalysisRunObserver((queryHash) => {
    void queryClient.invalidateQueries({
      queryKey: latestAnalysisQueryKey(queryHash),
    })
    void queryClient.invalidateQueries({
      queryKey: analysisHistoryQueryKey(queryHash),
    })
    // A query analyzed for the first time only gains its footer outcome once
    // the registry row carries the analysis date the card reads.
    void queryClient.invalidateQueries({
      queryKey: QUERY_REGISTRY_ROOT_KEY,
    })
  })
}
