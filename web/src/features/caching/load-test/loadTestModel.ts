import type {
  BenchmarkRequest,
  BenchmarkState,
  QueryBenchmarkStats,
} from '../../../lib/api'
import type { BackgroundRunStatus } from '../../../lib/backgroundRuns'
import type { BenchmarkProgress } from '../../../lib/sse'

export type LoadTestProfile = 'paced' | 'capacity'
export type LoadTestOutcome =
  | 'queued'
  | 'running'
  | 'complete'
  | 'partial'
  | 'cancelled'
  | 'failed'
  | 'no_measurements'

/** A load test lane: the origin database, or Readyset run alongside it. */
export type LoadTestLane = 'origin' | 'readyset'

export interface LoadTestSkippedQuery {
  query_hash: string
  query_name?: string
  reason: string
  /** Per-lane skip reasons, when the backend distinguishes them. */
  laneReasons?: Partial<Record<LoadTestLane, string>>
}

export interface LoadTestLaneStats {
  successes: number
  failures: number
  avg_ms: number
  p95_ms: number
  p99_ms: number
  last_error?: string | null
}

export interface LoadTestQueryLanes {
  origin: LoadTestLaneStats
  readyset: LoadTestLaneStats
}

export interface LoadTestReadysetSetup {
  status: 'ok' | 'unavailable'
  detail?: string
}

/** Readyset cache preparation, which runs before the measurement starts. */
export interface LoadTestPreparation {
  preparedCount: number
  prepareTotal: number
}

export interface LoadTestLaneAggregate {
  successes: number
  failures: number
  qps: number
  meanLatency: number
  p95: number
}

export interface LoadTestResultModel {
  outcome: LoadTestOutcome
  running: boolean
  queued: boolean
  totalExecutions: number
  totalSuccesses: number
  totalFailures: number
  errorRate: number
  p95: number
  meanLatency: number
  durationSeconds: number
  profile: LoadTestProfile
  clients: number
  intervalMs: number
  expectedPacedQps: number | null
  progressPercent: number
  title: string
  description: string
  statusLabel: string
  skippedQueries: LoadTestSkippedQuery[]
  /** Set while the run is still creating this workload's Readyset caches. */
  preparation: LoadTestPreparation | undefined
  /** True once every query in the payload carries both origin and Readyset lane stats. */
  comparative: boolean
  readysetSetup: LoadTestReadysetSetup | undefined
  laneAggregates:
    | { origin: LoadTestLaneAggregate; readyset: LoadTestLaneAggregate }
    | undefined
  /** Origin mean latency divided by Readyset mean latency, when comparative. */
  speedup: number | null
}

const KNOWN_LANES: readonly LoadTestLane[] = ['origin', 'readyset']

function filterLanes(value: unknown): LoadTestLane[] | undefined {
  if (!Array.isArray(value)) return undefined
  const lanes = value.filter((entry): entry is LoadTestLane =>
    KNOWN_LANES.includes(entry as LoadTestLane)
  )
  return lanes.length > 0 ? lanes : undefined
}

function readLaneReasons(
  value: unknown
): Partial<Record<LoadTestLane, string>> | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Record<string, unknown>
  const reasons: Partial<Record<LoadTestLane, string>> = {}
  for (const lane of KNOWN_LANES) {
    const reason = candidate[lane]
    if (typeof reason === 'string') reasons[lane] = reason
  }
  return Object.keys(reasons).length > 0 ? reasons : undefined
}

/**
 * A newer backend may report queries it skipped rather than ran (e.g. one
 * whose parameters had no stored value) as additive fields on the complete
 * event. Nothing in the generated `QueryBenchmarkEvent` type promises they
 * exist, so every read here is defensive -- an older payload without them
 * yields an empty list and the UI section that depends on it stays hidden.
 */
