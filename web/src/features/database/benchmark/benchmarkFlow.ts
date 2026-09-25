// Benchmark one query from its detail (DESIGN_V5 section 4): the presets the dialog
// offers, the request they become, whether Readyset can join the run, and the run's
// result and history read into one shape for the Benchmarks section.
import type { BenchmarkRequest } from '@/lib/api'
import type { components } from '@/lib/api.generated'
import type { BackgroundRunState, LoadTestProgress } from '@/lib/backgroundRuns'
import type { LastCompare } from '@/lib/monitor/types'

export type PresetId = 'quick' | 'standard'

export interface BenchmarkPreset {
  id: PresetId
  label: string
  durationSeconds: number
  mode: 'interval' | 'concurrency'
  /** Pause after each run, per connection (interval mode). */
  intervalMs: number
  connections: number
  /** How long and how hard, in words. */
  load: string
  /** How much load that is on the database. */
  intensity: string
}

export const PRESETS: readonly BenchmarkPreset[] = [
  {
    id: 'quick',
    label: 'Quick',
    durationSeconds: 30,
    mode: 'interval',
    intervalMs: 100,
    connections: 1,
    load: '30 seconds on one connection, with a short pause between runs.',
    intensity: 'Light load',
  },
  {
    id: 'standard',
    label: 'Standard',
    durationSeconds: 120,
    mode: 'concurrency',
    intervalMs: 0,
    connections: 2,
    load: '2 minutes on two connections, back to back, to see how much throughput it can reach.',
    intensity: 'Higher load',
  },
]

export const DEFAULT_PRESET: PresetId = 'quick'

export function presetById(id: PresetId): BenchmarkPreset {
  return PRESETS.find((preset) => preset.id === id) ?? PRESETS[0]
}

/** The most runs a paced preset can make; null when it runs back to back. */
export function maxRuns(preset: BenchmarkPreset): number | null {
  if (preset.mode !== 'interval' || preset.intervalMs <= 0) return null
  return Math.floor(
    ((preset.durationSeconds * 1000) / preset.intervalMs) * preset.connections
  )
}

/** The request one query's benchmark sends. `sql` carries filled-in parameters. */
export function queryRequest({
  identifier,
  sql,
  target,
  preset,
  withReadyset,
}: {
  identifier: string
  sql?: string
  target: string
  preset: BenchmarkPreset
  withReadyset: boolean
}): BenchmarkRequest & { lanes: Array<'origin' | 'readyset'> } {
  return {
    queries: [sql ? { identifier, sql } : { identifier }],
    target,
    mode: preset.mode,
    interval_ms: preset.mode === 'interval' ? preset.intervalMs : 0,
    concurrency: preset.connections,
    duration_seconds: preset.durationSeconds,
    lanes: withReadyset ? ['origin', 'readyset'] : ['origin'],
  }
}

/** The preset a past request matches, so "Run again" reopens on it. */
export function presetForRequest(
  request: Pick<BenchmarkRequest, 'mode' | 'duration_seconds'> | undefined
): PresetId {
  if (!request) return DEFAULT_PRESET
  return (
    PRESETS.find(
      (preset) =>
        preset.mode === request.mode &&
        preset.durationSeconds === request.duration_seconds
    )?.id ?? DEFAULT_PRESET
  )
}

export interface SandboxFacts {
  docker_installed?: boolean
  docker_running?: boolean
  phase?: string
  current_target?: string | null
  healthy?: boolean
}

export type ReadysetReadiness = 'ready' | 'setup' | 'unavailable' | 'unknown'

/** Whether the Readyset side can run, and what the dialog says about it. */
export function readysetReadiness(
  sandbox: SandboxFacts | undefined,
  target: string
): { state: ReadysetReadiness; note: string } {
  if (!sandbox)
    return {
      state: 'unknown',
      note: 'Needs Docker on this computer. RDST runs Readyset there in a temporary container.',
    }
  if (sandbox.docker_installed === false || sandbox.docker_running === false)
    return {
      state: 'unavailable',
      note: "Needs Docker, which isn't running on this computer. Start Docker to add Readyset.",
    }
  if (
    sandbox.phase === 'ready' &&
    sandbox.healthy !== false &&
    sandbox.current_target === target
  )
    return {
      state: 'ready',
      note: 'Readyset is already running for this database in a temporary container on this computer.',
    }
  return {
    state: 'setup',
    note: 'RDST starts Readyset in a temporary container on this computer first. That can add a few minutes before measuring starts.',
  }
}

const LIVE = new Set(['running', 'reconnecting', 'stopping', 'needs_key'])

export function isLiveRun(run: BackgroundRunState): boolean {
  return LIVE.has(run.status)
}

function requestIdentifiers(request: BenchmarkRequest | undefined): string[] {
  return (request?.queries ?? []).map((query) =>
    typeof query === 'string' ? query : (query.identifier ?? '')
  )
}

