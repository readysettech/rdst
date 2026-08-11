import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

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

import { SavedQueriesPage } from '../features/queries/saved/SavedQueriesPage'
import { useQueryRegistry } from '../lib/useQueryRegistry'

// vitest isn't configured with globals, so RTL's auto-cleanup never registers —
// clean the DOM between tests so leaked renders don't cause duplicate matches.
afterEach(() => cleanup())

beforeAll(() => {
  const proto = HTMLElement.prototype as unknown as {
    hasPointerCapture: () => boolean
    setPointerCapture: () => void
    releasePointerCapture: () => void
    scrollIntoView: () => void
  }
  proto.hasPointerCapture = () => false
  proto.setPointerCapture = () => {}
  proto.releasePointerCapture = () => {}
  proto.scrollIntoView = () => {}
})

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

vi.mock('../components/SQLInput', () => ({
  SQLInput: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string
    onChange: (value: string) => void
    placeholder?: string
  }) => (
    <textarea
      aria-label="SQL editor"
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

vi.mock('../components/PathPicker', () => ({
  PathPicker: ({
    value,
    onChange,
    label,
  }: {
    value: string
    onChange: (value: string) => void
    label?: string
  }) => (
    <input
      aria-label={label ?? 'File path'}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
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

function setup(
  list: Entry[] = queries,
  overrides: Record<string, unknown> = {},
  pageProps: {
    deepLinkHash?: string
    deepLinkRunId?: string
  } = {}
) {
  const refetch = vi.fn()
  const removeQuery = vi.fn()
  const updateTag = vi.fn()
  const updateSql = vi.fn()
  const addQuery = vi.fn()
  const importQueries = vi.fn()
  const resetImport = vi.fn()
  vi.mocked(useQueryRegistry).mockReturnValue({
    queries: list,
    isLoading: false,
    isFetching: false,
    total: list.length,
    listError: null,
    refetch,
    offset: 0,
    limit: 150,
    setLimit: vi.fn(),
    setOffset: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    resetPagination: vi.fn(),
    removeQuery,
    updateTag,
    addQuery: vi.fn(),
    addMutation: { mutate: addQuery, isPending: false } as never,
    updateSqlMutation: { mutate: updateSql, isPending: false } as never,
    importMutation: {
      mutate: importQueries,
      isPending: false,
      data: undefined,
      reset: resetImport,
    } as never,
    ...overrides,
  } as never)

  render(<SavedQueriesPage {...pageProps} />)
  return {
    refetch,
    removeQuery,
    updateTag,
    updateSql,
    addQuery,
    importQueries,
    resetImport,
  }
}

function rowCount() {
  return screen.queryAllByTestId('query-registry-row').length
}

describe('query-registry source filter', () => {
  beforeEach(() => vi.clearAllMocks())

  // The source filter is a radio-mode SegmentedControl: each source is a
  // `role="radio"` segment named `${label} ${count}`; the base label is what we
  // assert on, so strip the trailing count.
  function sourceLabels() {
    return screen
      .getAllByRole('radio')
      .map((r) => (r.textContent ?? '').replace(/\s*\d+$/, ''))
  }

  it('renders All + one segment per distinct source label (collapsing aliases)', () => {
    setup()
    // top + top-historical collapse to one "Slow Queries" segment.
    expect(sourceLabels()).toEqual(['All', 'Slow Queries', 'Ask', 'Manual'])
  })

  it('defaults to All (all rows) with All checked without relying on colour', () => {
    setup()
    expect(rowCount()).toBe(4)
    const all = screen.getByRole('radio', { name: /^All/ })
    // aria-checked is the non-colour structural cue for the selected segment.
    expect(all.getAttribute('aria-checked')).toBe('true')
    const ask = screen.getByRole('radio', { name: /^Ask/ })
    expect(ask.getAttribute('aria-checked')).toBe('false')
  })

  it('single-selects a source and narrows the list', () => {
    setup()
    fireEvent.click(screen.getByRole('radio', { name: /^Slow Queries/ }))
    // Both top + top-historical rows match "Slow Queries".
    expect(rowCount()).toBe(2)
    expect(
      screen
        .getByRole('radio', { name: /^Slow Queries/ })
        .getAttribute('aria-checked')
    ).toBe('true')
    // Selecting Ask replaces (single-select), not adds.
    fireEvent.click(screen.getByRole('radio', { name: /^Ask/ }))
    expect(rowCount()).toBe(1)
  })

  it('combines the source filter with text search (AND)', () => {
    setup()
    const search = document.querySelector<HTMLInputElement>('[name="search"]')!
    // Search narrows to the two orders rows (both Slow Queries).
    fireEvent.change(search, { target: { value: 'orders' } })
    expect(rowCount()).toBe(2)
    // Ask AND "orders" search → zero matches → empty state.
    fireEvent.click(screen.getByRole('radio', { name: /^Ask/ }))
    expect(rowCount()).toBe(0)
    expect(screen.getByText('No matching queries')).toBeTruthy()
  })

  it('resets to the full list when All is chosen again', () => {
    setup()
    fireEvent.click(screen.getByRole('radio', { name: /^Ask/ }))
    expect(rowCount()).toBe(1)
    fireEvent.click(screen.getByRole('radio', { name: /^All/ }))
    expect(rowCount()).toBe(4)
  })

  it('hides the source filter when only one source exists', () => {
    setup([
      entry({ hash: 'x1', source: 'manual' }),
      entry({ hash: 'x2', source: 'web' }), // both map to "Manual"
    ])
    expect(screen.queryByRole('radiogroup')).toBeNull()
    expect(screen.queryAllByRole('radio')).toHaveLength(0)
  })
})

describe('query-registry data states', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders canonical query-card skeletons while loading', () => {
    setup([], { isLoading: true, listError: 'offline' })

    expect(screen.getAllByTestId('query-card-skeleton')).toHaveLength(3)
    expect(screen.queryByText('No queries yet')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders a recoverable error before the empty state', () => {
    const refetch = vi.fn()
    setup([], { listError: 'connection refused', refetch })

    expect(screen.getByText("Queries couldn't be loaded")).toBeTruthy()
    expect(screen.queryByText('No queries yet')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('renders a first-use empty state with one primary action', () => {
    setup([])

    expect(screen.getByText('No queries yet')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /Add your first query/ })
    ).toBeTruthy()
  })
})

describe('query-registry add dialog', () => {
  beforeEach(() => vi.clearAllMocks())

  const savedQuery = entry({
    hash: 'saved-1',
    source: 'manual',
    sql: 'SELECT id FROM users',
    tag: 'Users query',
  })

  function openAddDialog() {
    fireEvent.click(screen.getByRole('button', { name: /Add query/ }))
  }

  it('replaces the separate import action with a default Add query tab', () => {
    const { importQueries, resetImport } = setup([savedQuery])

    expect(
      screen.queryByRole('button', { name: /Import from file/ })
    ).toBeNull()

    openAddDialog()

    expect(screen.getByRole('dialog', { name: 'Add query' })).toBeTruthy()
    const addTab = screen.getByRole('tab', { name: 'Add query' })
    const importTab = screen.getByRole('tab', { name: 'Import from file' })
    expect(addTab.getAttribute('aria-selected')).toBe('true')
    expect(importTab.getAttribute('aria-selected')).toBe('false')
    expect(screen.getByRole('textbox', { name: 'SQL editor' })).toBeTruthy()

    fireEvent.click(importTab)
    expect(importTab.getAttribute('aria-selected')).toBe('true')
    expect(screen.queryByRole('textbox', { name: 'SQL editor' })).toBeNull()

    fireEvent.change(screen.getByRole('textbox', { name: 'File path' }), {
      target: { value: '/tmp/queries.sql' },
    })
    const updateExisting = screen.getByRole('button', {
      name: 'Update existing queries',
    })
    fireEvent.click(updateExisting)
    expect(updateExisting.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: /Import queries/ }))

    expect(importQueries).toHaveBeenCalledWith(
      {
        file: '/tmp/queries.sql',
        update: true,
        target: 'prod',
      },
      expect.any(Object)
    )

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Add query' })).toBeNull()
    expect(resetImport).toHaveBeenCalledTimes(1)

    openAddDialog()
    expect(
      screen
        .getByRole('tab', { name: 'Add query' })
        .getAttribute('aria-selected')
    ).toBe('true')
  })

  it('preserves the existing add-query mutation from the modal', () => {
    const { addQuery } = setup([savedQuery])
    openAddDialog()

    fireEvent.change(screen.getByRole('textbox', { name: 'SQL editor' }), {
      target: { value: 'SELECT email FROM users' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Save query/ }))

    expect(addQuery).toHaveBeenCalledWith(
      {
        sql: 'SELECT email FROM users',
        target: 'prod',
      },
      expect.any(Object)
    )
  })
})

describe('query-registry card actions', () => {
  beforeEach(() => vi.clearAllMocks())

  const savedQuery = entry({
    hash: 'saved-1',
    source: 'manual',
    sql: 'SELECT id FROM users',
    tag: 'Users query',
  })

  function openActions() {
    fireEvent.keyDown(screen.getByRole('button', { name: 'More actions' }), {
      key: 'Enter',
    })
  }

  it('keeps edit, rename, and delete in the query-card header menu', () => {
    setup([savedQuery])

    const card = screen.getByTestId('query-registry-row')
    const header = screen.getByTestId('query-card-header')
    expect(header.parentElement).toBe(
      screen.getByTestId('query-card-main-content')
    )
    expect(
      header.contains(screen.getByRole('button', { name: 'More actions' }))
    ).toBe(true)

    openActions()

    expect(screen.getByRole('menuitem', { name: /Edit SQL/ })).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: /Rename/ })).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: /Delete/ })).toBeTruthy()
    expect(card.getAttribute('role')).toBeNull()
  })

  it('renames inside the canonical card without hiding its SQL', () => {
    const { updateTag } = setup([savedQuery])
    const card = screen.getByTestId('query-registry-row')
    openActions()
    fireEvent.click(screen.getByRole('menuitem', { name: /Rename/ }))

    expect(screen.getByTestId('query-registry-row')).toBe(card)
    const input = document.querySelector<HTMLInputElement>(
      '[name="edit-tag-saved-1"]'
    )
    expect(input).toBeTruthy()
    expect(screen.getByTitle(savedQuery.sql)).toBeTruthy()
    expect(screen.getByTestId('query-card-footer-content')).toBeTruthy()

    fireEvent.change(input!, { target: { value: 'Renamed query' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))

    expect(updateTag).toHaveBeenCalledWith('saved-1', 'Renamed query')
  })

  it('edits SQL inside the canonical card and can return to the header menu', () => {
    const { updateSql } = setup([savedQuery])
    const card = screen.getByTestId('query-registry-row')
    openActions()
    fireEvent.click(screen.getByRole('menuitem', { name: /Edit SQL/ }))

    expect(screen.getByTestId('query-registry-row')).toBe(card)
    expect(screen.getByText('Users query')).toBeTruthy()
    expect(screen.queryByTitle(savedQuery.sql)).toBeNull()
    const editor = screen.getByRole('textbox', { name: 'SQL editor' })
    expect(editor).toBeTruthy()
    expect(screen.getByTestId('query-card-footer-content')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Save/ })).toBeTruthy()

    fireEvent.change(editor, { target: { value: 'SELECT email FROM users' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    expect(updateSql).toHaveBeenCalledWith(
      { hash: 'saved-1', sql: 'SELECT email FROM users' },
      expect.any(Object)
    )

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByTitle(savedQuery.sql)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'More actions' })).toBeTruthy()
  })

  it('visually identifies a saved query reached through a deep link', () => {
    const linkedQuery = {
      ...savedQuery,
      avg_duration_ms: 250,
      observation_count: 1_000,
    }
    setup([linkedQuery], {}, { deepLinkHash: linkedQuery.hash })

    const linkedCard = screen.getByTestId('query-registry-row')
    expect(linkedCard.getAttribute('data-highlighted')).toBe('true')
    expect(linkedCard.className).toContain('bg-surface-primary-soft')
    expect(screen.getByText('Selected query')).toBeTruthy()
    expect(screen.queryByText('Your biggest win')).toBeNull()
  })

  it('confirms deletion without dropping the query-card anatomy', () => {
    const { removeQuery } = setup([savedQuery])
    openActions()
    fireEvent.click(screen.getByRole('menuitem', { name: /Delete/ }))

    expect(screen.getByText('Delete this query?')).toBeTruthy()
    expect(screen.getByTitle(savedQuery.sql)).toBeTruthy()
    expect(screen.getByTestId('query-card-footer-content')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Delete/ }))
    expect(removeQuery).toHaveBeenCalledWith('saved-1')
  })
})
