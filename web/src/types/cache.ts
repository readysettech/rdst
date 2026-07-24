// Temporary Readyset speed-test types
// ---------------------------------------------------------------------------

import type { components } from '../lib/api.generated'

export type CacheTestRunRequest = components['schemas']['CacheTestRunRequest']
export type CacheTestRunStartResponse =
  components['schemas']['CacheTestRunStartResponse']

// Background-run events carry this shape.
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
  speedup_mean: number
  speedup_median: number
  improvement_pct: number
  winner: 'readyset' | 'origin'
}
