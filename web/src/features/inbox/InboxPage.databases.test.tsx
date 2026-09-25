// The Inbox covers every database: each row names its database, and the filter
// defaults to all of them.
import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { renderWithClient } from '@/test-utils'

const routerState = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routerState.navigate,
  useSearch: () => routerState.search,
}))

vi.mock('@/features/monitor-kit/useMonitorPulse', () => ({
  useMonitorPulse: () => ({ state: 'live', last: null, nextAt: null }),
}))

vi.mock('@/features/monitor-kit/MonitorHeartbeat', () => ({
  MonitorHeartbeat: () => <span>Monitoring</span>,
}))

import { InboxPage } from './InboxPage'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
  routerState.search = {}
  routerState.navigate.mockReset()
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

it('names the database on every row', async () => {
  renderWithClient(<InboxPage />)
  const watch = await screen.findByRole('region', { name: 'Watch' })
  const rows = [...watch.querySelectorAll('article[data-inbox-item]')]
  expect(rows.length).toBeGreaterThan(0)
  for (const row of rows)
    expect(within(row as HTMLElement).getAllByTestId('item-database')[0].textContent).toMatch(/\S/)
})

it('shows every database by default, even after an older visit picked one', async () => {
  window.localStorage.setItem('rdst_inbox_target', 'somewhere-else')
  renderWithClient(<InboxPage />)
  await screen.findByRole('region', { name: 'Watch' })
  window.localStorage.removeItem('rdst_inbox_target')
  const filter = await screen.findByTestId('inbox-database-filter')
  const all = within(filter).getByRole('button', { name: /^All databases/ })
  expect(all.getAttribute('aria-pressed')).toBe('true')
  const other = within(filter)
    .getAllByRole('button')
    .find((chip) => chip !== all) as HTMLElement
  fireEvent.click(other)
  expect(routerState.navigate).toHaveBeenCalledWith(
    expect.objectContaining({
      search: expect.objectContaining({ target: expect.any(String) }),
      resetScroll: false,
    })
  )
})
