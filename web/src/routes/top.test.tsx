import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SlowQueriesPage } from '../features/queries/slow/SlowQueriesPage'
import { useQueryRegistry } from '../lib/useQueryRegistry'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import { useTop } from '../lib/useTop'

afterEach(cleanup)

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
  useNavigate: () => vi.fn(),
  // autoCodeSplitting rewrites the route's `component` to a lazyRouteComponent
  // call; the tests render SlowQueriesPage directly, so this just needs to exist.
  lazyRouteComponent: (loader: unknown) => loader,
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({
    invalidateQueries: vi.fn(),
    // SlowQueriesPage reads/writes its filters cache on this client; stub the surface
    // it touches so the page mounts (getQueryData returns "no persisted run").
    getQueryData: vi.fn(() => undefined),
    setQueryData: vi.fn(),
    removeQueries: vi.fn(),
  }),
  useQuery: () => ({
    data: undefined,
    isLoading: false,
    isFetching: false,
    error: null,
  }),
  useMutation: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    data: undefined,
    error: null,
    reset: vi.fn(),
  }),
}))

vi.mock('../lib/useQueryRegistry', () => ({
  useQueryRegistry: vi.fn(),
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'prod' }),
}))

vi.mock('../lib/useTop', () => ({
  useTop: vi.fn(),
}))

vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: vi.fn(),
}))

vi.mock('../components', () => ({
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}))

vi.mock('../components/top', () => ({
  ParameterDialog: () => null,
  hasParameters: () => false,
}))

function setupMocks(overrides: Partial<ReturnType<typeof useTop>> = {}) {
  vi.mocked(useQueryRegistry).mockReturnValue({
    queries: [],
    listError: null,
    isLoading: false,
    refetch: vi.fn(),
    addQuery: vi.fn(),
    addMutation: {
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
      isIdle: true,
      isSuccess: false,
      data: undefined,
      error: null,
      reset: vi.fn(),
      status: 'idle',
      variables: undefined,
      failureCount: 0,
      failureReason: null,
      submittedAt: 0,
      context: undefined,
      isPaused: false,
    } as any,
    removeQuery: vi.fn(),
    markReviewedMutation: { mutate: vi.fn(), isPending: false } as any,
    updateTag: vi.fn(),
    updateSqlMutation: { mutate: vi.fn(), isPending: false } as any,
    importMutation: {
      mutate: vi.fn(),
      isPending: false,
      data: undefined,
      reset: vi.fn(),
    } as any,
    isFetching: false,
    total: 0,
    limit: 50,
    offset: 0,
    setLimit: vi.fn(),
    setOffset: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    resetPagination: vi.fn(),
  })

  vi.mocked(useTargetPasswordLock).mockReturnValue({
    isResolved: true,
    isLocked: false,
    targetName: 'prod',
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  })

  vi.mocked(useTop).mockReturnValue({
    getTop: vi.fn(),
    startRealtime: vi.fn(),
    stopRealtime: vi.fn(),
    reset: vi.fn(),
    state: 'idle',
    queries: [],
    connectionInfo: null,
    sourceFallback: null,
    dbLimitWarning: null,
    runtimeSeconds: 0,
    totalTracked: 0,
    newlySaved: 0,
    savedHashes: new Set(),
    error: null,
    ...overrides,
  })
}

