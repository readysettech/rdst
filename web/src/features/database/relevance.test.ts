import { describe, expect, it } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import type { ChartMarker, InboxItem, MonitorQuery } from '@/lib/monitor/types'
import {
  inboxItemRelevant,
  isStopped,
  relevantMarkers,
  relevantQueryKeys,
} from './relevance'

const base = queriesFixture('localdev', '24h', FIXTURE_NOW).queries[0]

function q(key: string, db_time_ms: number, share: number): MonitorQuery {
  return { ...base, key, db_time_ms, db_time_share: share, state: [] }
}

function item(overrides: Partial<InboxItem>): InboxItem {
  return {
    id: 1,
    target: 'localdev',
    type: 'latency_regression',
    subject: 'a',
    state: 'new',
    severity: 0.8,
    title: 'Query latency increased',
    body_json: {},
    created_at: 0,
    updated_at: 0,
    resolved_at: null,
    resolved_by: null,
    seen_count: 0,
    reopened_count: 0,
    severity_band: 'act_now',
    tone: 'negative',
    summary: '',
    query_keys: ['a'],
    subject_label: '',
    group_key: '',
    muted_by: null,
    trend: [],
    ...overrides,
  }
}

// 12 queries: a is 40 %, b..l share the rest; k and l fall outside the top 10.
const queries = [
  q('a', 4000, 0.4),
  ...'bcdefghijkl'
    .split('')
    .map((key, index) => q(key, 600 - index * 10, 0.005)),
]

describe('relevantQueryKeys', () => {
  it('keeps the top 10 by database time and anything at 2 % or more', () => {
    const keys = relevantQueryKeys(queries)
    expect(keys.has('a')).toBe(true)
    expect(keys.has('j')).toBe(true)
    expect(keys.has('k')).toBe(false)
    expect(relevantQueryKeys([...queries, q('m', 1, 0.03)]).has('m')).toBe(true)
  })
})

describe('inboxItemRelevant', () => {
  const relevant = relevantQueryKeys(queries)
  const shares = new Map(queries.map((row) => [row.key, row.db_time_share]))

  it('drops digest types, FYI items and RDST sign-in issues', () => {
    expect(
      inboxItemRelevant(
        item({ type: 'daily_summary', query_keys: [] }),
        relevant,
        shares
      )
    ).toBe(false)
    expect(
      inboxItemRelevant(item({ severity_band: 'fyi' }), relevant, shares)
    ).toBe(false)
    expect(
      inboxItemRelevant(
        item({
          type: 'monitoring_issue',
          subject: 'jev_login',
          query_keys: [],
        }),
        relevant,
        shares
      )
    ).toBe(false)
    expect(
      inboxItemRelevant(
        item({
          type: 'monitoring_issue',
          subject: 'lost_connection',
          query_keys: [],
        }),
        relevant,
        shares
      )
    ).toBe(true)
  })

  it('keeps query items only for relevant queries', () => {
    expect(
      inboxItemRelevant(item({ query_keys: ['a'] }), relevant, shares)
    ).toBe(true)
    expect(
      inboxItemRelevant(item({ query_keys: ['l'] }), relevant, shares)
    ).toBe(false)
  })

  it('keeps a stopped query only when it carried real load', () => {
    const stopped = {
      type: 'query_stopped_running',
      severity_band: 'watch' as const,
    }
    expect(
      inboxItemRelevant(
        item({ ...stopped, query_keys: ['b'] }),
        relevant,
        shares
      )
    ).toBe(false)
    expect(
      inboxItemRelevant(
        item({ ...stopped, query_keys: ['a'] }),
        relevant,
        shares
      )
    ).toBe(true)
  })
})

describe('relevantMarkers', () => {
  const markers: ChartMarker[] = [
    { ts: 1, kind: 'index_added', label: 'idx added' },
    { ts: 2, kind: 'inbox', label: 'a', inbox_id: 1, severity: 0.8 },
    { ts: 3, kind: 'inbox', label: 'l', inbox_id: 2, severity: 0.9 },
    { ts: 4, kind: 'inbox', label: 'mild', inbox_id: 3, severity: 0.5 },
  ]

  it('filters inbox markers by their items and keeps everything else', () => {
    const items = [
      item({ id: 1, query_keys: ['a'] }),
      item({ id: 2, query_keys: ['l'] }),
      item({ id: 3, query_keys: ['a'] }),
    ]
    expect(
      relevantMarkers(markers, items, queries).map((m) => m.label)
    ).toEqual(['idx added', 'a', 'mild'])
  })

  it('shows only act-now markers until the items load', () => {
    expect(
      relevantMarkers(markers, undefined, queries).map((m) => m.label)
    ).toEqual(['idx added', 'a', 'l'])
  })
})

describe('isStopped', () => {
  it('needs calls earlier in the range and none in the last hour', () => {
    const series = (calls: number[]) =>
      calls.map((qps, index) => ({ ts: 1000 + index * 1800, qps, mean_ms: 1 }))
    const row = (calls: number[]) => ({
      ...base,
      state: [],
      series: series(calls),
    })
    const end = 1000 + 5 * 1800
    expect(isStopped(row([1, 1, 1, 0, 0, 0]), end)).toBe(true)
    expect(isStopped(row([1, 1, 1, 1, 0, 1]), end)).toBe(false)
    expect(isStopped(row([0, 0, 0, 0, 0, 0]), end)).toBe(false)
    expect(
      isStopped({ ...row([1, 0, 0, 0, 0, 0]), state: ['stuck'] }, end)
    ).toBe(false)
  })
})
