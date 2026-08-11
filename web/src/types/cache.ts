// Temporary Readyset speed-test and live comparison types.
//
// REST-backed requests are generated from OpenAPI. Background-run event
// payloads remain hand-written until the SSE contract is represented there.

import type { components } from '../lib/api.generated'

export type CacheTestRunRequest = components['schemas']['CacheTestRunRequest']
export type CacheTestRunStartResponse =
  components['schemas']['CacheTestRunStartResponse']

export interface CacheRunStats {
  mean: number
  median: number
  min: number
  max: number
  p50: number
  p95: number
  p99: number
  stddev?: number
}

export interface CacheRunResult {
  success: boolean
  query: string
  iterations: number
  origin_iterations?: number | null
  cache_iterations?: number | null
  origin_stats: CacheRunStats
  cache_stats: CacheRunStats
  origin_samples_ms?: number[]
  cache_samples_ms?: number[]
  speedup_mean: number
  speedup_median: number
  improvement_pct: number
  winner: 'readyset' | 'original' | 'origin'
}

export interface CacheCompareLaneSample {
  scheduled: number
  completed: number
  errors: number
  dropped: number
  in_flight?: number
  throughput_rps: number
  error_rate: number
  mean_ms: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
}

export interface CacheCompareSample {
  elapsed_seconds: number
  concurrency: number
  origin: CacheCompareLaneSample
  readyset: CacheCompareLaneSample
}

export interface CacheComparePhase {
  elapsed_seconds: number
  concurrency: number
}

export interface CacheCompareRunResult {
  success: boolean
  query: string
  duration_seconds: number
  elapsed_seconds: number
  concurrency: number
  origin: CacheCompareLaneSample
  readyset: CacheCompareLaneSample
  timeline: CacheCompareSample[]
  phases: CacheComparePhase[]
  speedup_mean: number
  improvement_pct: number
  winner: 'readyset' | 'origin'
}

const CACHE_RUN_STAT_KEYS = [
  'mean',
  'median',
  'min',
  'max',
  'p50',
  'p95',
  'p99',
] as const

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isCacheRunStats(value: unknown): value is CacheRunStats {
  if (!value || typeof value !== 'object') return false
  const stats = value as Record<string, unknown>
  return (
    CACHE_RUN_STAT_KEYS.every((key) => isFiniteNumber(stats[key])) &&
    (stats.stddev === undefined || isFiniteNumber(stats.stddev))
  )
}

function isOptionalSampleArray(value: unknown): value is number[] | undefined {
  return (
    value === undefined ||
    (Array.isArray(value) && value.every((sample) => isFiniteNumber(sample)))
  )
}

function isCacheCompareLaneSample(
  value: unknown
): value is CacheCompareLaneSample {
  if (!value || typeof value !== 'object') return false
  const lane = value as Record<string, unknown>
  return (
    [
      'scheduled',
      'completed',
      'errors',
      'dropped',
      'throughput_rps',
      'error_rate',
      'mean_ms',
      'p50_ms',
      'p95_ms',
      'p99_ms',
    ].every((key) => isFiniteNumber(lane[key])) &&
    (lane.in_flight === undefined || isFiniteNumber(lane.in_flight))
  )
}

export function isCacheCompareSample(
  value: unknown
): value is CacheCompareSample {
  if (!value || typeof value !== 'object') return false
  const sample = value as Record<string, unknown>
  return (
    isFiniteNumber(sample.elapsed_seconds) &&
    isFiniteNumber(sample.concurrency) &&
    isCacheCompareLaneSample(sample.origin) &&
    isCacheCompareLaneSample(sample.readyset)
  )
}

export function isCacheCompareRunResult(
  value: unknown
): value is CacheCompareRunResult {
  if (!value || typeof value !== 'object') return false
  const result = value as Record<string, unknown>
  return (
    result.success === true &&
    typeof result.query === 'string' &&
    isFiniteNumber(result.duration_seconds) &&
    isFiniteNumber(result.elapsed_seconds) &&
    isFiniteNumber(result.concurrency) &&
    isCacheCompareLaneSample(result.origin) &&
    isCacheCompareLaneSample(result.readyset) &&
    Array.isArray(result.timeline) &&
    result.timeline.every(isCacheCompareSample) &&
    Array.isArray(result.phases) &&
    result.phases.every(
      (phase) =>
        !!phase &&
        typeof phase === 'object' &&
        isFiniteNumber((phase as Record<string, unknown>).elapsed_seconds) &&
        isFiniteNumber((phase as Record<string, unknown>).concurrency)
    ) &&
    isFiniteNumber(result.speedup_mean) &&
    isFiniteNumber(result.improvement_pct) &&
    (result.winner === 'readyset' || result.winner === 'origin')
  )
}

/** Validate SSE and persisted results before UI reads nested stats. */
export function isCacheRunResult(value: unknown): value is CacheRunResult {
  if (!value || typeof value !== 'object') return false
  const result = value as Record<string, unknown>
  return (
    typeof result.success === 'boolean' &&
    typeof result.query === 'string' &&
    isFiniteNumber(result.iterations) &&
    (result.origin_iterations === undefined ||
      result.origin_iterations === null ||
      isFiniteNumber(result.origin_iterations)) &&
    (result.cache_iterations === undefined ||
      result.cache_iterations === null ||
      isFiniteNumber(result.cache_iterations)) &&
    isCacheRunStats(result.origin_stats) &&
    isCacheRunStats(result.cache_stats) &&
    isOptionalSampleArray(result.origin_samples_ms) &&
    isOptionalSampleArray(result.cache_samples_ms) &&
    isFiniteNumber(result.speedup_mean) &&
    isFiniteNumber(result.speedup_median) &&
    isFiniteNumber(result.improvement_pct) &&
    (result.winner === 'readyset' ||
      result.winner === 'original' ||
      result.winner === 'origin')
  )
}
