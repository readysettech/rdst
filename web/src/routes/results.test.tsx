import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAnalyze } from '../lib/sse'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import { ResultsPage } from './-results-page'
import { Route } from './results'

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => vi.fn(),
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

vi.mock('../components', () => ({
  AnalysisResults: () => <div>analysis-results</div>,
  SQLDisplay: () => <div>sql-display</div>,
  InteractivePanel: () => null,
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}))

vi.mock('../components/top', () => ({
  ParameterDialog: () => null,
  hasParameters: vi.fn(() => false),
}))

beforeEach(() => {
  window.localStorage.clear()
})

describe('ResultsPage analyze consent', () => {
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

    // Re-rendering the route starts another attempt and presents consent again.
    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )
    expect(await screen.findByText('Run EXPLAIN ANALYZE?')).toBeTruthy()

    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again" }))
    fireEvent.click(screen.getByRole('button', { name: 'Run analyze' }))

    expect(analyzeSpy).toHaveBeenCalledWith({
      query: 'select 1',
      target: 'prod',
      fast: false,
    })
    expect(
      window.localStorage.getItem('rdst.explain-analyze-consent')
    ).toBe('accepted')
  })
})

describe('ResultsPage password lock', () => {
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
    ) => { query: string }
    expect(() => validateSearch({})).not.toThrow()
    expect(validateSearch({}).query).toBe('')
    expect(validateSearch({ query: 'select 1' }).query).toBe('select 1')
  })

  it('beforeLoad redirects to /analyze when the query is missing', () => {
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
    expect(JSON.stringify(thrown)).toContain('/analyze')
  })

  it('beforeLoad allows a present query through (no redirect)', () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: { query: string }
    }) => void
    expect(() => beforeLoad({ search: { query: 'select 1' } })).not.toThrow()
  })
})
