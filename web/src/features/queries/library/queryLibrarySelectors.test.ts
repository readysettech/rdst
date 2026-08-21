import { describe, expect, it } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import {
  matchesActivityWindow,
  normalizeQueryLibraryFacetCounts,
  selectQueryLibrary,
} from './queryLibrarySelectors'

function query(
  hash: string,
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    hash,
    sql: `SELECT * FROM ${hash}`,
    tag: '',
    source: 'top-historical',
    target: 'demo',
    frequency: 0,
    last_analyzed: '',
    ...overrides,
  }
}

const isCached = (hash: string) => hash === 'cached'

describe('selectQueryLibrary', () => {
  const queries = [
    query('new', {
      is_new: true,
      first_observed_at: '2026-08-10T10:00:00Z',
      last_observed_at: '2026-08-10T11:00:00Z',
      frequency: 20,
      avg_duration_ms: 50,
      observation_count: 20,
    }),
    query('saved', {
      source: 'manual',
      sources: ['manual'],
      saved_at: '2026-08-09T10:00:00Z',
      last_analyzed_at: '2026-08-09T11:00:00Z',
    }),
    query('cached', {
      source: 'ask',
      sources: ['ask'],
      last_analyzed_at: '2026-08-08T11:00:00Z',
      readyset_query_id: 'q_cached',
    }),
    query('ready', {
      source: 'file',
      sources: ['file'],
      readyset_supported: 'yes',
      last_analyzed_at: '2026-08-07T11:00:00Z',
    }),
  ]

  it('treats lifecycle facets as independent views', () => {
    const select = (view: Parameters<typeof selectQueryLibrary>[0]['view']) =>
      selectQueryLibrary({
        queries,
        view,
        searchTerm: '',
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
        sort: 'highest-impact',
        isCached,
      }).queries.map((entry) => entry.hash)

    expect(select('new')).toEqual(['new'])
    expect(select('needs-analysis')).toEqual(['new'])
    expect(select('ready-to-cache')).toEqual(['ready'])
    expect(select('cached')).toEqual(['cached'])
  })

  it('does not treat a temporary comparison as persistent cache inventory', () => {
    const comparedOnly = query('cached')
    const result = selectQueryLibrary({
      queries: [comparedOnly],
      view: 'cached',
      searchTerm: '',
      source: 'all',
      params: 'all',
      activity: 'all',
      impact: 'all',
      sort: 'highest-impact',
      isCached,
    })

    expect(result.queries).toHaveLength(0)
  })

  it('composes search, source, view, and sort', () => {
    const result = selectQueryLibrary({
      queries,
      view: 'all',
      searchTerm: 'select',
      source: 'observed',
      params: 'all',
      activity: 'all',
      impact: 'all',
      sort: 'most-frequent',
      isCached,
    })

    expect(result.queries.map((entry) => entry.hash)).toEqual(['new'])
    expect(result.counts.new).toBe(1)
    expect(result.sourceCounts.observed).toBe(1)
    expect(result.isFiltered).toBe(true)
  })

  it('derives the filtered state from the current search term', () => {
    const select = (searchTerm: string) =>
      selectQueryLibrary({
        queries,
        view: 'all',
        searchTerm,
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
        sort: 'highest-impact',
        isCached,
      })

    expect(select('select').isFiltered).toBe(true)
    expect(select('   ').isFiltered).toBe(false)
  })

  it('sorts a newly saved manual query ahead of older observed queries', () => {
    const result = selectQueryLibrary({
      queries: [
        query('observed', {
          first_observed_at: '2026-08-10T10:00:00Z',
        }),
        query('manual', {
          source: 'manual',
          sources: ['manual'],
          first_observed_at: '',
          first_analyzed: '2026-08-10T11:00:00Z',
          saved_at: '2026-08-10T12:00:00Z',
        }),
      ],
      view: 'all',
      searchTerm: '',
      source: 'all',
      params: 'all',
      activity: 'all',
      impact: 'all',
      sort: 'newest',
      isCached,
    })

    expect(result.queries.map((entry) => entry.hash)).toEqual([
      'manual',
      'observed',
    ])
  })

  it('uses every provenance value instead of only the latest source', () => {
    const observedAndAsked = query('multi', {
      source: 'ask',
      sources: ['top-historical', 'ask'],
    })

    for (const source of ['observed', 'ask'] as const) {
      expect(
        selectQueryLibrary({
          queries: [observedAndAsked],
          view: 'all',
          searchTerm: '',
          source,
          params: 'all',
          activity: 'all',
          impact: 'all',
          sort: 'highest-impact',
          isCached,
        }).queries
      ).toHaveLength(1)
    }
  })

  it('composes parameter readiness, activity, and database-time filters', () => {
    const recentParameterized = query('recent-parameterized', {
      sql: 'SELECT * FROM users WHERE id = $1',
      most_recent_params: { p1: 42 },
      last_observed_at: '2026-08-10T10:00:00Z',
      frequency: 120,
      observation_count: 120,
      avg_duration_ms: 1_000,
    })
    const missingValues = query('missing-values', {
      sql: 'SELECT * FROM users WHERE id = $1',
      last_observed_at: '2026-08-10T10:00:00Z',
      frequency: 120,
      observation_count: 120,
      avg_duration_ms: 1_000,
    })
    const oldHighImpact = query('old-high-impact', {
      last_observed_at: '2026-06-01T10:00:00Z',
      frequency: 10_000,
      observation_count: 10_000,
      avg_duration_ms: 1_000,
    })

    const result = selectQueryLibrary({
      queries: [recentParameterized, missingValues, oldHighImpact],
      view: 'all',
      searchTerm: '',
      source: 'all',
      params: 'values-ready',
      activity: '24h',
      impact: '1m',
      sort: 'highest-impact',
      isCached,
      now: Date.parse('2026-08-10T12:00:00Z'),
    })

    expect(result.queries.map((entry) => entry.hash)).toEqual([
      'recent-parameterized',
    ])
    expect(result.isFiltered).toBe(true)
  })

  it('supports operational activity windows without treating missing activity as recent', () => {
    const now = Date.parse('2026-08-10T12:00:00Z')
    const recent = query('recent', {
      last_observed_at: '2026-08-10T11:59:30Z',
    })
    const hoursOld = query('hours-old', {
      last_observed_at: '2026-08-10T05:00:00Z',
    })
    const unknown = query('unknown')

    expect(matchesActivityWindow(recent, '1m', now)).toBe(true)
    expect(matchesActivityWindow(recent, '1h', now)).toBe(true)
    expect(matchesActivityWindow(hoursOld, '1h', now)).toBe(false)
    expect(matchesActivityWindow(hoursOld, '8h', now)).toBe(true)
    expect(matchesActivityWindow(unknown, '24h', now)).toBe(false)
  })

  it('reports prospective facet counts before a filter can create a dead end', () => {
    const askedWithoutValues = query('asked-without-values', {
      source: 'ask',
      sources: ['ask'],
      sql: 'SELECT * FROM users WHERE id = $1',
    })
    const manualWithValues = query('manual-with-values', {
      source: 'manual',
      sources: ['manual'],
      sql: 'SELECT * FROM users WHERE id = $1',
      most_recent_params: { p1: 42 },
    })

    const askSelection = selectQueryLibrary({
      queries: [askedWithoutValues, manualWithValues],
      view: 'all',
      searchTerm: '',
      source: 'ask',
      params: 'all',
      activity: 'all',
      impact: 'all',
      sort: 'highest-impact',
      isCached,
    })

    expect(askSelection.facetCounts.params['values-ready']).toBe(0)
    expect(askSelection.facetCounts.params['values-needed']).toBe(1)

    const deadEndSelection = selectQueryLibrary({
      queries: [askedWithoutValues, manualWithValues],
      view: 'all',
      searchTerm: '',
      source: 'ask',
      params: 'values-ready',
      activity: 'all',
      impact: 'all',
      sort: 'highest-impact',
      isCached,
    })

    expect(deadEndSelection.queries).toHaveLength(0)
    expect(deadEndSelection.facetCounts.params.all).toBe(1)
    expect(deadEndSelection.facetCounts.source.manual).toBe(1)
  })
})

describe('normalizeQueryLibraryFacetCounts', () => {
  it('keeps server counts and fills missing values with zero', () => {
    const counts = normalizeQueryLibraryFacetCounts({
      view: { all: 240, new: 12 },
      source: { observed: 200 },
      params: {},
      activity: { '24h': 31 },
      impact: {},
    })

    expect(counts.view.all).toBe(240)
    expect(counts.view.new).toBe(12)
    expect(counts.view.cached).toBe(0)
    expect(counts.source.observed).toBe(200)
    expect(counts.source.scan).toBe(0)
    expect(counts.activity['24h']).toBe(31)
    expect(counts.impact['1h']).toBe(0)
  })

  it('returns a complete zeroed set before the first page arrives', () => {
    const counts = normalizeQueryLibraryFacetCounts(null)

    expect(counts.view.all).toBe(0)
    expect(counts.source.all).toBe(0)
    expect(counts.params.all).toBe(0)
    expect(counts.activity.all).toBe(0)
    expect(counts.impact.all).toBe(0)
  })
})
