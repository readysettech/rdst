import { describe, expect, it } from 'vitest';

import { continueItems, deriveHomeState, portfolioCounts } from './homeState';
import type { CacheAwareEntry } from './homeState';

function entry(over: Partial<CacheAwareEntry>): CacheAwareEntry {
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
    readyset_query_id: '',
    readyset_supported: '',
    ...over,
  } as CacheAwareEntry;
}

describe('deriveHomeState', () => {
  it('is first-run with zero targets regardless of schema', () => {
    expect(deriveHomeState(0, true)).toBe('first-run');
    expect(deriveHomeState(0, undefined)).toBe('first-run');
  });
  it('is connected until the semantic layer exists', () => {
    expect(deriveHomeState(2, false)).toBe('connected');
    expect(deriveHomeState(2, undefined)).toBe('connected');
  });
  it('is active once the semantic layer exists', () => {
    expect(deriveHomeState(1, true)).toBe('active');
  });
});

describe('portfolioCounts', () => {
  const entries = [
    entry({ hash: 'a', source: 'ask' }),
    entry({ hash: 'b', source: 'ask', last_analyzed: '2026-07-01' }),
    entry({
      hash: 'c',
      last_analyzed: '2026-07-02',
      readyset_supported: 'yes',
      readyset_query_id: 'q_1',
    }),
    entry({ hash: 'd', last_analyzed: '2026-07-03', readyset_supported: 'yes' }),
    entry({ hash: 'e', readyset_supported: 'unsupported: joins' }),
    entry({ hash: 'f', target: 'other', source: 'ask' }),
  ];

  it('counts per target with candidates excluding already-cached', () => {
    const c = portfolioCounts(entries, 'imdb');
    expect(c.asked).toBe(2);
    expect(c.analyzed).toBe(3);
    expect(c.cached).toBe(1);
    expect(c.candidates).toBe(1);
    expect(c.benchmarked).toBeNull();
  });

  it('scopes to the given target', () => {
    expect(portfolioCounts(entries, 'other').asked).toBe(1);
  });
});

describe('continueItems', () => {
  it('orders by recency and maps kind to the next action', () => {
    const items = continueItems(
      [
        entry({ hash: 'old', source: 'ask', first_analyzed: '2026-01-01' }),
        entry({ hash: 'an', last_analyzed: '2026-07-02', tag: 'slow_join' }),
        entry({
          hash: 'ca',
          last_analyzed: '2026-07-03',
          readyset_query_id: 'q_9',
        }),
      ],
      'imdb',
    );
    expect(items.map((i) => i.hash)).toEqual(['ca', 'an', 'old']);
    expect(items[0].nextAction).toBe('Benchmark');
    expect(items[1].nextAction).toBe('Cache');
    expect(items[1].label).toBe('slow_join');
    expect(items[2].nextAction).toBe('Analyze');
  });
});
