/**
 * Analyze runs, held outside React so a run outlives the view that started it.
 *
 * The Query Library's analyze drawer can be closed mid-run; the background-run
 * registry (`backgroundRuns.ts`) already proves the shape for jobs the server
 * owns by run id. Analyze has no run id, so runs are keyed by what identifies
 * them to the user: the query, its target, and the analysis depth. Two views of
 * the same query — the drawer and `/results` — attach to one run rather than
 * measuring the database twice.
 */

import { useSyncExternalStore } from 'react'
import { normalizeRewriteTesting } from './analysisPayload'
import type {
  AnalysisState,
  AnalyzeRequest,
  CompleteEvent,
  ProgressEvent,
  ReadysetCacheability,
  RewriteTesting,
} from './api'
import type { components } from './api.generated'
import { dismissBackgroundRun, upsertLocalRun } from './backgroundRuns'
import {
  type ApiErrorEnvelope,
  normalizeExplainError,
  normalizeHttpError,
  normalizeSseError,
} from './errorContract'

type AnalyzeEvent = components['schemas']['AnalyzeEvent']
export type AnalyzeEventType = AnalyzeEvent['type']

/**
 * What the run is called outside the view that started it: the registry hash
 * the query is known by, and the name the sidebar job carries.
 */
export interface AnalysisRunMeta {
  queryHash?: string
  queryLabel?: string
}

export interface AnalysisRun {
  key: string
  /** Sidebar job id, so the run reports its own progress and completion. */
  jobId: string
  meta: AnalysisRunMeta
  request: AnalyzeRequest
  state: AnalysisState
  progress: ProgressEvent | undefined
  results: CompleteEvent | undefined
  rewriteTesting: RewriteTesting | undefined
  readysetCacheability: ReadysetCacheability | undefined
  error: string | undefined
  errorEnvelope: ApiErrorEnvelope | undefined
}

/**
 * What makes two analyze requests the same run. The query text is part of it,
 * so substituting parameter values starts a run of its own.
 */
export function analysisRunKey(request: AnalyzeRequest): string {
  return [
    request.target ?? '',
    request.fast ? 'fast' : 'full',
    request.query,
  ].join('\u0000')
}

const runs = new Map<string, AnalysisRun>()
const controllers = new Map<string, AbortController>()
const listeners = new Set<() => void>()
/**
 * Registry hash to run key. The key is the request (query text, target, depth),
 * so a card that knows only the hash — and cannot know which parameter values
 * the drawer substituted — still finds the run it started.
 */
const runKeysByHash = new Map<string, string>()
let jobSequence = 0

/** Notified with the query hash whenever a run finishes with results. */
type AnalysisRunObserver = (queryHash: string) => void
let observer: AnalysisRunObserver | null = null

export function setAnalysisRunObserver(next: AnalysisRunObserver | null): void {
  observer = next
}

function publish(): void {
  for (const listener of listeners) listener()
}

/** What the sidebar job for a run is called. */
function jobTitle(run: AnalysisRun): string {
  return run.meta.queryLabel || run.meta.queryHash || run.request.query
}

function reportJob(
  run: AnalysisRun,
  status: 'running' | 'done' | 'failed',
  message: string
): void {
  upsertLocalRun(
    {
      runId: run.jobId,
      kind: 'analyze',
      target: run.request.target ?? '',
      status,
      message,
      queryHash: run.meta.queryHash,
      queryLabel: jobTitle(run),
    },
    { onCancel: () => resetAnalysisRun(run.key) }
  )
}

function update(key: string, partial: Partial<AnalysisRun>): void {
  const current = runs.get(key)
  if (!current) return
  const next = { ...current, ...partial }
  runs.set(key, next)
  if (partial.state === 'complete') {
    reportJob(next, 'done', 'Analysis complete')
  } else if (partial.progress && next.state === 'analyzing') {
    reportJob(next, 'running', partial.progress.message)
  }
  publish()
}

