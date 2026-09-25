import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import { renderWithClient } from '@/test-utils'

const state = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
  total: 100000,
  empty: false,
  options: {} as Record<string, unknown>,
  located: undefined as number | null | undefined,
  locateAsked: undefined as unknown,
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => state.navigate,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: state.search } }),
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useMonitorQueries: (
    _target: string,
    _range: string,
    options?: Record<string, unknown>
  ) => {
    // The page request, not the focused query's locate request.
    if (options && !('locate' in options)) state.options = options
    const rows = queriesFixture('localdev', '24h', FIXTURE_NOW)
      .queries.filter((q) => /^select/i.test(q.text))
      .slice(0, 2)
    if (options && 'locate' in options && options.locate) {
      state.locateAsked = options.locate
      return {
        data: { total: state.total, queries: [], located: state.located },
      }
    }
    return {
      data: {
        total: options && state.empty ? 0 : state.total,
        queries: options && state.empty ? [] : rows,
      },
    }
  },
  useMonitorQuery: () => ({}),
  useAnalyzeJobs: () => ({ data: { jobs: [] } }),
  useEnqueueAnalysis: () => ({ mutate: vi.fn() }),
  useInbox: () => ({}),
}))
vi.mock('@/lib/useQueryRegistry', () => ({
  useQueryRegistry: () => ({ queries: [] }),
}))

import { QuerySection } from './QuerySection'

const section = () =>
  renderWithClient(
    <QuerySection
      target="db"
      range="24h"
      domain={[0, 4000000000]}
      model={null}
      scopeLabel="in the last 24 hours"
      hoverKey={null}
      onHoverKey={() => {}}
    />
  )
beforeEach(() => {
  state.search = {}
  state.empty = false
  state.located = undefined
  state.locateAsked = undefined
  state.navigate.mockReset()
})
afterEach(cleanup)

it.each([
  { q: 'missing_table' },
  { filter: 'attention' },
])('RV3-03 retains recovery controls for an empty remote result %j', (search) => {
  state.search = search
  state.empty = true
  section()
  expect(screen.getByTestId('query-toolbar')).toBeTruthy()
  expect(screen.queryByText('No queries recorded yet')).toBeNull()
  const clear = screen
    .getAllByRole('button', {
      name: search.q ? 'Clear search' : "Remove 'Needs attention'",
    })
    .at(-1)!
  fireEvent.click(clear)
  if (search.q)
    expect(
      (screen.getByLabelText('Search queries') as HTMLInputElement).value
    ).toBe('')
  else expect(state.navigate).toHaveBeenCalled()
})

it('RV3-04 sends affected keys to the server for the whole result set', () => {
  state.search = { queries: '000000000599,000000000600' }
  section()
  expect(state.options.keys).toEqual(['000000000599', '000000000600'])
})

it('RV3-05 bounds page controls at 100000 results and reaches the last page', () => {
  section()
  const nav = screen.getByRole('navigation', { name: 'Query pages' })
  expect(within(nav).getAllByRole('button').length).toBeLessThanOrEqual(9)
  fireEvent.click(within(nav).getByRole('button', { name: 'Page 10000' }))
  expect(state.options.offset).toBe(99990)
  expect(
    (within(nav).getByRole('button', { name: 'Next' }) as HTMLButtonElement)
      .disabled
  ).toBe(true)
  fireEvent.click(within(nav).getByRole('button', { name: 'Previous' }))
  expect(state.options.offset).toBe(99980)
})

it('opens the page a focused query sits on in a server-paged list', () => {
  state.search = { focus: 'abc', sort: 'qps', filter: 'attention' }
  state.located = 1234
  section()
  expect(state.locateAsked).toBe('abc')
  // Filters and sort are left as they are; only the page moves.
  expect(state.options.offset).toBe(1230)
  expect(state.options.sort).toBe('qps')
  expect(state.options.filter).toBe('attention')
})
