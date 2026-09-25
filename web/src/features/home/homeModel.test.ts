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

function history(firstTs: number | null, span = 0) {
  return {
    first_ts: firstTs,
    last_ts: firstTs == null ? null : firstTs + span,
    span_seconds: span,
    resolutions: {
      '60': { first_ts: firstTs },
      '300': { first_ts: null },
      '3600': { first_ts: null },
      '86400': { first_ts: null },
    },
  }
}

describe('deriveHomeState', () => {
  it('follows the setup: connect, turn on monitoring, collect, then monitor', () => {
    expect(deriveHomeState(0, true, undefined)).toBe('first-run')
    expect(deriveHomeState(2, false, history(null))).toBe('connected')
    expect(deriveHomeState(1, true, history(null))).toBe('collecting')
    expect(deriveHomeState(1, true, history(1000, 120))).toBe('collecting')
    expect(deriveHomeState(1, true, history(1000, 3600))).toBe('active')
  })

  it('keeps paused monitoring with history on the active home', () => {
    expect(deriveHomeState(1, false, history(1000, 86_400))).toBe('active')
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
