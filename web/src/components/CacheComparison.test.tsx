import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { CacheRunResult } from '../types/cache'
import { ComparisonCard } from './CacheComparison'

const stats = {
  mean: 2,
  median: 2,
  min: 1,
  max: 3,
  p50: 2,
  p95: 3,
  p99: 3,
}

function result(overrides: Partial<CacheRunResult> = {}): CacheRunResult {
  return {
    success: true,
    query: 'SELECT 1',
    iterations: 10,
    origin_stats: stats,
    cache_stats: { ...stats, mean: 1 },
    speedup_mean: 2,
    speedup_median: 2,
    improvement_pct: 50,
    winner: 'readyset',
    ...overrides,
  }
}

afterEach(cleanup)

describe('ComparisonCard sample counts', () => {
  it('shows one count when both sides measured equally', () => {
    render(<ComparisonCard result={result()} />)

    expect(screen.getByText(/10 samples each/)).toBeTruthy()
  })

  it('shows each side when a duration limit produced unequal samples', () => {
    render(
      <ComparisonCard
        result={result({ origin_iterations: 12, cache_iterations: 10 })}
      />
    )

    expect(screen.getByText(/12 origin \/ 10 Readyset samples/)).toBeTruthy()
  })
})
