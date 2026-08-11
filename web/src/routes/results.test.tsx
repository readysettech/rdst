import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompleteEvent } from '../lib/api'
import { useAnalyze } from '../lib/sse'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import { ResultsPage } from './-results-page'
import { Route } from './results'

const navigateSpy = vi.hoisted(() => vi.fn())
const hasParametersSpy = vi.hoisted(() => vi.fn(() => false))
const connectivity = vi.hoisted(() => ({
  failure: null as { target: string; message: string } | null,
  isChecking: false,
  ensureReachable: vi.fn(async () => true),
  reset: vi.fn(),
}))

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  }
})

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
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

vi.mock('../lib/sse', () => ({
  useAnalyze: vi.fn(),
}))

vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: vi.fn(),
}))

vi.mock('../lib/useTargetConnectivityGate', () => ({
  useTargetConnectivityGate: () => connectivity,
}))

vi.mock('../components', () => ({
  AnalysisResults: () => <div>analysis-results</div>,
  SQLDisplay: () => <div>sql-display</div>,
  InteractivePanel: () => null,
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}))

vi.mock('../components/top', () => ({
  ParameterDialog: ({
    isOpen,
    onClose,
  }: {
    isOpen: boolean
    onClose: () => void
  }) =>
    isOpen ? (
      <button type="button" onClick={onClose}>
        Cancel parameters
      </button>
    ) : null,
  hasParameters: hasParametersSpy,
}))

beforeEach(() => {
  window.localStorage.clear()
  navigateSpy.mockClear()
  hasParametersSpy.mockReturnValue(false)
  connectivity.failure = null
  connectivity.isChecking = false
  connectivity.ensureReachable.mockReset()
  connectivity.ensureReachable.mockResolvedValue(true)
})

describe('ResultsPage analyze consent', () => {
  it('does not restart an analysis that has already completed', async () => {
    const analyzeSpy = vi.fn()
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      query_hash: 'complete-query',
    }
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: analyzeSpy,
      state: 'complete',
      progress: undefined,
      results,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
      reset: vi.fn(),
    })
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: true,
      isLocked: false,
      targetName: 'prod',
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    })

    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )

    await waitFor(() => {
      expect(screen.queryByText('Run EXPLAIN ANALYZE?')).toBeNull()
    })
    expect(analyzeSpy).not.toHaveBeenCalled()
  })

  it('requires consent before the first run and remembers the opt-out', async () => {
    const analyzeSpy = vi.fn()
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: analyzeSpy,
      state: 'idle',
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
      reset: vi.fn(),
    })
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: true,
      isLocked: false,
      targetName: 'prod',
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    })

    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )

    expect(await screen.findByText('Run EXPLAIN ANALYZE?')).toBeTruthy()
    expect(
      screen.getByText(
        'Analyze runs EXPLAIN ANALYZE, which executes your query once against the database to measure it. Cancel if this query should not be executed.'
      )
    ).toBeTruthy()
    expect(
      screen
        .getByRole('checkbox', { name: "Don't ask again" })
        .getAttribute('aria-checked')
    ).toBe('false')
    expect(analyzeSpy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(analyzeSpy).not.toHaveBeenCalled()
    expect(navigateSpy).toHaveBeenCalledWith({ to: '/queries' })

    // Re-rendering the route starts another attempt and presents consent again.
    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )
    expect(await screen.findByText('Run EXPLAIN ANALYZE?')).toBeTruthy()

    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again" }))
    fireEvent.click(screen.getByRole('button', { name: 'Run analyze' }))

    await waitFor(() =>
      expect(analyzeSpy).toHaveBeenCalledWith({
        query: 'select 1',
        target: 'prod',
        fast: false,
      })
    )
    expect(window.localStorage.getItem('rdst.explain-analyze-consent')).toBe(
      'accepted'
    )
  })

  it('returns to Queries when parameter entry is cancelled', async () => {
    hasParametersSpy.mockReturnValue(true)
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: vi.fn(),
      state: 'idle',
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
      reset: vi.fn(),
    })
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: true,
      isLocked: false,
      targetName: 'prod',
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    })

    render(
      <ResultsPage
        search={{
          query: 'select * from posts where id = $1',
          target: 'prod',
          returnSearch: JSON.stringify({
            view: 'saved',
            q: 'posts',
            source: 'observed',
          }),
        }}
      />
    )

    fireEvent.click(
      await screen.findByRole('button', { name: 'Cancel parameters' })
    )
    expect(navigateSpy).toHaveBeenCalledWith({
      to: '/queries',
      search: expect.objectContaining({
        view: 'saved',
        q: 'posts',
        source: 'observed',
      }),
    })
  })
})