function readSkippedQueries(
  progress: BenchmarkProgress | undefined
): LoadTestSkippedQuery[] {
  const value = (progress as { skipped_queries?: unknown } | undefined)
    ?.skipped_queries
  if (!Array.isArray(value)) return []
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const candidate = entry as Partial<LoadTestSkippedQuery> & {
      lanes?: unknown
    }
    if (
      typeof candidate.query_hash !== 'string' ||
      typeof candidate.reason !== 'string'
    ) {
      return []
    }
    const laneReasons = readLaneReasons(candidate.lanes)
    return [
      {
        query_hash: candidate.query_hash,
        query_name: candidate.query_name,
        reason: candidate.reason,
        ...(laneReasons ? { laneReasons } : {}),
      },
    ]
  })
}

function isLaneStats(value: unknown): value is LoadTestLaneStats {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<LoadTestLaneStats>
  return (
    typeof candidate.successes === 'number' &&
    typeof candidate.failures === 'number' &&
    typeof candidate.avg_ms === 'number' &&
    typeof candidate.p95_ms === 'number' &&
    typeof candidate.p99_ms === 'number'
  )
}

/**
 * A newer backend may report per-lane stats (origin vs Readyset) as an
 * additive `lanes` field on each query entry. Nothing in the generated
 * `QueryBenchmarkStats` type promises it exists, so this read is defensive --
 * an older payload without it yields `undefined` and callers keep rendering
 * the existing origin-only view for that query.
 */
export function readQueryLanes(
  query: QueryBenchmarkStats
): LoadTestQueryLanes | undefined {
  const value = (query as { lanes?: unknown }).lanes
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as { origin?: unknown; readyset?: unknown }
  if (!isLaneStats(candidate.origin) || !isLaneStats(candidate.readyset)) {
    return undefined
  }
  return { origin: candidate.origin, readyset: candidate.readyset }
}

/**
 * The complete event may report which lanes actually ran (an additive
 * `lanes_run` field) -- e.g. `["origin"]` when Readyset fell back to
 * unavailable mid-run. Defensive by the same rule as the other readers here.
 */
export function readLanesRun(
  progress: BenchmarkProgress | undefined
): LoadTestLane[] | undefined {
  return filterLanes(
    (progress as { lanes_run?: unknown } | undefined)?.lanes_run
  )
}

/**
 * The request sent to start a run may carry the lanes the user asked for (an
 * additive `lanes` field on `BenchmarkRequest`). Read defensively since the
 * generated request type does not declare it.
 */
export function readRequestLanes(
  request: BenchmarkRequest | undefined
): LoadTestLane[] | undefined {
  return filterLanes((request as { lanes?: unknown } | undefined)?.lanes)
}

/**
 * The complete event may report whether Readyset was available for this run
 * (an additive `readyset_setup` field). Absent it, the run is treated as
 * plain origin-only -- there is nothing to fall back to note.
 */
export function readReadysetSetup(
  progress: BenchmarkProgress | undefined
): LoadTestReadysetSetup | undefined {
  const value = (progress as { readyset_setup?: unknown } | undefined)
    ?.readyset_setup
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Partial<LoadTestReadysetSetup>
  if (candidate.status !== 'ok' && candidate.status !== 'unavailable') {
    return undefined
  }
  return {
    status: candidate.status,
    ...(typeof candidate.detail === 'string'
      ? { detail: candidate.detail }
      : {}),
  }
}

/**
 * A run that has to create this workload's Readyset caches first says so on
 * its progress ticks (additive `phase` / `prepared_count` / `prepare_total`),
 * because a cold sandbox can hold the run for minutes before the first
 * request. Defensive by the same rule as the other readers here: a payload
 * without the fields is a run that is already measuring.
 */
export function readPreparation(
  progress: BenchmarkProgress | undefined
): LoadTestPreparation | undefined {
  const candidate = progress as
    | { phase?: unknown; prepared_count?: unknown; prepare_total?: unknown }
    | undefined
  if (candidate?.phase !== 'preparing') return undefined
  const prepareTotal = candidate.prepare_total
  const preparedCount = candidate.prepared_count
  if (typeof prepareTotal !== 'number' || typeof preparedCount !== 'number') {
    return undefined
  }
  return { preparedCount, prepareTotal }
}

