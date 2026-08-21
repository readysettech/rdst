import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  CacheCompareRunResult,
  CacheCompareSample,
} from '../../../types/cache'
import { ComparePage } from './ComparePage'
import type { CompareBatchSnapshot, CompareQueryOutcome } from './compareRuns'
import { useCompareController } from './useCompareController'

vi.mock('./useCompareController', () => ({
  MAX_COMPARE_CONCURRENCY: 4,
  MAX_COMPARE_QUERIES: 4,
  MIN_COMPARE_CONCURRENCY: 2,
  initialCompareConcurrency: (queryCount: number) =>
    Math.min(4, Math.max(2, queryCount)),
  useCompareController: vi.fn(),
}))

vi.mock('@tanstack/react-router', async () => ({
  Link: (await import('@/test-utils')).LinkStub,
}))

const RESULT: CacheCompareRunResult = {
  success: true,
  query: 'SELECT 1',
  duration_seconds: 30,
  elapsed_seconds: 30,
  concurrency: 4,
  origin: {
    scheduled: 3000,
    completed: 3000,
    errors: 0,
    dropped: 0,
    throughput_rps: 100,
    error_rate: 0,
    mean_ms: 20,
    p50_ms: 18,
    p95_ms: 24,
    p99_ms: 25,
  },
  readyset: {
    scheduled: 15600,
    completed: 15600,
    errors: 0,
    dropped: 0,
    throughput_rps: 520,
    error_rate: 0,
    mean_ms: 2,
    p50_ms: 1.8,
    p95_ms: 2.4,
    p99_ms: 2.5,
  },
  timeline: [],
  phases: [{ elapsed_seconds: 0, concurrency: 4 }],
  speedup_mean: 10,
  improvement_pct: 900,
  winner: 'readyset',
}

function outcome(
  cacheId: string,
  label: string,
  status: CompareQueryOutcome['status'],
  overrides: Partial<CompareQueryOutcome> = {}
): CompareQueryOutcome {
  return {
    cacheId,
    label,
    runId: `run-${cacheId}`,
    status,
    timeline: [],
    elapsedSeconds: 0,
    ...overrides,
  }
}

function snapshot(overrides: Partial<CompareBatchSnapshot> = {}) {
  const queryOutcomes = overrides.queryOutcomes ?? []
  const countOf = (status: CompareQueryOutcome['status']) =>
    queryOutcomes.filter((entry) => entry.status === status).length
  return {
    status: 'running',
    completed: countOf('succeeded') + countOf('failed') + countOf('cancelled'),
    total: queryOutcomes.length,
    succeeded: countOf('succeeded'),
    failed: countOf('failed'),
    cancelled: countOf('cancelled'),
    running: countOf('running'),
    queued: countOf('queued'),
    percent: 0,
    runs: [],
    results: queryOutcomes.flatMap((entry) =>
      entry.result
        ? [
            {
              cacheId: entry.cacheId,
              label: entry.label,
              runId: entry.runId,
              result: entry.result,
            },
          ]
        : []
    ),
    ...overrides,
    queryOutcomes,
  } satisfies CompareBatchSnapshot
}

function batch(queries: Array<{ cacheId: string; label: string }>) {
  return {
    id: 'compare-1',
    target: 'demo',
    createdAt: '2026-07-23T10:00:00.000Z',
    concurrency: 4,
    durationSeconds: 30,
    queries: queries.map((query) => ({
      ...query,
      runId: `run-${query.cacheId}`,
    })),
  }
}

