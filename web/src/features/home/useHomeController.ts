import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useTarget } from '../../hooks/useTarget'
import { fetchQueryRegistry, fetchSchemaStatus } from '../../lib/api'
import { useBackgroundRuns } from '../../lib/backgroundRuns'
import { formatTimestamp } from '../../lib/formatters'
import { fetchAuditRuns } from '../../lib/useAudit'
import { useEnvRequirements } from '../../lib/useEnvRequirements'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { continueItems, deriveHomeState, portfolioCounts } from './homeModel'

export type AiKeyState = 'loading' | 'ready' | 'missing' | 'unknown'
export type HomePhase = 'loading' | 'error' | 'degraded' | 'ready'

function detail(error: unknown) {
  return error instanceof Error ? error.message : String(error ?? '')
}

export function useHomeController() {
  const { target: contextTarget } = useTarget()
  const statusQuery = useSystemStatus()
  const envQuery = useEnvRequirements()
  const backgroundRuns = useBackgroundRuns()

  const status = statusQuery.data
  const targetCount = status?.targets?.length ?? 0
  const hasTargets = targetCount > 0
  const target =
    contextTarget ?? status?.default_target ?? status?.targets?.[0]?.name ?? ''

  const schemaQuery = useQuery({
    queryKey: ['home', 'schema-status', target],
    queryFn: ({ signal }) => fetchSchemaStatus(target, signal),
    staleTime: 60_000,
    enabled: hasTargets && Boolean(target),
  })

  const auditQuery = useQuery({
    queryKey: ['audit-runs', target],
    queryFn: () => fetchAuditRuns(target || undefined),
    staleTime: 60_000,
    enabled: hasTargets,
  })

  const registryQuery = useQuery({
    queryKey: ['home', 'registry'],
    queryFn: () => fetchQueryRegistry(),
    staleTime: 60_000,
    enabled: hasTargets,
  })

  const aiKeyState: AiKeyState = envQuery.isPending
    ? 'loading'
    : envQuery.isError
      ? 'unknown'
      : envQuery.data?.requirements.find(
            (requirement) => requirement.kind === 'anthropic_api_key'
          )?.satisfied
        ? 'ready'
        : 'missing'

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
  const auditRuns = auditQuery.data?.runs ?? []
  const lastAudit = auditRuns[0]
  const oldestAudit = auditRuns[auditRuns.length - 1]
  const retentionDays = oldestAudit
    ? Math.floor(
        (Date.now() - new Date(oldestAudit.started_at).getTime()) / 86_400_000
      )
    : null

  const statusFailure = statusQuery.error || status?.error
  const phase: HomePhase = statusQuery.isPending
    ? 'loading'
    : statusQuery.isError || Boolean(status?.error)
      ? 'error'
      : hasTargets && schemaQuery.isPending
        ? 'loading'
        : hasTargets && schemaQuery.isError
          ? 'degraded'
          : 'ready'

  return {
    phase,
    state: deriveHomeState(targetCount, schemaQuery.data?.exists),
    target,
    aiKeyState,
    counts,
    recents,
    retentionDays,
    lastAuditLabel: lastAudit ? formatTimestamp(lastAudit.started_at) : null,
    registryState: registryQuery.isPending
      ? ('loading' as const)
      : registryQuery.isError
        ? ('error' as const)
        : ('ready' as const),
    auditState: auditQuery.isPending
      ? ('loading' as const)
      : auditQuery.isError
        ? ('error' as const)
        : ('ready' as const),
    errorDetail:
      phase === 'error'
        ? detail(statusFailure)
        : phase === 'degraded'
          ? detail(schemaQuery.error)
          : undefined,
    retryCore: () => {
      void statusQuery.refetch()
      if (hasTargets) void schemaQuery.refetch()
    },
    retryRegistry: () => void registryQuery.refetch(),
    retryAudit: () => void auditQuery.refetch(),
  }
}
