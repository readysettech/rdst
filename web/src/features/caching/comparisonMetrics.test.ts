import { describe, expect, it } from 'vitest'
import type { CacheCompareRunResult, CacheRunResult } from '../../types/cache'
import {
  summarizeQuickComparison,
  summarizeSustainedComparison,
} from './comparisonMetrics'

const stats = {
  mean: 20,
  median: 18,
  min: 10,
  max: 30,
  p50: 18,
  p95: 28,
  p99: 30,
}

describe('comparison metrics', () => {
  it('normalizes a quick comparison into upstream and Readyset lanes', () => {
    const result: CacheRunResult = {
      success: true,
      query: 'SELECT 1',
      iterations: 10,
      origin_stats: stats,
      cache_stats: { ...stats, mean: 2 },
      speedup_mean: 10,
      speedup_median: 9,
      improvement_pct: 90,
      winner: 'readyset',
    }

    expect(summarizeQuickComparison(result)).toMatchObject({
      mode: 'quick',
      upstream: { completed: 10, mean: 20 },
      readyset: { completed: 10, mean: 2 },
      speedup: 10,
      winner: 'readyset',
    })
  })

  it('weights sustained latency by completed work across queries', () => {
    const lane = {
      scheduled: 100,
      completed: 100,
      errors: 0,
      dropped: 0,
      throughput_rps: 10,
      error_rate: 0,
      mean_ms: 20,
      p50_ms: 18,
      p95_ms: 28,
      p99_ms: 30,
    }
    const result = (
      completed: number,
      mean: number
    ): CacheCompareRunResult => ({
      success: true,
      query: 'SELECT 1',
      duration_seconds: 30,
      elapsed_seconds: 30,
      concurrency: 4,
      origin: { ...lane, completed, mean_ms: mean },
      readyset: { ...lane, completed, mean_ms: mean / 10 },
      timeline: [],
      phases: [],
      speedup_mean: 10,
      improvement_pct: 90,
      winner: 'readyset',
    })

    const summary = summarizeSustainedComparison([
      result(100, 20),
      result(300, 40),
    ])

    expect(summary.upstream.mean).toBe(35)
    expect(summary.readyset.mean).toBe(3.5)
    expect(summary.speedup).toBe(10)
  })
})