function fail(key: string, envelope: ApiErrorEnvelope): void {
  update(key, {
    state: 'error',
    error: envelope.message,
    errorEnvelope: envelope,
  })
  const run = runs.get(key)
  if (run) reportJob(run, 'failed', envelope.message)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getAnalysisRun(key: string | null): AnalysisRun | undefined {
  return key ? runs.get(key) : undefined
}

/** Read one run, re-rendering as its stream advances. */
export function useAnalysisRun(key: string | null): AnalysisRun | undefined {
  return useSyncExternalStore(
    subscribe,
    () => getAnalysisRun(key),
    () => undefined
  )
}

/**
 * The run for a query as a Query Library card knows it: by registry hash when
 * one of this session's runs claimed that hash, and otherwise by the request a
 * card would make itself. The second half is what keeps a card in step with a
 * run started from `/results`, which carries no hash of its own.
 */
export function useAnalysisRunForQuery(query: {
  hash?: string | null
  sql?: string | null
  target?: string | null
  fast?: boolean
}): AnalysisRun | undefined {
  const { hash, sql, target, fast = false } = query
  const requestKey = sql
    ? analysisRunKey({ query: sql.trim(), target: target ?? undefined, fast })
    : null
  return useSyncExternalStore(
    subscribe,
    () => {
      const hashed = hash ? runKeysByHash.get(hash) : undefined
      return (
        (hashed ? runs.get(hashed) : undefined) ??
        (requestKey ? runs.get(requestKey) : undefined)
      )
    },
    () => undefined
  )
}

/**
 * Measure a query. An existing run under the same key is abandoned, so every
 * call is a real re-run; attaching to work already in flight is what
 * `useAnalysisRun` is for.
 */
export function startAnalysisRun(
  request: AnalyzeRequest,
  meta: AnalysisRunMeta = {}
): string {
  const key = analysisRunKey(request)
  controllers.get(key)?.abort()
  const controller = new AbortController()
  controllers.set(key, controller)
  // Re-running replaces the previous job rather than stacking a second card
  // for work that no longer exists.
  const previousJobId = runs.get(key)?.jobId
  if (previousJobId) dismissBackgroundRun(previousJobId)
  const run: AnalysisRun = {
    key,
    jobId: `analyze_${++jobSequence}`,
    meta,
    request,
    state: 'analyzing',
    progress: undefined,
    results: undefined,
    rewriteTesting: undefined,
    readysetCacheability: undefined,
    error: undefined,
    errorEnvelope: undefined,
  }
  runs.set(key, run)
  if (meta.queryHash) runKeysByHash.set(meta.queryHash, key)
  reportJob(run, 'running', 'Measuring the query...')
  publish()
  void streamRun(key, request, controller)
  return key
}

/** Forget one run. The stream, if any, is abandoned with it. */
export function resetAnalysisRun(key: string | null): void {
  if (!key) return
  controllers.get(key)?.abort()
  controllers.delete(key)
  const run = runs.get(key)
  if (!run) return
  if (run.meta.queryHash && runKeysByHash.get(run.meta.queryHash) === key) {
    runKeysByHash.delete(run.meta.queryHash)
  }
  runs.delete(key)
  dismissBackgroundRun(run.jobId)
  publish()
}

export function __resetAnalysisRunsForTests(): void {
  for (const controller of controllers.values()) controller.abort()
  controllers.clear()
  runs.clear()
  runKeysByHash.clear()
  jobSequence = 0
  observer = null
  publish()
}

async function streamRun(
  key: string,
  request: AnalyzeRequest,
  controller: AbortController
): Promise<void> {
  try {
    const response = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: controller.signal,
    })

    if (!response.ok) {
      // Normalize the HTTP failure into the shared envelope rather than
      // surfacing a raw status/body string to the UI (B7/T24).
      let parsedBody: unknown
      try {
        parsedBody = await response.clone().json()
      } catch {
        parsedBody = await response.text().catch(() => undefined)
      }
      fail(key, normalizeHttpError(response.status, parsedBody))
      return
    }
    if (!response.body) throw new Error('No response body')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
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
          continue
        }
        if (!trimmed.startsWith('data:')) continue
        try {
          applyFrame(key, currentEvent, JSON.parse(trimmed.substring(5).trim()))
        } catch (parseError) {
          console.error('[analyze] Failed to parse JSON:', parseError)
        }
      }
    }
  } catch (streamError: unknown) {
    if (streamError instanceof Error && streamError.name === 'AbortError') {
      return
    }
    // Transport-level failure (network down, stream aborted mid-flight):
    // normalize into the shared envelope instead of a raw message.
    fail(key, {
      code: 'network_error',
      message:
        'Could not reach the analysis service. Check that it is running and try again.',
      detail: streamError instanceof Error ? streamError.message : undefined,
    })
  } finally {
    if (controllers.get(key) === controller) controllers.delete(key)
  }
}

function applyFrame(key: string, event: string, data: unknown): void {
  const payload = data as CompleteEvent
  const eventType = event as AnalyzeEventType
  switch (eventType) {
    case 'progress':
      update(key, { progress: data as ProgressEvent })
      break

    case 'explain_complete':
      break

    case 'rewrites_tested':
      update(key, { rewriteTesting: normalizeRewriteTesting(data) })
      break

    case 'readyset_checked':
      update(key, { readysetCacheability: data as ReadysetCacheability })
      break

    case 'complete': {
      // A failed EXPLAIN (e.g. invalid SQL) comes back on the `complete` event
      // as top-level success with explain_results.success === false. Render it
      // as a real error, never a zero-score "success" (B3/T3).
      if (payload.explain_results?.success === false) {
        const explainError = payload.explain_results.error as string | undefined
        fail(key, normalizeExplainError(explainError))
        break
      }
      // A run started outside the Query Library learns its registry hash only
      // here; claiming it now is what lets that query's card and the job's own
      // link find this run.
      if (payload.query_hash) {
        const current = runs.get(key)
        if (current && !current.meta.queryHash) {
          runs.set(key, {
            ...current,
            meta: { ...current.meta, queryHash: payload.query_hash },
          })
        }
        runKeysByHash.set(payload.query_hash, key)
      }
      update(key, {
        state: 'complete',
        results: payload,
        rewriteTesting:
          normalizeRewriteTesting(payload.rewrite_testing) ??
          normalizeRewriteTesting(payload.formatted?.rewrite_testing),
        readysetCacheability:
          payload.readyset_cacheability ??
          runs.get(key)?.readysetCacheability ??
          undefined,
      })
      if (payload.query_hash) observer?.(payload.query_hash)
      break
    }

    case 'error':
      fail(key, normalizeSseError(data))
      break

    default: {
      // Exhaustiveness guard: adding a variant to AnalyzeEventType without
      // handling it here fails tsc. Do NOT use `as never`.
      const _exhaustive: never = eventType
      console.warn('[analyze] Unknown event type (ignored):', event, data)
      void _exhaustive
      break
    }
  }
}
