import type { CompareOutcomeRequest } from '../../../lib/api'
import {
  type BackgroundRunState,
  cancelBackgroundRun,
  startCacheCompareRun,
  updateCacheCompareLoad,
} from '../../../lib/backgroundRuns'
import { sanitizeWebError } from '../../../lib/errorContract'
import type {
  CacheCompareLaneSample,
  CacheCompareRunResult,
  CacheCompareSample,
} from '../../../types/cache'

const STORAGE_KEY = 'rdst_capacity_compare_batches_v3'
const ACTIVE_STORAGE_KEY = 'rdst_active_capacity_compare_batches_v3'
const MAX_STORED_BATCHES = 5

export interface CompareBatchQuery {
  cacheId: string
  label: string
  queryHash?: string
  runId?: string
  startError?: string
  outcome?: CompareQueryTerminalOutcome
}

export type CompareQueryOutcomeStatus =
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface CompareQueryTerminalOutcome {
  status: Exclude<CompareQueryOutcomeStatus, 'running'>
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
}

export interface CompareBatch {
  id: string
  target: string
  createdAt: string
  concurrency: number
  durationSeconds: number
  queries: CompareBatchQuery[]
  timeline?: CacheCompareSample[]
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
  completed: number
  total: number
  succeeded: number
  failed: number
  cancelled: number
  percent: number
  runs: BackgroundRunState[]
  results: CompareStoredResult[]
  queryOutcomes: CompareQueryOutcome[]
  timeline: CacheCompareSample[]
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

function writeBatches(batches: CompareBatch[]): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(batches.slice(-MAX_STORED_BATCHES))
    )
  } catch {
    // Route persistence is progressive enhancement when storage is unavailable.
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

function aggregateLane(
  lanes: CacheCompareLaneSample[]
): CacheCompareLaneSample {
  const completed = lanes.reduce((sum, lane) => sum + lane.completed, 0)
  const attempts = lanes.reduce(
    (sum, lane) => sum + lane.completed + lane.errors,
    0
  )
  const weighted = (key: 'mean_ms' | 'p50_ms' | 'p95_ms' | 'p99_ms') =>
    completed > 0
      ? lanes.reduce((sum, lane) => sum + lane[key] * lane.completed, 0) /
        completed
      : 0
  return {
    scheduled: lanes.reduce((sum, lane) => sum + lane.scheduled, 0),
    completed,
    errors: lanes.reduce((sum, lane) => sum + lane.errors, 0),
    dropped: lanes.reduce((sum, lane) => sum + lane.dropped, 0),
    in_flight: lanes.reduce((sum, lane) => sum + (lane.in_flight ?? 0), 0),
    throughput_rps: lanes.reduce((sum, lane) => sum + lane.throughput_rps, 0),
    error_rate:
      attempts > 0
        ? lanes.reduce(
            (sum, lane) =>
              sum + lane.error_rate * (lane.completed + lane.errors),
            0
          ) / attempts
        : 0,
    mean_ms: weighted('mean_ms'),
    p50_ms: weighted('p50_ms'),
    p95_ms: weighted('p95_ms'),
    p99_ms: weighted('p99_ms'),
  }
}

function aggregateSampleTimelines(
  timelines: CacheCompareSample[][]
): CacheCompareSample[] {
  const maxLength = Math.max(0, ...timelines.map((timeline) => timeline.length))
  return Array.from({ length: maxLength }, (_, index) => {
    const samples = timelines.flatMap((timeline) => {
      const sample = timeline[index]
      return sample ? [sample] : []
    })
    return {
      elapsed_seconds: Math.max(
        0,
        ...samples.map((sample) => sample.elapsed_seconds)
      ),
      concurrency: samples.reduce((sum, sample) => sum + sample.concurrency, 0),
      origin: aggregateLane(samples.map((sample) => sample.origin)),
      readyset: aggregateLane(samples.map((sample) => sample.readyset)),
    }
  })
}

export function aggregateCompareTimeline(
  results: CompareStoredResult[]
): CacheCompareSample[] {
  return aggregateSampleTimelines(results.map(({ result }) => result.timeline))
}

function compareQueryOutcome(
  query: CompareBatchQuery,
  run: BackgroundRunState | undefined,
  storedResult: CompareStoredResult | undefined,
  batchOutcome: CompareBatch['outcome']
): CompareQueryOutcome {
  if (storedResult) {
    return {
      cacheId: query.cacheId,
      label: query.label,
      runId: query.runId,
      status: 'succeeded',
      result: storedResult.result,
    }
  }

  if (query.outcome) {
    return {
      cacheId: query.cacheId,
      label: query.label,
      runId: query.runId,
      ...query.outcome,
    }
  }

  if (query.startError) {
    return {
      cacheId: query.cacheId,
      label: query.label,
      runId: query.runId,
      status: 'failed',
      message: query.startError,
    }
  }

  if (run?.status === 'cancelled') {
    return {
      cacheId: query.cacheId,
      label: query.label,
      runId: query.runId,
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
      cacheId: query.cacheId,
      label: query.label,
      runId: query.runId,
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
      cacheId: query.cacheId,
      label: query.label,
      runId: query.runId,
      status: batchOutcome.status === 'cancelled' ? 'cancelled' : 'failed',
      message:
        batchOutcome.status === 'cancelled'
          ? 'Comparison was cancelled.'
          : 'No comparison measurement was produced.',
    }
  }

  return {
    cacheId: query.cacheId,
    label: query.label,
    runId: query.runId,
    status: 'running',
    message: run?.message,
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
  const timeline =
    batch.timeline && batch.timeline.length > 0
      ? batch.timeline
      : aggregateSampleTimelines(
          runs.some((run) => (run.compareSamples?.length ?? 0) > 0)
            ? runs.map((run) => run.compareSamples ?? [])
            : results.map(({ result }) => result.timeline)
        )

  if (batch.outcome) {
    return {
      status: batch.outcome.status,
      completed: batch.queries.length,
      total: batch.queries.length,
      succeeded: results.length,
      failed: batch.outcome.failed,
      cancelled: batch.outcome.cancelled,
      percent: 100,
      runs,
      results,
      queryOutcomes,
      timeline,
    }
  }

  const succeeded = queryOutcomes.filter(
    (outcome) => outcome.status === 'succeeded'
  ).length
  const cancelled = queryOutcomes.filter(
    (outcome) => outcome.status === 'cancelled'
  ).length
  const failed = queryOutcomes.filter(
    (outcome) => outcome.status === 'failed'
  ).length
  const running = runs.filter((run) =>
    ['running', 'reconnecting', 'needs_key'].includes(run.status)
  )
  const terminal = succeeded + failed + cancelled
  const total = batch.queries.length
  const progress =
    running.reduce((sum, run) => sum + (run.current ?? 0), 0) / 100
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
    percent,
    runs,
    results,
    queryOutcomes,
    timeline,
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
    timeline: snapshot.timeline,
    queries: batch.queries.map((query) => {
      const outcome = snapshot.queryOutcomes.find(
        (candidate) => candidate.cacheId === query.cacheId
      )
      if (!outcome || outcome.status === 'running') return query
      const { status, message, errorCode, errorCategory } = outcome
      return {
        ...query,
        outcome: { status, message, errorCode, errorCategory },
      }
    }),
    results: snapshot.results.map((stored) => ({
      ...stored,
      result: { ...stored.result, timeline: [] },
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