function controller(overrides: Record<string, unknown> = {}) {
  return {
    target: 'demo',
    passwordLock: {
      isResolved: true,
      isLocked: false,
      targetName: 'demo',
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    },
    connectivity: {
      failure: null,
      isChecking: false,
      ensureReachable: vi.fn().mockResolvedValue(true),
      reset: vi.fn(),
    },
    statusQuery: {
      data: { docker_installed: true, docker_running: true },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    },
    registry: {
      queries: [],
      isLoading: false,
      listError: null,
      refetch: vi.fn(),
    },
    queries: [],
    selectedIds: [],
    selectedWithParams: [],
    toggleQuery: vi.fn(),
    selectAll: vi.fn(),
    paramValues: {},
    parameterSources: {},
    updateParameter: vi.fn(),
    suggestingParameters: false,
    suggestionMessage: null,
    suggestionSchemaUnavailable: false,
    suggestParameterValues: vi.fn(),
    parameterCount: 0,
    missingParameterCount: 0,
    concurrency: 2,
    durationSeconds: 30,
    updatingLoad: false,
    reviewOpen: false,
    setReviewOpen: vi.fn(),
    historyOpen: false,
    setHistoryOpen: vi.fn(),
    historyEntries: [],
    openHistoryBatch: vi.fn(),
    deleteHistoryBatch: vi.fn(),
    canReview: false,
    starting: false,
    startComparison: vi.fn(),
    batch: null,
    snapshot: null,
    selectedOutcome: null,
    followingLive: true,
    pinQuery: vi.fn(),
    cancelComparison: vi.fn(),
    clearBatch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useCompareController>
}

function renderPage(
  overrides: { onFindQueries?: () => void; onOpenQueries?: () => void } = {}
) {
  return render(
    <ComparePage
      onFindQueries={overrides.onFindQueries ?? vi.fn()}
      onOpenQueries={overrides.onOpenQueries ?? vi.fn()}
    />
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ComparePage state matrix', () => {
  it('keeps status failures distinct from unavailable Docker', () => {
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        statusQuery: {
          data: undefined,
          isLoading: false,
          isError: true,
          error: new Error('status unavailable'),
          refetch: vi.fn(),
        },
      })
    )
    renderPage()

    expect(screen.getByText("Cache status couldn't be checked")).toBeTruthy()
    expect(screen.queryByText('Docker is required for comparisons')).toBeNull()
  })

  it('keeps unavailable Docker in the unified comparison workspace', () => {
    const refetch = vi.fn()
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        statusQuery: {
          data: { docker_installed: true, docker_running: false },
          isLoading: false,
          isError: false,
          error: null,
          refetch,
        },
      })
    )
    renderPage()

    expect(screen.getByText('Docker is required for comparisons')).toBeTruthy()
    screen.getByRole('button', { name: 'Check again' }).click()
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('does not present a registry failure as an empty query list', () => {
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        registry: {
          queries: [],
          isLoading: false,
          listError: 'registry unavailable',
          refetch: vi.fn(),
        },
      })
    )
    renderPage()

    expect(screen.getByText("Queries couldn't be loaded")).toBeTruthy()
    expect(screen.queryByText('No queries to compare')).toBeNull()
  })

  it('routes an empty query list to query discovery', () => {
    vi.mocked(useCompareController).mockReturnValue(controller())
    renderPage()

    expect(screen.getByText('No queries to compare')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Find queries' })).toBeTruthy()
  })

  it('keeps an unverified registry query available for comparison', () => {
    const query = {
      hash: 'system-query',
      sql: 'SELECT * FROM pg_catalog.pg_description',
      readyset_supported: '',
    }
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        registry: {
          queries: [query],
          isLoading: false,
          listError: null,
          refetch: vi.fn(),
        },
        queries: [query],
      })
    )
    renderPage()

    expect(screen.getByText('Compare cache performance')).toBeTruthy()
    expect(screen.getByTitle(query.sql)).toBeTruthy()
  })

  it('annotates prior not-cacheable evidence without gating selection', () => {
    const queries = [
      {
        hash: 'blocked',
        tag: 'Complex report',
        sql: 'SELECT DISTINCT ON (id) * FROM reports',
        readyset_supported: 'unsupported: unsupported query',
        readyset_last_observed_at: new Date(
          Date.now() - 5 * 60_000
        ).toISOString(),
      },
      {
        hash: 'unknown',
        tag: 'Top posts',
        sql: 'SELECT * FROM posts',
        readyset_supported: '',
      },
    ]
    vi.mocked(useCompareController).mockReturnValue(controller({ queries }))
    renderPage()

    expect(screen.getByText('Last check: not cacheable (5m ago)')).toBeTruthy()
    expect(screen.getAllByText(/Last check/)).toHaveLength(1)
    const selectionControls = screen
      .getAllByRole('button')
      .filter((button) => button.hasAttribute('aria-pressed'))
    expect(selectionControls).toHaveLength(2)
    expect(
      selectionControls.every(
        (control) => control.getAttribute('aria-disabled') !== 'true'
      )
    ).toBe(true)
  })

  it('reveals the full SQL beside the parameter inputs of a selected query', () => {
    const query = {
      hash: 'one',
      tag: 'Users by status',
      sql: 'SELECT id, email\nFROM users\nWHERE status = :status',
    }
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        queries: [query],
        selectedIds: ['one'],
        selectedWithParams: [
          {
            entry: query,
            parameters: [{ placeholder: ':status', index: 1, type: 'named' }],
          },
        ],
        parameterCount: 1,
        missingParameterCount: 1,
      })
    )
    renderPage()

    // getByTitle collapses attribute whitespace before matching.
    const code = screen.getByTitle(
      'SELECT id, email FROM users WHERE status = :status'
    )
    expect(code.textContent).toContain('\nFROM users')
    expect(code.textContent).toContain('WHERE status = :status')
    expect(code.className).not.toContain('truncate')
    expect(
      screen.getByPlaceholderText('Enter a representative value')
    ).toBeTruthy()
  })

  it('counts parameters from normalized SQL when original SQL contains literals', () => {
    const query = {
      hash: 'normalized-parameter',
      tag: 'User by id',
      sql: 'SELECT * FROM users WHERE id = :p1',
      original_sql: 'SELECT * FROM users WHERE id = 42',
    }
    vi.mocked(useCompareController).mockReturnValue(
      controller({ queries: [query] })
    )
    renderPage()

    expect(screen.getByText('1 parameter')).toBeTruthy()
  })

  it('places Suggest values under the Parameter readiness summary row', () => {
    const query = {
      hash: 'one',
      tag: 'Users by status',
      sql: 'SELECT id FROM users WHERE status = :status',
    }
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        queries: [query],
        selectedIds: ['one'],
        parameterCount: 1,
        missingParameterCount: 1,
      })
    )
    renderPage()

    const readiness = screen.getByText('Parameter readiness')
    const suggest = screen.getByRole('button', { name: 'Suggest values' })
    expect(
      readiness.compareDocumentPosition(suggest) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('points a schema-unavailable suggestion pass at the Schema page', () => {
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        queries: [
          { hash: 'one', tag: 'Top posts', sql: 'SELECT * FROM posts' },
        ],
        parameterCount: 1,
        suggestionMessage:
          'No schema evidence is available for this database. 1 parameter needs a value you provide.',
        suggestionSchemaUnavailable: true,
      })
    )
    renderPage()

    const initLink = screen.getByRole('link', {
      name: 'Initialize the semantic layer',
    })
    expect(initLink.getAttribute('href')).toBe('/schema')
  })

  it('renders the single-card setup when queries are available', () => {
    const queries = [
      {
        hash: 'one',
        tag: 'Top posts',
        sql: 'SELECT * FROM posts',
        readyset_supported: 'yes',
      },
    ]
    vi.mocked(useCompareController).mockReturnValue(controller({ queries }))
    renderPage()

    expect(screen.getByText('Compare cache performance')).toBeTruthy()
    expect(screen.getByText('Top posts')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run comparison' })).toBeTruthy()
    expect(screen.getByText('Safe · 2 → 4 clients per lane')).toBeTruthy()
    expect(screen.getByText('30 seconds')).toBeTruthy()
  })

  it('discloses that a multi-query batch is serialized before the run (USE-065)', () => {
    const queries = [
      { hash: 'one', tag: 'Top posts', sql: 'SELECT * FROM posts' },
      { hash: 'two', tag: 'Orders', sql: 'SELECT * FROM orders' },
      { hash: 'three', tag: 'Carts', sql: 'SELECT * FROM carts' },
      { hash: 'four', tag: 'Sessions', sql: 'SELECT * FROM sessions' },
    ]
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        queries,
        selectedIds: ['one', 'two', 'three', 'four'],
        canReview: true,
      })
    )
    renderPage()

    expect(
      screen.getByText('4 queries run one at a time · about 2 minutes')
    ).toBeTruthy()
    expect(screen.queryByText('30 seconds')).toBeNull()
  })

  it('keeps completed comparisons available in device-local history', () => {
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        historyOpen: true,
        historyEntries: [
          {
            batch: {
              id: 'compare-1',
              target: 'demo',
              createdAt: '2026-07-23T10:00:00.000Z',
              concurrency: 4,
              durationSeconds: 30,
              queries: [{ cacheId: 'one', label: 'Top posts', runId: 'run-1' }],
              outcome: {
                status: 'complete',
                completedAt: '2026-07-23T10:01:00.000Z',
                failed: 0,
                cancelled: 0,
              },
            },
            snapshot: {
              status: 'complete',
              completed: 1,
              total: 1,
              succeeded: 1,
              failed: 0,
              cancelled: 0,
              percent: 100,
              runs: [],
              timeline: [],
              results: [
                {
                  cacheId: 'one',
                  label: 'Top posts',
                  runId: 'run-1',
                  result: RESULT,
                },
              ],
            },
          },
        ],
      })
    )
    renderPage()

    expect(screen.getByText('Comparison history')).toBeTruthy()
    expect(
      screen.getByText(/1 query · 2 → 4 total clients per lane · 30s/)
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'View result' })).toBeTruthy()
  })

  it('shows a designed empty state instead of a blank history panel (C3)', () => {
    const clearBatch = vi.fn()
    const setHistoryOpen = vi.fn()
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        historyOpen: true,
        historyEntries: [],
        clearBatch,
        setHistoryOpen,
      })
    )
    renderPage()

    expect(screen.getByText('No comparisons yet')).toBeTruthy()
    const cta = screen.getByRole('button', { name: 'New comparison' })
    cta.click()
    expect(clearBatch).toHaveBeenCalledTimes(1)
    expect(setHistoryOpen).toHaveBeenCalledWith(false)
  })

  it('shows sandbox preparation explicitly with a stop action', () => {
    const pending = outcome('one', 'Top posts', 'running')
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([{ cacheId: 'one', label: 'Top posts' }]),
        snapshot: snapshot({ queryOutcomes: [pending], percent: 40 }),
        selectedOutcome: pending,
      })
    )
    renderPage()

    expect(screen.getByText('Preparing comparison')).toBeTruthy()
    expect(screen.getAllByText('Starting').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Stop comparison' })).toBeTruthy()
    expect(
      screen.getByText('This comparison continues if you leave the page.')
    ).toBeTruthy()
  })

  it('separates a completed comparison result from running and error states', () => {
    const measured = outcome('one', 'Top posts', 'succeeded', {
      result: RESULT,
    })
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([{ cacheId: 'one', label: 'Top posts' }]),
        snapshot: snapshot({
          status: 'complete',
          queryOutcomes: [measured],
          percent: 100,
        }),
        selectedOutcome: measured,
      })
    )
    renderPage()

    expect(screen.getByText('10× faster with Readyset')).toBeTruthy()
    // The rail persists past completion, so the headline keeps a visible
    // source instead of handing over to a separate breakdown card.
    expect(screen.getByText('Queries in this comparison')).toBeTruthy()
    expect(screen.getByText('1 of 1 done')).toBeTruthy()
    expect(screen.queryByText('Per-query breakdown')).toBeNull()
  })

  it('shows unsupported outcomes beside successful per-query measurements', () => {
    const measured = outcome('one', 'Top posts', 'succeeded', {
      result: RESULT,
    })
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([
          { cacheId: 'one', label: 'Top posts' },
          { cacheId: 'two', label: 'Complex report' },
        ]),
        snapshot: snapshot({
          status: 'partial',
          percent: 100,
          queryOutcomes: [
            measured,
            outcome('two', 'Complex report', 'failed', {
              message: 'This query uses an unsupported aggregate.',
              errorCode: 'unsupported_query',
            }),
          ],
        }),
        selectedOutcome: measured,
      })
    )
    renderPage()

    expect(screen.getByText('Complex report')).toBeTruthy()
    expect(
      screen.getByText('This query uses an unsupported aggregate.')
    ).toBeTruthy()
    expect(screen.getByText('Unsupported')).toBeTruthy()
  })

  it.each([
    ['partial', 'Completed with errors'],
    ['failed', 'Failed'],
    ['cancelled', 'Cancelled'],
  ] as const)('renders the %s terminal state explicitly', (status, label) => {
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([{ cacheId: 'one', label: 'Top posts' }]),
        snapshot: snapshot({
          status,
          percent: 100,
          queryOutcomes: [
            outcome(
              'one',
              'Top posts',
              status === 'cancelled' ? 'cancelled' : 'failed'
            ),
          ],
        }),
      })
    )
    renderPage()

    // The batch chrome and the query's own row both name the outcome.
    expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    expect(screen.getByText('No measurements completed')).toBeTruthy()
  })

  it('keeps persisted results visible if the current cache later stops', () => {
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        statusQuery: {
          data: { docker_installed: true, docker_running: false },
          isLoading: false,
          isError: false,
          error: null,
          refetch: vi.fn(),
        },
        batch: batch([{ cacheId: 'one', label: 'Top posts' }]),
        snapshot: snapshot({
          status: 'complete',
          percent: 100,
          queryOutcomes: [
            outcome('one', 'Top posts', 'succeeded', { result: RESULT }),
          ],
        }),
      })
    )
    renderPage()

    expect(screen.getByText('10× faster with Readyset')).toBeTruthy()
    expect(screen.queryByText('Docker is required for comparisons')).toBeNull()
  })
})

