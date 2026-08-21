import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTarget } from '../../../hooks/useTarget'
import { reportCompareOutcome, updateQueryParameters } from '../../../lib/api'
import { useBackgroundRuns } from '../../../lib/backgroundRuns'
import {
  buildParameterSuggestions,
  fetchParameterSchema,
  parameterValueKey,
  suggestionSummaryMessage,
} from '../../../lib/parameterSuggestions'
import { byImpact } from '../../../lib/queryImpact'
import {
  detectParameters,
  hasResidualPlaceholders,
  resolveInitialValue,
  substituteParameters,
  toBackendParams,
} from '../../../lib/sqlParameters'
import {
  type AutoSuggestFill,
  useAutoSuggestParameters,
} from '../../../lib/useAutoSuggestParameters'
import { useQueryRegistry } from '../../../lib/useQueryRegistry'
import { useTargetConnectivityGate } from '../../../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../../../lib/useTargetPasswordLock'
import { fetchSandboxDiagnostics, queueSandboxPrewarm } from '../sandbox'
import {
  type CompareBatch,
  cancelCompareBatch,
  clearActiveCompareBatch,
  compareBatchSnapshot,
  deriveCompareOutcomeReport,
  forgetCompareBatch,
  latestCompareBatch,
  listCompareBatches,
  selectCompareBatch,
  settleCompareBatch,
  startCompareBatch,
  updateCompareBatchLoad,
} from './compareRuns'

export const MIN_COMPARE_CONCURRENCY = 2
export const MAX_COMPARE_CONCURRENCY = 4
export const DEFAULT_COMPARE_CONCURRENCY = 2
export const MAX_COMPARE_QUERIES = 4
export const DEFAULT_COMPARE_DURATION = 30

export function initialCompareConcurrency(queryCount: number) {
  return Math.min(
    MAX_COMPARE_CONCURRENCY,
    Math.max(MIN_COMPARE_CONCURRENCY, queryCount)
  )
}

function parameterKey(queryHash: string, placeholder: string, index: number) {
  return `${queryHash}:${placeholder === '?' ? `?${index}` : placeholder}`
}

