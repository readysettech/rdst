import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getInbox,
  inboxAction,
  resetStubState,
  setFixtureTiming,
} from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { monitorKeys } from '@/lib/monitor/keys'
import { createTestQueryClient, renderWithClient } from '@/test-utils'

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
  window.localStorage.removeItem('rdst_inbox_target')
  window.localStorage.removeItem('rdst_inbox_sections')
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

function band(name: string) {
  const section = screen.getByRole('region', { name })
  return section
}

describe('InboxPage', () => {
  it('lists Urgent then Watch as sections, with no tab bar', async () => {
    renderWithClient(<InboxPage />)
    await screen.findByRole('region', { name: 'Urgent' })
    expect(within(band('Urgent')).getByLabelText('2 alerts')).toBeTruthy()
    // FYI items list at the bottom of Watch; there is no FYI or Recovered section.
    expect(within(band('Watch')).getByLabelText('7 alerts')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'FYI' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Recovered' })).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
    for (const heading of screen.getAllByRole('heading')) {
      const text = heading.textContent ?? ''
      if (text.length > 3) expect(text).not.toBe(text.toUpperCase())
    }
  })

  it('addresses every alert row by its id', async () => {
    renderWithClient(<InboxPage />)
    const watch = await screen.findByRole('region', { name: 'Watch' })
    const rows = [...watch.querySelectorAll('article')]
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows)
      expect(row.getAttribute('data-inbox-item')).toMatch(/^\d+$/)
  })

  it('shows one section for a link to it', async () => {
    routerState.search = { state: 'act_now' }
    renderWithClient(<InboxPage />)
    await screen.findByRole('region', { name: 'Urgent' })
    expect(screen.queryByRole('region', { name: 'Watch' })).toBeNull()
  })

  it('resolves an item out of its band', async () => {
    renderWithClient(<InboxPage />)
    const title = await within(
      await screen.findByRole('region', { name: 'Urgent' })
    ).findByText('Stuck query')
    const row = title.closest('article') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Resolve' }))
    await waitFor(() => expect(screen.queryByText('Stuck query')).toBeNull())
  })

  it('lists an alert resolved here under Recovered on Open', async () => {
    const reopenStuck = { name: /^Reopen Stuck query \(/ }
    renderWithClient(<InboxPage />)
    const recovered = () =>
      screen.findByRole('region', { name: 'Recovered in the last 24 hours' })
    const title = await within(
      await screen.findByRole('region', { name: 'Urgent' })
    ).findByText('Stuck query')
    const row = title.closest('article') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Resolve' }))
    await waitFor(() => expect(screen.queryByText('Stuck query')).toBeNull())
    const section = await recovered()
    await waitFor(() =>
      expect(within(section).getAllByRole('button', reopenStuck).length).toBe(1)
    )
  })

  it('shows what you can do as tiles, with the SQL ready to copy', async () => {
    renderWithClient(<InboxPage />)
    const title = await within(
      await screen.findByRole('region', { name: 'Urgent' })
    ).findByText('Stuck query')
    const row = title.closest('article') as HTMLElement
    // Items start folded to one line; the title opens them.
    expect(
      within(row).queryByRole('region', { name: 'What you can do' })
    ).toBeNull()
    const toggle = within(row).getByRole('button', { name: 'Stuck query' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(within(row).getByTestId('item-line').textContent).toMatch(/\S/)
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const tiles = within(row).getByRole('region', { name: 'What you can do' })
    expect(
      within(tiles).getByText('SELECT pg_cancel_backend(<pid>);')
    ).toBeTruthy()
    expect(
      within(tiles).getAllByRole('button', { name: /copy SELECT/ }).length
    ).toBeGreaterThan(0)
  })

  it('opens an alert at the exact query and tab', async () => {
    renderWithClient(<InboxPage />)
    const watch = await screen.findByRole('region', { name: 'Watch' })
    const title = await within(watch).findByText('New heavy query')
    const row = title.closest('article') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Open query' }))
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/database',
        search: expect.objectContaining({
          target: 'localdev',
          tab: 'analysis',
          query: expect.any(String),
        }),
      })
    )
  })

  it('lists recoveries and closed alerts on Open, in green, as observed', async () => {
    renderWithClient(<InboxPage />)
    const recovered = await screen.findByRole('region', {
      name: 'Recovered in the last 24 hours',
    })
    expect(recovered.querySelector('[data-band="recovered"]')).toBeTruthy()
    expect(
      (await within(recovered).findAllByRole('article')).length
    ).toBeGreaterThan(0)
    expect(
      within(recovered).getAllByText(/back to normal /).length
    ).toBeGreaterThan(0)
    expect(
      within(recovered).getAllByRole('button', { name: /^Reopen / }).length
    ).toBeGreaterThan(0)
  })

  it('lands old resolved and done links on Open', async () => {
    routerState.search = {}
    renderWithClient(<InboxPage />)
    await screen.findByRole('region', { name: 'Urgent' })
    expect(screen.getByRole('region', { name: 'Watch' })).toBeTruthy()
  })

  it('shows muted alerts only with the filter on', async () => {
    renderWithClient(<InboxPage />)
    await screen.findByRole('region', { name: 'Urgent' })
    expect(screen.queryByRole('region', { name: 'Muted' })).toBeNull()
    fireEvent.click(screen.getByRole('switch'))
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({ search: { muted: true } })
    )
    cleanup()
    routerState.search = { muted: true }
    renderWithClient(<InboxPage />)
    const muted = await screen.findByRole('region', { name: 'Muted' })
    expect(
      within(muted).getAllByRole('button', { name: 'Unmute' }).length
    ).toBeGreaterThan(0)
  })
})

