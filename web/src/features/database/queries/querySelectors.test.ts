import { describe, expect, it } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import type { MonitorQuery } from '@/lib/monitor/types'
import { relevantQueryKeys } from '../relevance'
import {
  countQueries,
  isTextlessTail,
  matchesFilter,
  orderedRows,
  parseFilter,
  parseQueryKeys,
  parseSort,
  parseTab,
  QUERY_FILTERS,
  queryName,
  type RowFacts,
  recoveryCopy,
  selectQueryList,
  significantLatencyChange,
  sortQueries,
  tableCounts,
} from './querySelectors'
import { type QueryStatusId, STATUS_LABELS } from './queryStatus'

const page = queriesFixture('localdev', '24h', FIXTURE_NOW)
const all = page.queries

function facts(id: QueryStatusId = 'healthy'): RowFacts {
  return {
    status: {
      id,
      label: STATUS_LABELS[id],
      tone: 'neutral',
      reason: null,
      itemId: null,
    },
    classification: null,
  }
}
const healthy = () => facts()

it('uses the backend friendly name without rebuilding it from SQL', () => {
  expect(
    queryName({ ...all[0], display_name: 'SELECT on orders (132 columns)' })
  ).toBe('SELECT on orders (132 columns)')
  expect(queryName({ ...all[0], display_name: 'SELECT' })).toBe('SELECT')
})

function query(overrides: Partial<MonitorQuery>): MonitorQuery {
  return {
    ...all[0],
    change: { latency: null, qps: null, significant: false, verdict: null },
    state: [],
    fix: null,
    ...overrides,
  }
}

const view0 = selectQueryList(all, {
  search: '',
  filter: 'all',
  sort: 'impact',
  factsOf: healthy,
})

