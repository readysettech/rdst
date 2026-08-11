import { describe, expect, it } from 'vitest'
import {
  getSlowQueryFilterError,
  slowQueryLastFiltersQueryKey,
} from './slowQueryFilters'

describe('slow query filters', () => {
  it('scopes persisted filters to the selected target', () => {
    expect(slowQueryLastFiltersQueryKey('production')).toEqual([
      'top',
      'lastFilters',
      'production',
    ])
  })

  it('validates historical regex filters', () => {
    expect(getSlowQueryFilterError('SELECT.*users', 'historical')).toBeNull()
    expect(getSlowQueryFilterError('(', 'historical')).toMatch(
      /unterminated|missing/i
    )
  })

  it('ignores a stale historical regex in realtime mode', () => {
    expect(getSlowQueryFilterError('(', 'realtime')).toBeNull()
  })
})
