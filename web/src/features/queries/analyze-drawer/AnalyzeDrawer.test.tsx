import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/api'
import type { ResultsSearch } from '../results/types'
import type { ResultsShell } from '../results/useResultsController'
import { AnalyzeDrawer } from './AnalyzeDrawer'
import type { AnalyzeDrawerLink } from './analyzeDrawerState'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  useResultsController: vi.fn(),
  latest: {
    summary: { analysis_id: 'a2' } as { analysis_id: string } | null,
    isResolved: true,
  },
  useLatestAnalysisQuery: vi.fn(),
  history: [] as Array<{
    analysis_id: string
    created_at: string
    target: string
    overall_rating: string
    efficiency_score: number | null
  }>,
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock('../results/ResultsBody', () => ({
  ResultsBody: ({
    followUp,
    prompts,
  }: {
    followUp?: string
    prompts?: string
  }) => (
    <div
      data-testid="results-body"
      data-follow-up={followUp}
      data-prompts={prompts}
    />
  ),
}))
vi.mock('../results/useResultsController', () => ({
  useResultsController: mocks.useResultsController,
}))
vi.mock('../saved/useLatestAnalysis', () => ({
  useLatestAnalysisQuery: mocks.useLatestAnalysisQuery,
}))
// The Overview lists the runs a query already has; the fetch behind that list
// is `useStoredAnalysis`'s and is covered there.
vi.mock('../results/useStoredAnalysis', () => ({
  useAnalysisHistory: () => ({ entries: mocks.history, isLoading: false }),
}))
// Resolving a hash the library has not loaded is its own read; here the loaded
// rows are the whole world.
vi.mock('./useAnalyzeDrawerEntry', () => ({
  useAnalyzeDrawerEntry: ({
    hash,
    loaded,
  }: {
    hash: string
    loaded: QueryRegistryEntry[]
  }) => ({
    entry: loaded.find((row) => row.hash === hash) ?? null,
    isLoading: false,
    error: null,
  }),
}))

const entry: QueryRegistryEntry = {
  hash: 'h1',
  sql: 'SELECT * FROM orders',
  tag: 'Orders lookup',
  source: 'observed',
  target: 'demo',
  frequency: 3,
  last_analyzed: '',
  is_new: false,
} as QueryRegistryEntry

const onClose = vi.fn()
const onOpenLink = vi.fn()
const onToggleStar = vi.fn()

function renderDrawer(
  link: AnalyzeDrawerLink | null,
  row: QueryRegistryEntry = entry
) {
  return render(
    <AnalyzeDrawer
      link={link}
      loaded={[row]}
      librarySearch={{ view: 'new', q: 'orders' }}
      target="demo"
      onClose={onClose}
      onOpenLink={onOpenLink}
      onToggleStar={onToggleStar}
    />
  )
}

// Both the drawer body and the Overview tab are lazy chunks, so the first
// render in this file waits on a module load rather than on a render.
const LAZY_CHUNK_TIMEOUT = { timeout: 5_000 }

const findDrawer = () =>
  screen.findByTestId('analyze-drawer', undefined, LAZY_CHUNK_TIMEOUT)

const findOverview = () =>
  screen.findByTestId('analyze-drawer-overview', undefined, LAZY_CHUNK_TIMEOUT)

/** The `ResultsSearch` the drawer handed the shared controller. */
function controllerSearch(): ResultsSearch {
  return mocks.useResultsController.mock.calls.at(-1)?.[0] as ResultsSearch
}

function controllerShell(): ResultsShell {
  return mocks.useResultsController.mock.calls.at(-1)?.[1] as ResultsShell
}

beforeEach(() => {
  mocks.navigate.mockClear()
  onClose.mockClear()
  onOpenLink.mockClear()
  onToggleStar.mockClear()
  mocks.history = []
  mocks.latest.summary = { analysis_id: 'a2' }
  mocks.latest.isResolved = true
  mocks.useLatestAnalysisQuery.mockImplementation(() => mocks.latest)
  mocks.useResultsController.mockClear()
  mocks.useResultsController.mockReturnValue({
    origin: 'query-library',
    backLabel: 'Back to queries',
    actions: {},
  })
})

afterEach(cleanup)

