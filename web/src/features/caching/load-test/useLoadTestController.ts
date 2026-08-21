import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTarget } from '../../../hooks/useTarget'
import type {
  BenchmarkQueryInput,
  BenchmarkRequest,
  TargetInfo,
} from '../../../lib/api'
import {
  fetchSchema,
  fetchTargets,
  updateQueryParameters,
} from '../../../lib/api'
import {
  buildParameterSuggestions,
  fetchParameterSchema,
  parameterValueKey,
  suggestionSummaryMessage,
} from '../../../lib/parameterSuggestions'
import {
  filterQueriesBySearch,
  queryDisplayName,
} from '../../../lib/queryIdentity'
import {
  detectParameters,
  hasResidualPlaceholders,
  resolveInitialValue,
  substituteParameters,
  toBackendParams,
} from '../../../lib/sqlParameters'
import { useBenchmark } from '../../../lib/sse'
import { isRemoteTargetHost } from '../../../lib/targetHost'
import {
  type AutoSuggestFill,
  useAutoSuggestParameters,
} from '../../../lib/useAutoSuggestParameters'
import { useQueryRegistry } from '../../../lib/useQueryRegistry'
import { useSystemStatus } from '../../../lib/useSystemStatus'
import { useTargetConnectivityGate } from '../../../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../../../lib/useTargetPasswordLock'
import { type LoadTestLane, readRequestLanes } from './loadTestModel'

export type LoadTestProfile = 'paced' | 'capacity'

/** `BenchmarkRequest` widened with the additive `lanes` field. */
export type LoadTestBenchmarkRequest = BenchmarkRequest & {
  lanes?: LoadTestLane[]
}

export const BENCHMARK_EXECUTION_CAP = 100_000
const TABLE_REFERENCE_RE = /\b(?:FROM|JOIN)\s+(\w+)/gi

function extractReferencedTables(sql: string) {
  return Array.from(sql.matchAll(TABLE_REFERENCE_RE), (match) =>
    match[1].toLowerCase()
  )
}

