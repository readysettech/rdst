import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResultsSearch } from '../features/queries/results/types'
import type { CompleteEvent } from '../lib/api'
import { setFixtureOverride } from '../lib/monitor/contract'
import { CA_KEYS, CA_TARGET, caFixtures } from '../lib/monitor/fixturesCa'
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

vi.mock('../lib/analytics', () => ({ trackEvent: vi.fn() }))

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
      <a href={to}>{children}</a>
    ),
  }
})

// Reads the page makes, keyed the way the hooks key them: the stored record, the
// monitor row and its digest.
const reads = vi.hoisted(() => ({
  storedAnalysis: undefined as unknown,
  analysisHistory: undefined as unknown,
  query: undefined as unknown,
  digest: undefined as unknown,
  keys: [] as (readonly unknown[])[],
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
  useQuery: ({ queryKey }: { queryKey?: readonly unknown[] } = {}) => {
    if (queryKey) reads.keys.push(queryKey)
    const data =
      queryKey?.[0] === 'storedAnalysis'
        ? reads.storedAnalysis
        : queryKey?.[0] === 'analysisHistory'
          ? reads.analysisHistory
          : queryKey?.[2] === 'query-analysis'
            ? reads.digest
            : queryKey?.[2] === 'query'
              ? reads.query
              : undefined
    return {
      data,
      isLoading: false,
      isPending: false,
      isFetching: false,
      error: null,
    }
  },
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

vi.mock('../lib/sse', () => ({ useAnalyze: vi.fn() }))
vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: vi.fn(),
}))
vi.mock('../lib/useTargetConnectivityGate', () => ({
  useTargetConnectivityGate: () => connectivity,
}))
vi.mock('../components/TargetLockNotice', () => ({
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}))
vi.mock('../components/TargetConnectivityNotice', () => ({
  TargetConnectivityNotice: () => null,
}))
vi.mock('../components/top', () => ({
  ParameterDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div>parameter-dialog</div> : null,
  hasParameters: hasParametersSpy,
}))

type LiveRun = ReturnType<typeof useAnalyze>

function liveRun(overrides: Partial<LiveRun> = {}): LiveRun {
  return {
    analyze: vi.fn(),
    state: 'idle',
    progress: undefined,
    results: undefined,
    rewriteTesting: undefined,
    readysetCacheability: undefined,
    error: undefined,
    errorEnvelope: undefined,
    reset: vi.fn(),
    ...overrides,
  }
}

function unlocked() {
  vi.mocked(useTargetPasswordLock).mockReturnValue({
    isResolved: true,
    isLocked: false,
    targetName: 'prod',
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  })
}

const set = caFixtures('ca-database')
const KEY = CA_KEYS.postsByOwneruserid
const monitoredSearch: ResultsSearch = {
  query:
    'select id, title from posts where owneruserid = $1 order by creationdate desc limit $2',
  target: CA_TARGET,
  hash: KEY,
  analysisId: `ca_${KEY}`,
}

function monitored() {
  setFixtureOverride('ca-database')
  reads.query = set.query(CA_TARGET, KEY)
  reads.digest = set.queryAnalysis(CA_TARGET, KEY)
  window.localStorage.setItem('rdst.explain-analyze-consent', 'accepted')
}

beforeEach(() => {
  window.localStorage.clear()
  navigateSpy.mockClear()
  reads.storedAnalysis = undefined
  reads.analysisHistory = undefined
  reads.query = undefined
  reads.digest = undefined
  reads.keys = []
  hasParametersSpy.mockReturnValue(false)
  connectivity.failure = null
  connectivity.isChecking = false
  connectivity.ensureReachable.mockReset()
  connectivity.ensureReachable.mockResolvedValue(true)
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } })
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

