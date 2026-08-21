import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  TopConnectionInfo,
  TopDbLimitWarningEventData,
  TopQuery,
  TopSourceFallback,
  TopState,
} from '../../../types/top'
import { useSlowQueriesController } from './useSlowQueriesController'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getQueryData: vi.fn(),
  setQueryData: vi.fn(),
  removeQueries: vi.fn(),
  cacheQuery: vi.fn(),
  addQuery: vi.fn(),
  toast: vi.fn(),
  registryQueries: [] as Array<{
    hash: string
    most_recent_params?: Record<string, unknown>
  }>,
  run: {
    getTop: vi.fn(),
    startRealtime: vi.fn(),
    stopRealtime: vi.fn(),
    reset: vi.fn(),
    state: 'idle' as TopState,
    queries: [] as TopQuery[],
    connectionInfo: null as TopConnectionInfo | null,
    sourceFallback: null as TopSourceFallback | null,
    dbLimitWarning: null as TopDbLimitWarningEventData | null,
    runtimeSeconds: 0,
    totalTracked: 0,
    newlySaved: 0,
    savedHashes: new Set<string>(),
    error: null,
  },
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({
    getQueryData: mocks.getQueryData,
    setQueryData: mocks.setQueryData,
    removeQueries: mocks.removeQueries,
  }),
}))

vi.mock('@rs/ui-new/use-toast', () => ({
  toast: mocks.toast,
}))

vi.mock('../../../components/top', () => ({
  hasParameters: (query: string) => query.includes('$1'),
}))

vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'production' }),
}))

vi.mock('../../../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isResolved: true,
    isLocked: false,
    targetName: 'production',
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  }),
}))

vi.mock('../../../lib/useQueryRegistry', () => ({
  useQueryRegistry: () => ({
    queries: mocks.registryQueries,
    addMutation: { mutateAsync: mocks.addQuery },
  }),
}))

vi.mock('../../../lib/useCacheAction', () => ({
  useCacheAction: () => ({
    cacheQuery: mocks.cacheQuery,
    cachingId: null,
    isCached: () => false,
  }),
}))

vi.mock('../../../lib/useTop', () => ({
  useTop: () => mocks.run,
}))

function query(hash: string, sql = 'select id from users'): TopQuery {
  return {
    query_hash: hash,
    query_text: sql,
    normalized_query: sql,
    freq: 1,
    total_time: '1s',
    avg_time: '1s',
    pct_load: '10%',
  }
}

beforeEach(() => {
  mocks.navigate.mockReset()
  mocks.getQueryData.mockReset()
  mocks.getQueryData.mockReturnValue(undefined)
  mocks.setQueryData.mockReset()
  mocks.removeQueries.mockReset()
  mocks.cacheQuery.mockReset()
  mocks.addQuery.mockReset()
  mocks.toast.mockReset()
  mocks.registryQueries.splice(0)
  mocks.run.getTop.mockReset()
  mocks.run.startRealtime.mockReset()
  mocks.run.stopRealtime.mockReset()
  mocks.run.reset.mockReset()
  mocks.run.state = 'idle'
  mocks.run.queries = []
  mocks.run.connectionInfo = null
  mocks.run.sourceFallback = null
  mocks.run.dbLimitWarning = null
  mocks.run.savedHashes = new Set()
})

describe('useSlowQueriesController', () => {
  it('starts a historical run with the selected filters', () => {
    const { result } = renderHook(() => useSlowQueriesController())

    act(() => {
      result.current.filters.setSource('pg_stat')
      result.current.filters.setSort('avg_time')
      result.current.filters.setLimit(20)
      result.current.filters.setMinFreq(3)
      result.current.filters.setMinLoadPct(5)
      result.current.filters.setFilterPattern('SELECT.*users')
    })
    act(() => result.current.actions.start())

    expect(mocks.run.getTop).toHaveBeenCalledWith('production', {
      limit: 20,
      source: 'pg_stat',
      sort: 'avg_time',
      filter_pattern: 'SELECT.*users',
      auto_save: true,
      min_freq: 3,
      min_load_pct: 5,
    })
    expect(mocks.run.startRealtime).not.toHaveBeenCalled()
  })

  it('clears the row filters and re-runs with them cleared', () => {
    const { result } = renderHook(() => useSlowQueriesController())

    act(() => {
      result.current.filters.setMinFreq(3)
      result.current.filters.setFilterPattern('SELECT.*users')
    })
    expect(result.current.filters.active).toBe(true)

    act(() => result.current.actions.clearFilters())

    expect(result.current.filters.active).toBe(false)
    expect(result.current.filters.filterPattern).toBe('')
    // The filters are applied server-side, so the same tick must re-run with
    // the cleared values rather than the ones that emptied the list.
    expect(mocks.run.getTop).toHaveBeenCalledWith(
      'production',
      expect.objectContaining({
        filter_pattern: undefined,
        min_freq: 0,
        min_load_pct: 0,
      })
    )
  })

  it('starts and stops realtime monitoring through the same controller', () => {
    const { result } = renderHook(() => useSlowQueriesController())

    act(() => {
      result.current.filters.setMode('realtime')
      result.current.filters.setDuration(30)
      result.current.filters.setLimit(50)
    })
    act(() => result.current.actions.start())
    act(() => result.current.actions.stop())

    expect(mocks.run.reset).toHaveBeenCalledTimes(1)
    expect(mocks.run.startRealtime).toHaveBeenCalledWith('production', {
      limit: 50,
      duration: 30,
      auto_save: true,
      min_freq: 0,
      min_load_pct: 0,
    })
    expect(mocks.run.stopRealtime).toHaveBeenCalledTimes(1)
  })

  it('opens parameter capture before analyzing parameterized SQL', () => {
    mocks.registryQueries.push({
      hash: 'query-1',
      most_recent_params: { $1: 'active' },
    })
    const { result } = renderHook(() => useSlowQueriesController())

    act(() =>
      result.current.actions.analyze(
        query('query-1', 'select * from users where status = $1')
      )
    )

    expect(result.current.parameterDialog.query).toContain('$1')
    expect(result.current.parameterDialog.initialValues).toEqual({
      $1: 'active',
    })
    expect(mocks.navigate).not.toHaveBeenCalled()

    act(() =>
      result.current.parameterDialog.submit(
        "select * from users where status = 'active'"
      )
    )
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/results',
      search: {
        query: "select * from users where status = 'active'",
        target: 'production',
        origin: 'slow-queries',
      },
    })
  })

  it('does not re-save queries already present in Saved', async () => {
    mocks.registryQueries.push({ hash: 'query-1' })
    mocks.run.queries = [query('query-1')]
    const { result } = renderHook(() => useSlowQueriesController())

    await act(async () => result.current.actions.saveAll())

    expect(mocks.addQuery).not.toHaveBeenCalled()
    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'Nothing to save',
      description: 'Every listed query is already saved.',
      variant: 'primary',
    })
  })

  it('persists completed-run filters under the run target', async () => {
    mocks.run.state = 'complete'
    mocks.run.connectionInfo = {
      target: 'reported-target',
      source: 'pg_stat',
      engine: 'postgresql',
    }

    renderHook(() => useSlowQueriesController())

    await waitFor(() => {
      expect(mocks.setQueryData).toHaveBeenCalledWith(
        ['top', 'lastFilters', 'reported-target'],
        expect.objectContaining({
          mode: 'historical',
          source: 'auto',
          sort: 'total_time',
        })
      )
    })
  })
})
