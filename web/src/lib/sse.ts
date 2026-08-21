import { useCallback, useEffect, useState } from 'react'
import {
  type AnalysisRunMeta,
  resetAnalysisRun,
  startAnalysisRun,
  useAnalysisRun,
} from './analysisRuns'
import { trackEvent } from './analytics'
import type {
  AnalysisState,
  AnalyzeRequest,
  BenchmarkRequest,
  BenchmarkState,
  CompleteEvent,
  ProgressEvent,
  ReadysetCacheability,
  RewriteTesting,
} from './api'
import type { components } from './api.generated'
import {
  type BackgroundRunStatus,
  cancelBackgroundRun,
  type LoadTestProgress,
  startLoadTestRun,
  useBackgroundRuns,
} from './backgroundRuns'
import type { ApiErrorEnvelope } from './errorContract'
import { useTargetSwitchLock } from './targetSwitchLock'

// Benchmark SSE event types are derived from the backend-generated discriminated union.
// Backend source of truth: rdst/features/query_registry/events.py (QueryBenchmarkEvent).
type QueryBenchmarkEvent = components['schemas']['QueryBenchmarkEvent']
type QueryBenchmarkProgressEvent = Extract<
  QueryBenchmarkEvent,
  { type: 'progress' }
>
type QueryBenchmarkCompleteEvent = Extract<
  QueryBenchmarkEvent,
  { type: 'complete' }
>
// Progress state stores only counter-bearing variants; errors go to the `error` field.
export type BenchmarkProgress =
  | QueryBenchmarkProgressEvent
  | QueryBenchmarkCompleteEvent

interface UseAnalyzeReturn {
  analyze: (request: AnalyzeRequest, meta?: AnalysisRunMeta) => Promise<void>
  state: AnalysisState
  progress: ProgressEvent | undefined
  results: CompleteEvent | undefined
  rewriteTesting: RewriteTesting | undefined
  readysetCacheability: ReadysetCacheability | undefined
  error: string | undefined
  errorEnvelope: ApiErrorEnvelope | undefined
  reset: () => void
}

/**
 * One analyze run, as a view needs it. The run itself lives in
 * `analysisRuns.ts`, so it survives this hook unmounting: pass `attachKey`
 * (from `analysisRunKey`) to pick up a run this view did not start, which is
 * what lets the analyze drawer be closed and reopened mid-run.
 */
export function useAnalyze(attachKey?: string | null): UseAnalyzeReturn {
  const [activeKey, setActiveKey] = useState<string | null>(attachKey ?? null)
  const run = useAnalysisRun(activeKey)

  useEffect(() => {
    setActiveKey(attachKey ?? null)
  }, [attachKey])

  useTargetSwitchLock('analyze', run?.state === 'analyzing')

  const analyze = useCallback(
    async (request: AnalyzeRequest, meta?: AnalysisRunMeta) => {
      setActiveKey(startAnalysisRun(request, meta))
    },
    []
  )

  const reset = useCallback(() => {
    resetAnalysisRun(activeKey)
    setActiveKey(null)
  }, [activeKey])

  return {
    analyze,
    reset,
    state: run?.state ?? 'idle',
    progress: run?.progress,
    results: run?.results,
    rewriteTesting: run?.rewriteTesting,
    readysetCacheability: run?.readysetCacheability,
    error: run?.error,
    errorEnvelope: run?.errorEnvelope,
  }
}

// ============================================================================
// Benchmark SSE Hook
// ============================================================================

interface UseBenchmarkReturn {
  start: (request: BenchmarkRequest) => Promise<void>
  stop: () => void
  state: BenchmarkState
  stage: string | undefined
  message: string | undefined
  progress: BenchmarkProgress | undefined
  timeline: LoadTestProgress[]
  request: BenchmarkRequest | undefined
  status: BackgroundRunStatus | undefined
  error: string | undefined
  reset: () => void
}

export function useBenchmark(
  selectedRunId?: string,
  selectedTarget?: string | null
): UseBenchmarkReturn {
  const backgroundRuns = useBackgroundRuns()
  const [runId, setRunId] = useState<string | null>(selectedRunId ?? null)
  const [allowFallback, setAllowFallback] = useState(
    selectedRunId === undefined
  )
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    if (selectedRunId === undefined) return
    setRunId(selectedRunId)
    setAllowFallback(false)
  }, [selectedRunId])

  const fallbackRun = allowFallback
    ? [...backgroundRuns]
        .reverse()
        .find(
          (candidate) =>
            candidate.kind === 'load_test' &&
            (!selectedTarget || candidate.target === selectedTarget) &&
            (candidate.status === 'running' ||
              candidate.status === 'reconnecting' ||
              candidate.status === 'stopping' ||
              candidate.status === 'needs_key')
        )
    : undefined
  const run = runId
    ? backgroundRuns.find((candidate) => candidate.runId === runId)
    : fallbackRun
  const state: BenchmarkState = starting
    ? 'running'
    : !run
      ? 'idle'
      : run.status === 'failed' || run.status === 'interrupted'
        ? 'error'
        : run.status === 'done' ||
            run.status === 'cancelled' ||
            run.status === 'partial'
          ? 'complete'
          : 'running'
  const progress = run?.loadResult
  const timeline = run?.loadSamples ?? []
  const request = run?.loadRequest
  const error =
    state === 'error' ? run?.message || 'Benchmark failed' : undefined
  useTargetSwitchLock('benchmark', state === 'running')

  const reset = useCallback(() => {
    setAllowFallback(false)
    setRunId(null)
    setStarting(false)
  }, [])

  const stop = useCallback(() => {
    if (run) void cancelBackgroundRun(run.runId)
  }, [run])

  const start = useCallback(async (request: BenchmarkRequest) => {
    setStarting(true)
    setAllowFallback(false)
    const started = await startLoadTestRun(request)
    setStarting(false)
    if (started) {
      trackEvent('load_test_run')
      setRunId(started)
    }
  }, [])

  return {
    start,
    stop,
    state,
    stage: run?.stage,
    message: run?.message,
    progress,
    timeline,
    request,
    status: run?.status,
    error,
    reset,
  }
}
