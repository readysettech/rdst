import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
  // autoCodeSplitting rewrites the route's `component` into a lazyRouteComponent
  // call; the test imports the exported page directly, so this just needs to exist.
  lazyRouteComponent: (loader: unknown) => loader,
  useNavigate: () => vi.fn(),
}))

vi.mock('../lib/useQueryRegistry', () => ({
  useQueryRegistry: vi.fn(),
}))

import { useQueryRegistry } from '../lib/useQueryRegistry'
// Exported from the route-ignored `-query-registry-page` sibling (kept out of the
// route module so the CodeMirror stack stays code-split, FIX-1) so we can render
// the real page (with its filter-chip logic: search + single-select source, AND)
// directly.
import { QueryRegistryPage } from './-query-registry-page'

// vitest isn't configured with globals, so RTL's auto-cleanup never registers —
// clean the DOM between tests so leaked renders don't cause duplicate matches.
afterEach(() => cleanup())

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'prod' }),
}))

vi.mock('../lib/useCacheAction', () => ({
  useCacheAction: () => ({
    cacheQuery: vi.fn(),
    cachingId: null,
    isCached: () => false,
  }),
}))

type Entry = {
  sql: string
  hash: string
  tag: string
  source: string
  frequency: number
  avg_duration_ms: number
  max_duration_ms: number
  target: string
  last_analyzed: string
  first_analyzed: string
  observation_count: number
  most_recent_params: Record<string, unknown>
}

function entry(
  partial: Partial<Entry> & { hash: string; source: string }
): Entry {
  return {
    sql: 'SELECT 1',
    tag: '',
    frequency: 1,
    avg_duration_ms: 0,
    max_duration_ms: 0,
    target: 'prod',
    last_analyzed: '2026-07-01T00:00:00Z',
    first_analyzed: '2026-07-01T00:00:00Z',
    observation_count: 0,
    most_recent_params: {},
    ...partial,
  }
}

const queries: Entry[] = [
  entry({
    hash: 'a1',
    source: 'top-historical',
    sql: 'SELECT slow_one FROM orders',
    tag: 'alpha',
  }),
  entry({
    hash: 'a2',
    source: 'top',
    sql: 'SELECT slow_two FROM orders',
    tag: 'beta',
  }),
  entry({
    hash: 'b1',
    source: 'ask',
    sql: 'SELECT asked FROM users',
    tag: 'gamma',
  }),
  entry({
    hash: 'c1',
    source: 'manual',
    sql: 'SELECT typed FROM misc',
    tag: 'delta',
  }),
]

function setup(list: Entry[] = queries) {
  vi.mocked(useQueryRegistry).mockReturnValue({
    queries: list,
    isLoading: false,
    isFetching: false,
    total: list.length,
    listError: null,
    offset: 0,
    limit: 150,
    setLimit: vi.fn(),
    setOffset: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    resetPagination: vi.fn(),
    removeQuery: vi.fn(),
    updateTag: vi.fn(),
    addQuery: vi.fn(),
    addMutation: { mutate: vi.fn(), isPending: false } as never,
    updateSqlMutation: { mutate: vi.fn(), isPending: false } as never,
    importMutation: {
      mutate: vi.fn(),
      isPending: false,
      data: undefined,
      reset: vi.fn(),
    } as never,
  } as never)

  render(<QueryRegistryPage />)
}

function rowCount() {
  return screen.queryAllByTestId('query-registry-row').length
}

describe('query-registry source filter chips', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders All + one chip per distinct source label (collapsing aliases)', () => {
    setup()
    const chips = screen
      .getAllByTestId('source-chip')
      .map((c) => c.getAttribute('data-source'))
    // top + top-historical collapse to one "Slow Queries" chip.
    expect(chips).toEqual(['All', 'Slow Queries', 'Ask', 'Manual'])
  })

  it('defaults to All (all rows) with All marked active without relying on colour', () => {
    setup()
    expect(rowCount()).toBe(4)
    const all = screen.getAllByTestId('source-chip')[0]
    expect(all.getAttribute('data-source')).toBe('All')
    expect(all.getAttribute('aria-pressed')).toBe('true')
    // A tick icon renders only on the active chip — a non-colour structural cue.
    // Inactive chips have no icon, so the svg's presence marks selection.
    expect(all.querySelector('svg')).not.toBeNull()
    const askChip = screen
      .getAllByTestId('source-chip')
      .find((c) => c.getAttribute('data-source') === 'Ask')!
    expect(askChip.getAttribute('aria-pressed')).toBe('false')
    expect(askChip.querySelector('svg')).toBeNull()
  })

  it('single-selects a source and narrows the list', () => {
    setup()
    // Select the chip by its stable data-source (the page also has a "Slow
    // Queries" CTA card whose name would collide with a role+name lookup).
    fireEvent.click(
      screen
        .getAllByTestId('source-chip')
        .find((c) => c.getAttribute('data-source') === 'Slow Queries')!,
    )
    // Both top + top-historical rows match "Slow Queries".
    expect(rowCount()).toBe(2)
    const slow = screen
      .getAllByTestId('source-chip')
      .find((c) => c.getAttribute('data-source') === 'Slow Queries')!
    expect(slow.getAttribute('aria-pressed')).toBe('true')
    // Selecting Ask replaces (single-select), not adds.
    fireEvent.click(screen.getByRole('button', { name: /^Ask/ }))
    expect(rowCount()).toBe(1)
  })

  it('combines the chip with text search (AND)', () => {
    setup()
    const search = document.querySelector<HTMLInputElement>('[name="search"]')!
    // Search narrows to the two orders rows (both Slow Queries).
    fireEvent.change(search, { target: { value: 'orders' } })
    expect(rowCount()).toBe(2)
    // Ask chip AND "orders" search → zero matches → empty state.
    fireEvent.click(screen.getByRole('button', { name: /^Ask/ }))
    expect(rowCount()).toBe(0)
    expect(screen.getByText('No matching queries')).toBeTruthy()
  })

  it('resets to the full list when All is chosen again', () => {
    setup()
    fireEvent.click(screen.getByRole('button', { name: /^Ask/ }))
    expect(rowCount()).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: /^All/ }))
    expect(rowCount()).toBe(4)
  })

  it('hides the chip row when only one source exists', () => {
    setup([
      entry({ hash: 'x1', source: 'manual' }),
      entry({ hash: 'x2', source: 'web' }), // both map to "Manual"
    ])
    expect(screen.queryAllByTestId('source-chip')).toHaveLength(0)
  })
})
