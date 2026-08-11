import { describe, expect, it } from 'vitest'
import {
  DEFAULT_COMPARE_DURATION,
  initialCompareConcurrency,
  MAX_COMPARE_CONCURRENCY,
} from './useCompareController'

describe('safe Compare load profile', () => {
  it.each([
    [0, 2],
    [1, 2],
    [2, 2],
    [3, 3],
    [4, 4],
    [8, 4],
  ])('allocates at least one worker across %i queries', (queries, clients) => {
    expect(initialCompareConcurrency(queries)).toBe(clients)
  })

  it('keeps the automatic run inside the measured device-safe envelope', () => {
    expect(MAX_COMPARE_CONCURRENCY).toBe(4)
    expect(DEFAULT_COMPARE_DURATION).toBe(30)
  })
})
