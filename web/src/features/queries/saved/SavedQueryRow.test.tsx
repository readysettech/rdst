import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  __resetAnalysisRunsForTests,
  resetAnalysisRun,
  startAnalysisRun,
} from '../../../lib/analysisRuns'
import {
  __resetBackgroundRunsForTests,
  type BackgroundRunState,
} from '../../../lib/backgroundRuns'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import type { CacheRunResult } from '../../../types/cache'
import { SavedQueryRow } from './SavedQueryRow'
import type { SavedQueriesController } from './useSavedQueriesController'

afterEach(cleanup)

function entry(
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    hash: 'abc1234567890',
    sql: 'SELECT * FROM users WHERE id = 1',
    tag: '',
    source: 'top',
    target: 'demo',
    frequency: 3,
    last_analyzed: '',
    observation_count: 100,
    avg_duration_ms: 12,
    ...overrides,
  }
}

function makeState() {
  return {
    highlightedHash: null,
    confirmingHash: null,
    editingHash: null,
    editingSqlHash: null,
    tagDraft: '',
    sqlDraft: '',
  } as unknown as SavedQueriesController['rowState']
}

function makeActions() {
  return {
    isCached: () => false,
    cacheRunFor: () => undefined,
    analyze: vi.fn(),
    cacheQuery: vi.fn(),
    runTest: vi.fn(),
    startEditSql: vi.fn(),
    startRename: vi.fn(),
    markReviewed: vi.fn(),
    confirmDelete: vi.fn(),
    deleteQuery: vi.fn(),
    cancelDelete: vi.fn(),
    cancelRename: vi.fn(),
    cancelEditSql: vi.fn(),
    saveSql: vi.fn(),
    setTagDraft: vi.fn(),
    setSqlDraft: vi.fn(),
    updateSqlPending: false,
    dismissRun: vi.fn(),
    acknowledgeRun: vi.fn(),
    toggleStar: vi.fn(),
    openOverview: vi.fn(),
  } as unknown as SavedQueriesController['rowActions']
}

function stubLatestAnalysis(
  analysis: {
    analysis_id?: string
    analyzed_at?: string
    overall_rating?: string
    efficiency_score?: number | null
  } | null
) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () =>
      analysis
        ? {
            found: true,
            analysis: {
              analysis_id: analysis.analysis_id ?? 'an-1',
              analyzed_at: analysis.analyzed_at ?? '2026-08-21T06:00:00Z',
              target: 'demo',
              overall_rating: analysis.overall_rating ?? 'good',
              efficiency_score: analysis.efficiency_score ?? 82,
            },
          }
        : { found: false, analysis: null },
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/**
 * Hold the analyze stream open so the card keeps reporting a live run: the
 * stored-analysis read still answers, the measurement never settles.
 */
function stubAnalysisInFlight() {
  const latest = stubLatestAnalysis(null)
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes('/api/analyze')
        ? new Promise<Response>(() => {})
        : latest(input, init)
    )
  )
}

const twoHoursAgo = () => new Date(Date.now() - 2 * 3_600_000).toISOString()

const analyzedEntry = () =>
  entry({
    last_analyzed_at: twoHoursAgo(),
    most_recent_params: { p1: '7' },
  })

