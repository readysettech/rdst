import { describe, expect, it } from 'vitest'
import {
  addedQuerySearchPatch,
  newQueriesSearchPatch,
  parseQueryLibrarySearch,
  resolvedQueryLibraryState,
} from './queryLibraryState'

describe('query library URL state', () => {
  it('parses supported filters and drops unknown values', () => {
    expect(
      parseQueryLibrarySearch({
        view: 'new',
        q: 'orders',
        source: 'observed',
        params: 'values-needed',
        activity: '7d',
        impact: '10m',
        sort: 'recently-observed',
        action: 'add',
        hash: 'abc',
      })
    ).toEqual({
      view: 'new',
      q: 'orders',
      source: 'observed',
      params: 'values-needed',
      activity: '7d',
      impact: '10m',
      sort: 'recently-observed',
      action: 'add',
      hash: 'abc',
      run: undefined,
    })

    expect(
      parseQueryLibrarySearch({ view: 'unknown', source: 4, sort: 'fastest' })
    ).toEqual({
      view: undefined,
      q: undefined,
      source: undefined,
      params: undefined,
      activity: undefined,
      impact: undefined,
      sort: undefined,
      action: undefined,
      hash: undefined,
      run: undefined,
    })
  })

  it('parses short operational activity windows', () => {
    for (const activity of ['1m', '1h', '8h'] as const) {
      expect(parseQueryLibrarySearch({ activity }).activity).toBe(activity)
    }
  })

  it('maps legacy workspace views without retaining the old IA', () => {
    expect(parseQueryLibrarySearch({ view: 'slow' }).view).toBe('high-impact')
    expect(parseQueryLibrarySearch({ view: 'saved' }).view).toBe('saved')
    expect(parseQueryLibrarySearch({ view: 'analyze' }).action).toBeUndefined()
  })

  it('keeps live capture as a contextual library action', () => {
    expect(parseQueryLibrarySearch({ action: 'live' }).action).toBeUndefined()
  })

  it('resolves stable defaults', () => {
    expect(resolvedQueryLibraryState({})).toEqual({
      view: 'all',
      searchTerm: '',
      source: 'all',
      params: 'all',
      activity: 'all',
      impact: 'all',
      sort: 'highest-impact',
    })
  })

  it('orients the library to a newly added query', () => {
    expect(addedQuerySearchPatch('new-hash')).toEqual({
      view: 'saved',
      q: undefined,
      source: undefined,
      params: undefined,
      activity: undefined,
      impact: undefined,
      sort: 'newest',
      action: undefined,
      hash: 'new-hash',
      run: undefined,
    })
  })

  it('reveals discovered queries in an unblocked New view', () => {
    expect(newQueriesSearchPatch()).toEqual({
      view: 'new',
      q: undefined,
      source: undefined,
      params: undefined,
      activity: undefined,
      impact: undefined,
      sort: 'newest',
      action: undefined,
      hash: undefined,
      run: undefined,
    })
  })
})