export function useCompareController(initialQueryHash?: string) {
  const { target } = useTarget()
  const passwordLock = useTargetPasswordLock(target)
  const connectivity = useTargetConnectivityGate(
    passwordLock.targetName ?? target
  )
  const registry = useQueryRegistry(250, target)
  const backgroundRuns = useBackgroundRuns()
  const [selectedIds, setSelectedIds] = useState<string[]>([])
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
  const [concurrency, setConcurrency] = useState(DEFAULT_COMPARE_CONCURRENCY)
  const [durationSeconds, setDurationSeconds] = useState(
    DEFAULT_COMPARE_DURATION
  )
  const [updatingLoad, setUpdatingLoad] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [residualQueryHash, setResidualQueryHash] = useState<string | null>(
    null
  )
  const [historyOpen, setHistoryOpen] = useState(false)
  const [starting, setStarting] = useState(false)
  const [batch, setBatch] = useState<CompareBatch | null>(() =>
    latestCompareBatch(target)
  )
  const [history, setHistory] = useState<CompareBatch[]>(() =>
    listCompareBatches(target)
  )
  const initializedSelectionKey = useRef<string | null>(null)
  const steppedBatchId = useRef<string | null>(null)
  const prewarmedTarget = useRef<string | null>(null)
  const reportedOutcomes = useRef<Set<string>>(new Set())

  useEffect(() => {
    setBatch(latestCompareBatch(target))
    setHistory(listCompareBatches(target))
    setHistoryOpen(false)
    setSelectedIds([])
    setParamValues({})
    setParameterSources({})
    setSuggestionMessage(null)
    setSuggestionSchemaUnavailable(false)
    setResidualQueryHash(null)
    initializedSelectionKey.current = null
    steppedBatchId.current = null
    reportedOutcomes.current.clear()
  }, [target])

  const statusQuery = useQuery({
    queryKey: ['readyset-sandbox'],
    queryFn: fetchSandboxDiagnostics,
    enabled: !!target && !passwordLock.isLocked,
    refetchInterval: 5_000,
  })

  const dockerReady =
    statusQuery.data?.docker_installed === true &&
    statusQuery.data?.docker_running === true

  // Warm the Readyset sandbox as soon as the user lands on Compare setup with
  // a usable target, so it is ready by the time they finish configuring the
  // run instead of only starting once they press "Run comparison". Asked for
  // only once the diagnostics say a sandbox can exist at all, so a machine
  // without Docker -- where the page offers "Check again" instead of a run --
  // stays quiet. Guarded per target so re-renders don't re-queue it; the
  // backend itself replaces an obsolete queued prewarm if the target changes
  // again before it starts.
  useEffect(() => {
    if (!target || passwordLock.isLocked || !dockerReady) return
    if (prewarmedTarget.current === target) return
    prewarmedTarget.current = target
    void queueSandboxPrewarm(target).catch(() => {
      // A head start is a convenience; a failure here must stay silent and
      // let the real run surface its own sandbox errors if any remain.
    })
  }, [dockerReady, target, passwordLock.isLocked])

  // Cacheability is evidence produced by a comparison, not query identity.
  // Keep candidates visible and let each run report unsupported SQL explicitly.
  // The picker's own list has no sort control, so its one ordering should
  // lead with the queries most worth comparing, same as the Query Library's
  // impact-first default -- not the registry's last-analyzed API order.
  const queries = useMemo(
    () => [...registry.queries].sort(byImpact),
    [registry.queries]
  )
  const selected = useMemo(
    () => queries.filter((entry) => selectedIds.includes(entry.hash)),
    [queries, selectedIds]
  )

  useEffect(() => {
    if (!target || !initialQueryHash) return
    const key = `${target}:${initialQueryHash}`
    if (initializedSelectionKey.current === key) return
    if (!queries.some((entry) => entry.hash === initialQueryHash)) return
    initializedSelectionKey.current = key
    setSelectedIds([initialQueryHash])
  }, [initialQueryHash, queries, target])
  const selectedWithParams = useMemo(
    () =>
      selected.map((entry) => ({
        entry,
        parameters: detectParameters(entry.sql),
      })),
    [selected]
  )

  useEffect(() => {
    setParamValues((current) => {
      const next = { ...current }
      let changed = false
      for (const item of selectedWithParams) {
        for (const parameter of item.parameters) {
          const key = parameterKey(
            item.entry.hash,
            parameter.placeholder,
            parameter.index
          )
          if (next[key]) continue
          const initial = resolveInitialValue(
            parameter,
            item.entry.most_recent_params
          )
          if (initial) {
            next[key] = initial
            changed = true
          }
        }
      }
      return changed ? next : current
    })
    setParameterSources((current) => {
      const next = { ...current }
      let changed = false
      for (const item of selectedWithParams) {
        for (const parameter of item.parameters) {
          const initial = resolveInitialValue(
            parameter,
            item.entry.most_recent_params
          )
          if (!initial) continue
          const key = parameterKey(
            item.entry.hash,
            parameter.placeholder,
            parameter.index
          )
          if (!next[key]) {
            next[key] = 'Observed value'
            changed = true
          }
        }
      }
      return changed ? next : current
    })
  }, [selectedWithParams])

  const missingParameterCount = useMemo(
    () =>
      selectedWithParams.reduce(
        (count, item) =>
          count +
          item.parameters.filter(
            (parameter) =>
              !paramValues[
                parameterKey(
                  item.entry.hash,
                  parameter.placeholder,
                  parameter.index
                )
              ]?.trim()
          ).length,
        0
      ),
    [paramValues, selectedWithParams]
  )
  const parameterCount = selectedWithParams.reduce(
    (count, item) => count + item.parameters.length,
    0
  )

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
  // the user reaches "Run comparison" instead of only on an explicit
  // "Suggest values" click.
  useAutoSuggestParameters({
    target,
    items: useMemo(
      () =>
        selectedWithParams.map((item) => ({
          id: item.entry.hash,
          hash: item.entry.hash,
          sql: item.entry.sql,
          parameters: item.parameters,
        })),
      [selectedWithParams]
    ),
    values: paramValues,
    keyFor: useCallback(
      (id: string, parameter) =>
        parameterKey(id, parameter.placeholder, parameter.index),
      []
    ),
    onApply: applyAutoSuggestFills,
  })

  useEffect(() => {
    setConcurrency(initialCompareConcurrency(selectedIds.length))
  }, [selectedIds.length])

  const snapshot = useMemo(
    () => (batch ? compareBatchSnapshot(batch, backgroundRuns) : null),
    [backgroundRuns, batch]
  )
  const historyEntries = useMemo(
    () =>
      history.map((historyBatch) => ({
        batch: historyBatch,
        snapshot: compareBatchSnapshot(historyBatch, backgroundRuns),
      })),
    [backgroundRuns, history]
  )
  const activeBatch = snapshot?.status === 'running'

  useEffect(() => {
    if (!batch || !snapshot || snapshot.status === 'running' || batch.outcome) {
      return
    }
    const settled = settleCompareBatch(batch, snapshot)
    setBatch(settled)
    setHistory(listCompareBatches(target))
  }, [batch, snapshot, target])

  // Record what each query's comparison found beside the query itself, as
  // soon as that one query reaches a terminal state -- not only once the
  // whole batch finishes -- so the Query Library reflects results as they
  // land. Fire-and-forget per query, same as updateQueryParameters: this
  // history is a convenience for the next person who opens the query, and
  // must not affect the run in progress or its local results.
  useEffect(() => {
    if (!batch || !snapshot) return
    const queryHashByCacheId = new Map(
      batch.queries.map((query) => [query.cacheId, query.queryHash])
    )
    for (const outcome of snapshot.queryOutcomes) {
      if (outcome.status === 'running') continue
      const key = `${batch.id}:${outcome.cacheId}`
      if (reportedOutcomes.current.has(key)) continue
      reportedOutcomes.current.add(key)
      const report = deriveCompareOutcomeReport(
        outcome,
        queryHashByCacheId.get(outcome.cacheId),
        batch.target
      )
      if (!report) continue
      void reportCompareOutcome(report.queryHash, report.request).catch(() => {
        // Best-effort history; the run and its local results already
        // reflect this outcome regardless of whether the write lands.
      })
    }
  }, [batch, snapshot])

  // The normal comparison has one safe, predictable load profile: begin at
  // two clients per lane, then step to four halfway through. Advanced/manual
  // load shaping remains in Load test.
  useEffect(() => {
    if (
      !batch ||
      !snapshot ||
      snapshot.status !== 'running' ||
      updatingLoad ||
      batch.concurrency >= MAX_COMPARE_CONCURRENCY ||
      steppedBatchId.current === batch.id
    ) {
      return
    }
    const latest = snapshot.timeline[snapshot.timeline.length - 1]
    if (!latest || latest.elapsed_seconds < batch.durationSeconds / 2) return
    steppedBatchId.current = batch.id
    setUpdatingLoad(true)
    void updateCompareBatchLoad(batch, MAX_COMPARE_CONCURRENCY)
      .then((updated) => {
        setBatch(updated)
        setConcurrency(updated.concurrency)
      })
      .catch(() => {
        // A transient PATCH failure should not strand the run at its first
        // stage. The next snapshot can safely retry the idempotent step.
        steppedBatchId.current = null
      })
      .finally(() => setUpdatingLoad(false))
  }, [batch, snapshot, updatingLoad])

  const toggleQuery = (queryHash: string) => {
    setSelectedIds((current) =>
      current.includes(queryHash)
        ? current.filter((id) => id !== queryHash)
        : current.length < MAX_COMPARE_QUERIES
          ? [...current, queryHash]
          : current
    )
  }
  const selectAll = () => {
    setSelectedIds(
      selectedIds.length === Math.min(queries.length, MAX_COMPARE_QUERIES)
        ? []
        : queries.slice(0, MAX_COMPARE_QUERIES).map((entry) => entry.hash)
    )
  }
  const updateParameter = (
    queryHash: string,
    placeholder: string,
    index: number,
    value: string
  ) => {
    const key = parameterKey(queryHash, placeholder, index)
    setResidualQueryHash((current) => (current === queryHash ? null : current))
    setParamValues((current) => ({
      ...current,
      [key]: value,
    }))
    setParameterSources((current) => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
  }

  const suggestParameterValues = useCallback(async () => {
    if (!target || missingParameterCount === 0) return
    setSuggestingParameters(true)
    setSuggestionMessage(null)
    setSuggestionSchemaUnavailable(false)
    try {
      const schema = await fetchParameterSchema(target)
      const applicable: Array<[string, string, string]> = []
      for (const item of selectedWithParams) {
        const suggestions = buildParameterSuggestions(
          item.entry.sql,
          item.parameters,
          schema
        )
        for (const [suffix, suggestion] of Object.entries(suggestions)) {
          const parameter = item.parameters.find(
            (candidate) => parameterValueKey(candidate) === suffix
          )
          if (!parameter) continue
          const key = parameterKey(
            item.entry.hash,
            parameter.placeholder,
            parameter.index
          )
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
          queryCount: selectedWithParams.filter(
            (item) => item.parameters.length > 0
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
  }, [missingParameterCount, paramValues, selectedWithParams, target])

  const canReview =
    !!target &&
    dockerReady &&
    selected.length > 0 &&
    selected.length <= MAX_COMPARE_QUERIES &&
    missingParameterCount === 0 &&
    !passwordLock.isLocked &&
    !connectivity.isChecking &&
    !activeBatch &&
    !starting

  const startComparison = async () => {
    if (!target || !canReview) return
    setStarting(true)
    try {
      if (!(await connectivity.ensureReachable())) return
      const initialConcurrency = initialCompareConcurrency(selected.length)
      setConcurrency(initialConcurrency)
      const preparedQueries = selectedWithParams.map((item) => {
        const values: Record<string, string> = {}
        for (const parameter of item.parameters) {
          const value =
            paramValues[
              parameterKey(
                item.entry.hash,
                parameter.placeholder,
                parameter.index
              )
            ] ?? ''
          values[
            parameter.placeholder === '?'
              ? `?${parameter.index}`
              : parameter.placeholder
          ] = value
        }
        return {
          hash: item.entry.hash,
          parameters: item.parameters,
          values,
          cacheId: item.entry.hash,
          label:
            item.entry.tag?.trim() || `Query ${item.entry.hash.slice(0, 8)}`,
          queryHash: item.entry.hash,
          sql:
            item.parameters.length > 0
              ? substituteParameters(item.entry.sql, item.parameters, values)
              : item.entry.sql,
        }
      })

      // Defensive last line before running real queries: the Run gate
      // already requires every detected placeholder to carry a value, but a
      // substitution that silently failed (or a placeholder the detector
      // missed) must not reach the database as broken SQL.
      const unresolved = preparedQueries.find((query) =>
        hasResidualPlaceholders(query.sql)
      )
      if (unresolved) {
        setResidualQueryHash(unresolved.hash)
        setReviewOpen(false)
        return
      }
      setResidualQueryHash(null)

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

      const nextBatch = await startCompareBatch({
        target,
        concurrency: initialConcurrency,
        durationSeconds: DEFAULT_COMPARE_DURATION,
        queries: preparedQueries.map(({ cacheId, label, queryHash, sql }) => ({
          cacheId,
          label,
          queryHash,
          sql,
        })),
      })
      setBatch(nextBatch)
      setDurationSeconds(DEFAULT_COMPARE_DURATION)
      steppedBatchId.current = null
      setHistory(listCompareBatches(target))
      setReviewOpen(false)
    } finally {
      setStarting(false)
    }
  }

  const clearBatch = () => {
    clearActiveCompareBatch(target)
    setBatch(null)
    setConcurrency(DEFAULT_COMPARE_CONCURRENCY)
    setDurationSeconds(DEFAULT_COMPARE_DURATION)
    steppedBatchId.current = null
  }

  const deleteHistoryBatch = (batchId: string) => {
    forgetCompareBatch(batchId)
    if (batch?.id === batchId) setBatch(null)
    setHistory(listCompareBatches(target))
  }

  return {
    target,
    passwordLock,
    connectivity,
    statusQuery,
    registry,
    queries,
    selectedIds,
    selectedWithParams,
    toggleQuery,
    selectAll,
    paramValues,
    parameterSources,
    updateParameter,
    suggestingParameters,
    suggestionMessage,
    suggestionSchemaUnavailable,
    suggestParameterValues,
    parameterCount,
    missingParameterCount,
    residualQueryHash,
    concurrency,
    durationSeconds,
    updatingLoad,
    reviewOpen,
    setReviewOpen,
    historyOpen,
    setHistoryOpen,
    historyEntries,
    openHistoryBatch: (historyBatch: CompareBatch) => {
      selectCompareBatch(historyBatch)
      setBatch(historyBatch)
      setConcurrency(historyBatch.concurrency)
      setDurationSeconds(historyBatch.durationSeconds)
      setHistoryOpen(false)
    },
    deleteHistoryBatch,
    canReview,
    starting,
    startComparison,
    batch,
    snapshot,
    cancelComparison: () => (batch ? cancelCompareBatch(batch) : undefined),
    clearBatch,
  }
}

export type CompareController = ReturnType<typeof useCompareController>
export type CompareQuerySelection = {
  entry: ReturnType<typeof useQueryRegistry>['queries'][number]
  parameters: ReturnType<typeof detectParameters>
}