const SAMPLE: CacheCompareSample = {
  elapsed_seconds: 18,
  concurrency: 2,
  origin: {
    scheduled: 300,
    completed: 300,
    errors: 0,
    dropped: 0,
    throughput_rps: 142,
    error_rate: 0,
    mean_ms: 8,
    p50_ms: 7,
    p95_ms: 8.1,
    p99_ms: 9,
  },
  readyset: {
    scheduled: 900,
    completed: 900,
    errors: 0,
    dropped: 0,
    throughput_rps: 455,
    error_rate: 0,
    mean_ms: 1.8,
    p50_ms: 1.7,
    p95_ms: 1.9,
    p99_ms: 2,
  },
}

function fourQueryBatch() {
  const compared = outcome('one', 'orders_by_customer', 'succeeded', {
    result: { ...RESULT, speedup_mean: 3.2 },
    timeline: [SAMPLE],
    elapsedSeconds: 30,
  })
  const notComparable = outcome('two', 'recent_sessions', 'failed', {
    message:
      'This query uses LIMIT without ORDER BY, so the two results are not comparable.',
  })
  const measuring = outcome('three', 'cart_totals', 'running', {
    timeline: [SAMPLE],
    elapsedSeconds: 18,
    percent: 60,
  })
  const waiting = outcome('four', 'inventory_rollup', 'queued')
  return { compared, notComparable, measuring, waiting }
}

