import { describe, expect, it } from 'vitest'
import {
  addedQuerySearchPatch,
  parseQueryLibrarySearch,
  QUERY_LIBRARY_VIEWS,
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
    expect(parseQueryLibrarySearch({ view: 'analyze' }).action).toBeUndefined()
  })

  it('keeps Status to the statuses the server computes', () => {
    expect(QUERY_LIBRARY_VIEWS).not.toContain('saved')
  })

  it('migrates the old Saved status into the independent star', () => {
    const migrated = parseQueryLibrarySearch({ view: 'saved' })
    expect(migrated.starred).toBe(true)
    expect(migrated.view).toBeUndefined()
  })

  it('reads the star from either spelling and composes it with a status', () => {
    expect(parseQueryLibrarySearch({ starred: '1' }).starred).toBe(true)
    expect(parseQueryLibrarySearch({ starred: true }).starred).toBe(true)
    expect(parseQueryLibrarySearch({ starred: 'no' }).starred).toBeUndefined()

    const composed = parseQueryLibrarySearch({
      view: 'needs-analysis',
      starred: '1',
    })
    expect(composed).toMatchObject({ view: 'needs-analysis', starred: true })
    expect(resolvedQueryLibraryState(composed)).toMatchObject({
      view: 'needs-analysis',
      starred: true,
    })
  })

  it('reads the drawer tab, defaulting a bare analyze link to Analyze', () => {
    expect(parseQueryLibrarySearch({ analyze: 'h1' }).tab).toBeUndefined()
    expect(
      parseQueryLibrarySearch({ analyze: 'h1', tab: 'overview' }).tab
    ).toBe('overview')
    expect(
      parseQueryLibrarySearch({ analyze: 'h1', tab: 'nonsense' }).tab
    ).toBeUndefined()
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
      starred: false,
    })
  })

  it('orients the library to a newly added query', () => {
    expect(addedQuerySearchPatch('new-hash')).toEqual({
      view: undefined,
      starred: true,
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
})
