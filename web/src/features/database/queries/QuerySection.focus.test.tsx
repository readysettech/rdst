import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import type { MonitorQuery } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

const rows: MonitorQuery[] = queriesFixture('localdev', '24h', FIXTURE_NOW)
  .queries.filter((query) => /^select/i.test(query.text))
  .map((query, index) => ({
    ...query,
    key: `k${index}`,
    display_name: `Query ${index}`,
    db_time_ms: 1000 - index * 10,
    tables: index === 0 ? ['orders'] : ['customers'],
  }))

const state = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => state.navigate,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: state.search } }),
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useMonitorQueries: () => ({ data: { total: rows.length, queries: rows } }),
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
  state.navigate.mockReset()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

const lastSearch = () =>
  state.navigate.mock.calls.at(-1)?.[0].search as Record<string, unknown>

it('needs more than one page of fixture rows', () => {
  expect(rows.length).toBeGreaterThan(10)
})

it('opens the page a focused query sits on and highlights it, keeping the sort', () => {
  const key = rows[rows.length - 1].key
  state.search = { focus: key, sort: 'impact' }
  section()
  const row = document.getElementById(`query-${key}`)
  expect(row?.getAttribute('data-highlighted')).toBe('true')
  // The focus is cleared from the URL once it's in view; the sort stays.
  expect(lastSearch()).toEqual({ sort: 'impact' })
  act(() => {
    vi.advanceTimersByTime(3500)
  })
  expect(
    document.getElementById(`query-${key}`)?.getAttribute('data-highlighted')
  ).toBeNull()
})

it('says which filter hides a focused query and clears only that one', () => {
  state.search = { focus: 'k1', table: 'orders', sort: 'qps' }
  section()
  const notice = screen.getByTestId('focus-hidden')
  expect(notice.textContent).toContain(
    'Query 1 is filtered out by the orders table filter.'
  )
  fireEvent.click(screen.getByRole('button', { name: 'Show it' }))
  expect(lastSearch()).toEqual({ focus: 'k1', sort: 'qps' })
})