describe('the full analysis of a monitored query', () => {
  it('reads the digest into the crumb bar, the hero and the cards', async () => {
    monitored()
    vi.mocked(useAnalyze).mockReturnValue(liveRun())
    unlocked()
    render(<ResultsPage search={monitoredSearch} />)

    const bar = screen.getByRole('banner')
    expect(within(bar).getByText('Full analysis')).toBeTruthy()
    expect(within(bar).getByRole('link', { name: 'demo' })).toBeTruthy()
    expect(within(bar).getByRole('link', { name: 'Queries' })).toBeTruthy()
    expect(within(bar).getByRole('button', { name: 'Copy fix' })).toBeTruthy()
    expect(
      within(bar).getByRole('button', { name: 'Analyze again' })
    ).toBeTruthy()
    expect(within(bar).getByRole('button', { name: 'Benchmark' })).toBeTruthy()
    expect(
      within(bar).getByRole('button', { name: 'Send to Ask' })
    ).toBeTruthy()

    const hero = screen.getByTestId('analysis-hero')
    expect(
      within(hero).getByRole('img', { name: 'Plan score 72 out of 100' })
    ).toBeTruthy()
    expect(within(hero).getByText('Plan score')).toBeTruthy()
    expect(
      within(hero).getByRole('heading', {
        name: 'SELECT on posts by owneruserid',
      })
    ).toBeTruthy()
    expect(within(hero).getByText('Estimated')).toBeTruthy()
    expect(within(hero).getByText('sampled from table data')).toBeTruthy()
    expect(within(hero).getByText('analyzed 9 min ago')).toBeTruthy()
    expect(within(hero).getByText('17% of database time')).toBeTruthy()
    expect(
      within(hero).getByText('One index removes the scan and the sort.')
    ).toBeTruthy()
    expect(within(hero).getByText('245,290')).toBeTruthy()
    expect(within(hero).getByText('to return 583')).toBeTruthy()
    expect(within(hero).getByText('107,916')).toBeTruthy()
    expect(within(hero).getByText('one seq scan')).toBeTruthy()
    expect(within(hero).getByText('174 calls today')).toBeTruthy()

    const fix = screen.getByTestId('analysis-fix')
    expect(within(fix).getByText('one index, both bottlenecks')).toBeTruthy()
    expect(
      within(fix).getByText(/idx_posts_owneruserid_creationdate/)
    ).toBeTruthy()
    expect(
      within(fix).getByText('Planner uses it: cost 107,916 to 41')
    ).toBeTruthy()
    expect(
      within(fix).getByText('About 9 MB, one more index on writes')
    ).toBeTruthy()
    expect(screen.queryByTestId('analysis-benchmark')).toBeNull()

    const plan = screen.getByTestId('analysis-plan')
    expect(within(plan).getByText('3 findings')).toBeTruthy()
    expect(within(plan).getByText('Sequential scan on posts')).toBeTruthy()
    expect(within(plan).getByText('245,290 rows')).toBeTruthy()

    const rewrites = screen.getByTestId('analysis-rewrites')
    expect(within(rewrites).getByText('20 runs each on demo')).toBeTruthy()
    expect(within(rewrites).getByText('65.4 to 64.9 ms')).toBeTruthy()
    expect(within(rewrites).getAllByText('No change')).toHaveLength(2)

    expect(
      within(screen.getByTestId('analysis-also')).getByText(
        'Select only id and title'
      )
    ).toBeTruthy()
    expect(
      within(screen.getByTestId('analysis-readyset')).getByText(
        'No blockers. Not verified yet.'
      )
    ).toBeTruthy()

    const query = screen.getByTestId('analysis-query')
    expect(within(query).getByText('$1 = 1876')).toBeTruthy()
    expect(within(query).getByText('sampled from table data')).toBeTruthy()
    fireEvent.click(within(query).getByRole('button', { name: 'EXPLAIN plan' }))
    expect(within(query).getByText(/Seq Scan on posts/)).toBeTruthy()

    expect(screen.getByText('Claude Sonnet 4.6')).toBeTruthy()
    expect(screen.getByText('8,028 tokens')).toBeTruthy()
    expect(screen.getByText('Analysis #412')).toBeTruthy()

    fireEvent.click(within(bar).getByRole('button', { name: 'Copy fix' }))
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining('CREATE INDEX CONCURRENTLY')
      )
    )
    fireEvent.click(within(bar).getByRole('button', { name: 'Send to Ask' }))
    expect(navigateSpy).toHaveBeenCalledWith({
      to: '/ask',
      search: { db: 'demo', sql: monitoredSearch.query },
    })
  })

  it('opens from the key alone, reading the SQL from the monitored row', () => {
    monitored()
    vi.mocked(useAnalyze).mockReturnValue(liveRun())
    unlocked()
    render(
      <ResultsPage
        search={{
          query: '',
          target: CA_TARGET,
          hash: KEY,
          monitorFixture: 'ca-database',
        }}
      />
    )
    expect(
      screen.getByRole('heading', { name: 'SELECT on posts by owneruserid' })
    ).toBeTruthy()
    expect(screen.getByText('Analysis #412')).toBeTruthy()
    expect(
      within(screen.getByTestId('analysis-query')).getByText(
        /owneruserid = \$1/
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Analyze again' }))
    expect(navigateSpy).toHaveBeenCalledWith({
      to: '/results',
      search: expect.objectContaining({ hash: KEY, analysisId: undefined }),
    })
    expect(
      (navigateSpy.mock.calls[0][0] as { search: ResultsSearch }).search.query
    ).toMatch(/^select id, title from posts/)
  })

  it('reads the monitored row by its key when the registry hash differs', () => {
    monitored()
    vi.mocked(useAnalyze).mockReturnValue(liveRun())
    unlocked()
    render(
      <ResultsPage
        search={{ ...monitoredSearch, hash: 'registryhash', key: KEY }}
      />
    )
    const monitorReads = reads.keys.filter(
      (key) => key[2] === 'query' || key[2] === 'query-analysis'
    )
    expect(monitorReads.length).toBeGreaterThan(0)
    expect(monitorReads.every((key) => key.includes(KEY))).toBe(true)
    expect(reads.keys.some((key) => key.includes(monitoredSearch.query))).toBe(
      false
    )
    expect(reads.keys.find((key) => key[0] === 'storedAnalysis')?.[1]).toBe(
      'registryhash'
    )
  })

  it('shows the benchmark card after a benchmark ran', () => {
    monitored()
    const digest = set.queryAnalysis(CA_TARGET, KEY)
    reads.digest = {
      ...digest,
      benchmark: {
        before_ms: 65.4,
        after_ms: 1.9,
        change: 34,
        method: '20 runs each on demo, 2 min ago',
      },
    }
    vi.mocked(useAnalyze).mockReturnValue(liveRun())
    unlocked()
    render(<ResultsPage search={monitoredSearch} />)
    const benchmark = screen.getByTestId('analysis-benchmark')
    expect(
      within(benchmark).getByText('20 runs each on demo, 2 min ago')
    ).toBeTruthy()
    expect(within(benchmark).getByText('65.4')).toBeTruthy()
    expect(within(benchmark).getByText('1.9')).toBeTruthy()
    expect(within(benchmark).getByText('34x')).toBeTruthy()
    expect(
      within(benchmark).getByRole('button', { name: 'Rerun' })
    ).toBeTruthy()
  })

  it('while analyzing again, dims the cards under the ring and the steps', () => {
    monitored()
    vi.mocked(useAnalyze).mockReturnValue(
      liveRun({
        state: 'analyzing',
        progress: {
          type: 'progress',
          stage: 'testing_rewrites',
          percent: 60,
          message: '',
        },
      })
    )
    unlocked()
    render(
      <ResultsPage search={{ ...monitoredSearch, analysisId: undefined }} />
    )
    const bar = screen.getByRole('banner')
    expect(within(bar).getByRole('button', { name: 'Cancel' })).toBeTruthy()
    expect(within(bar).queryByRole('button', { name: 'Copy fix' })).toBeNull()
    expect(screen.getByRole('img', { name: 'Analyzing' })).toBeTruthy()
    const steps = screen.getByRole('list', { name: 'Analysis steps' })
    expect(
      within(steps)
        .getAllByRole('listitem')
        .map((item) => item.getAttribute('data-state'))
    ).toEqual(['done', 'done', 'now', 'todo'])
    expect(screen.getByTestId('analysis-cards').getAttribute('aria-busy')).toBe(
      'true'
    )
    expect(screen.getByText(/started \d/)).toBeTruthy()
  })
})