function aggregateLaneStats(
  queries: QueryBenchmarkStats[] | undefined,
  lane: LoadTestLane,
  elapsedSeconds: number
): LoadTestLaneAggregate | undefined {
  if (!queries?.length) return undefined
  const laneEntries = queries.map((query) => readQueryLanes(query)?.[lane])
  if (laneEntries.some((entry) => entry === undefined)) return undefined
  const entries = laneEntries as LoadTestLaneStats[]
  const successes = entries.reduce(
    (total, entry) => total + Math.max(0, entry.successes),
    0
  )
  const failures = entries.reduce(
    (total, entry) => total + Math.max(0, entry.failures),
    0
  )
  const weighted = (select: (entry: LoadTestLaneStats) => number) =>
    successes > 0
      ? entries.reduce(
          (total, entry) =>
            total + select(entry) * Math.max(0, entry.successes),
          0
        ) / successes
      : 0
  return {
    successes,
    failures,
    qps: elapsedSeconds > 0 ? successes / elapsedSeconds : 0,
    meanLatency: weighted((entry) => entry.avg_ms),
    p95: weighted((entry) => entry.p95_ms),
  }
}

function aggregateLatency(
  queries: QueryBenchmarkStats[] | undefined,
  select: (query: QueryBenchmarkStats) => number
) {
  if (!queries?.length) return 0
  const successes = queries.reduce(
    (total, query) => total + Math.max(0, query.successes),
    0
  )
  if (successes === 0) return 0
  return (
    queries.reduce(
      (total, query) => total + select(query) * Math.max(0, query.successes),
      0
    ) / successes
  )
}

function formatQps(value: number) {
  return value.toFixed(value >= 10 ? 0 : 1)
}