it('reveals the sixth recent recovery when opened by a deep link', async () => {
  const original = (await getInbox({ state: 'done' })).items[0]
  const now = Date.now() / 1000
  const items = Array.from({ length: 6 }, (_, index) => ({
    ...original,
    id: 900 + index,
    title: `Recovery ${index}`,
    state: 'resolved' as const,
    severity_band: 'recovered' as const,
    resolved_at: now - index,
    updated_at: now - index,
  }))
  routerState.search = { item: 905 }
  const client = createTestQueryClient()
  client.setQueryData(monitorKeys.inbox({ state: 'done', target: undefined }), {
    items,
  })
  renderWithClient(<InboxPage />, client)
  const recovered = await screen.findByRole('region', {
    name: 'Recovered in the last 24 hours',
  })
  await waitFor(() =>
    expect(recovered.querySelector('[data-inbox-item="905"]')).toBeTruthy()
  )
  expect(
    recovered.querySelector('[data-inbox-item="905"]')?.className
  ).toContain('shadow-focus')
})

it('shows a snoozed alert and a later same-ID recurrence without remounting', async () => {
  const { client } = renderWithClient(<InboxPage />)
  const title = await within(
    await screen.findByRole('region', { name: 'Urgent' })
  ).findByText('Stuck query')
  const row = title.closest('article') as HTMLElement
  const id = Number(row.dataset.inboxItem)
  fireEvent.keyDown(
    within(row).getByRole('button', { name: 'More ways to close' }),
    { key: 'ArrowDown' }
  )
  fireEvent.click(await screen.findByText('Snooze for 1 hour'))
  await screen.findByRole('button', { name: 'Show snoozed' })
  fireEvent.click(screen.getByRole('button', { name: 'Show snoozed' }))
  expect(await screen.findByRole('button', { name: 'Unsnooze' })).toBeTruthy()
  await inboxAction(id, 'reopen')
  await client.invalidateQueries({ queryKey: monitorKeys.inboxAll })
  expect(
    await within(
      await screen.findByRole('region', { name: 'Urgent' })
    ).findByText('Stuck query')
  ).toBeTruthy()
})
