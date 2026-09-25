// Shared benchmark settings, runs, and history for a database or an opened query.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLoadTestController } from '@/features/caching/load-test/useLoadTestController'
import { fetchSandboxDiagnostics } from '@/features/caching/sandbox'
import type { QueryRegistryEntry } from '@/lib/api'
import { cancelBackgroundRun, useBackgroundRuns } from '@/lib/backgroundRuns'
import type { MonitorQuery } from '@/lib/monitor/types'
import {
  benchmarkTimeline,
  DEFAULT_PRESET,
  isLiveRun,
  latestQueryRun,
  type PresetId,
  presetById,
  presetForRequest,
  type QueryBenchmarkRun,
  readysetReadiness,
} from './benchmarkFlow'
import { hasQueryText } from './eligibility'

/** Rows scale with the Library size; finding one query by hash needs them all. */
const REGISTRY_LIMIT = 500

export const queryBenchmarksKey = (target: string, hash: string) =>
  ['query-benchmarks', target, hash] as const

async function fetchQueryBenchmarks(
  target: string,
  hash: string
): Promise<QueryBenchmarkRun[]> {
  const response = await fetch(
    `/api/query-registry/queries/${encodeURIComponent(hash)}/benchmarks?target=${encodeURIComponent(target)}`
  )
  // A backend from before saved benchmark history has no such route.
  if (response.status === 404) return []
  if (!response.ok) throw new Error(`Benchmark history: ${response.status}`)
  const body = (await response.json()) as { runs?: QueryBenchmarkRun[] }
  return body.runs ?? []
}

const NO_MONITORED_QUERIES: MonitorQuery[] = []

