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
    title: title[outcome],
    description: description[outcome],
    statusLabel: statusLabel[outcome],
  }
}
