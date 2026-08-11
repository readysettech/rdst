import type { CacheCompareRunResult, CacheRunResult } from '../../types/cache'

export interface ComparisonLaneMetrics {
  completed: number
  errors: number
  throughput: number | null
  mean: number
  p50: number
  p95: number
  p99: number
}

export interface ComparisonMetrics {
  mode: 'quick' | 'sustained'
  upstream: ComparisonLaneMetrics
  readyset: ComparisonLaneMetrics
  speedup: number
  improvementPercent: number
  winner: 'readyset' | 'upstream'
}

export function summarizeQuickComparison(
  result: CacheRunResult
): ComparisonMetrics {
  const upstreamCompleted = result.origin_iterations ?? result.iterations
  const readysetCompleted = result.cache_iterations ?? result.iterations
  return {
    mode: 'quick',
    upstream: {
      completed: upstreamCompleted,
      errors: 0,
      throughput: null,
      mean: result.origin_stats.mean,
      p50: result.origin_stats.p50,
      p95: result.origin_stats.p95,
      p99: result.origin_stats.p99,
    },
    readyset: {
      completed: readysetCompleted,
      errors: 0,
      throughput: null,
      mean: result.cache_stats.mean,
      p50: result.cache_stats.p50,
      p95: result.cache_stats.p95,
      p99: result.cache_stats.p99,
    },
    speedup: result.speedup_mean,
    improvementPercent: result.improvement_pct,
    winner: result.winner === 'readyset' ? 'readyset' : 'upstream',
  }
}

export function summarizeSustainedComparison(
  results: CacheCompareRunResult[]
): ComparisonMetrics {
  const summarizeLane = (lane: 'origin' | 'readyset') => {
    const completed = results.reduce(
      (sum, result) => sum + result[lane].completed,
      0
    )
    const weighted = (key: 'mean_ms' | 'p50_ms' | 'p95_ms' | 'p99_ms') =>
      completed > 0
        ? results.reduce(
            (sum, result) => sum + result[lane][key] * result[lane].completed,
            0
          ) / completed
        : 0
    return {
      completed,
      errors: results.reduce((sum, result) => sum + result[lane].errors, 0),
      throughput: results.reduce(
        (sum, result) => sum + result[lane].throughput_rps,
        0
      ),
      mean: weighted('mean_ms'),
      p50: weighted('p50_ms'),
      p95: weighted('p95_ms'),
      p99: weighted('p99_ms'),
    }
  }

  const upstream = summarizeLane('origin')
  const readyset = summarizeLane('readyset')
  const speedup = readyset.mean > 0 ? upstream.mean / readyset.mean : 0
  return {
    mode: 'sustained',
    upstream,
    readyset,
    speedup,
    improvementPercent: (speedup - 1) * 100,
    winner: speedup >= 1 ? 'readyset' : 'upstream',
  }
}
