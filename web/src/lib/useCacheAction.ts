/**
 * Shared temporary Readyset speed-test action used by query surfaces.
 *
 * The name is retained while callers migrate, but this hook no longer creates
 * a durable cache. One background job provisions the sandbox, creates a
 * temporary cache, measures it, and removes it.
 */

import { toast } from '@rs/ui-new/use-toast'
import { useCallback, useMemo, useState } from 'react'
import { startCacheTestRun, useBackgroundRuns } from './backgroundRuns'

interface UseCacheActionOptions {
  target: string | null
  /** Called after the background comparison is accepted. */
  onCached?: (sql: string, id: string) => void
}

interface UseCacheActionReturn {
  cacheQuery: (sql: string, id: string) => void
  cachingId: string | null
  /** True when this browser retains a completed measured comparison. */
  isCached: (registryHash: string) => boolean
  isPending: boolean
}

export function useCacheAction({
  target,
  onCached,
}: UseCacheActionOptions): UseCacheActionReturn {
  const backgroundRuns = useBackgroundRuns()
  const [startingId, setStartingId] = useState<string | null>(null)

  const speedRuns = useMemo(
    () =>
      backgroundRuns.filter(
        (run) =>
          (run.kind === 'speed_test' || run.kind === 'cache_test') &&
          run.target === target
      ),
    [backgroundRuns, target]
  )

  const active = speedRuns.find(
    (run) =>
      run.status === 'running' ||
      run.status === 'reconnecting' ||
      run.status === 'needs_key'
  )

  const isCached = useCallback(
    (registryHash: string) =>
      speedRuns.some(
        (run) =>
          run.queryHash === registryHash &&
          run.status === 'done' &&
          Boolean(run.result)
      ),
    [speedRuns]
  )

  const cacheQuery = useCallback(
    (sql: string, id: string) => {
      if (!target) return
      setStartingId(id)
      void startCacheTestRun({
        query: sql,
        target,
        query_hash: id,
        iterations: 15,
        warmup: 5,
      }).then((runId) => {
        setStartingId(null)
        if (!runId) {
          toast({
            title: 'Comparison could not start',
            description: 'Open Jobs for details.',
            variant: 'negative',
          })
          return
        }
        toast({
          title: 'Comparison started',
          description:
            'Readyset will use a temporary cache and remove it after the test.',
          variant: 'positive',
        })
        onCached?.(sql, id)
      })
    },
    [target, onCached]
  )

  return {
    cacheQuery,
    cachingId: startingId ?? active?.queryHash ?? null,
    isCached,
    isPending: startingId !== null || Boolean(active),
  }
}
