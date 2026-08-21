import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { drawerResultsSearch } from '../features/queries/analyze-drawer/analyzeDrawerState'
import type { ResultsSearch } from '../features/queries/results/types'
import { trackEvent } from '../lib/analytics'
import type { CompleteEvent, QueryRegistryEntry } from '../lib/api'
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

vi.mock('../lib/analytics', () => ({
  trackEvent: vi.fn(),
}))

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  }
})

// Reads the stored-analysis viewer makes, keyed the way the hooks key them.
const storedReads = vi.hoisted(() => ({
  storedAnalysis: undefined as unknown,
  analysisHistory: undefined as unknown,
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
  useQuery: ({ queryKey }: { queryKey?: readonly unknown[] } = {}) => ({
    data:
      queryKey?.[0] === 'storedAnalysis'
        ? storedReads.storedAnalysis
        : queryKey?.[0] === 'analysisHistory'
          ? storedReads.analysisHistory
          : undefined,
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
  vi.mocked(trackEvent).mockClear()
  storedReads.storedAnalysis = undefined
  storedReads.analysisHistory = undefined
  hasParametersSpy.mockReturnValue(false)
  connectivity.failure = null
  connectivity.isChecking = false
  connectivity.ensureReachable.mockReset()
  connectivity.ensureReachable.mockResolvedValue(true)
})

afterEach(cleanup)

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
      expect(analyzeSpy).toHaveBeenCalledWith(
        {
          query: 'select 1',
          target: 'prod',
          fast: false,
        },
        { queryHash: undefined, queryLabel: undefined }
      )
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
            view: 'new',
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
        view: 'new',
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
      validateSearch({ query: 'select 1', returnSearch: '{"view":"new"}' })
        .returnSearch
    ).toBe('{"view":"new"}')
  })

  it('parses the full-view link the drawer builds into the same analysis', () => {
    const validateSearch = Route.options.validateSearch as (
      s: Record<string, unknown>
    ) => ResultsSearch

    const fullView = drawerResultsSearch({
      entry: {
        hash: 'h1',
        sql: 'select 1',
        tag: '',
        source: 'observed',
        target: 'prod',
        frequency: 1,
        last_analyzed: '',
        is_new: false,
      } as QueryRegistryEntry,
      hash: 'h1',
      analysisId: 'a2',
      returnSearch: { view: 'new' },
      target: 'prod',
    })

    expect(validateSearch({ ...fullView })).toEqual(fullView)
  })

  it('validateSearch accepts a known origin and drops an unknown one', () => {
    const validateSearch = Route.options.validateSearch as (
      s: Record<string, unknown>
    ) => { query: string; origin?: string }
    expect(validateSearch({ query: 'select 1', origin: 'ask' }).origin).toBe(
      'ask'
    )
    expect(
      validateSearch({ query: 'select 1', origin: 'not-a-real-origin' }).origin
    ).toBeUndefined()
    expect(validateSearch({ query: 'select 1' }).origin).toBeUndefined()
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

describe('ResultsPage back navigation (B1 origin-aware back button)', () => {
  beforeEach(() => {
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: vi.fn(),
      state: 'complete',
      progress: undefined,
      results: {
        type: 'complete',
        success: true,
        query_hash: 'complete-query',
      } as CompleteEvent,
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
  })

  it('defaults to "Back to queries" -> /queries when no origin is given', async () => {
    render(<ResultsPage search={{ query: 'select 1', target: 'prod' }} />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Back to queries' })
    )
    expect(navigateSpy).toHaveBeenCalledWith({ to: '/queries' })
  })

  it.each([
    ['home', 'Back to Home', '/'],
    ['ask', 'Back to Ask', '/ask'],
    ['scan', 'Back to Code scan', '/scan'],
    ['slow-queries', 'Back to Slow queries', '/queries'],
    ['query-library', 'Back to queries', '/queries'],
  ] as const)('origin %s labels the back button %s and navigates to %s', async (origin, label, to) => {
    render(
      <ResultsPage search={{ query: 'select 1', target: 'prod', origin }} />
    )
    fireEvent.click(await screen.findByRole('button', { name: label }))
    expect(navigateSpy).toHaveBeenCalledWith({ to })
  })

  it('emits back_to_origin with the resolved origin when clicked', async () => {
    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', origin: 'ask' }}
      />
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Back to Ask' }))
    expect(trackEvent).toHaveBeenCalledWith('back_to_origin', {
      origin: 'ask',
    })
  })

  it('falls back to the query-library origin (analytics + label) on an untagged deep link', async () => {
    render(<ResultsPage search={{ query: 'select 1', target: 'prod' }} />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Back to queries' })
    )
    expect(trackEvent).toHaveBeenCalledWith('back_to_origin', {
      origin: 'query-library',
    })
  })
})

describe('ResultsPage analytics (E1 analysis_started)', () => {
  it('emits analysis_started with the resolved origin when a run kicks off', async () => {
    window.localStorage.setItem('rdst.explain-analyze-consent', 'accepted')
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
        search={{ query: 'select 1', target: 'prod', origin: 'scan' }}
      />
    )

    await waitFor(() => expect(analyzeSpy).toHaveBeenCalled())
    expect(trackEvent).toHaveBeenCalledWith('analysis_started', {
      origin: 'scan',
    })
  })
})

describe('ResultsPage stored analysis viewer (A3/A4)', () => {
  const HOUR = 3_600_000
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

  function idleLiveRun() {
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
    return analyzeSpy
  }

  function storedRecord(
    displayPayload: Record<string, unknown>,
    age = 2 * HOUR
  ) {
    return {
      hash: 'stored-hash',
      analysis_id: 'an-7',
      created_at: ago(age),
      target: 'prod',
      overall_rating: 'fair',
      efficiency_score: 65,
      analysis: { display_payload: displayPayload },
    }
  }

  const storedSearch = {
    query: 'select 1',
    target: 'prod',
    origin: 'ask' as const,
    hash: 'stored-hash',
    analysisId: 'an-7',
  }

  it('renders a stored record through the live results presentation and starts no run', async () => {
    const analyzeSpy = idleLiveRun()
    storedReads.storedAnalysis = storedRecord({
      explain_results: {
        success: true,
        database_engine: 'postgresql',
        execution_time_ms: 49.44,
        rows_examined: 81_169,
        rows_returned: 6,
        cost_estimate: 33_384.48,
      },
      llm_analysis: {
        success: true,
        performance_assessment: {
          overall_rating: 'fair',
          efficiency_score: 65,
          primary_concerns: ['Scans more rows than it returns.'],
        },
      },
    })

    render(<ResultsPage search={storedSearch} />)

    expect(
      await screen.findByText('Viewing analysis from 2 hours ago')
    ).toBeTruthy()
    // The same presentation the live flow renders, fed the stored payload.
    expect(
      screen.getByLabelText('Performance score 65 out of 100')
    ).toBeTruthy()
    expect(screen.getByText('81,169 → 6')).toBeTruthy()
    expect(screen.queryByText('Run EXPLAIN ANALYZE?')).toBeNull()
    await waitFor(() => expect(analyzeSpy).not.toHaveBeenCalled())
  })

  it('emits analysis_viewed_stored with the record age bucket', async () => {
    idleLiveRun()
    storedReads.storedAnalysis = storedRecord({
      explain_results: { success: true },
    })

    render(<ResultsPage search={storedSearch} />)

    await waitFor(() =>
      expect(trackEvent).toHaveBeenCalledWith('analysis_viewed_stored', {
        age_bucket: '1h-24h',
      })
    )
  })

  it('says so honestly when the stored analysis predates full result storage', async () => {
    idleLiveRun()
    storedReads.storedAnalysis = storedRecord({})

    render(<ResultsPage search={storedSearch} />)

    expect(
      (await screen.findAllByText(/ran before full results were saved/)).length
    ).toBeGreaterThan(0)
    // The score and date it does have are still shown, with a way forward.
    expect(screen.getAllByText('Fair · 65/100').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Re-run analysis' })).toBeTruthy()
  })

  it('re-runs by dropping analysisId from the URL and reports the reason', async () => {
    idleLiveRun()
    storedReads.storedAnalysis = storedRecord({})

    render(<ResultsPage search={storedSearch} />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Re-run analysis' })
    )
    expect(trackEvent).toHaveBeenCalledWith('analysis_rerun', {
      reason: 'missing-body',
    })
    expect(navigateSpy).toHaveBeenCalledWith({
      to: '/results',
      search: expect.objectContaining({
        query: 'select 1',
        hash: 'stored-hash',
        origin: 'ask',
        analysisId: undefined,
      }),
    })
  })

  it('keeps the origin back affordance while a stored record is open', async () => {
    idleLiveRun()
    storedReads.storedAnalysis = storedRecord({
      explain_results: { success: true },
    })

    render(<ResultsPage search={storedSearch} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Back to Ask' }))
    expect(navigateSpy).toHaveBeenCalledWith({ to: '/ask' })
  })

  it('opens an earlier run from the analysis history', async () => {
    idleLiveRun()
    storedReads.storedAnalysis = storedRecord({
      explain_results: { success: true },
    })
    storedReads.analysisHistory = {
      hash: 'stored-hash',
      analyses: [
        {
          analysis_id: 'an-7',
          created_at: ago(2 * HOUR),
          target: 'prod',
          overall_rating: 'fair',
          efficiency_score: 65,
        },
        {
          analysis_id: 'an-1',
          created_at: ago(72 * HOUR),
          target: 'prod',
          overall_rating: 'poor',
          efficiency_score: 41,
        },
      ],
    }

    render(<ResultsPage search={storedSearch} />)

    fireEvent.keyDown(
      await screen.findByRole('button', { name: 'Analysis history' }),
      { key: 'Enter' }
    )
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /3 days ago · Poor · 41/ })
    )
    expect(navigateSpy).toHaveBeenCalledWith({
      to: '/results',
      search: expect.objectContaining({ analysisId: 'an-1' }),
    })
  })
})