describe('AnalyzeDrawer', () => {
  it('stays closed until the URL names a query', () => {
    renderDrawer(null)
    expect(screen.queryByTestId('analyze-drawer')).toBeNull()
  })

  it('shows the analysis a query already has, without measuring it again', async () => {
    renderDrawer({ hash: 'h1' })

    expect(await findDrawer()).toBeTruthy()
    expect(screen.getByText('Orders lookup')).toBeTruthy()
    // Parameter entry and consent are drawer content, never a dialog on top.
    expect(screen.getByTestId('results-body').dataset.prompts).toBe('inline')
    expect(controllerSearch()).toMatchObject({
      query: 'SELECT * FROM orders',
      target: 'demo',
      origin: 'query-library',
      hash: 'h1',
      analysisId: 'a2',
    })
  })

  it('gives the analysis the widest drawer the design system has', async () => {
    renderDrawer({ hash: 'h1' })

    const drawer = await findDrawer()
    expect(drawer.className).toContain('max-w-6xl')
    expect(drawer.className).not.toContain('max-w-2xl')
  })

  it('measures the query when the link asks for a re-run', async () => {
    renderDrawer({ hash: 'h1', rerun: true })

    await findDrawer()
    expect(mocks.useLatestAnalysisQuery).toHaveBeenCalledWith('h1', false)
    expect(controllerSearch().analysisId).toBeUndefined()
  })

  it('offers the full view rather than inline chat', async () => {
    renderDrawer({ hash: 'h1', analysisId: 'a5' })

    const drawer = await findDrawer()
    expect(
      screen.getByTestId('results-body').getAttribute('data-follow-up')
    ).toBe('none')
    // Header actions are only clickable while the drawer is the top layer: a
    // dialog opened over it takes pointer events away from everything below.
    expect(drawer.style.pointerEvents).toBe('auto')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Open full view' }))

    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/results',
      search: {
        query: 'SELECT * FROM orders',
        target: 'demo',
        fast: false,
        params: undefined,
        returnSearch: JSON.stringify({
          view: 'new',
          q: 'orders',
          source: undefined,
          params: undefined,
          activity: undefined,
          impact: undefined,
          sort: undefined,
        }),
        origin: 'query-library',
        hash: 'h1',
        analysisId: 'a5',
      },
    })
  })

  it('turns moves made by the shared controller into drawer URLs', async () => {
    renderDrawer({ hash: 'h1', analysisId: 'a5' })
    await findDrawer()

    // Opening a sibling analysis from the stored header.
    controllerShell().openSearch({ ...controllerSearch(), analysisId: 'a9' })
    expect(onOpenLink).toHaveBeenLastCalledWith(
      { hash: 'h1', analysisId: 'a9', rerun: false },
      undefined
    )

    // Re-running leaves the stored record for a measurement.
    controllerShell().openSearch({
      ...controllerSearch(),
      analysisId: undefined,
    })
    expect(onOpenLink).toHaveBeenLastCalledWith(
      { hash: 'h1', analysisId: undefined, rerun: true },
      undefined
    )
  })

  it('reports its own dismissal instead of closing behind the URL', async () => {
    renderDrawer({ hash: 'h1' })
    await findDrawer()

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalled()
  })

  it('explains a link whose query is gone', async () => {
    renderDrawer({ hash: 'missing', analysisId: 'a5' })

    expect(
      await screen.findByText('This query is no longer in the library')
    ).toBeTruthy()
  })
})

describe('AnalyzeDrawer tabs', () => {
  it('opens a bare link on Analyze, as every existing link expects', async () => {
    renderDrawer({ hash: 'h1' })
    await findDrawer()

    expect(
      screen.getByRole('tab', { name: 'Analyze' }).getAttribute('aria-selected')
    ).toBe('true')
    expect(screen.getByTestId('results-body')).toBeTruthy()
    expect(screen.queryByTestId('analyze-drawer-overview')).toBeNull()
  })

  it('opens the Overview when the link asks for it, measuring nothing', async () => {
    renderDrawer({ hash: 'h1', tab: 'overview' })
    await findDrawer()

    expect(
      screen
        .getByRole('tab', { name: 'Overview' })
        .getAttribute('aria-selected')
    ).toBe('true')
    expect(await findOverview()).toBeTruthy()
    // The analysis controller never mounts, so reading the Overview cannot
    // start a run.
    expect(mocks.useResultsController).not.toHaveBeenCalled()
    // `/results` has no overview, so its escape hatch stays on Analyze.
    expect(screen.queryByRole('button', { name: 'Open full view' })).toBeNull()
  })

  it('writes the tab into the URL rather than holding it locally', async () => {
    renderDrawer({ hash: 'h1', analysisId: 'a5' })
    await findDrawer()

    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }))

    expect(onOpenLink).toHaveBeenLastCalledWith({
      hash: 'h1',
      analysisId: 'a5',
      tab: 'overview',
    })
  })

  it('opens a stored run from the Overview on the Analyze tab', async () => {
    mocks.history = [
      {
        analysis_id: 'a5',
        created_at: new Date().toISOString(),
        target: 'demo',
        overall_rating: 'good',
        efficiency_score: 82,
      },
    ]
    renderDrawer({ hash: 'h1', tab: 'overview' })
    await findOverview()

    fireEvent.click(
      screen.getByRole('button', { name: /Open the analysis from/ })
    )

    expect(onOpenLink).toHaveBeenLastCalledWith({
      hash: 'h1',
      analysisId: 'a5',
      tab: 'analyze',
    })
  })

  it('shows the stored comparison, and omits the row when there is none', async () => {
    renderDrawer({ hash: 'h1', tab: 'overview' })
    await findOverview()
    expect(screen.queryByText('Last comparison')).toBeNull()

    cleanup()
    renderDrawer({ hash: 'h1', tab: 'overview' }, {
      ...entry,
      last_compare: {
        status: 'ok',
        at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
        readyset_ms: 4,
        origin_ms: 180,
      },
    } as QueryRegistryEntry)
    await findOverview()

    expect(screen.getByText('45.0x faster')).toBeTruthy()
    expect(screen.getByText('3 hours ago')).toBeTruthy()
  })

  it('stars the query from the drawer header', async () => {
    renderDrawer({ hash: 'h1', tab: 'overview' })
    await findDrawer()

    fireEvent.click(screen.getByTestId('query-star-toggle'))

    expect(onToggleStar).toHaveBeenCalledWith('h1', true)
  })
})