export function deriveLoadTestResultModel({
  state,
  status,
  stage,
  progress,
  request,
  fallbackDurationSeconds,
  fallbackIntervalMs,
}: {
  state: BenchmarkState
  status: BackgroundRunStatus | undefined
  stage: string | undefined
  progress: BenchmarkProgress | undefined
  request: BenchmarkRequest | undefined
  fallbackDurationSeconds: number
  fallbackIntervalMs: number
}): LoadTestResultModel {
  const totalExecutions = progress?.total_executions ?? 0
  const totalSuccesses = progress?.total_successes ?? 0
  const totalFailures = progress?.total_failures ?? 0
  const running = state === 'running'
  const queued = running && stage === 'queued' && !progress
  const preparation = running ? readPreparation(progress) : undefined
  const allFailed =
    !running && totalExecutions > 0 && totalFailures >= totalExecutions

  let outcome: LoadTestOutcome
  if (queued) outcome = 'queued'
  else if (running) outcome = 'running'
  else if (status === 'cancelled') outcome = 'cancelled'
  else if (
    state === 'error' ||
    status === 'failed' ||
    status === 'interrupted' ||
    allFailed
  ) {
    outcome = 'failed'
  } else if (totalExecutions === 0) outcome = 'no_measurements'
  else if (status === 'partial' || totalFailures > 0) outcome = 'partial'
  else outcome = 'complete'

  const profile: LoadTestProfile =
    request?.mode === 'concurrency' ? 'capacity' : 'paced'
  const durationSeconds = request?.duration_seconds ?? fallbackDurationSeconds
  const clients = profile === 'capacity' ? (request?.concurrency ?? 2) : 1
  const intervalMs = request?.interval_ms ?? fallbackIntervalMs
  const p95 = aggregateLatency(progress?.queries, (query) => query.p95_ms)
  const meanLatency = aggregateLatency(
    progress?.queries,
    (query) => query.avg_ms
  )
  const expectedPacedQps =
    profile === 'paced' && totalSuccesses > 0
      ? 1000 / (Math.max(0, intervalMs) + meanLatency)
      : null
  const qps = progress?.qps ?? 0
  const skippedQueries = readSkippedQueries(progress)
  const readysetSetup = readReadysetSetup(progress)
  const elapsedSeconds = progress?.elapsed_seconds ?? 0
  const originLane = aggregateLaneStats(
    progress?.queries,
    'origin',
    elapsedSeconds
  )
  const readysetLane = aggregateLaneStats(
    progress?.queries,
    'readyset',
    elapsedSeconds
  )
  const comparative = Boolean(originLane && readysetLane)
  const laneAggregates = comparative
    ? {
        origin: originLane as LoadTestLaneAggregate,
        readyset: readysetLane as LoadTestLaneAggregate,
      }
    : undefined
  const speedup =
    laneAggregates && laneAggregates.readyset.meanLatency > 0
      ? laneAggregates.origin.meanLatency / laneAggregates.readyset.meanLatency
      : null

  const statusLabel: Record<LoadTestOutcome, string> = {
    queued: 'Queued',
    running: 'Running',
    complete: 'Complete',
    partial: 'Completed with errors',
    cancelled: 'Cancelled',
    failed: 'Failed',
    no_measurements: 'No measurements',
  }

  const title: Record<LoadTestOutcome, string> = {
    queued: 'Waiting for an isolated test slot',
    running: 'Load test in progress',
    complete: `${formatQps(qps)} ${
      profile === 'capacity' ? 'sustained' : 'observed'
    } QPS at ${p95 > 0 ? `${p95.toFixed(p95 >= 10 ? 0 : 1)}ms` : '—'} p95`,
    partial: `${formatQps(qps)} ${
      profile === 'capacity' ? 'sustained' : 'observed'
    } QPS with errors`,
    cancelled: 'Load test cancelled',
    failed: 'Load test failed',
    no_measurements: 'No measurements completed',
  }

  const description: Record<LoadTestOutcome, string> = {
    queued:
      'Another performance measurement is active. This test will begin automatically.',
    running: `Running ${request?.queries.length ?? 0} read-only ${
      (request?.queries.length ?? 0) === 1 ? 'query' : 'queries'
    } against ${request?.target ?? 'the selected database'}.`,
    complete: `${totalSuccesses.toLocaleString()} requests completed across the configured workload.`,
    partial: `${totalSuccesses.toLocaleString()} requests completed; ${totalFailures.toLocaleString()} failed.`,
    cancelled:
      totalExecutions > 0
        ? `Stopped after ${totalExecutions.toLocaleString()} attempted requests.`
        : 'The run was stopped before a request completed.',
    failed:
      totalExecutions > 0
        ? `${totalFailures.toLocaleString()} of ${totalExecutions.toLocaleString()} attempted requests failed.`
        : 'The run ended before a request completed.',
    no_measurements: 'The run completed without a usable measurement.',
  }

  return {
    outcome,
    running,
    queued,
    totalExecutions,
    totalSuccesses,
    totalFailures,
    errorRate: totalExecutions ? (totalFailures / totalExecutions) * 100 : 0,
    p95,
    meanLatency,
    durationSeconds,
    profile,
    clients,
    intervalMs,
    expectedPacedQps,
    progressPercent: Math.min(
      100,
      Math.max(
        0,
        ((progress?.elapsed_seconds ?? 0) / Math.max(1, durationSeconds)) * 100
      )
    ),
    title: preparation
      ? `Preparing Readyset caches — ${preparation.preparedCount} of ${preparation.prepareTotal}`
      : title[outcome],
    description: preparation
      ? 'Readyset caches every query in this run before the first request. A cold sandbox can take a few minutes.'
      : description[outcome],
    statusLabel: preparation ? 'Preparing' : statusLabel[outcome],
    skippedQueries,
    preparation,
    comparative,
    readysetSetup,
    laneAggregates,
    speedup,
  }
}