export function useLoadTestController({
  selectedRunId,
  onClearSelectedRun,
}: {
  selectedRunId?: string
  onClearSelectedRun?: () => void
}) {
  const { target } = useTarget()
  const {
    data: status,
    isLoading: statusLoading,
    error: statusError,
    refetch: refetchStatus,
  } = useSystemStatus()
  const availableTargets = status?.targets ?? []
  const [destinationTarget, setDestinationTarget] = useState<string | null>(
    target
  )
  const userOverrodeDestination = useRef(false)

  useEffect(() => {
    if (userOverrodeDestination.current) return
    if (target) setDestinationTarget(target)
    else if (availableTargets.length > 0)
      setDestinationTarget(availableTargets[0].name)
  }, [target, availableTargets])

  const handleDestinationChange = useCallback((value: string) => {
    userOverrodeDestination.current = true
    setDestinationTarget(value || null)
  }, [])

  // The picker offers only queries that belong to the destination database:
  // otherwise switching Database left cross-target queries selectable even
  // though the run always executes against destinationTarget.
  const {
    queries,
    isLoading: registryLoading,
    listError,
    refetch: refetchRegistry,
  } = useQueryRegistry(undefined, destinationTarget)

  const destinationLock = useTargetPasswordLock(destinationTarget)
  const connectivity = useTargetConnectivityGate(
    destinationLock.targetName ?? destinationTarget
  )
  const {
    start,
    stop,
    state,
    stage: runStage,
    message: runMessage,
    progress,
    timeline,
    request: activeRequest,
    status: runStatus,
    error,
    reset,
  } = useBenchmark(selectedRunId, destinationTarget)

  const [pageState, setPageState] = useState<'configure' | 'run'>('configure')
  useEffect(() => {
    if (state !== 'idle') setPageState('run')
  }, [state])

  const [selectedQueries, setSelectedQueries] = useState<string[]>([])
  const [searchTerm, setSearchTerm] = useState('')
  const [sourceFilter, setSourceFilter] = useState(target || 'all')
  const [testProfile, setTestProfile] = useState<LoadTestProfile>('paced')
  // The Readyset lane is opt-in per run: it leases the sandbox and creates a
  // cache per query first, which on a cold sandbox costs minutes before the
  // measurement starts. A run says so explicitly rather than paying that by
  // default.
  const [comparative, setComparative] = useState(false)
  const [intervalMs, setIntervalMs] = useState(100)
  const [capacityClients, setCapacityClients] = useState(2)
  const [durationSeconds, setDurationSeconds] = useState(30)
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [parameterSources, setParameterSources] = useState<
    Record<string, string>
  >({})
  const [suggestingParameters, setSuggestingParameters] = useState(false)
  const [suggestionMessage, setSuggestionMessage] = useState<string | null>(
    null
  )
  const [suggestionSchemaUnavailable, setSuggestionSchemaUnavailable] =
    useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [residualQueryHash, setResidualQueryHash] = useState<string | null>(
    null
  )
  const [repeatPrevious, setRepeatPrevious] = useState(false)
  const [loadSettingsOpen, setLoadSettingsOpen] = useDisclosure({})

  const uniqueQueryTargets = useMemo(() => {
    const values = new Set<string>()
    for (const entry of queries) if (entry.target) values.add(entry.target)
    return Array.from(values).sort()
  }, [queries])
  const sourceOptions = useMemo(
    () => [
      { value: 'all', label: 'All databases' },
      ...uniqueQueryTargets.map((name) => ({ value: name, label: name })),
    ],
    [uniqueQueryTargets]
  )
  const normalizedSourceFilter = sourceOptions.some(
    (option) => option.value === sourceFilter
  )
    ? sourceFilter
    : 'all'

  const {
    data: targetDetails,
    isLoading: targetDetailsLoading,
    error: targetDetailsError,
    refetch: refetchTargetDetails,
  } = useQuery({
    queryKey: ['configure-targets-hosts'],
    queryFn: fetchTargets,
    staleTime: 60_000,
  })
  const remoteTargetNames = useMemo(() => {
    const values = new Set<string>()
    for (const item of targetDetails ?? []) {
      if (isRemoteTargetHost(item.host)) values.add(item.name)
    }
    return values
  }, [targetDetails])
  const destinationOptions = useMemo(
    () =>
      availableTargets.map((item: TargetInfo) => ({
        value: item.name,
        label: remoteTargetNames.has(item.name)
          ? `${item.name} · remote`
          : item.name,
      })),
    [availableTargets, remoteTargetNames]
  )
  const destinationIsRemote = destinationTarget
    ? remoteTargetNames.has(destinationTarget)
    : false

  const queryById = useMemo(() => {
    const map = new Map<string, (typeof queries)[number]>()
    for (const query of queries) map.set(query.tag || query.hash, query)
    return map
  }, [queries])

  const { data: destinationSchema } = useQuery({
    queryKey: ['schema', destinationTarget],
    queryFn: () => fetchSchema(destinationTarget!),
    enabled: !!destinationTarget,
    staleTime: 60_000,
  })

  const sourceFilteredQueries = useMemo(
    () =>
      normalizedSourceFilter === 'all'
        ? queries
        : queries.filter((query) => query.target === normalizedSourceFilter),
    [queries, normalizedSourceFilter]
  )
  const filteredQueries = useMemo(
    () => filterQueriesBySearch(sourceFilteredQueries, searchTerm),
    [searchTerm, sourceFilteredQueries]
  )

  const selectedQueryObjects = useMemo(
    () =>
      selectedQueries
        .map((identifier) => {
          const query = queryById.get(identifier)
          if (!query) return null
          return {
            ...query,
            identifier,
            name: queryDisplayName(query),
            parameters: detectParameters(query.sql),
          }
        })
        .filter((query): query is NonNullable<typeof query> => query !== null),
    [queryById, selectedQueries]
  )

  const visibleIdentifiers = useMemo(
    () => new Set(filteredQueries.map((query) => query.tag || query.hash)),
    [filteredQueries]
  )
  const selectedQueryById = useMemo(
    () =>
      new Map(
        selectedQueryObjects.map((query) => [query.identifier, query] as const)
      ),
    [selectedQueryObjects]
  )
  const runnableQueryObjects = useMemo(
    () =>
      selectedQueryObjects.filter((query) =>
        visibleIdentifiers.has(query.identifier)
      ),
    [selectedQueryObjects, visibleIdentifiers]
  )
  const selectedCount = selectedQueryObjects.length
  const runnableCount = runnableQueryObjects.length
  const hiddenSelectedCount = selectedCount - runnableCount
  const queriesWithParameters = runnableQueryObjects.filter(
    (query) => query.parameters.length > 0
  )

  const missingParameterCount = useMemo(() => {
    let count = 0
    for (const query of runnableQueryObjects) {
      for (const parameter of query.parameters) {
        const suffix =
          parameter.placeholder === '?'
            ? `?${parameter.index}`
            : parameter.placeholder
        if (!paramValues[`${query.identifier}:${suffix}`]?.trim()) count += 1
      }
    }
    return count
  }, [paramValues, runnableQueryObjects])

  useEffect(() => {
    setParamValues((previous) => {
      const next = { ...previous }
      for (const query of selectedQueryObjects) {
        if (!query.most_recent_params) continue
        for (const parameter of query.parameters) {
          const suffix =
            parameter.placeholder === '?'
              ? `?${parameter.index}`
              : parameter.placeholder
          const key = `${query.identifier}:${suffix}`
          if (!next[key]) {
            const initial = resolveInitialValue(
              parameter,
              query.most_recent_params
            )
            next[key] = initial
          }
        }
      }
      return next
    })
    setParameterSources((current) => {
      const next = { ...current }
      for (const query of selectedQueryObjects) {
        for (const parameter of query.parameters) {
          const initial = resolveInitialValue(
            parameter,
            query.most_recent_params
          )
          if (!initial) continue
          const key = `${query.identifier}:${parameterValueKey(parameter)}`
          if (!next[key]) next[key] = 'Observed value'
        }
      }
      return next
    })
  }, [selectedQueryObjects])

  const updateParameter = useCallback((key: string, value: string) => {
    setParamValues((current) => ({ ...current, [key]: value }))
    setParameterSources((current) => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
    setResidualQueryHash(null)
  }, [])

  const autoSuggestTarget = destinationLock.targetName ?? destinationTarget
  const applyAutoSuggestFills = useCallback((fills: AutoSuggestFill[]) => {
    setParamValues((current) => {
      const next = { ...current }
      for (const fill of fills) {
        if (!next[fill.key]?.trim()) next[fill.key] = fill.value
      }
      return next
    })
    setParameterSources((current) => {
      const next = { ...current }
      for (const fill of fills) next[fill.key] = fill.provenance
      return next
    })
  }, [])

  // Fire the backend value-suggestion service in the background as soon as a
  // query with unfilled placeholders is selected, so values are ready before
  // the user reaches "Start test" instead of only on an explicit
  // "Suggest values" click.
  useAutoSuggestParameters({
    target: autoSuggestTarget,
    items: useMemo(
      () =>
        runnableQueryObjects.map((query) => ({
          id: query.identifier,
          hash: query.hash,
          sql: query.sql,
          parameters: query.parameters,
        })),
      [runnableQueryObjects]
    ),
    values: paramValues,
    keyFor: useCallback(
      (id: string, parameter) =>
        `${id}:${parameter.placeholder === '?' ? `?${parameter.index}` : parameter.placeholder}`,
      []
    ),
    onApply: applyAutoSuggestFills,
  })

  const suggestParameterValues = useCallback(async () => {
    const suggestionTarget = destinationLock.targetName ?? destinationTarget
    if (!suggestionTarget || missingParameterCount === 0) return
    setSuggestingParameters(true)
    setSuggestionMessage(null)
    setSuggestionSchemaUnavailable(false)
    try {
      const schema = await fetchParameterSchema(suggestionTarget)
      const applicable: Array<[string, string, string]> = []
      for (const query of runnableQueryObjects) {
        const suggestions = buildParameterSuggestions(
          query.sql,
          query.parameters,
          schema
        )
        for (const [suffix, suggestion] of Object.entries(suggestions)) {
          const key = `${query.identifier}:${suffix}`
          if (!paramValues[key]?.trim()) {
            applicable.push([key, suggestion.value, suggestion.provenance])
          }
        }
      }
      setParamValues((current) => {
        const next = { ...current }
        for (const [key, value] of applicable) {
          if (!next[key]?.trim()) next[key] = value
        }
        return applicable.length > 0 ? next : current
      })
      setParameterSources((current) => ({
        ...current,
        ...Object.fromEntries(
          applicable.map(([key, _value, provenance]) => [key, provenance])
        ),
      }))
      setSuggestionMessage(
        suggestionSummaryMessage({
          filled: applicable.length,
          missingBefore: missingParameterCount,
          queryCount: runnableQueryObjects.filter(
            (query) => query.parameters.length > 0
          ).length,
          schemaAvailable: schema !== null,
        })
      )
      setSuggestionSchemaUnavailable(schema === null)
    } catch {
      setSuggestionMessage('Schema suggestions are unavailable.')
    } finally {
      setSuggestingParameters(false)
    }
  }, [
    destinationLock.targetName,
    destinationTarget,
    missingParameterCount,
    paramValues,
    runnableQueryObjects,
  ])

  const missingTables = useMemo(() => {
    if (!destinationSchema?.tables || runnableCount === 0) return []
    const destinationTables = new Set(
      Object.keys(destinationSchema.tables).map((table) => table.toLowerCase())
    )
    const missing = new Set<string>()
    for (const query of runnableQueryObjects) {
      for (const table of extractReferencedTables(query.sql)) {
        if (!destinationTables.has(table)) missing.add(table)
      }
    }
    return Array.from(missing).sort()
  }, [destinationSchema, runnableCount, runnableQueryObjects])

  const runTarget = destinationLock.targetName
  const canStart =
    Boolean(runTarget) &&
    !statusError &&
    !statusLoading &&
    !targetDetailsLoading &&
    !targetDetailsError &&
    !destinationLock.isLocked &&
    !connectivity.isChecking &&
    runnableCount > 0 &&
    missingParameterCount === 0

  const toggleQuery = (identifier: string) => {
    setSelectedQueries((current) =>
      current.includes(identifier)
        ? current.filter((item) => item !== identifier)
        : [...current, identifier]
    )
  }

  const clearFilters = () => {
    setSearchTerm('')
    setSourceFilter('all')
  }

  const prepareRunQueries = useCallback(
    () =>
      runnableQueryObjects.map((query) => {
        if (query.parameters.length === 0) {
          return {
            hash: query.hash,
            parameters: query.parameters,
            values: {},
            input: {
              identifier: query.identifier,
            } satisfies BenchmarkQueryInput,
          }
        }
        const values: Record<string, string> = {}
        for (const parameter of query.parameters) {
          const suffix =
            parameter.placeholder === '?'
              ? `?${parameter.index}`
              : parameter.placeholder
          values[suffix] = paramValues[`${query.identifier}:${suffix}`] || ''
        }
        const sql = substituteParameters(query.sql, query.parameters, values)
        return {
          hash: query.hash,
          parameters: query.parameters,
          values,
          input: {
            identifier: query.identifier,
            sql,
          } satisfies BenchmarkQueryInput,
        }
      }),
    [runnableQueryObjects, paramValues]
  )

  const buildRequest = (): LoadTestBenchmarkRequest | null => {
    if (!runTarget) return null
    const capacityTest = testProfile === 'capacity'
    return {
      queries: prepareRunQueries().map((query) => query.input),
      target: runTarget,
      mode: capacityTest ? ('concurrency' as const) : ('interval' as const),
      interval_ms: capacityTest ? 0 : intervalMs,
      concurrency: capacityTest ? capacityClients : 1,
      duration_seconds: durationSeconds,
      lanes: comparative ? ['origin', 'readyset'] : ['origin'],
    }
  }

  const runConfiguredTest = () => {
    const preparedQueries = prepareRunQueries()

    // Defensive last line before running real queries: canStart already
    // requires every detected placeholder to carry a value, but a
    // substitution that silently failed must not reach the database as
    // broken SQL.
    const unresolved = preparedQueries.find((query) =>
      hasResidualPlaceholders(query.input.sql ?? '')
    )
    if (unresolved) {
      setResidualQueryHash(unresolved.hash)
      setConfirmOpen(false)
      return
    }
    setResidualQueryHash(null)

    const request = buildRequest()
    if (!request || !canStart) return

    for (const query of preparedQueries) {
      const backendValues = toBackendParams(query.parameters, query.values)
      if (Object.keys(backendValues).length === 0) continue
      void updateQueryParameters(query.hash, backendValues, 'user').catch(
        () => {
          // Persistence is a convenience for next time; it must not block
          // this run.
        }
      )
    }

    setPageState('run')
    void start(request)
  }

  const handleStart = () => {
    if (!canStart) return
    setRepeatPrevious(false)
    setConfirmOpen(true)
  }
  const handleConfirmRun = async () => {
    setConfirmOpen(false)
    if (!(await connectivity.ensureReachable())) return
    if (repeatPrevious && activeRequest) {
      setPageState('run')
      void start(activeRequest)
      return
    }
    runConfiguredTest()
  }
  const handleBack = () => {
    if (activeRequest) {
      const previousProfile: LoadTestProfile =
        activeRequest.mode === 'concurrency' ? 'capacity' : 'paced'
      setTestProfile(previousProfile)
      setDurationSeconds(activeRequest.duration_seconds ?? 30)
      if (previousProfile === 'capacity') {
        setCapacityClients(activeRequest.concurrency === 4 ? 4 : 2)
      } else {
        setIntervalMs(activeRequest.interval_ms ?? 100)
      }
      const previousLanes = readRequestLanes(activeRequest)
      if (previousLanes) setComparative(previousLanes.includes('readyset'))
    }
    reset()
    onClearSelectedRun?.()
    setResidualQueryHash(null)
    setPageState('configure')
  }
  const handleRunAgain = () => {
    if (!activeRequest || destinationLock.isLocked) return
    setRepeatPrevious(true)
    setConfirmOpen(true)
  }

  const confirmRequest = repeatPrevious ? activeRequest : buildRequest()
  const confirmProfile: LoadTestProfile =
    confirmRequest?.mode === 'concurrency' ? 'capacity' : 'paced'
  const confirmIntervalMs = confirmRequest?.interval_ms ?? intervalMs
  const confirmClients = confirmRequest?.concurrency ?? 1
  const confirmDurationSeconds =
    confirmRequest?.duration_seconds ?? durationSeconds
  const confirmEstimatedExecutions =
    confirmProfile === 'capacity' || confirmIntervalMs <= 0
      ? null
      : Math.min(
          Math.ceil((confirmDurationSeconds * 1000) / confirmIntervalMs),
          BENCHMARK_EXECUTION_CAP
        )
  const confirmTarget =
    confirmRequest?.target ?? runTarget ?? destinationTarget ?? ''

  return {
    queries,
    registryLoading,
    listError,
    refetchRegistry,
    statusLoading,
    statusError,
    refetchStatus,
    destinationTarget,
    handleDestinationChange,
    destinationLock,
    connectivity,
    state,
    runStage,
    runMessage,
    progress,
    timeline,
    activeRequest,
    runStatus,
    error,
    stop,
    pageState,
    selectedQueries,
    setSelectedQueries,
    searchTerm,
    setSearchTerm,
    sourceFilter,
    setSourceFilter,
    testProfile,
    setTestProfile,
    comparative,
    setComparative,
    intervalMs,
    setIntervalMs,
    capacityClients,
    setCapacityClients,
    durationSeconds,
    setDurationSeconds,
    paramValues,
    setParamValues,
    parameterSources,
    updateParameter,
    suggestingParameters,
    suggestionMessage,
    suggestionSchemaUnavailable,
    suggestParameterValues,
    confirmOpen,
    setConfirmOpen,
    loadSettingsOpen,
    setLoadSettingsOpen,
    sourceOptions,
    normalizedSourceFilter,
    targetDetailsLoading,
    targetDetailsError,
    refetchTargetDetails,
    destinationOptions,
    destinationIsRemote,
    filteredQueries,
    selectedQueryById,
    selectedCount,
    runnableCount,
    hiddenSelectedCount,
    queriesWithParameters,
    missingParameterCount,
    residualQueryHash,
    missingTables,
    canStart,
    toggleQuery,
    clearFilters,
    handleStart,
    handleConfirmRun,
    handleBack,
    handleRunAgain,
    confirmTarget,
    confirmIsRemote: remoteTargetNames.has(confirmTarget),
    // The dialog names the Readyset lane only when this run drives it, which
    // for a repeat is the previous request's choice rather than the form's.
    confirmIncludesReadyset:
      readRequestLanes(confirmRequest ?? undefined)?.includes('readyset') ??
      false,
    confirmQueryCount: confirmRequest?.queries.length ?? runnableCount,
    confirmLoadSummary: `${
      confirmProfile === 'capacity'
        ? `${confirmClients} continuous clients`
        : confirmIntervalMs === 0
          ? 'no rest after completion'
          : `${confirmIntervalMs}ms rest after completion`
    } · ${confirmDurationSeconds}s`,
    confirmEstimatedExecutions,
  }
}

export type LoadTestController = ReturnType<typeof useLoadTestController>
