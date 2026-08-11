import { useCallback, useEffect, useRef, useState } from 'react'
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
import {
  type ApiErrorEnvelope,
  normalizeExplainError,
  normalizeHttpError,
  normalizeSseError,
} from './errorContract'
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

// SSE event types are derived from the backend-generated discriminated union.
// Backend source of truth: rdst/features/analyze/events.py (AnalyzeEvent).
// The `CompleteEvent`/`ProgressEvent` shapes in api.ts refine the generated
// `{[key: string]: unknown}` payloads for downstream consumers.
type AnalyzeEvent = components['schemas']['AnalyzeEvent']
export type AnalyzeEventType = AnalyzeEvent['type']

interface UseAnalyzeReturn {
  analyze: (request: AnalyzeRequest) => Promise<void>
  state: AnalysisState
  progress: ProgressEvent | undefined
  results: CompleteEvent | undefined
  rewriteTesting: RewriteTesting | undefined
  readysetCacheability: ReadysetCacheability | undefined
  error: string | undefined
  errorEnvelope: ApiErrorEnvelope | undefined
  reset: () => void
}

function normalizeRewriteTesting(
  candidate: unknown
): RewriteTesting | undefined {
  if (!candidate || typeof candidate !== 'object') {
    return undefined
  }

  const testing = candidate as RewriteTesting & {
    success?: boolean
    rewrite_results?: unknown
    best_rewrite?: unknown
  }

  if (typeof testing.tested === 'boolean') {
    return testing
  }

  if (testing.skipped_reason || testing.success === false) {
    return { ...testing, tested: false }
  }

  if (testing.success === true) {
    const rewriteResults = Array.isArray(testing.rewrite_results)
      ? testing.rewrite_results
      : []
    return {
      ...testing,
      tested: rewriteResults.length > 0 || Boolean(testing.best_rewrite),
      rewrite_results: rewriteResults as RewriteTesting['rewrite_results'],
    }
  }

  return undefined
}

