import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useTarget } from '../../hooks/useTarget'
import { fetchQueryRegistry } from '../../lib/api'
import { useBackgroundRuns } from '../../lib/backgroundRuns'
import {
  useInbox,
  useMonitorHealth,
  useMonitorQueries,
  useMonitorSettings,
} from '../../lib/monitor/hooks'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { inboxSignals } from '../database/queries/queryStatus'
import { useMonitorPulse } from '../monitor-kit/useMonitorPulse'
import { continueItems, deriveHomeState, portfolioCounts } from './homeModel'

export type HomePhase = 'loading' | 'error' | 'degraded' | 'ready'

/** The Database page's default range, so Home warms the same cache entries. */
const HOME_RANGE = '1h'

function detail(error: unknown) {
  return error instanceof Error ? error.message : String(error ?? '')
}

export function useHomeController() {
  const { target: contextTarget } = useTarget()
  const statusQuery = useSystemStatus()
  const backgroundRuns = useBackgroundRuns()

  const status = statusQuery.data
  const targetCount = status?.targets?.length ?? 0
  const hasTargets = targetCount > 0
  const target =
    contextTarget ?? status?.default_target ?? status?.targets?.[0]?.name ?? ''
  const monitorTarget = hasTargets ? target : ''

  const settingsQuery = useMonitorSettings(monitorTarget)
  const healthQuery = useMonitorHealth(monitorTarget, HOME_RANGE)
  const queriesQuery = useMonitorQueries(monitorTarget, HOME_RANGE)
  const monitoring = settingsQuery.data?.enabled
  const pulse = useMonitorPulse(monitoring ? monitorTarget : null)
  // "Need attention" follows the Database page: queries with an open Inbox item.
  const inboxQuery = useInbox({
    state: 'open',
    target: monitorTarget,
    include_digest: true,
  })
  const attentionCount = useMemo(
    () =>
      inboxQuery.data
        ? inboxSignals(inboxQuery.data.items, monitorTarget).size
        : null,
    [inboxQuery.data, monitorTarget]
  )

  const registryQuery = useQuery({
    queryKey: ['home', 'registry'],
    queryFn: () => fetchQueryRegistry(),
    staleTime: 60_000,
    enabled: hasTargets,
  })

  const comparedHashes = useMemo(
    () =>
      new Set(
        backgroundRuns
          .filter(
            (run) =>
              (run.kind === 'speed_test' || run.kind === 'cache_compare') &&
              run.target === target &&
              run.status === 'done' &&
              Boolean(run.result || run.compareResult) &&
              Boolean(run.queryHash)
          )
          .map((run) => run.queryHash as string)
      ),
    [backgroundRuns, target]
  )

  const entries = registryQuery.data?.queries ?? []
  const counts = portfolioCounts(entries, target || undefined, comparedHashes)
  const recents = continueItems(entries, target || undefined, comparedHashes)
  const history = healthQuery.data?.history
  const historyDays = history?.first_ts
    ? Math.floor((Date.now() / 1000 - history.first_ts) / 86_400)
    : null

  const statusFailure = statusQuery.error || status?.error
  const phase: HomePhase = statusQuery.isPending
    ? 'loading'
    : statusQuery.isError || Boolean(status?.error)
      ? 'error'
      : hasTargets && (settingsQuery.isError || healthQuery.isError)
        ? 'degraded'
        : hasTargets && (settingsQuery.isPending || healthQuery.isPending)
          ? 'loading'
          : 'ready'

  return {
    phase,
    state: deriveHomeState(targetCount, monitoring, history),
    target,
    health: healthQuery.data,
    monitoringOn: Boolean(monitoring),
    monitorQueries: queriesQuery.data,
    attentionCount,
    pulse,
    counts,
    recents,
    historyDays,
    errorDetail:
      phase === 'error'
        ? detail(statusFailure)
        : phase === 'degraded'
          ? detail(settingsQuery.error ?? healthQuery.error)
          : undefined,
    retryCore: () => {
      void statusQuery.refetch()
      if (hasTargets) {
        void settingsQuery.refetch()
        void healthQuery.refetch()
      }
    },
  }
}