describe('Compare run rail', () => {
  it('gives every query in the batch its own row and state', () => {
    const { compared, notComparable, measuring, waiting } = fourQueryBatch()
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([
          { cacheId: 'one', label: 'orders_by_customer' },
          { cacheId: 'two', label: 'recent_sessions' },
          { cacheId: 'three', label: 'cart_totals' },
          { cacheId: 'four', label: 'inventory_rollup' },
        ]),
        snapshot: snapshot({
          queryOutcomes: [compared, notComparable, measuring, waiting],
        }),
        selectedOutcome: measuring,
      })
    )
    renderPage()

    // Once visibly above the rail, once in the live region that announces it.
    expect(
      screen.getAllByText(/2 of 4 done · 1 running · 1 queued/)
    ).toHaveLength(2)
    expect(screen.getByText('3.2× faster')).toBeTruthy()
    expect(screen.getByText('Not comparable')).toBeTruthy()
    expect(screen.getByText('Running · 18s of 30s · 12s left')).toBeTruthy()
    expect(screen.getByText('Queued for the Readyset sandbox')).toBeTruthy()
  })

  it('never claims the batch is finished while a query is still queued (D1)', () => {
    const { compared, measuring, waiting } = fourQueryBatch()
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([
          { cacheId: 'one', label: 'orders_by_customer' },
          { cacheId: 'three', label: 'cart_totals' },
          { cacheId: 'four', label: 'inventory_rollup' },
        ]),
        snapshot: snapshot({ queryOutcomes: [compared, measuring, waiting] }),
        selectedOutcome: measuring,
      })
    )
    renderPage()

    // The only percentage on screen belongs to the query being measured.
    expect(screen.queryByText('100%')).toBeNull()
    expect(screen.getByText('60%')).toBeTruthy()
    expect(screen.getByText(/~42s left/)).toBeTruthy()
  })

  it('binds the chart to one query and names it in the title', () => {
    const { compared, measuring, waiting } = fourQueryBatch()
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([
          { cacheId: 'one', label: 'orders_by_customer' },
          { cacheId: 'three', label: 'cart_totals' },
          { cacheId: 'four', label: 'inventory_rollup' },
        ]),
        snapshot: snapshot({ queryOutcomes: [compared, measuring, waiting] }),
        selectedOutcome: measuring,
      })
    )
    renderPage()

    expect(screen.getByText('Live comparison · cart_totals')).toBeTruthy()
    expect(screen.getByText('Following live')).toBeTruthy()
  })

  it('pins the chart to the query whose row was clicked', () => {
    const { compared, measuring, waiting } = fourQueryBatch()
    const pinQuery = vi.fn()
    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([
          { cacheId: 'one', label: 'orders_by_customer' },
          { cacheId: 'three', label: 'cart_totals' },
          { cacheId: 'four', label: 'inventory_rollup' },
        ]),
        snapshot: snapshot({ queryOutcomes: [compared, measuring, waiting] }),
        selectedOutcome: compared,
        followingLive: false,
      })
    )
    renderPage()

    // The pinned row is marked twice over: an explicit chip and aria-current.
    const pinned = screen.getByRole('button', {
      name: 'Show orders_by_customer in the comparison chart',
    })
    expect(pinned.getAttribute('aria-current')).toBe('true')
    expect(screen.getByText('Showing')).toBeTruthy()
    expect(
      screen.getByText('Live comparison · orders_by_customer')
    ).toBeTruthy()
    expect(screen.queryByText('Following live')).toBeNull()

    vi.mocked(useCompareController).mockReturnValue(
      controller({
        batch: batch([{ cacheId: 'three', label: 'cart_totals' }]),
        snapshot: snapshot({ queryOutcomes: [measuring] }),
        selectedOutcome: measuring,
        pinQuery,
      })
    )
    cleanup()
    renderPage()
    screen
      .getByRole('button', { name: 'Show cart_totals in the comparison chart' })
      .click()
    expect(pinQuery).toHaveBeenCalledWith('three')
  })
})