describe('ResultsPage analyze consent', () => {
  it('does not restart an analysis that has already completed', async () => {
    const results: CompleteEvent = {
      type: 'complete',
      success: true,
      query_hash: 'complete-query',
    }
    const run = liveRun({ state: 'complete', results })
    vi.mocked(useAnalyze).mockReturnValue(run)
    unlocked()
    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )
    await waitFor(() => {
      expect(screen.queryByText('Run EXPLAIN ANALYZE?')).toBeNull()
    })
    expect(run.analyze).not.toHaveBeenCalled()
  })

  it('requires consent before the first run and remembers the opt-out', async () => {
    const run = liveRun()
    vi.mocked(useAnalyze).mockReturnValue(run)
    unlocked()
    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )
    expect(await screen.findByText('Run EXPLAIN ANALYZE?')).toBeTruthy()
    expect(run.analyze).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(run.analyze).not.toHaveBeenCalled()
    expect(navigateSpy).toHaveBeenCalledWith({
      to: '/database',
      search: { target: 'prod' },
    })

    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )
    expect(await screen.findByText('Run EXPLAIN ANALYZE?')).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again" }))
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() =>
      expect(run.analyze).toHaveBeenCalledWith(
        { query: 'select 1', target: 'prod', fast: false },
        { queryHash: undefined, queryLabel: 'Select' }
      )
    )
    expect(window.localStorage.getItem('rdst.explain-analyze-consent')).toBe(
      'accepted'
    )
  })

  it('does not auto-run analyze when target is password-locked', async () => {
    const run = liveRun()
    vi.mocked(useAnalyze).mockReturnValue(run)
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isResolved: true,
      isLocked: true,
      targetName: 'prod',
      message: 'Unlock prod to analyze.',
      missingTargetRequirements: [],
      keyringAvailable: false,
    })
    render(
      <ResultsPage
        search={{ query: 'select 1', target: 'prod', fast: false }}
      />
    )
    expect(screen.getByText('Unlock prod to analyze.')).toBeTruthy()
    await waitFor(() => expect(run.analyze).not.toHaveBeenCalled())
  })
})