export function useAnalyze(): UseAnalyzeReturn {
  const [state, setState] = useState<AnalysisState>('idle')
  const [progress, setProgress] = useState<ProgressEvent | undefined>(undefined)
  const [results, setResults] = useState<CompleteEvent | undefined>(undefined)
  const [rewriteTesting, setRewriteTesting] = useState<
    RewriteTesting | undefined
  >(undefined)
  const [readysetCacheability, setReadysetCacheability] = useState<
    ReadysetCacheability | undefined
  >(undefined)
  const [error, setError] = useState<string | undefined>(undefined)
  const [errorEnvelope, setErrorEnvelope] = useState<
    ApiErrorEnvelope | undefined
  >(undefined)

  const abortControllerRef = useRef<AbortController | null>(null)
  useTargetSwitchLock('analyze', state === 'analyzing')

  const failWith = useCallback((envelope: ApiErrorEnvelope) => {
    setError(envelope.message)
    setErrorEnvelope(envelope)
    setState('error')
  }, [])

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setState('idle')
    setProgress(undefined)
    setResults(undefined)
    setRewriteTesting(undefined)
    setReadysetCacheability(undefined)
    setError(undefined)
    setErrorEnvelope(undefined)
  }, [])

  const analyze = useCallback(
    async (request: AnalyzeRequest) => {
      console.log('[SSE] Starting analysis with request:', request)

      if (abortControllerRef.current) {
        console.log('[SSE] Aborting previous request')
        abortControllerRef.current.abort()
      }

      const controller = new AbortController()
      abortControllerRef.current = controller

      setState('analyzing')
      setProgress(undefined)
      setResults(undefined)
      setRewriteTesting(undefined)
      setReadysetCacheability(undefined)
      setError(undefined)
      setErrorEnvelope(undefined)

      try {
        console.log('[SSE] Sending POST to /api/analyze')
        const response = await fetch('/api/analyze', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(request),
          signal: controller.signal,
        })

        console.log('[SSE] Response status:', response.status)

        if (!response.ok) {
          // Normalize the HTTP failure into the shared envelope rather than
          // surfacing a raw status/body string to the UI (B7/T24).
          let parsedBody: unknown
          try {
            parsedBody = await response.clone().json()
          } catch {
            parsedBody = await response.text().catch(() => undefined)
          }
          failWith(normalizeHttpError(response.status, parsedBody))
          return
        }

        if (!response.body) {
          throw new Error('No response body')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let currentEvent = ''

        console.log('[SSE] Starting to read stream...')

        while (true) {
          const { done, value } = await reader.read()

          if (done) {
            console.log('[SSE] Stream done')
            break
          }

          const chunk = decoder.decode(value, { stream: true })
          buffer += chunk
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            const trimmed = line.trim()

            if (!trimmed) {
              currentEvent = ''
              continue
            }

            if (trimmed.startsWith('event:')) {
              currentEvent = trimmed.substring(6).trim()
              console.log('[SSE] Event:', currentEvent)
            } else if (trimmed.startsWith('data:')) {
              const dataStr = trimmed.substring(5).trim()

              try {
                const data = JSON.parse(dataStr)

                const eventType = currentEvent as AnalyzeEventType
                switch (eventType) {
                  case 'progress':
                    setProgress(data as ProgressEvent)
                    break

                  case 'explain_complete':
                    console.log('[SSE] EXPLAIN complete:', data)
                    break

                  case 'rewrites_tested':
                    console.log('[SSE] Rewrites tested:', data)
                    setRewriteTesting(normalizeRewriteTesting(data))
                    break

                  case 'readyset_checked':
                    console.log('[SSE] Readyset checked:', data)
                    setReadysetCacheability(data as ReadysetCacheability)
                    break

                  case 'complete': {
                    console.log('[SSE] Analysis complete:', data)
                    const completeData = data as CompleteEvent
                    // A failed EXPLAIN (e.g. invalid SQL) comes back on the
                    // `complete` event as top-level success with
                    // explain_results.success === false. Render it as a real
                    // error, never a zero-score "success" (B3/T3).
                    if (completeData.explain_results?.success === false) {
                      const explainError = completeData.explain_results
                        ?.error as string | undefined
                      failWith(normalizeExplainError(explainError))
                      break
                    }
                    setResults(completeData)
                    setRewriteTesting(
                      normalizeRewriteTesting(data.rewrite_testing) ??
                        normalizeRewriteTesting(data.formatted?.rewrite_testing)
                    )
                    if (data.readyset_cacheability) {
                      setReadysetCacheability(data.readyset_cacheability)
                    }
                    setState('complete')
                    break
                  }

                  case 'error':
                    console.log('[SSE] Error:', data.message)
                    failWith(normalizeSseError(data))
                    break

                  default: {
                    // Exhaustiveness guard: adding a variant to AnalyzeEventType
                    // without handling it here fails tsc. Do NOT use `as never`.
                    const _exhaustive: never = eventType
                    console.warn(
                      '[SSE] Unknown event type (ignored):',
                      currentEvent,
                      data
                    )
                    void _exhaustive
                    break
                  }
                }
              } catch (e) {
                console.error('[SSE] Failed to parse JSON:', e)
              }
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          console.log('[SSE] Request aborted by user')
          return
        }
        console.error('[SSE] Error during analysis:', err)
        // Transport-level failure (network down, stream aborted mid-flight):
        // normalize into the shared envelope instead of a raw err.message.
        failWith({
          code: 'network_error',
          message:
            'Could not reach the analysis service. Check that it is running and try again.',
          detail: err instanceof Error ? err.message : undefined,
        })
      } finally {
        abortControllerRef.current = null
      }
    },
    [failWith]
  )

  return {
    analyze,
    state,
    progress,
    results,
    rewriteTesting,
    readysetCacheability,
    error,
    errorEnvelope,
    reset,
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
    if (started) setRunId(started)
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
