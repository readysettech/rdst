import { describe, expect, it } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import {
  concreteSqlForTest,
  deriveQueryName,
  getSourceMeta,
  selectSavedQueries,
} from './savedQuerySelectors'

function query(
  overrides: Partial<QueryRegistryEntry> & Pick<QueryRegistryEntry, 'hash'>
): QueryRegistryEntry {
  return {
    sql: 'SELECT * FROM users',
    tag: '',
    source: 'manual',
    target: 'prod',
    frequency: 0,
    last_analyzed: '2026-07-01T00:00:00Z',
    ...overrides,
    hash: overrides.hash,
  }
}

describe('saved query selectors', () => {
  it('collapses backend source aliases into stable UI labels', () => {
    expect(getSourceMeta('top-historical')).toEqual({
      label: 'Slow Queries',
      variant: 'informative',
    })
    expect(getSourceMeta('prompt').label).toBe('Ask')
    expect(getSourceMeta('unknown')).toEqual({
      label: 'Manual',
      variant: 'neutral',
    })
  })

  it('derives readable fallback names without changing SQL', () => {
    expect(
      deriveQueryName('SELECT COUNT(*) FROM public.orders WHERE id > 10')
    ).toBe('COUNT on orders')
    expect(deriveQueryName('UPDATE users SET active = true')).toBe(
      'Update · users'
    )
    expect(deriveQueryName('')).toBe('Untitled query')
  })

  it('combines text and source filtering while preserving source counts', () => {
    const selection = selectSavedQueries({
      queries: [
        query({
          hash: 'slow-orders',
          source: 'top',
          tag: 'Orders',
          sql: 'SELECT * FROM orders',
        }),
        query({
          hash: 'ask-orders',
          source: 'ask',
          tag: 'Asked orders',
          sql: 'SELECT id FROM orders',
        }),
        query({
          hash: 'manual-users',
          source: 'manual',
          tag: 'Users',
        }),
      ],
      searchTerm: 'orders',
      sourceFilter: 'Ask',
      isCached: () => false,
    })

    expect(selection.sourceOptions).toEqual([
      { label: 'Slow Queries', count: 1 },
      { label: 'Ask', count: 1 },
      { label: 'Manual', count: 0 },
    ])
    expect(selection.filteredQueries.map((entry) => entry.hash)).toEqual([
      'ask-orders',
    ])
    expect(selection.isFiltered).toBe(true)
  })

  it('promotes one measurable uncached query and removes it from the list', () => {
    const selection = selectSavedQueries({
      queries: [
        query({
          hash: 'largest',
          source: 'top',
          observation_count: 20,
          avg_duration_ms: 100,
        }),
        query({
          hash: 'smaller',
          source: 'top',
          observation_count: 10,
          avg_duration_ms: 50,
        }),
      ],
      searchTerm: '',
      sourceFilter: 'all',
      isCached: () => false,
    })

    expect(selection.hero?.hash).toBe('largest')
    expect(selection.heroMoreCount).toBe(1)
    expect(selection.displayedQueries.map((entry) => entry.hash)).toEqual([
      'smaller',
    ])
    expect(selection.hasMeasuredImpact).toBe(true)
  })

  it('keeps automatic observations out of the legacy Saved adapter', () => {
    const selection = selectSavedQueries({
      queries: [
        query({
          hash: 'observed-only',
          first_observed_at: '2026-08-10T10:00:00Z',
          saved_at: '',
        }),
        query({
          hash: 'explicitly-saved',
          first_observed_at: '2026-08-10T10:00:00Z',
          saved_at: '2026-08-10T10:01:00Z',
        }),
      ],
      searchTerm: '',
      sourceFilter: 'all',
      isCached: () => false,
    })

    expect(selection.filteredQueries.map((entry) => entry.hash)).toEqual([
      'explicitly-saved',
    ])
  })
})

describe('concreteSqlForTest', () => {
  it('fills captured parameters and rejects unresolved ones', () => {
    const sql = 'SELECT * FROM users WHERE id = :p1 AND score > :p2'

    expect(concreteSqlForTest(sql, { p1: 7, p2: 10 })).toBe(
      'SELECT * FROM users WHERE id = 7 AND score > 10'
    )
    expect(concreteSqlForTest(sql, { p1: 7 })).toBeNull()
  })
})