describe('a stored analysis', () => {
  it('renders the record through the same cards and starts no run', async () => {
    const run = liveRun()
    vi.mocked(useAnalyze).mockReturnValue(run)
    unlocked()
    reads.storedAnalysis = {
      hash: 'stored-hash',
      analysis_id: 'an-7',
      created_at: new Date(Date.now() - 7_200_000).toISOString(),
      target: 'prod',
      overall_rating: 'fair',
      efficiency_score: 65,
      analysis: {
        display_payload: {
          explain_results: {
            success: true,
            database_engine: 'postgresql',
            plan_format: 'explain',
            execution_time_ms: 0,
            rows_examined: 81_169,
            rows_returned: 6,
            cost_estimate: 33_384,
            explain_plan: { Plan: { 'Node Type': 'Seq Scan' } },
          },
          llm_analysis: {
            success: true,
            performance_assessment: {
              overall_rating: 'fair',
              efficiency_score: 65,
              primary_concerns: ['Scans more rows than it returns.'],
            },
            index_recommendations: [
              {
                table: 'orders',
                columns: ['customer_id'],
                index_type: 'btree',
                sql: 'CREATE INDEX ON orders (customer_id)',
                rationale: 'Serves the filter from one index.',
                estimated_impact: 'high',
              },
            ],
          },
        },
      },
    }
    render(
      <ResultsPage
        search={{
          query: 'select 1',
          target: 'prod',
          origin: 'ask',
          hash: 'stored-hash',
          analysisId: 'an-7',
        }}
      />
    )
    expect(
      await screen.findByRole('img', { name: 'Plan score 65 out of 100' })
    ).toBeTruthy()
    expect(
      within(screen.getByTestId('analysis-fix')).getByText(
        'Serves the filter from one index.'
      )
    ).toBeTruthy()
    expect(screen.getByText('81,169')).toBeTruthy()
    expect(screen.getByText('to return 6')).toBeTruthy()
    expect(
      within(screen.getByTestId('analysis-plan')).getByText(
        'Scans more rows than it returns.'
      )
    ).toBeTruthy()
    expect(run.analyze).not.toHaveBeenCalled()
    expect(screen.queryByText('Run EXPLAIN ANALYZE?')).toBeNull()
  })
})

describe('/results no-query guard (B1)', () => {
  it('validateSearch never throws; it parses a missing query to empty', () => {
    const validateSearch = Route.options.validateSearch as (
      search: Record<string, unknown>
    ) => ResultsSearch
    expect(validateSearch({})).toEqual({
      query: '',
      target: undefined,
      fast: false,
      params: undefined,
      origin: undefined,
      hash: undefined,
      key: undefined,
      analysisId: undefined,
      monitorFixture: undefined,
    })
    expect(
      validateSearch({ hash: 'k', monitorFixture: 'ca-database' })
        .monitorFixture
    ).toBe('ca-database')
  })

  it('validateSearch accepts a known origin and drops an unknown one', () => {
    const validateSearch = Route.options.validateSearch as (
      search: Record<string, unknown>
    ) => ResultsSearch
    expect(validateSearch({ query: 'select 1', origin: 'ask' }).origin).toBe(
      'ask'
    )
    expect(
      validateSearch({ query: 'select 1', origin: 'elsewhere' }).origin
    ).toBeUndefined()
  })

  it('beforeLoad redirects to Database when the query is missing', () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: ResultsSearch
    }) => void
    expect(() =>
      beforeLoad({ search: { query: '' } as ResultsSearch })
    ).toThrow()
  })

  it('beforeLoad allows a present query or a monitored key through (no redirect)', () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: ResultsSearch
    }) => void
    expect(() =>
      beforeLoad({ search: { query: 'select 1' } as ResultsSearch })
    ).not.toThrow()
    expect(() =>
      beforeLoad({ search: { query: '', hash: 'k' } as ResultsSearch })
    ).not.toThrow()
    expect(() =>
      beforeLoad({ search: { query: '', key: 'k' } as ResultsSearch })
    ).not.toThrow()
  })
})
