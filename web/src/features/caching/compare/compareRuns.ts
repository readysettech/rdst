import type { CompareOutcomeRequest } from '../../../lib/api'
import {
  type BackgroundRunState,
  cancelBackgroundRun,
  isQueuedRun,
  startCacheCompareRun,
  updateCacheCompareLoad,
} from '../../../lib/backgroundRuns'
import { sanitizeWebError } from '../../../lib/errorContract'
import type {
  CacheCompareRunResult,
  CacheCompareSample,
} from '../../../types/cache'

const STORAGE_KEY = 'rdst_capacity_compare_batches_v3'
const ACTIVE_STORAGE_KEY = 'rdst_active_capacity_compare_batches_v3'
const MAX_STORED_BATCHES = 5

/**
 * Samples kept per query when a batch settles, so a finished comparison can
 * still draw each query's own curve. The budget is
 * `MAX_STORED_BATCHES x MAX_COMPARE_QUERIES x MAX_RETAINED_SAMPLES` samples;
 * `writeBatches` drops the curves rather than the verdicts if storage still
 * refuses the write.
 */
const MAX_RETAINED_SAMPLES = 60

export interface CompareBatchQuery {
  cacheId: string
  label: string
  queryHash?: string
  runId?: string
  startError?: string
  outcome?: CompareQueryTerminalOutcome
}

export type CompareQueryOutcomeStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface CompareQueryTerminalOutcome {
  status: Exclude<CompareQueryOutcomeStatus, 'queued' | 'running'>
  message?: string
  errorCode?: string
  errorCategory?: string
}

export interface CompareQueryOutcome {
  cacheId: string
  label: string
  runId?: string
  status: CompareQueryOutcomeStatus
  message?: string
  errorCode?: string
  errorCategory?: string
  result?: CacheCompareRunResult
  /** This query's own samples: live while it measures, retained once it settles. */
  timeline: CacheCompareSample[]
  /** Seconds this query has measured for, from its last sample. */
  elapsedSeconds: number
  /** Backend-reported progress through this query's own measurement. */
  percent?: number
}

export interface CompareBatch {
  id: string
  target: string
  createdAt: string
  concurrency: number
  durationSeconds: number
  queries: CompareBatchQuery[]
  outcome?: {
    status: Exclude<CompareBatchStatus, 'running'>
    completedAt: string
    failed: number
    cancelled: number
  }
  results?: CompareStoredResult[]
}

export type CompareBatchStatus =
  | 'running'
  | 'complete'
  | 'partial'
  | 'failed'
  | 'cancelled'

export interface CompareBatchSnapshot {
  status: CompareBatchStatus
  /** Queries that reached a terminal outcome, however it was classified. */
  completed: number
  total: number
  succeeded: number
  failed: number
  cancelled: number
  /** Queries measuring right now -- at most one, since the sandbox serializes. */
  running: number
  /** Queries still waiting for the sandbox lease. */
  queued: number
  percent: number
  runs: BackgroundRunState[]
  results: CompareStoredResult[]
  queryOutcomes: CompareQueryOutcome[]
}

export interface CompareStoredResult {
  cacheId: string
  label: string
  runId?: string
  result: CacheCompareRunResult
}

function readBatches(): CompareBatch[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(parsed) ? (parsed as CompareBatch[]) : []
  } catch {
    return []
  }
}

/** Downsample to at most `MAX_RETAINED_SAMPLES` points, keeping both ends. */
function retainedTimeline(
  timeline: CacheCompareSample[]
): CacheCompareSample[] {
  if (timeline.length <= MAX_RETAINED_SAMPLES) return timeline
  const step = (timeline.length - 1) / (MAX_RETAINED_SAMPLES - 1)
  return Array.from(
    { length: MAX_RETAINED_SAMPLES },
    (_, index) => timeline[Math.round(index * step)]
  )
}

function withoutTimelines(batch: CompareBatch): CompareBatch {
  return {
    ...batch,
    results: batch.results?.map((stored) => ({
      ...stored,
      result: { ...stored.result, timeline: [] },
    })),
  }
}

function writeBatches(batches: CompareBatch[]): void {
  const kept = batches.slice(-MAX_STORED_BATCHES)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(kept))
  } catch {
    try {
      // Retained curves are by far the largest part of a stored batch. If they
      // no longer fit, keep every verdict and lose only the redrawn charts.
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(kept.map(withoutTimelines))
      )
    } catch {
      // Route persistence is progressive enhancement when storage is
      // unavailable.
    }
  }
}