describe('selectQueryList', () => {
  it('renders every query as a card or in a collapsed group, never dropped', () => {
    const view = selectQueryList(all, {
      search: '',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    expect(view.rows.length + view.tail.length + view.quiet.length).toBe(
      all.length
    )
    for (const row of view.tail) expect(isTextlessTail(row)).toBe(true)
  })

  it('collapses queries outside the relevance gate and keeps stuck ones', () => {
    const view = selectQueryList(all, {
      search: '',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    const relevant = relevantQueryKeys(all)
    expect(view.quiet.length).toBeGreaterThan(0)
    for (const row of view.rows) {
      const stuck = row.state.includes('stuck')
      expect(stuck || relevant.has(row.key)).toBe(true)
    }
    for (const row of view.quiet) expect(row.state).not.toContain('stuck')
    expect(view.quietShare).toBeCloseTo(
      view.quiet.reduce((sum, row) => sum + row.db_time_share, 0)
    )
  })

  it('collapses a small query that stopped running', () => {
    const end = FIXTURE_NOW
    const series = (lastCall: number) =>
      [5, 4, 3, 2, 1, 0].map((step) => ({
        ts: end - step * 1800,
        qps: end - step * 1800 <= lastCall ? 2 : 0,
        mean_ms: 1,
      }))
    const stopped = query({
      key: 'stopped',
      db_time_ms: 50,
      db_time_share: 0.01,
      series: series(end - 3 * 1800),
    })
    const running = query({
      key: 'running',
      db_time_ms: 40,
      db_time_share: 0.008,
      series: series(end),
    })
    const big = query({
      key: 'big',
      db_time_ms: 5000,
      db_time_share: 0.982,
      series: series(end),
    })
    const view = selectQueryList([big, stopped, running], {
      search: '',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    expect(view.rows.map((row) => row.key)).toEqual(['big', 'running'])
    expect(view.quiet.map((row) => row.key)).toEqual(['stopped'])
    expect(view.quietStopped).toBe(1)
  })

  it('shows quiet rows as cards when a filter matches nothing else', () => {
    const end = FIXTURE_NOW
    const series = (lastCall: number) =>
      [3, 2, 1, 0].map((step) => ({
        ts: end - step * 1800,
        qps: end - step * 1800 <= lastCall ? 2 : 0,
        mean_ms: 1,
      }))
    const big = query({
      key: 'big',
      db_time_ms: 5000,
      db_time_share: 0.99,
      series: series(end),
    })
    const gone = query({
      key: 'gone',
      state: ['new'],
      db_time_ms: 50,
      db_time_share: 0.01,
      series: series(end - 3 * 1800),
    })
    const all = selectQueryList([big, gone], {
      search: '',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    expect(all.quiet.map((row) => row.key)).toEqual(['gone'])
    const fresh = selectQueryList([big, gone], {
      search: '',
      filter: 'watch',
      sort: 'impact',
      factsOf: (row) => facts(row.key === 'gone' ? 'watch' : 'healthy'),
    })
    expect(fresh.rows.map((row) => row.key)).toEqual(['gone'])
    expect(fresh.quiet).toHaveLength(0)
  })

  it('shows search matches as cards, including text-less ones', () => {
    const textless = all.find((row) => row.text_status === 'unavailable')
    expect(textless).toBeDefined()
    const view = selectQueryList(all, {
      search: textless!.key.slice(0, 8),
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    expect(view.rows.map((row) => row.key)).toContain(textless!.key)
    expect(view.tail).toHaveLength(0)
  })

  it('matches SQL, tables and display names case-insensitively', () => {
    const byTable = selectQueryList(all, {
      search: 'ORDER_ITEMS',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    expect(byTable.rows.length).toBeGreaterThan(0)
    for (const row of byTable.rows)
      expect(
        `${row.text} ${row.tables.join(' ')} ${row.display_name}`.toLowerCase()
      ).toContain('order_items')
  })

  it('counts facets over the searched set', () => {
    const view = selectQueryList(all, {
      search: 'orders',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    expect(view.counts.all).toBe(view.searched.length)
    expect(view.counts).toEqual(countQueries(view.searched, healthy))
  })

  it('filters needs attention to queries with an open Inbox item, Watch included', () => {
    const levels: Record<string, QueryStatusId> = {
      [all[0].key]: 'attention',
      [all[1].key]: 'watch',
      [all[2].key]: 'fix_ready',
    }
    const view = selectQueryList(all, {
      search: '',
      filter: 'attention',
      sort: 'impact',
      factsOf: (row) => facts(levels[row.key] ?? 'healthy'),
    })
    expect([...view.rows, ...view.quiet].map((row) => row.key).sort()).toEqual(
      [all[0].key, all[1].key].sort()
    )
    expect(view.counts.attention).toBe(2)
    expect(view.counts.watch).toBe(1)
    expect(view.counts.fix_ready).toBe(1)
  })

  it('never collapses a query with an open Inbox item', () => {
    const view = selectQueryList(all, {
      search: '',
      filter: 'all',
      sort: 'impact',
      factsOf: (row) => facts(row === view0.quiet[0] ? 'watch' : 'healthy'),
    })
    expect(view.quiet).not.toContain(view0.quiet[0])
  })

  it('filters by classification finding', () => {
    const flagged = all[0]
    const view = selectQueryList(all, {
      search: '',
      filter: 'index_coverage',
      sort: 'impact',
      factsOf: (row) => ({
        ...facts(),
        classification:
          row === flagged
            ? {
                status: 'complete',
                findings: [{ id: 'index_coverage', label: 'Missing index' }],
              }
            : { status: 'complete', findings: [] },
      }),
    })
    expect([...view.rows, ...view.quiet]).toEqual([flagged])
    expect(view.counts.none).toBe(all.length - 1)
  })
})

describe('sortQueries', () => {
  it('orders by database time, frequency and latency', () => {
    const impact = sortQueries(all, 'impact')
    expect(impact[0].db_time_ms).toBeGreaterThanOrEqual(impact[1].db_time_ms)
    const qps = sortQueries(all, 'qps')
    expect(qps[0].qps).toBe(Math.max(...all.map((row) => row.qps)))
    const slow = sortQueries(all, 'latency')
    expect(slow[0].mean_ms).toBe(Math.max(...all.map((row) => row.mean_ms)))
  })
})

describe('significantLatencyChange', () => {
  it('hides small or low-impact changes', () => {
    expect(
      significantLatencyChange(
        query({
          change: {
            latency: 0.05,
            qps: 0,
            significant: false,
            verdict: 'steady',
          },
          db_time_share: 0.3,
        })
      )
    ).toBeNull()
    expect(
      significantLatencyChange(
        query({
          change: { latency: 0.9, qps: 0, significant: false, verdict: null },
          db_time_share: 0.001,
        })
      )
    ).toBeNull()
  })

  it('keeps large changes on queries that matter, and backend-flagged ones', () => {
    expect(
      significantLatencyChange(
        query({
          change: { latency: 0.4, qps: 0, significant: false, verdict: null },
          db_time_share: 0.05,
        })
      )
    ).toBe(0.4)
    expect(
      significantLatencyChange(
        query({
          change: {
            latency: 0.1,
            qps: 0,
            significant: true,
            verdict: 'regressed',
          },
          db_time_share: 0,
        })
      )
    ).toBe(0.1)
  })
})

describe('status filter', () => {
  it('matches statuses and never shows index attribution states', () => {
    expect(matchesFilter(facts('watch'), 'attention')).toBe(true)
    expect(matchesFilter(facts('attention'), 'watch')).toBe(false)
    expect(matchesFilter(facts('healthy'), 'attention')).toBe(false)
    expect(QUERY_FILTERS).not.toContain('fixed')
    expect(parseFilter('fixed')).toBe('all')
  })

  it('reads old links: retired filters and the Jev filter', () => {
    expect(parseFilter('analyzed')).toBe('analyzed')
    expect(parseFilter('new')).toBe('all')
    expect(parseFilter(undefined, 'index_coverage')).toBe('index_coverage')
    expect(parseFilter(undefined, 'high')).toBe('all')
    expect(parseFilter('fix_ready', 'broad_work')).toBe('fix_ready')
    expect(parseSort('jev')).toBe('impact')
  })
})

describe('recoveryCopy', () => {
  it('names the filter to remove and what it would show', () => {
    expect(
      recoveryCopy({
        filter: 'attention',
        search: '',
        total: 37,
        searchedCount: 37,
      })
    ).toEqual({
      title: 'No queries need attention',
      body: "Remove 'Needs attention' to show 37 queries.",
      clear: 'filter',
    })
  })

  it('offers clearing the search when nothing matches it', () => {
    expect(
      recoveryCopy({ filter: 'all', search: 'zzz', total: 5, searchedCount: 0 })
        .body
    ).toBe('Clear the search to show 5 queries.')
  })
})

describe('URL parsing', () => {
  it('falls back to defaults for unknown values', () => {
    expect(parseFilter('nope')).toBe('all')
    expect(parseSort(undefined)).toBe('impact')
    expect(parseTab('readyset')).toBe('readyset')
    expect(parseTab(3)).toBe('performance')
  })
})

describe('tableCounts', () => {
  it('counts the queries on each table, heaviest table first', () => {
    const row = (key: string, tables: string[], ms: number) =>
      ({ key, tables, db_time_ms: ms }) as MonitorQuery
    expect(
      tableCounts([
        row('a', ['orders', 'customers'], 10),
        row('b', ['orders'], 5),
        row('c', ['customers', 'customers'], 50),
      ])
    ).toEqual([
      { table: 'customers', count: 2 },
      { table: 'orders', count: 2 },
    ])
  })
})

describe('sort by analysis score', () => {
  const scored = (key: string, score: number | null, db_time_ms = 10) =>
    query({
      key,
      db_time_ms,
      last_analysis:
        score == null
          ? null
          : ({
              state: 'done',
              summary: { efficiency_score: score, rating: null },
            } as unknown as MonitorQuery['last_analysis']),
    })
  const rows = [
    scored('none', null, 900),
    scored('good', 92),
    scored('poor', 31),
    scored('fair', 55),
  ]

  it('sorts both ways and puts queries without a score last', () => {
    expect(sortQueries(rows, 'score_low').map((row) => row.key)).toEqual([
      'poor',
      'fair',
      'good',
      'none',
    ])
    expect(sortQueries(rows, 'score_high').map((row) => row.key)).toEqual([
      'good',
      'fair',
      'poor',
      'none',
    ])
    expect(parseSort('score_low')).toBe('score_low')
  })
})

describe("an Inbox item's queries", () => {
  it('reads the keys from the URL', () => {
    expect(parseQueryKeys('a1,b2, a1,')).toEqual(['a1', 'b2'])
    expect(parseQueryKeys('')).toBeNull()
    expect(parseQueryKeys(undefined)).toBeNull()
  })

  it('narrows the list to exactly those queries', () => {
    const view = selectQueryList(all, {
      search: '',
      filter: 'all',
      sort: 'impact',
      factsOf: healthy,
    })
    const keys = [all[0].key, all[2].key]
    expect(
      orderedRows(view, 'impact', null, keys)
        .map((row) => row.key)
        .sort()
    ).toEqual([...keys].sort())
  })
})
