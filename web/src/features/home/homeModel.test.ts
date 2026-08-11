import { describe, expect, it } from 'vitest'
import type { QueryRegistryEntry } from '../../lib/api'
import { continueItems, deriveHomeState, portfolioCounts } from './homeModel'

function entry(over: Partial<QueryRegistryEntry>): QueryRegistryEntry {
  return {
    sql: 'SELECT 1',
    hash: 'h',
    tag: '',
    last_analyzed: '',
    target: 'imdb',
    frequency: 0,
    source: 'manual',
    most_recent_params: {},
    first_analyzed: null,
    max_duration_ms: 0,
    avg_duration_ms: 0,
    observation_count: 0,
    readyset_supported: '',
    ...over,
  } as QueryRegistryEntry
}

describe('deriveHomeState', () => {
  it('keeps the targetless, connected, and active boundaries explicit', () => {
    expect(deriveHomeState(0, true)).toBe('first-run')
    expect(deriveHomeState(2, false)).toBe('connected')
    expect(deriveHomeState(1, true)).toBe('active')
  })
})

describe('portfolioCounts', () => {
  const entries = [
    entry({ hash: 'a', source: 'ask' }),
    entry({ hash: 'b', source: 'ask', last_analyzed: '2026-07-01' }),
    entry({
      hash: 'c',
      last_analyzed: '2026-07-02',
      readyset_supported: 'yes',
    }),
    entry({
      hash: 'd',
      last_analyzed: '2026-07-03',
      readyset_supported: 'yes',
    }),
    entry({ hash: 'e', readyset_supported: 'unsupported: joins' }),
    entry({ hash: 'f', target: 'other', source: 'ask' }),
  ]

  it('counts completed comparisons separately from Readyset candidates', () => {
    const counts = portfolioCounts(entries, 'imdb', new Set(['c']))
    expect(counts).toEqual({
      asked: 2,
      analyzed: 3,
      compared: 1,
      candidates: 1,
    })
  })

  it('scopes the portfolio to the selected target', () => {
    expect(portfolioCounts(entries, 'other').asked).toBe(1)
  })
})

describe('continueItems', () => {
  it('orders by recency and maps each state to an honest next action', () => {
    const items = continueItems(
      [
        entry({ hash: 'old', source: 'ask', first_analyzed: '2026-01-01' }),
        entry({ hash: 'an', last_analyzed: '2026-07-02', tag: 'slow_join' }),
        entry({ hash: 'co', last_analyzed: '2026-07-03' }),
      ],
      'imdb',
      new Set(['co'])
    )
    expect(items.map((item) => item.hash)).toEqual(['co', 'an', 'old'])
    expect(items[0].nextAction).toBe('Compare')
    expect(items[1].nextAction).toBe('Review')
    expect(items[1].label).toBe('slow_join')
    expect(items[2].nextAction).toBe('Analyze')
  })
})