describe('ResultsPage password lock', () => {
  it('waits for target access to resolve before requesting consent', async () => {
    const analyzeSpy = vi.fn()
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: analyzeSpy,
      state: 'idle',
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
      reset: vi.fn(),
    })
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: false,
      isLocked: false,
      targetName: null,
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    })

    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )

    await waitFor(() => {
      expect(screen.queryByText('Run EXPLAIN ANALYZE?')).toBeNull()
    })
    expect(analyzeSpy).not.toHaveBeenCalled()
  })

  it('stops before Analyze when the database preflight fails', async () => {
    const analyzeSpy = vi.fn()
    window.localStorage.setItem('rdst.explain-analyze-consent', 'accepted')
    connectivity.failure = {
      target: 'prod',
      message: 'connection refused',
    }
    connectivity.ensureReachable.mockResolvedValue(false)
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: analyzeSpy,
      state: 'idle',
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
      reset: vi.fn(),
    })
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: true,
      isLocked: false,
      targetName: 'prod',
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    })

    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )

    expect(await screen.findByText("Can't reach prod")).toBeTruthy()
    await waitFor(() =>
      expect(connectivity.ensureReachable).toHaveBeenCalledTimes(1)
    )
    expect(analyzeSpy).not.toHaveBeenCalled()
  })

  it('does not auto-run analyze when target is password-locked', async () => {
    const analyzeSpy = vi.fn()
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: analyzeSpy,
      state: 'idle',
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
      reset: vi.fn(),
    })
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: true,
      isLocked: true,
      targetName: 'prod',
      message: "Target 'prod' is locked.",
      missingTargetRequirements: [],
      keyringAvailable: true,
    })

    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )

    await waitFor(() => {
      expect(screen.getByText("Target 'prod' is locked.")).toBeTruthy()
    })

    expect(analyzeSpy).not.toHaveBeenCalled()
  })
})

describe('/results no-query guard (B1)', () => {
  it('validateSearch never throws — it parses a missing query to empty', () => {
    // Throwing `redirect` from validateSearch is what crashed the whole app to
    // the chrome-less screen; parsing must stay pure.
    const validateSearch = Route.options.validateSearch as (
      s: Record<string, unknown>
    ) => { query: string; returnSearch?: string }
    expect(() => validateSearch({})).not.toThrow()
    expect(validateSearch({}).query).toBe('')
    expect(validateSearch({ query: 'select 1' }).query).toBe('select 1')
    expect(
      validateSearch({ query: 'select 1', returnSearch: '{"view":"saved"}' })
        .returnSearch
    ).toBe('{"view":"saved"}')
  })

  it('beforeLoad redirects to the Queries workspace when the query is missing', () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: { query: string }
    }) => void

    let thrown: unknown
    try {
      beforeLoad({ search: { query: '' } })
    } catch (e) {
      thrown = e
    }
    expect(thrown).toBeDefined()
    expect(JSON.stringify(thrown)).toContain('/queries')
  })

  it('beforeLoad allows a present query through (no redirect)', () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: { query: string }
    }) => void
    expect(() => beforeLoad({ search: { query: 'select 1' } })).not.toThrow()
  })
})
