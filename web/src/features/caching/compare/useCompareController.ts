import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTarget } from '../../../hooks/useTarget'
import { useBackgroundRuns } from '../../../lib/backgroundRuns'
import {
  buildParameterSuggestions,
  fetchParameterSchema,
  parameterValueKey,
} from '../../../lib/parameterSuggestions'
import {
  detectParameters,
  resolveInitialValue,
  substituteParameters,
} from '../../../lib/sqlParameters'
import { useQueryRegistry } from '../../../lib/useQueryRegistry'
import { useTargetConnectivityGate } from '../../../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../../../lib/useTargetPasswordLock'
import { fetchSandboxDiagnostics } from '../sandbox'
import {
  type CompareBatch,
  cancelCompareBatch,
  clearActiveCompareBatch,
  compareBatchSnapshot,
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
  const [concurrency, setConcurrency] = useState(DEFAULT_COMPARE_CONCURRENCY)
  const [durationSeconds, setDurationSeconds] = useState(
    DEFAULT_COMPARE_DURATION
  )
  const [updatingLoad, setUpdatingLoad] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
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

  useEffect(() => {
    setBatch(latestCompareBatch(target))
    setHistory(listCompareBatches(target))
    setHistoryOpen(false)
    setSelectedIds([])
    setParamValues({})
    setParameterSources({})
    setSuggestionMessage(null)
    initializedSelectionKey.current = null
    steppedBatchId.current = null
  }, [target])

  const statusQuery = useQuery({
    queryKey: ['readyset-sandbox'],
    queryFn: fetchSandboxDiagnostics,
    enabled: !!target && !passwordLock.isLocked,
    refetchInterval: 5_000,
  })

  // Cacheability is evidence produced by a comparison, not query identity.
  // Keep candidates visible and let each run report unsupported SQL explicitly.
  const queries = registry.queries
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
        applicable.length > 0
          ? `Filled ${applicable.length} unresolved ${applicable.length === 1 ? 'parameter' : 'parameters'} from safe schema evidence. Review before running.`
          : 'No safe schema-grounded suggestions were found.'
      )
    } catch {
      setSuggestionMessage('Schema suggestions are unavailable.')
    } finally {
      setSuggestingParameters(false)
    }
  }, [missingParameterCount, paramValues, selectedWithParams, target])

  const dockerReady =
    statusQuery.data?.docker_installed === true &&
    statusQuery.data?.docker_running === true
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
      const nextBatch = await startCompareBatch({
        target,
        concurrency: initialConcurrency,
        durationSeconds: DEFAULT_COMPARE_DURATION,
        queries: selectedWithParams.map((item) => {
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
            cacheId: item.entry.hash,
            label:
              item.entry.tag?.trim() || `Query ${item.entry.hash.slice(0, 8)}`,
            queryHash: item.entry.hash,
            sql:
              item.parameters.length > 0
                ? substituteParameters(item.entry.sql, item.parameters, values)
                : item.entry.sql,
          }
        }),
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
    suggestParameterValues,
    parameterCount,
    missingParameterCount,
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