describe('SavedQueryRow stored analysis (A2/A3)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows the outcome and when it was measured, without hovering (B-11)', async () => {
    stubLatestAnalysis({
      analyzed_at: twoHoursAgo(),
      overall_rating: 'good',
      efficiency_score: 82,
    })

    renderWithClient(
      <SavedQueryRow
        entry={analyzedEntry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    expect(
      await screen.findByText('Deep analysis · Good · 82/100')
    ).toBeTruthy()
    expect(screen.getByText('Analyzed 2 hours ago')).toBeTruthy()
  })

  it('carries the outcome tone on the card itself (B-11)', async () => {
    stubLatestAnalysis({
      analyzed_at: twoHoursAgo(),
      overall_rating: 'poor',
      efficiency_score: 32,
    })

    renderWithClient(
      <SavedQueryRow
        entry={analyzedEntry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    await screen.findByText('Deep analysis · Poor · 32/100')
    expect(screen.getByTestId('query-registry-row').className).toContain(
      'border-l-border-negative-soft'
    )
  })

  it('leaves the footer outcome out entirely when nothing was analyzed', async () => {
    const fetchMock = stubLatestAnalysis(null)

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    expect(screen.queryByText(/Analyzed/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('turns the row action into View analysis and opens the stored record', async () => {
    stubLatestAnalysis({ analysis_id: 'an-7' })
    const actions = makeActions()

    renderWithClient(
      <SavedQueryRow
        entry={analyzedEntry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    fireEvent.click(
      await screen.findByRole('button', { name: 'View analysis' })
    )
    expect(screen.queryByRole('button', { name: 'Analyze' })).toBeNull()
    expect(actions.analyze).toHaveBeenCalledWith(
      'SELECT * FROM users WHERE id = 1',
      'demo',
      { p1: '7' },
      { stored: { hash: 'abc1234567890', analysisId: 'an-7' } }
    )
  })
})

/** The labels of the filled buttons in a card's action row. */
function solidActionLabels(): string[] {
  const footer = screen.getByTestId('query-card-footer-content')
  return Array.from(footer.querySelectorAll('button'))
    .filter((button) =>
      button.className.split(/\s+/).includes('bg-surface-primary-solid')
    )
    .map((button) => button.textContent?.trim() ?? '')
}

describe('SavedQueryRow action hierarchy and names (D-08/D-08b)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('gives the card exactly one solid action, and it is Analyze', async () => {
    stubLatestAnalysis(null)

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    await screen.findByRole('button', { name: 'Analyze' })
    expect(solidActionLabels()).toEqual(['Analyze'])
  })

  it('names the comparison by what it compares against', async () => {
    stubLatestAnalysis(null)

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    expect(
      await screen.findByRole('button', { name: 'Compare against Readyset' })
    ).toBeTruthy()
  })

  it('names the benchmark on a cached query Load test', async () => {
    stubLatestAnalysis(null)
    const actions = {
      ...makeActions(),
      isCached: () => true,
    } as unknown as SavedQueriesController['rowActions']

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    expect(
      await screen.findByRole('button', { name: 'Load test' })
    ).toBeTruthy()
    expect(solidActionLabels()).toEqual(['Analyze'])
  })
})

describe('SavedQueryRow evidence provenance', () => {
  it('attaches the provenance note to the observed metrics on the card meta line', () => {
    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    const evidence = screen.getByTitle(OBSERVED_EVIDENCE_PROVENANCE)
    expect(evidence.textContent).toContain('100 runs')
    expect(evidence.textContent).toContain('avg 12.0ms')
    // The note covers only the observed evidence, not identity metadata.
    expect(evidence.textContent).not.toContain('hash')
  })

  it('attaches the provenance note to the impact rail in impact mode', () => {
    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        displayMode="card-2"
        animateEntry={false}
      />
    )

    const rail = screen
      .getAllByTitle(OBSERVED_EVIDENCE_PROVENANCE)
      .find((node) => node.textContent?.includes('Observed runs'))
    expect(rail).toBeTruthy()
    expect(rail?.textContent).toContain('Avg latency')
  })

  it('states that an un-observed query has no measurement', () => {
    renderWithClient(
      <SavedQueryRow
        entry={entry({ observation_count: 0, avg_duration_ms: 0 })}
        state={makeState()}
        actions={makeActions()}
        displayMode="card-2"
        animateEntry={false}
      />
    )

    expect(screen.getByText('Not yet observed')).toBeTruthy()
    expect(screen.queryByText('Observed runs')).toBeNull()
    expect(screen.getByTestId('query-registry-row').textContent).not.toContain(
      '0ms'
    )
  })
})

describe('SavedQueryRow live analysis', () => {
  afterEach(() => {
    __resetAnalysisRunsForTests()
    __resetBackgroundRunsForTests()
    vi.unstubAllGlobals()
  })

  it('reports a run started elsewhere and reopens it instead of re-running', async () => {
    stubAnalysisInFlight()
    const actions = makeActions()
    const row = entry()
    // A run this card did not start — the analyze drawer did, and was closed.
    startAnalysisRun(
      { query: row.sql, target: row.target, fast: false },
      { queryHash: row.hash }
    )

    renderWithClient(
      <SavedQueryRow
        entry={row}
        target="demo"
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    // The Analyze action is shut while the run lasts; the card's own
    // in-progress panel is what reopens it.
    const shut = await screen.findByRole('button', { name: 'Analyzing' })
    expect((shut as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'View progress' }))

    expect(actions.analyze).toHaveBeenCalledWith(
      row.sql,
      row.target,
      undefined,
      {
        hash: row.hash,
      }
    )
  })

  it('carries the run on the card: tone, motion and shut actions (B-06)', async () => {
    stubAnalysisInFlight()
    const row = entry()
    startAnalysisRun(
      { query: row.sql, target: row.target, fast: false },
      { queryHash: row.hash }
    )

    renderWithClient(
      <SavedQueryRow
        entry={row}
        target="demo"
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    const card = await screen.findByTestId('query-registry-row')
    expect(card.className).toContain('bg-surface-primary-soft/10')
    expect(card.className).toContain('ring-border-primary-soft')
    expect(screen.getByLabelText('Analysis in progress')).toBeTruthy()
    expect(screen.getByText('Analysis in progress')).toBeTruthy()

    // Nothing that would measure this query a second time is reachable.
    expect(
      (screen.getByRole('button', { name: 'Analyzing' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
    expect(
      (
        screen.getByRole('button', {
          name: 'Compare against Readyset',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
  })

  it('reopens every action once the run is forgotten', async () => {
    stubAnalysisInFlight()
    const row = entry()
    const key = startAnalysisRun(
      { query: row.sql, target: row.target, fast: false },
      { queryHash: row.hash }
    )

    renderWithClient(
      <SavedQueryRow
        entry={row}
        target="demo"
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )
    await screen.findByRole('button', { name: 'Analyzing' })

    resetAnalysisRun(key)

    await waitFor(() => {
      expect(
        (
          screen.getByRole('button', {
            name: 'Compare against Readyset',
          }) as HTMLButtonElement
        ).disabled
      ).toBe(false)
    })
  })

  it('goes back to Analyze once no run is in flight', async () => {
    stubLatestAnalysis(null)
    const row = entry()

    renderWithClient(
      <SavedQueryRow
        entry={row}
        target="demo"
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    expect(await screen.findByRole('button', { name: 'Analyze' })).toBeTruthy()
  })
})

describe('SavedQueryRow star and recall', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('offers the star on every card, saying which state it is in', async () => {
    stubLatestAnalysis(null)
    const actions = makeActions()

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    const star = await screen.findByTestId('query-star-toggle')
    expect(star.getAttribute('aria-pressed')).toBe('false')
    expect(star.getAttribute('aria-label')).toBe('Star this query')

    fireEvent.click(star)
    expect(actions.toggleStar).toHaveBeenCalledWith('abc1234567890', true)
  })

  it('clears the star of a query that already carries one', async () => {
    stubLatestAnalysis(null)
    const actions = makeActions()

    renderWithClient(
      <SavedQueryRow
        entry={entry({ starred: true, starred_at: '2026-08-20T10:00:00Z' })}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    const star = await screen.findByTestId('query-star-toggle')
    expect(star.getAttribute('aria-pressed')).toBe('true')
    // The name is the action; aria-pressed alone carries the state. [B-20]
    expect(star.getAttribute('aria-label')).toBe('Star this query')

    fireEvent.click(star)
    expect(actions.toggleStar).toHaveBeenCalledWith('abc1234567890', false)
  })

  it('opens the query Overview from its name', async () => {
    stubLatestAnalysis(null)
    const actions = makeActions()
    const row = entry({ tag: 'Users lookup' })

    renderWithClient(
      <SavedQueryRow
        entry={row}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    fireEvent.click(await screen.findByText('Users lookup'))

    expect(actions.openOverview).toHaveBeenCalledWith(row.hash)
  })

  it('opens the same Overview from the card Details action', async () => {
    stubLatestAnalysis(null)
    const actions = makeActions()
    const row = entry()

    renderWithClient(
      <SavedQueryRow
        entry={row}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Overview' }))

    expect(actions.openOverview).toHaveBeenCalledWith(row.hash)
  })
})

describe('SavedQueryRow running test', () => {
  function runningRun(): BackgroundRunState {
    return {
      runId: 'cache_test_demo_1',
      kind: 'cache_test',
      target: 'demo',
      stage: 'measuring',
      status: 'running',
      message: 'Measuring upstream',
      lastSeq: 2,
      current: 5,
      total: 15,
      hasWarnings: false,
      queryHash: 'abc1234567890',
    }
  }

  function completeCacheResult(): CacheRunResult {
    return {
      success: true,
      query: 'SELECT 1',
      iterations: 15,
      origin_stats: {
        mean: 20,
        median: 19,
        min: 15,
        max: 30,
        p50: 19,
        p95: 28,
        p99: 30,
      },
      cache_stats: {
        mean: 1,
        median: 1,
        min: 0.5,
        max: 2,
        p50: 1,
        p95: 1.8,
        p99: 2,
      },
      speedup_mean: 20,
      speedup_median: 19,
      improvement_pct: 1_900,
      winner: 'readyset',
    }
  }

  it('reports a live test on the card without anything being opened', async () => {
    stubLatestAnalysis(null)
    const actions = {
      ...makeActions(),
      cacheRunFor: () => runningRun(),
    } as unknown as SavedQueriesController['rowActions']

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    expect(await screen.findByText('Testing in the background')).toBeTruthy()
    expect(screen.getByText('Measuring upstream')).toBeTruthy()
  })

  it('stops reporting a test the user has acknowledged', async () => {
    stubLatestAnalysis(null)
    const actions = {
      ...makeActions(),
      cacheRunFor: () => ({ ...runningRun(), hidden: true }),
    } as unknown as SavedQueriesController['rowActions']

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    await screen.findByRole('button', { name: 'Overview' })
    expect(screen.queryByText('Testing in the background')).toBeNull()
  })

  it('stays quiet for a completed run reattached from a past session', async () => {
    // A result restored from `localStorage` on load never passed through a
    // live status in this tab, so it must not reopen the panel on every card
    // that ever ran a test — it stays reachable in the drawer instead.
    stubLatestAnalysis(null)
    const actions = {
      ...makeActions(),
      cacheRunFor: (): BackgroundRunState => ({
        ...runningRun(),
        status: 'done',
        result: completeCacheResult(),
      }),
    } as unknown as SavedQueriesController['rowActions']

    renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )

    await screen.findByRole('button', { name: 'Overview' })
    expect(screen.queryByText('Testing in the background')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Close details' })).toBeNull()
  })

  it('keeps reporting a test through its own completion, until closed', async () => {
    // The run this tab watched go live must stay reported once it finishes —
    // that is the completion the user is here for — and only goes away once
    // they acknowledge it, not the instant it lands. The real controller
    // vends a fresh `cacheRunFor` off the reactive background-run store, so
    // each render below builds its own actions object the same way, rather
    // than mutating a closure behind one stable reference.
    stubLatestAnalysis(null)
    const actionsFor = (run: BackgroundRunState) =>
      ({
        ...makeActions(),
        cacheRunFor: () => run,
      }) as unknown as SavedQueriesController['rowActions']

    const liveRun: BackgroundRunState = { ...runningRun(), seenLive: true }
    const { rerender } = renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actionsFor(liveRun)}
        animateEntry={false}
      />
    )

    expect(await screen.findByText('Testing in the background')).toBeTruthy()

    const finishedRun: BackgroundRunState = {
      ...liveRun,
      status: 'done',
      result: completeCacheResult(),
    }
    rerender(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actionsFor(finishedRun)}
        animateEntry={false}
      />
    )

    expect(
      await screen.findByRole('button', { name: 'Close details' })
    ).toBeTruthy()
    expect(screen.queryByText('Testing in the background')).toBeNull()

    rerender(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actionsFor({ ...finishedRun, hidden: true })}
        animateEntry={false}
      />
    )

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Close details' })).toBeNull()
    )
  })
})

describe('SavedQueryRow SQL (B-03)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows the SQL as written, matching what the meta line counts', async () => {
    stubLatestAnalysis(null)

    renderWithClient(
      <SavedQueryRow
        entry={entry({
          sql: 'SELECT :p1 FROM revealed_table',
          original_sql: 'SELECT 1 FROM revealed_table',
          most_recent_params: { p1: '1' },
        })}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    const row = await screen.findByTestId('query-registry-row')
    expect(
      row.querySelector('[title="SELECT 1 FROM revealed_table"]')
    ).toBeTruthy()
    expect(
      row.querySelector('[title="SELECT :p1 FROM revealed_table"]')
    ).toBeNull()
    expect(screen.getByText(/no parameters/)).toBeTruthy()
  })
})

describe('SavedQueryRow Jev quick assessment', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows pending state and discloses a completed advisory assessment', async () => {
    stubLatestAnalysis(null)
    const actions = makeActions()
    const { rerender } = renderWithClient(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )
    expect(screen.getByText('Waiting for Jev assessment')).toBeTruthy()

    rerender(
      <SavedQueryRow
        entry={entry({
          jev_assessment: {
            status: 'complete',
            band: 'High',
            priority_score: 80,
            findings: [
              {
                id: 'index_coverage',
                label: 'Possible index gap',
                verdict: 'strong_concern',
                confidence: 0.9,
                description: 'Check important filters with Deep Analyze.',
              },
            ],
          },
        })}
        state={makeState()}
        actions={actions}
        animateEntry={false}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: 'View Jev results' }))
    expect(screen.getByText(/Jev classified this query/)).toBeTruthy()
    // The label appears twice once open: the collapsed chip and the panel row,
    // and the panel adds the plain-language definition beneath it.
    expect(screen.getAllByText('Missing index').length).toBe(2)
    expect(screen.getByText(/column with no supporting index/)).toBeTruthy()
    expect(screen.getByText(/Schema context:/)).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'Deep analyze this query' })
    )
    expect(actions.analyze).toHaveBeenCalled()
  })

  it('shows each concern as a chip before the panel is opened', () => {
    stubLatestAnalysis(null)
    renderWithClient(
      <SavedQueryRow
        entry={entry({
          jev_assessment: {
            status: 'complete',
            band: 'High',
            priority_score: 80,
            findings: [
              {
                id: 'index_coverage',
                label: 'Possible index gap',
                verdict: 'strong_concern',
                confidence: 0.9,
                description: 'Check important filters with Deep Analyze.',
              },
              {
                id: 'join_growth',
                label: 'Join expansion concern',
                verdict: 'possible_concern',
                confidence: 0.5,
                description: 'Measure row flow before changing it.',
              },
            ],
          },
        })}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    // Collapsed: the concerns and the score read off the card, and the action
    // says plainly that there is something to open.
    expect(screen.queryByText(/Jev classified this query/)).toBeNull()
    expect(screen.queryByText('80/100')).toBeNull()
    const toggle = screen.getByRole('button', { name: 'View Jev results' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.getByText('Missing index')).toBeTruthy()
    expect(screen.getByText('Possible join growth')).toBeTruthy()
  })

  it('drops the per-card attribution now that the page header carries it', () => {
    stubLatestAnalysis(null)
    renderWithClient(
      <SavedQueryRow
        entry={entry({
          jev_assessment: {
            status: 'complete',
            band: 'Low',
            priority_score: 20,
            findings: [],
          },
        })}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    expect(
      screen.queryByText('AI query classification powered by Jev')
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: 'View Jev results' })
    ).toBeTruthy()
  })
})
