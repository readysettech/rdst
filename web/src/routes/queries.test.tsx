import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const openBenchmark = vi.fn()
const openAddQuery = vi.fn()
const controller = {
  target: 'demo',
  library: {
    discovery: { state: 'watching', updated_at: null },
  },
  navigation: { openBenchmark },
  addDialog: { openDialog: openAddQuery },
  rowActions: { toggleStar: vi.fn() },
  analyzeDrawer: {
    link: null,
    librarySearch: {},
    entries: [],
    open: vi.fn(),
    close: vi.fn(),
  },
}

vi.mock('../features/queries/library/useQueryLibraryController', () => ({
  useQueryLibraryController: () => controller,
}))

vi.mock('../features/queries/library/QueryLibraryPage', () => ({
  QueryLibraryPage: () => (
    <div data-testid="query-library-page">Query library</div>
  ),
}))

import { QueriesPage } from './-queries-page'
import { Route } from './queries'

afterEach(() => {
  cleanup()
  openBenchmark.mockClear()
  openAddQuery.mockClear()
})

describe('queries route validateSearch', () => {
  const validate = Route.options.validateSearch as (
    search: Record<string, unknown>
  ) => Record<string, unknown>

  it('keeps supported Query Library state', () => {
    expect(
      validate({
        view: 'new',
        q: 'orders',
        source: 'observed',
        sort: 'recently-observed',
        hash: 'abc',
        run: 'r1',
      })
    ).toEqual({
      view: 'new',
      q: 'orders',
      source: 'observed',
      sort: 'recently-observed',
      action: undefined,
      hash: 'abc',
      run: 'r1',
    })
  })

  it('drops unknown filter values', () => {
    expect(validate({ view: 'bogus', source: 123, sort: 'fastest' })).toEqual({
      view: undefined,
      q: undefined,
      source: undefined,
      sort: undefined,
      action: undefined,
      hash: undefined,
      run: undefined,
    })
  })
})

describe('QueriesPage workspace', () => {
  it('renders one library without legacy view tabs', async () => {
    render(<QueriesPage search={{}} />)

    expect(await screen.findByTestId('query-library-page')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.getByRole('region', { name: 'Queries' })).toBeTruthy()
  })

  it('moves page actions into the header', () => {
    render(<QueriesPage search={{}} />)

    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add query' }))

    expect(openBenchmark).toHaveBeenCalledOnce()
    expect(openAddQuery).toHaveBeenCalledOnce()
  })
})