export function latestCompareBatch(target: string | null): CompareBatch | null {
  if (!target) return null
  try {
    const activeByTarget = JSON.parse(
      localStorage.getItem(ACTIVE_STORAGE_KEY) ?? '{}'
    ) as Record<string, string>
    const activeId = activeByTarget[target]
    return (
      readBatches().find(
        (batch) => batch.target === target && batch.id === activeId
      ) ?? null
    )
  } catch {
    return null
  }
}

export function listCompareBatches(target: string | null): CompareBatch[] {
  if (!target) return []
  return readBatches()
    .filter((batch) => batch.target === target)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}

export function forgetCompareBatch(batchId: string): void {
  const batch = readBatches().find((candidate) => candidate.id === batchId)
  writeBatches(readBatches().filter((candidate) => candidate.id !== batchId))
  if (batch) clearActiveCompareBatch(batch.target, batchId)
}

export function selectCompareBatch(batch: CompareBatch): void {
  try {
    const activeByTarget = JSON.parse(
      localStorage.getItem(ACTIVE_STORAGE_KEY) ?? '{}'
    ) as Record<string, string>
    activeByTarget[batch.target] = batch.id
    localStorage.setItem(ACTIVE_STORAGE_KEY, JSON.stringify(activeByTarget))
  } catch {
    // Route persistence is progressive enhancement when storage is unavailable.
  }
}

export function clearActiveCompareBatch(
  target: string | null,
  batchId?: string
): void {
  if (!target) return
  try {
    const activeByTarget = JSON.parse(
      localStorage.getItem(ACTIVE_STORAGE_KEY) ?? '{}'
    ) as Record<string, string>
    if (batchId && activeByTarget[target] !== batchId) return
    delete activeByTarget[target]
    localStorage.setItem(ACTIVE_STORAGE_KEY, JSON.stringify(activeByTarget))
  } catch {
    // Route persistence is progressive enhancement when storage is unavailable.
  }
}

function saveCompareBatch(batch: CompareBatch): CompareBatch {
  const batches = readBatches()
  const existingIndex = batches.findIndex(
    (candidate) => candidate.id === batch.id
  )
  if (existingIndex >= 0) {
    batches[existingIndex] = batch
  } else {
    batches.push(batch)
  }
  writeBatches(batches)
  return batch
}

