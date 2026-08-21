import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  __resetAnalysisRunsForTests,
  startAnalysisRun,
} from '../../../lib/analysisRuns'
import { __resetBackgroundRunsForTests } from '../../../lib/backgroundRuns'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
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
    expandedHash: null,
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
    toggleExpanded: vi.fn(),
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

const twoHoursAgo = () => new Date(Date.now() - 2 * 3_600_000).toISOString()

const analyzedEntry = () =>
  entry({
    last_analyzed_at: twoHoursAgo(),
    most_recent_params: { p1: '7' },
  })

describe('SavedQueryRow stored analysis (A2/A3)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows the outcome in the card footer without expanding the card', async () => {
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

    expect(await screen.findByText('Good · 82/100')).toBeTruthy()
    expect(screen.queryByText('Analyzed 2 hours ago')).toBeNull()
  })

  it('reveals the relative analyzed time in a tooltip on keyboard focus', async () => {
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

    const badge = await screen.findByText('Good · 82/100')
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.focus(badge)
    expect((await screen.findByRole('tooltip')).textContent).toBe(
      'Analyzed 2 hours ago'
    )

    fireEvent.blur(badge)
    expect(screen.queryByRole('tooltip')).toBeNull()
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

  it('opens the stored record from the expanded details view action', async () => {
    stubLatestAnalysis({ analysis_id: 'an-7' })
    const actions = makeActions()
    const analyzed = analyzedEntry()

    renderWithClient(
      <SavedQueryRow
        entry={analyzed}
        state={{ ...makeState(), expandedHash: analyzed.hash }}
        actions={actions}
        animateEntry={false}
      />
    )

    const viewButtons = await screen.findAllByRole('button', {
      name: 'View analysis',
    })
    fireEvent.click(viewButtons[viewButtons.length - 1])
    expect(actions.analyze).toHaveBeenCalledWith(
      'SELECT * FROM users WHERE id = 1',
      'demo',
      { p1: '7' },
      { stored: { hash: 'abc1234567890', analysisId: 'an-7' } }
    )
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
})

describe('SavedQueryRow live analysis', () => {
  afterEach(() => {
    __resetAnalysisRunsForTests()
    __resetBackgroundRunsForTests()
    vi.unstubAllGlobals()
  })

  it('reports a run started elsewhere and reopens it instead of re-running', async () => {
    stubLatestAnalysis(null)
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

    const action = await screen.findByRole('button', { name: 'Analyzing...' })
    fireEvent.click(action)

    expect(actions.analyze).toHaveBeenCalledWith(
      row.sql,
      row.target,
      undefined,
      {
        hash: row.hash,
      }
    )
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
    expect(star.getAttribute('aria-label')).toBe('Starred')

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
    // The inline Details toggle keeps its own job: it still expands in place.
    expect(actions.toggleExpanded).not.toHaveBeenCalled()
  })
})
