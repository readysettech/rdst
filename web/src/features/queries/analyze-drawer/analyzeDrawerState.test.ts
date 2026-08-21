import { describe, expect, it } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/api'
import {
  analyzeDrawerLink,
  analyzeDrawerPatch,
  analyzeReturnSearch,
  drawerResultsSearch,
} from './analyzeDrawerState'

function entry(overrides: Partial<QueryRegistryEntry> = {}) {
  return {
    hash: 'h1',
    sql: '  SELECT * FROM orders  ',
    tag: '',
    source: 'observed',
    target: 'demo',
    frequency: 4,
    last_analyzed: '',
    is_new: false,
    ...overrides,
  } as QueryRegistryEntry
}

describe('analyze drawer URL state', () => {
  it('is closed until the URL names a query', () => {
    expect(analyzeDrawerLink({})).toBeNull()
    expect(analyzeDrawerLink({ view: 'new', q: 'orders' })).toBeNull()
  })

  it('reads the open drawer and its stored analysis from the URL', () => {
    expect(analyzeDrawerLink({ analyze: 'h1', analysisId: 'a2' })).toEqual({
      hash: 'h1',
      analysisId: 'a2',
      rerun: undefined,
      tab: 'analyze',
    })
  })

  it('defaults a bare link to Analyze and honours an explicit Overview', () => {
    expect(analyzeDrawerLink({ analyze: 'h1' })?.tab).toBe('analyze')
    expect(analyzeDrawerLink({ analyze: 'h1', tab: 'overview' })?.tab).toBe(
      'overview'
    )
    // Analyze is the default, so a link on it says nothing about the tab.
    expect(
      analyzeDrawerPatch({ hash: 'h1', tab: 'analyze' }).tab
    ).toBeUndefined()
    expect(analyzeDrawerPatch({ hash: 'h1', tab: 'overview' }).tab).toBe(
      'overview'
    )
    expect(analyzeDrawerPatch(null).tab).toBeUndefined()
  })

  it('opens, switches and closes through the same three params', () => {
    expect(analyzeDrawerPatch({ hash: 'h1' })).toEqual({
      analyze: 'h1',
      analysisId: undefined,
      rerun: undefined,
    })
    expect(analyzeDrawerPatch({ hash: 'h1', analysisId: 'a2' })).toEqual({
      analyze: 'h1',
      analysisId: 'a2',
      rerun: undefined,
    })
    expect(analyzeDrawerPatch({ hash: 'h1', rerun: true })).toEqual({
      analyze: 'h1',
      analysisId: undefined,
      rerun: true,
    })
    expect(analyzeDrawerPatch(null)).toEqual({
      analyze: undefined,
      analysisId: undefined,
      rerun: undefined,
    })
  })

  it('keeps the library filters, not the drawer state, as the return search', () => {
    expect(
      analyzeReturnSearch({
        view: 'new',
        q: 'orders',
        sort: 'newest',
        analyze: 'h1',
        analysisId: 'a2',
        hash: 'h1',
      })
    ).toEqual({
      view: 'new',
      q: 'orders',
      source: undefined,
      params: undefined,
      activity: undefined,
      impact: undefined,
      sort: 'newest',
    })
  })

  it('describes the same analysis as an equivalent /results link', () => {
    const search = drawerResultsSearch({
      entry: entry({ most_recent_params: { id: 7 } }),
      hash: 'h1',
      analysisId: 'a2',
      returnSearch: { view: 'new' },
      target: 'demo',
    })

    expect(search).toEqual({
      query: 'SELECT * FROM orders',
      target: 'demo',
      fast: false,
      params: '{"id":7}',
      returnSearch: '{"view":"new"}',
      origin: 'query-library',
      hash: 'h1',
      analysisId: 'a2',
    })
  })

  it('measures the parameter values the user substituted in the drawer', () => {
    const search = drawerResultsSearch({
      entry: entry(),
      hash: 'h1',
      returnSearch: {},
      sqlOverride: 'SELECT * FROM orders WHERE id = 7',
    })

    expect(search.query).toBe('SELECT * FROM orders WHERE id = 7')
    expect(search.analysisId).toBeUndefined()
    expect(search.params).toBeUndefined()
  })
})