export function useQueryBenchmark(
  query: MonitorQuery | undefined,
  target: string,
  monitored: MonitorQuery[] = NO_MONITORED_QUERIES
) {
  const extraQueries = useMemo<QueryRegistryEntry[]>(
    () =>
      [...monitored, ...(query ? [query] : [])]
        .filter(hasQueryText)
        .map((item) => ({
          hash: item.key,
          tag: '',
          sql: item.text,
          target,
          source: 'monitor-benchmark',
          frequency: 0,
          last_analyzed: '',
          question: item.display_name,
        })),
    [monitored, query, target]
  )
  const controller = useLoadTestController({
    registryLimit: REGISTRY_LIMIT,
    sourceTarget: target,
    extraQueries,
    keepFilteredSelection: true,
  })
  const client = useQueryClient()
  const entry = controller.queries.find((item) => item.hash === query?.key)
  const identifier = entry ? entry.tag || entry.hash : null
  const identifiers = useMemo(
    () => [...(query ? [query.key] : []), ...(entry?.tag ? [entry.tag] : [])],
    [query?.key, entry?.tag]
  )

  const registryReady = !controller.registryLoading && !controller.listError

  const { setSelectedQueries } = controller
  useEffect(() => {
    if (identifier) setSelectedQueries([identifier])
  }, [identifier, setSelectedQueries])

  const runs = useBackgroundRuns()
  const targetRuns = runs.filter(
    (item) => item.kind === 'load_test' && item.target === target
  )
  const submittedRun = controller.submittedRequest
    ? targetRuns.find(
        (item) => item.loadRequest === controller.submittedRequest
      )
    : undefined
  const run =
    submittedRun ??
    (query
      ? latestQueryRun(runs, target, identifiers)
      : (targetRuns.find(isLiveRun) ?? targetRuns[targetRuns.length - 1]))
  const live = run ? isLiveRun(run) : false

  const history = useQuery({
    queryKey: queryBenchmarksKey(target, query?.key ?? ''),
    queryFn: () => fetchQueryBenchmarks(target, query?.key ?? ''),
    enabled: Boolean(target && query),
    staleTime: 30_000,
  })
  // A finished run is saved as it ends; read it back.
  const runId = run?.runId
  useEffect(() => {
    if (runId && !live && query)
      void client.invalidateQueries({
        queryKey: queryBenchmarksKey(target, query?.key ?? ''),
      })
  }, [runId, live, client, target, query?.key])

  const timeline = useMemo(
    () =>
      benchmarkTimeline({
        run,
        history: history.data ?? [],
        lastCompare: query?.last_compare,
      }),
    [run, history.data, query?.last_compare]
  )

  const sandbox = useQuery({
    queryKey: ['readyset-sandbox'],
    queryFn: fetchSandboxDiagnostics,
    staleTime: 5_000,
  })
  const readyset = readysetReadiness(
    sandbox.data as Parameters<typeof readysetReadiness>[0],
    target
  )

  const [presetId, setPresetId] = useState<PresetId>(DEFAULT_PRESET)
  const runChoice = controller.runChoice ?? 'source'
  const setRunChoice = controller.setRunChoice
  const withReadyset = runChoice === 'both'
  const setWithReadyset = (value: boolean) =>
    setRunChoice(value ? 'both' : 'source')
  const readysetAllowed = readyset.state !== 'unavailable'
  const useReadyset = withReadyset && readysetAllowed
  // The controller builds the request from its own settings; keep them on the choice.
  const {
    setTestProfile,
    setDurationSeconds,
    setIntervalMs,
    setCapacityClients,
    setComparative,
  } = controller
  useEffect(() => {
    const preset = presetById(presetId)
    setTestProfile(preset.mode === 'concurrency' ? 'capacity' : 'paced')
    setDurationSeconds(preset.durationSeconds)
    setIntervalMs(preset.intervalMs)
    setCapacityClients(preset.connections === 4 ? 4 : 2)
    setComparative(useReadyset)
  }, [
    presetId,
    useReadyset,
    setTestProfile,
    setDurationSeconds,
    setIntervalMs,
    setCapacityClients,
    setComparative,
  ])

  /** Opens the dialog's choices on the last run's settings. */
  const primeFrom = useCallback(() => {
    if (!run?.loadRequest) return
    setPresetId(presetForRequest(run.loadRequest))
    const lanes = (run.loadRequest as { lanes?: string[] }).lanes
    setRunChoice(
      lanes?.includes('readyset')
        ? lanes.includes('origin')
          ? 'both'
          : 'readyset'
        : 'source'
    )
  }, [run?.loadRequest, setRunChoice])

  const start = useCallback(async () => {
    await controller.handleConfirmRun()
  }, [controller.handleConfirmRun])

  const stop = useCallback(() => {
    if (run && live) void cancelBackgroundRun(run.runId)
  }, [run, live])

  const selected = identifier
    ? controller.selectedQueryById.get(identifier)
    : undefined

  return {
    controller,
    identifier,
    parameters: selected?.parameters ?? [],
    run,
    live,
    timeline,
    historyLoading: Boolean(query) && history.isLoading,
    presetId,
    setPresetId,
    runChoice,
    setRunChoice,
    withReadyset: useReadyset,
    setWithReadyset,
    readyset,
    primeFrom,
    start,
    stop,
    /** Why Start is off, in words; null when it can start. */
    blocker: startBlocker(controller, registryReady),
  }
}

function startBlocker(
  controller: ReturnType<typeof useLoadTestController>,
  registryReady: boolean
): string | null {
  if (controller.destinationLock.isLocked)
    return 'RDST needs the database password first.'
  if (controller.connectivity.isChecking) return 'Checking the connection.'
  if (!registryReady || controller.statusLoading)
    return 'Getting the query ready.'
  if (!controller.runnableCount) return 'Pick at least one query.'
  if (
    ['both', 'readyset'].includes(controller.runChoice ?? '') &&
    controller.dockerUnavailable
  )
    return 'Start Docker to add Readyset.'
  if (controller.residualQueryHash)
    return 'Check the parameter values and try again.'
  if (controller.missingParameterCount > 0)
    return controller.missingParameterCount === 1
      ? 'Fill in the missing value.'
      : `Fill in the ${controller.missingParameterCount} missing values.`
  if (!controller.canStart) return 'Getting the query ready.'
  return null
}

export type QueryBenchmark = ReturnType<typeof useQueryBenchmark>