export async function startCompareBatch(input: {
  target: string
  concurrency: number
  durationSeconds: number
  queries: Array<{
    cacheId: string
    label: string
    queryHash?: string
    sql: string
  }>
}): Promise<CompareBatch> {
  const batch: CompareBatch = {
    id: `compare_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    target: input.target,
    createdAt: new Date().toISOString(),
    concurrency: input.concurrency,
    durationSeconds: input.durationSeconds,
    queries: [],
  }

  const baseConcurrency = Math.floor(input.concurrency / input.queries.length)
  const remainder = input.concurrency % input.queries.length
  batch.queries = await Promise.all(
    input.queries.map(async (query, index) => {
      const runId = await startCacheCompareRun({
        query: query.sql,
        target: input.target,
        query_hash: query.queryHash,
        label: query.label,
        concurrency: baseConcurrency + (index < remainder ? 1 : 0),
        duration_seconds: input.durationSeconds,
      })
      return {
        cacheId: query.cacheId,
        label: query.label,
        queryHash: query.queryHash,
        runId: runId ?? undefined,
        startError: runId ? undefined : 'Comparison could not start',
      }
    })
  )

  const saved = saveCompareBatch(batch)
  selectCompareBatch(saved)
  return saved
}

/** The label a queued query carries, naming what it is waiting for. */
export const COMPARE_QUEUED_MESSAGE = 'Queued for the Readyset sandbox'

function compareQueryOutcome(
  query: CompareBatchQuery,
  run: BackgroundRunState | undefined,
  storedResult: CompareStoredResult | undefined,
  batchOutcome: CompareBatch['outcome']
): CompareQueryOutcome {
  const measured = storedResult?.result.timeline ?? []
  const timeline = measured.length > 0 ? measured : (run?.compareSamples ?? [])
  const base = {
    cacheId: query.cacheId,
    label: query.label,
    runId: query.runId,
    timeline,
    elapsedSeconds:
      timeline[timeline.length - 1]?.elapsed_seconds ??
      storedResult?.result.elapsed_seconds ??
      0,
  }

  if (storedResult) {
    return {
      ...base,
      status: 'succeeded',
      result: storedResult.result,
      percent: 100,
    }
  }

  if (query.outcome) return { ...base, ...query.outcome }

  if (query.startError) {
    return { ...base, status: 'failed', message: query.startError }
  }

  if (run?.status === 'cancelled') {
    return {
      ...base,
      status: 'cancelled',
      message: run.message || 'Comparison was cancelled.',
      errorCode: run.errorCode,
      errorCategory: run.errorCategory,
    }
  }

  if (
    run &&
    ['done', 'failed', 'interrupted', 'partial'].includes(run.status)
  ) {
    return {
      ...base,
      status: 'failed',
      message: run.message || 'No comparison measurement was produced.',
      errorCode: run.errorCode,
      errorCategory: run.errorCategory,
    }
  }

  // Older persisted batches predate per-query outcomes. Preserve their
  // terminal state without hiding the queries that produced no measurement.
  if (batchOutcome) {
    return {
      ...base,
      status: batchOutcome.status === 'cancelled' ? 'cancelled' : 'failed',
      message:
        batchOutcome.status === 'cancelled'
          ? 'Comparison was cancelled.'
          : 'No comparison measurement was produced.',
    }
  }

  // The sandbox admits one comparison at a time, so the queries behind the
  // measuring one are waiting rather than running.
  if (run && isQueuedRun(run)) {
    return {
      ...base,
      status: 'queued',
      message: run.message || COMPARE_QUEUED_MESSAGE,
    }
  }

  return {
    ...base,
    status: 'running',
    message: run?.message,
    percent: run?.current ?? 0,
  }
}

export function compareBatchSnapshot(
  batch: CompareBatch,
  backgroundRuns: BackgroundRunState[]
): CompareBatchSnapshot {
  const runById = new Map(backgroundRuns.map((run) => [run.runId, run]))
  const runs = batch.queries.flatMap((query) => {
    const run = query.runId ? runById.get(query.runId) : undefined
    return run ? [run] : []
  })
  const liveResults = batch.queries.flatMap((query) => {
    const run = query.runId ? runById.get(query.runId) : undefined
    return run?.compareResult
      ? [
          {
            cacheId: query.cacheId,
            label: query.label,
            runId: query.runId,
            result: run.compareResult,
          },
        ]
      : []
  })
  const resultByCacheId = new Map(
    [...(batch.results ?? []), ...liveResults].map((result) => [
      result.cacheId,
      result,
    ])
  )
  const results = batch.queries.flatMap((query) => {
    const result = resultByCacheId.get(query.cacheId)
    return result ? [result] : []
  })
  const queryOutcomes = batch.queries.map((query) =>
    compareQueryOutcome(
      query,
      query.runId ? runById.get(query.runId) : undefined,
      resultByCacheId.get(query.cacheId),
      batch.outcome
    )
  )

  if (batch.outcome) {
    return {
      status: batch.outcome.status,
      completed: batch.queries.length,
      total: batch.queries.length,
      succeeded: results.length,
      failed: batch.outcome.failed,
      cancelled: batch.outcome.cancelled,
      running: 0,
      queued: 0,
      percent: 100,
      runs,
      results,
      queryOutcomes,
    }
  }

  const countOf = (status: CompareQueryOutcomeStatus) =>
    queryOutcomes.filter((outcome) => outcome.status === status).length
  const succeeded = countOf('succeeded')
  const cancelled = countOf('cancelled')
  const failed = countOf('failed')
  const queued = countOf('queued')
  const running = countOf('running')
  const terminal = succeeded + failed + cancelled
  const total = batch.queries.length
  // Batch progress is whole queries plus the one being measured -- a queued
  // query has made none, whatever its run's last reported percent was.
  const progress =
    queryOutcomes
      .filter((outcome) => outcome.status === 'running')
      .reduce((sum, outcome) => sum + (outcome.percent ?? 0), 0) / 100
  const percent =
    total > 0
      ? Math.min(100, Math.round(((terminal + progress) / total) * 100))
      : 0

  let status: CompareBatchStatus
  if (terminal < total) {
    status = 'running'
  } else if (succeeded === total) {
    status = 'complete'
  } else if (cancelled === total) {
    status = 'cancelled'
  } else if (succeeded > 0) {
    status = 'partial'
  } else {
    status = 'failed'
  }

  return {
    status,
    completed: terminal,
    total,
    succeeded,
    failed,
    cancelled,
    running,
    queued,
    percent,
    runs,
    results,
    queryOutcomes,
  }
}

export function settleCompareBatch(
  batch: CompareBatch,
  snapshot: CompareBatchSnapshot
): CompareBatch {
  if (snapshot.status === 'running' || batch.outcome) return batch
  return saveCompareBatch({
    ...batch,
    outcome: {
      status: snapshot.status,
      completedAt: new Date().toISOString(),
      failed: snapshot.failed,
      cancelled: snapshot.cancelled,
    },
    queries: batch.queries.map((query) => {
      const outcome = snapshot.queryOutcomes.find(
        (candidate) => candidate.cacheId === query.cacheId
      )
      if (
        !outcome ||
        outcome.status === 'running' ||
        outcome.status === 'queued'
      )
        return query
      const { status, message, errorCode, errorCategory } = outcome
      return {
        ...query,
        outcome: { status, message, errorCode, errorCategory },
      }
    }),
    // Keep each query's own curve, downsampled: after settlement the
    // background runs are cleaned up, and this is the only record of what
    // each query measured.
    results: snapshot.results.map((stored) => ({
      ...stored,
      result: {
        ...stored.result,
        timeline: retainedTimeline(
          stored.result.timeline.length > 0
            ? stored.result.timeline
            : (snapshot.queryOutcomes.find(
                (outcome) => outcome.cacheId === stored.cacheId
              )?.timeline ?? [])
        ),
      },
    })),
  })
}

export async function cancelCompareBatch(batch: CompareBatch): Promise<void> {
  await Promise.all(
    batch.queries.flatMap((query) =>
      query.runId ? [cancelBackgroundRun(query.runId)] : []
    )
  )
}

export async function updateCompareBatchLoad(
  batch: CompareBatch,
  concurrency: number
): Promise<CompareBatch> {
  const runIds = batch.queries.flatMap((query) =>
    query.runId ? [query.runId] : []
  )
  if (runIds.length === 0) return batch
  const baseConcurrency = Math.floor(concurrency / batch.queries.length)
  const remainder = concurrency % batch.queries.length
  const updates = await Promise.all(
    runIds.map((runId, index) =>
      updateCacheCompareLoad(
        runId,
        baseConcurrency + (index < remainder ? 1 : 0)
      )
    )
  )
  if (updates.some((updated) => !updated)) return batch
  return saveCompareBatch({ ...batch, concurrency })
}

// A query's outcome is classification -- Readyset declining to cache it, or a
// pre-flight check finding the two lanes can't be compared -- rather than a
// system failure, whenever its evidence says so. Both the per-query chip and
// the reported compare-outcome status read this the same way.
const COMPARE_EQUIVALENT_THRESHOLD_PCT = 5
const COMPARE_OUTCOME_DETAIL_MAX = 500

function truncateDetail(text: string): string {
  return text.length > COMPARE_OUTCOME_DETAIL_MAX
    ? text.slice(0, COMPARE_OUTCOME_DETAIL_MAX)
    : text
}

function compareFailureEvidence(outcome: CompareQueryOutcome): string {
  return [outcome.errorCode, outcome.errorCategory, outcome.message]
    .filter(Boolean)
    .join(' ')
}

/** Readyset itself declined to cache the query (an EXPLAIN CREATE CACHE
 * verdict), so the comparison never ran. */
export function isUnsupportedCompareOutcome(outcome: CompareQueryOutcome) {
  return (
    outcome.status === 'failed' &&
    /unsupported|uncacheable|not cacheable/i.test(
      compareFailureEvidence(outcome)
    )
  )
}

/** Any pre-flight exclusion, Readyset-support or not -- e.g. a LIMIT-without-
 * ORDER-BY query whose origin and Readyset rows can't be compared. */
export function isNotComparableCompareOutcome(outcome: CompareQueryOutcome) {
  return (
    isUnsupportedCompareOutcome(outcome) ||
    (outcome.status === 'failed' &&
      /not comparable/i.test(compareFailureEvidence(outcome)))
  )
}

/**
 * Map one query's terminal Compare outcome onto the compare-outcome API
 * contract (POST /query-registry/queries/{hash}/compare-outcome). Running
 * and cancelled outcomes are not durable measurements, so this reports
 * nothing for them.
 */
export function deriveCompareOutcomeReport(
  outcome: CompareQueryOutcome,
  queryHash: string | undefined,
  target: string
): { queryHash: string; request: CompareOutcomeRequest } | null {
  if (!queryHash) return null

  if (outcome.status === 'succeeded' && outcome.result) {
    const pct = outcome.result.improvement_pct
    const status =
      Math.abs(pct) < COMPARE_EQUIVALENT_THRESHOLD_PCT
        ? 'equivalent'
        : pct > 0
          ? 'improved'
          : 'regressed'
    return {
      queryHash,
      request: {
        target,
        status,
        readyset_ms: outcome.result.readyset.mean_ms,
        origin_ms: outcome.result.origin.mean_ms,
        readyset_supported: 'yes',
      },
    }
  }

  if (outcome.status !== 'failed') return null

  const detail = truncateDetail(sanitizeWebError(outcome.message))
  if (isUnsupportedCompareOutcome(outcome)) {
    return {
      queryHash,
      request: {
        target,
        status: 'not_comparable',
        detail,
        readyset_supported: 'no',
        unsupported_reason: detail,
      },
    }
  }
  if (isNotComparableCompareOutcome(outcome)) {
    return {
      queryHash,
      request: { target, status: 'not_comparable', detail },
    }
  }
  return { queryHash, request: { target, status: 'error', detail } }
}
