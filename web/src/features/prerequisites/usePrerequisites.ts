import { useQuery } from '@tanstack/react-query'
import { fetchPrerequisites, type PrereqResult, prereqKey } from './api'
import { type CheckerPhase, checkerPhase, pollInterval } from './model'

export interface PrerequisitesState {
  result: PrereqResult | undefined
  phase: CheckerPhase
  /** A request is in flight (first load or a re-check). */
  checking: boolean
  error: Error | null
  /** ms between automatic checks, or false once everything passes. */
  interval: number | false
  /** When the latest answer arrived (ms epoch), for "checked 3 s ago". */
  updatedAt: number
  recheck: () => void
}

/**
 * Live prerequisites for one saved target. Re-checks every few seconds while
 * something still fails, so fixes made in another window show up on their own.
 * Callers that share a target share one query.
 */
export function usePrerequisites(
  target: string | null | undefined,
  { enabled = true, live = true }: { enabled?: boolean; live?: boolean } = {}
): PrerequisitesState {
  const query = useQuery({
    queryKey: prereqKey(target ?? ''),
    queryFn: ({ signal }) => fetchPrerequisites(target ?? '', { signal }),
    enabled: enabled && Boolean(target),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchInterval: (current) =>
      live
        ? pollInterval(
            checkerPhase(current.state.data, current.state.status === 'error'),
            current.state.data?.connection_error
          )
        : false,
  })
  const phase = checkerPhase(query.data, query.isError)
  return {
    result: query.data,
    phase,
    checking: query.isFetching,
    error: query.error,
    interval: live ? pollInterval(phase, query.data?.connection_error) : false,
    updatedAt: Math.max(query.dataUpdatedAt, query.errorUpdatedAt),
    recheck: () => void query.refetch(),
  }
}