/** Benchmarks that include this query on this database. */
export function isQueryRun(
  run: BackgroundRunState,
  target: string,
  identifiers: readonly string[]
): boolean {
  if (run.kind !== 'load_test' || run.target !== target) return false
  const ids = requestIdentifiers(run.loadRequest)
  return ids.some((id) => identifiers.includes(id))
}

/** The newest run of this query in this browser, live or finished. */
export function latestQueryRun(
  runs: readonly BackgroundRunState[],
  target: string,
  identifiers: readonly string[]
): BackgroundRunState | undefined {
  const own = runs.filter((run) => isQueryRun(run, target, identifiers))
  return own.find(isLiveRun) ?? own[own.length - 1]
}

export type RunPhase = 'waiting' | 'readyset' | 'measuring' | 'done'

/** Where a live run is, for the progress steps. */
export function runPhase(run: BackgroundRunState): RunPhase {
  if (!isLiveRun(run)) return 'done'
  if (run.stage === 'preparing') return 'readyset'
  if (run.stage === 'queued' || !run.loadResult) return 'waiting'
  return 'measuring'
}

export function runWantsReadyset(run: BackgroundRunState): boolean {
  const lanes = (run.loadRequest as { lanes?: unknown } | undefined)?.lanes
  return Array.isArray(lanes) && lanes.includes('readyset')
}

export interface LaneNumbers {
  p50: number | null
  p95: number | null
  qps: number | null
  executions: number
  failures: number
}

export interface BenchmarkSummary {
  /** Seconds since the epoch. */
  ts: number
  source: 'run' | 'history' | 'compare'
  outcome: 'done' | 'partial' | 'failed' | 'cancelled'
  durationSeconds: number | null
  connections: number | null
  database: LaneNumbers | null
  readyset: LaneNumbers | null
  /** Median latency on the database over median through Readyset. */
  speedup: number | null
  /** Why the Readyset side didn't measure, when it was asked for. */
  readysetNote: string | null
  withReadyset: boolean
  /** Paced runs pause between runs, so their throughput is the pace. */
  paced: boolean
}

export function speedupOf(
  database: number | null | undefined,
  readyset: number | null | undefined
): number | null {
  if (!database || !readyset || database <= 0 || readyset <= 0) return null
  return database / readyset
}

/** '12x faster', '1.4x faster', 'about the same', '1.3x slower'. */
export function speedupLabel(speedup: number | null): string | null {
  if (speedup == null || !Number.isFinite(speedup)) return null
  if (speedup >= 0.9 && speedup < 1.1) return 'about the same'
  if (speedup >= 1) {
    const x = speedup >= 10 ? Math.round(speedup) : speedup.toFixed(1)
    return `${x}x faster`
  }
  const slower = 1 / speedup
  return `${slower >= 10 ? Math.round(slower) : slower.toFixed(1)}x slower`
}

type StatsLike = {
  p50_ms?: number | null
  p95_ms?: number | null
  successes?: number | null
  executions?: number | null
  failures?: number | null
}

function laneFrom(
  stats: StatsLike | undefined,
  elapsed: number
): LaneNumbers | null {
  if (!stats || !stats.executions) return null
  const successes = stats.successes ?? 0
  return {
    p50: stats.p50_ms ?? null,
    p95: stats.p95_ms ?? null,
    qps: elapsed > 0 ? successes / elapsed : null,
    executions: stats.executions ?? 0,
    failures: stats.failures ?? 0,
  }
}

function outcomeOf(
  status: BackgroundRunState['status']
): BenchmarkSummary['outcome'] {
  if (status === 'cancelled') return 'cancelled'
  if (status === 'failed' || status === 'interrupted') return 'failed'
  if (status === 'partial') return 'partial'
  return 'done'
}

/** A run from this browser: its progress so far, or its final numbers. */
export function summaryFromRun(
  run: BackgroundRunState,
  nowSeconds = Date.now() / 1000
): BenchmarkSummary {
  const progress: LoadTestProgress | undefined = run.loadResult
  const elapsed = progress?.elapsed_seconds ?? 0
  const stats = progress?.queries?.[0]
  const lanes = (stats as { lanes?: Record<string, StatsLike> } | undefined)
    ?.lanes
  const readysetOnly =
    run.loadRequest?.lanes?.length === 1 &&
    run.loadRequest.lanes[0] === 'readyset'
  const database = readysetOnly ? null : laneFrom(stats, elapsed)
  const readyset = laneFrom(
    lanes?.readyset ?? (readysetOnly ? stats : undefined),
    elapsed
  )
  const setup = (
    progress as
      | { readyset_setup?: { status?: string; detail?: string } }
      | undefined
  )?.readyset_setup
  const withReadyset = runWantsReadyset(run)
  return {
    ts: nowSeconds,
    source: 'run',
    outcome: outcomeOf(run.status),
    durationSeconds: run.loadRequest?.duration_seconds ?? null,
    connections: run.loadRequest?.concurrency ?? null,
    database,
    readyset,
    speedup: speedupOf(database?.p50, readyset?.p50),
    readysetNote:
      withReadyset && !readyset && !isLiveRun(run)
        ? (setup?.detail ?? "Readyset couldn't measure this run.")
        : null,
    withReadyset,
    paced:
      run.loadRequest?.mode === 'interval' &&
      (run.loadRequest.interval_ms ?? 100) > 0,
  }
}