describe('SlowQueriesPage db limit warning', () => {
  it('does not show warning when dbLimitWarning is null', () => {
    setupMocks({ dbLimitWarning: null })
    render(<SlowQueriesPage />)

    expect(
      screen.queryByText(/Database query text may be truncated/i)
    ).toBeNull()
  })

  it('shows warning when dbLimitWarning is present', () => {
    setupMocks({
      dbLimitWarning: {
        db_limit_bytes: 1024,
        recommended_bytes: 4096,
        setting_name: 'track_activity_query_size',
        db_engine: 'postgresql',
      },
    })
    render(<SlowQueriesPage />)

    expect(
      screen.getByText(/Database query text may be truncated/i)
    ).toBeTruthy()
    expect(
      screen.getAllByText(/track_activity_query_size/i).length
    ).toBeGreaterThan(0)
    expect(screen.getByText(/1\s*KB/i)).toBeTruthy()
    expect(screen.getByText(/4\s*KB/i)).toBeTruthy()
  })

  it('shows ALTER SYSTEM command for PostgreSQL', () => {
    setupMocks({
      dbLimitWarning: {
        db_limit_bytes: 1024,
        recommended_bytes: 4096,
        setting_name: 'track_activity_query_size',
        db_engine: 'postgresql',
      },
    })
    render(<SlowQueriesPage />)

    expect(
      screen.getAllByText(/ALTER SYSTEM SET track_activity_query_size/i).length
    ).toBeGreaterThan(0)
  })

  it('shows SET GLOBAL command for MySQL', () => {
    setupMocks({
      dbLimitWarning: {
        db_limit_bytes: 1024,
        recommended_bytes: 4096,
        setting_name: 'performance_schema_max_digest_length',
        db_engine: 'mysql',
      },
    })
    render(<SlowQueriesPage />)

    expect(
      screen.getAllByText(/SET GLOBAL performance_schema_max_digest_length/i)
        .length
    ).toBeGreaterThan(0)
  })
})

describe('SlowQueriesPage header composition', () => {
  it('opens directly in realtime mode without the legacy mode switch', () => {
    setupMocks()

    render(<SlowQueriesPage initialMode="realtime" realtimeOnly />)

    expect(screen.queryByRole('radiogroup', { name: 'Query mode' })).toBeNull()
    expect(screen.getByText(/Start live monitoring/i)).toBeTruthy()
  })

  it('shows one primary action while idle', () => {
    setupMocks()

    render(<SlowQueriesPage />)

    expect(
      screen.getAllByRole('button', { name: /Find slow queries/ })
    ).toHaveLength(1)
  })

  it('keeps the primary controls and results controls in one card', () => {
    setupMocks({
      state: 'complete',
      queries: [
        {
          query_hash: 'query-1',
          query_text: 'select 1',
          normalized_query: 'select 1',
          freq: 1,
          total_time: '1s',
          avg_time: '1s',
          pct_load: '10%',
        },
      ],
    })

    render(<SlowQueriesPage />)

    const headerCard = screen.getByRole('region', {
      name: 'Slow query controls',
    })
    const primaryContent = screen.getByTestId('slow-query-control-content')
    const resultsContent = screen.getByTestId(
      'slow-query-results-header-content'
    )
    const statusRow = within(primaryContent).getByTestId(
      'slow-query-status-row'
    )
    const scopeRow = within(primaryContent).getByTestId('slow-query-scope-row')
    const context = within(resultsContent).getByTestId('slow-query-context')

    expect(primaryContent.parentElement).toBe(headerCard)
    expect(resultsContent.parentElement).toBe(headerCard)
    expect(within(statusRow).getByText('Status: Complete')).toBeTruthy()
    expect(
      within(statusRow).getByRole('button', { name: /Find slow queries/ })
    ).toBeTruthy()
    expect(
      within(scopeRow).getByRole('radiogroup', { name: 'Query mode' })
    ).toBeTruthy()
    expect(
      within(scopeRow).getByRole('button', { name: 'Filters' })
    ).toBeTruthy()
    expect(within(scopeRow).getByText('1 query')).toBeTruthy()
    expect(within(context).getByText('Target')).toBeTruthy()
    expect(within(context).getByText('prod')).toBeTruthy()
    expect(within(context).getByText('Source')).toBeTruthy()
    expect(within(context).getByText('auto')).toBeTruthy()
    expect(primaryContent.firstElementChild?.className).toContain('space-y-3')
  })
})
