import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { actionKeys } from '@/lib/monitor/actions'
import { monitorKeys } from '@/lib/monitor/keys'
import type { AnalyzeJob } from '@/lib/monitor/types'

/** Queries with an analysis running, or queued to start on its own. */
export function activeAnalysisKeys(
  jobs: AnalyzeJob[] | undefined
): Set<string> {
  return new Set(
    (jobs ?? [])
      .filter(
        (job) =>
          job.state === 'running' ||
          (job.state === 'queued' && job.queue_state !== 'held')
      )
      .map((job) => job.query_key)
  )
}

/** A finished analysis changes the list, the row, its digest and the plan. */
export function refreshAnalyzed(
  client: QueryClient,
  target: string,
  keys: string[]
): void {
  for (const part of ['queries', 'query'])
    void client.invalidateQueries({ queryKey: ['monitor', target, part] })
  for (const key of keys)
    void client.invalidateQueries({
      queryKey: monitorKeys.queryAnalysis(target, key),
    })
  void client.invalidateQueries({ queryKey: actionKeys.actions(target) })
  void client.invalidateQueries({ queryKey: actionKeys.indexPlan(target) })
  void client.invalidateQueries({ queryKey: actionKeys.global })
}

/**
 * The analyses under way on a database, from its job list. When one leaves the list's
 * running or queued set, the views it changes refetch. `follow` marks a key just started
 * here, so a job that finishes before the list sees it running still refreshes them.
 */
export function useFollowAnalyses(
  target: string,
  jobs: AnalyzeJob[] | undefined
): { active: Set<string>; follow: (key: string) => void } {
  const client = useQueryClient()
  const active = useMemo(() => activeAnalysisKeys(jobs), [jobs])
  const watched = useRef({ target, keys: new Set<string>() })
  if (watched.current.target !== target)
    watched.current = { target, keys: new Set() }

  useEffect(() => {
    const finished = [...watched.current.keys].filter((key) => !active.has(key))
    watched.current.keys = new Set(active)
    if (finished.length) refreshAnalyzed(client, target, finished)
  }, [active, client, target])

  const follow = useCallback(
    (key: string) => {
      watched.current.keys.add(key)
      for (const part of ['queries', 'query'])
        void client.invalidateQueries({ queryKey: ['monitor', target, part] })
    },
    [client, target]
  )
  return { active, follow }
}