/** GET /api/query-registry/queries/{hash}/benchmarks */
export type QueryBenchmarkRun = components['schemas']['QueryBenchmarkRun']
type QueryBenchmarkLane = components['schemas']['BenchmarkLaneNumbers']

function laneFromHistory(
  lane: QueryBenchmarkLane | null | undefined
): LaneNumbers | null {
  if (!lane?.executions) return null
  return {
    p50: lane.p50_ms ?? null,
    p95: lane.p95_ms ?? null,
    qps: lane.qps ?? null,
    executions: lane.executions,
    failures: lane.failures ?? 0,
  }
}

export function summaryFromHistory(run: QueryBenchmarkRun): BenchmarkSummary {
  const database =
    run.lanes?.length === 1 && run.lanes[0] === 'readyset'
      ? null
      : laneFromHistory(run.query)
  const readyset = laneFromHistory(run.query.readyset)
  const withReadyset = (run.lanes ?? []).includes('readyset')
  return {
    ts: run.ts,
    source: 'history',
    outcome: run.outcome === 'failed' ? 'failed' : 'done',
    durationSeconds: run.duration_seconds ?? null,
    connections: run.concurrency ?? null,
    database,
    readyset,
    speedup: speedupOf(database?.p50, readyset?.p50),
    readysetNote:
      withReadyset && !readyset
        ? (run.readyset_setup?.detail ?? "Readyset couldn't measure this run.")
        : null,
    withReadyset,
    paced: run.mode === 'interval' && (run.interval_ms ?? 0) > 0,
  }
}

/** A Compare against Readyset from before Benchmark took it over. */
export function summaryFromCompare(last: LastCompare): BenchmarkSummary {
  const database: LaneNumbers | null =
    last.origin_ms != null
      ? {
          p50: last.origin_ms,
          p95: null,
          qps: null,
          executions: 0,
          failures: 0,
        }
      : null
  const readyset: LaneNumbers | null =
    last.readyset_ms != null
      ? {
          p50: last.readyset_ms,
          p95: null,
          qps: null,
          executions: 0,
          failures: 0,
        }
      : null
  return {
    ts: last.ts,
    source: 'compare',
    outcome: 'done',
    durationSeconds: null,
    connections: null,
    database,
    readyset,
    speedup: last.speedup ?? speedupOf(last.origin_ms, last.readyset_ms),
    readysetNote: readyset ? null : last.verdict || null,
    withReadyset: true,
    paced: false,
  }
}

/**
 * The latest benchmark and the ones before it. A run in this browser leads while
 * it's live; once it finishes it's also saved, and the saved copy (same numbers)
 * stands in for it. An older Compare against Readyset stays in the list.
 */
export function benchmarkTimeline({
  run,
  history,
  lastCompare,
  nowSeconds = Date.now() / 1000,
}: {
  run?: BackgroundRunState
  history: readonly QueryBenchmarkRun[]
  lastCompare?: LastCompare | null
  nowSeconds?: number
}): { latest: BenchmarkSummary | null; past: BenchmarkSummary[] } {
  const saved = history.map(summaryFromHistory)
  const own = run ? summaryFromRun(run, nowSeconds) : null
  const ownLane = own?.database ?? own?.readyset
  const recorded =
    ownLane &&
    !isLiveRun(run as BackgroundRunState) &&
    saved.some(
      (item) =>
        Boolean(item.database) === Boolean(own?.database) &&
        (item.database ?? item.readyset)?.executions === ownLane.executions &&
        (item.database ?? item.readyset)?.p50 === ownLane.p50
    )
  const all = [...saved]
  if (lastCompare) all.push(summaryFromCompare(lastCompare))
  all.sort((a, b) => b.ts - a.ts)
  if (own && !recorded) all.unshift(own)
  return { latest: all[0] ?? null, past: all.slice(1) }
}

/** '30 s', '2 min', one connection or two: how a past run was set up. */
export function runShape(summary: BenchmarkSummary): string | null {
  if (summary.source === 'compare') return 'Compared against Readyset'
  if (summary.durationSeconds == null) return null
  const length =
    summary.durationSeconds >= 60 && summary.durationSeconds % 60 === 0
      ? `${summary.durationSeconds / 60} min`
      : `${summary.durationSeconds} s`
  const connections = summary.connections ?? 1
  return `${length}, ${connections} ${connections === 1 ? 'connection' : 'connections'}`
}
