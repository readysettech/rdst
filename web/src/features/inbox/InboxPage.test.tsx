import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createMuteRule,
  deleteMuteRule,
  getInbox,
  inboxAction,
  resetStubState,
  setFixtureTiming,
} from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { CA_NOW } from '@/lib/monitor/fixturesCa'
import { renderWithClient } from '@/test-utils'
import { incidentInboxKey } from './useInboxUnread'

const routerState = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routerState.navigate,
  useSearch: () => routerState.search,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: routerState.search } }),
  Link: ({
    to,
    search,
    children,
    ...rest
  }: {
    to: string
    search?: Record<string, unknown>
    children?: ReactNode
  }) => (
    <a
      href={`${to}${search ? `?${new URLSearchParams(search as Record<string, string>)}` : ''}`}
      {...rest}
    >
      {children}
    </a>
  ),
}))

import { InboxPage } from './InboxPage'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('ca-inbox')
  routerState.search = {}
  routerState.navigate.mockReset()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  setFixtureOverride(undefined)
  localStorage.clear()
})

const region = (name: string) => screen.findByRole('region', { name })
const solid = (el: HTMLElement) =>
  el.className.includes('bg-surface-primary-solid')

describe('InboxPage queue', () => {
  it('reads the open list with incident windows, so items link to their time frame', async () => {
    const { client } = renderWithClient(<InboxPage />)
    expect(await screen.findByRole('radio', { name: 'Open 2' })).toBeTruthy()
    const key = incidentInboxKey({
      state: 'open',
      target: undefined,
      include_muted: true,
      incidents: true,
    })
    expect(client.getQueryData(key)).toBeTruthy()
  })

  it('shows two panels: the queue with its filter row and groups, and nothing selected', async () => {
    renderWithClient(<InboxPage />)
    expect(
      screen.getByRole('heading', { level: 1, name: 'Inbox' })
    ).toBeTruthy()
    const filters = screen.getByRole('group', { name: 'Filters' })
    const scope = within(filters).getByRole('radiogroup', {
      name: 'Open or closed',
    })
    expect(
      (
        await within(scope).findByRole('radio', { name: 'Open 2' })
      ).getAttribute('aria-checked')
    ).toBe('true')
    expect(within(scope).getByRole('radio', { name: 'Closed 8' })).toBeTruthy()
    expect(
      within(filters).getByRole('button', { name: 'Database: All databases' })
    ).toBeTruthy()
    for (const name of ['Act now', 'Watch'])
      expect(await region(name)).toBeTruthy()
    // Closed items live behind Closed.
    expect(screen.queryByRole('region', { name: 'Closed today' })).toBeNull()
    expect(
      within(await region('Act now')).getByRole('button', {
        name: 'Stuck query on demo',
      })
    ).toBeTruthy()
    expect(screen.getByText('running 4 min')).toBeTruthy()
    const empty = await region('Nothing selected')
    expect(
      within(empty).getByText('1 item needs you, 1 to watch.')
    ).toBeTruthy()
    expect(within(empty).getByText(/Closed items stay 7 days/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Notifications' })).toBeTruthy()
  })

  it('puts the time over the database at the right edge of each row', async () => {
    renderWithClient(<InboxPage />)
    const row = await screen.findByRole('button', {
      name: 'Stuck query on demo',
    })
    const database = within(row).getByTestId('inbox-item-database')
    expect(database.textContent).toBe('demo')
    expect(database.previousElementSibling?.textContent).toBe('9:55')
    const ready = screen.getByRole('button', {
      name: '5 changes ready on demo',
    })
    expect(ready.querySelector('svg')).toBeTruthy()
    expect(
      within(ready).getByText(
        '2 to do first, starting with index posts (creationdate, owneruserid, score).'
      )
    ).toBeTruthy()
  })

  it('narrows to one database, remembered for the Inbox, and keeps reading an older ?target=', async () => {
    renderWithClient(<InboxPage />)
    fireEvent.keyDown(
      await screen.findByRole('button', { name: 'Database: All databases' }),
      { key: 'Enter' }
    )
    fireEvent.click(await screen.findByRole('menuitem', { name: /^staging/ }))
    expect(localStorage.getItem('rdst_filter_db:inbox')).toBe('staging')
    expect(routerState.navigate).toHaveBeenLastCalledWith(
      expect.objectContaining({
        to: '/inbox',
        search: { db: 'staging' },
      })
    )
    cleanup()
    localStorage.clear()
    routerState.search = { target: 'analytics', state: 'closed' }
    renderWithClient(<InboxPage />)
    expect(
      await screen.findByRole('button', { name: 'Database: analytics' })
    ).toBeTruthy()
    await waitFor(() =>
      expect(localStorage.getItem('rdst_filter_db:inbox')).toBe('analytics')
    )
    expect(
      await screen.findByRole('button', {
        name: 'Stuck query on analytics ended by itself',
      })
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Traffic spike on demo ended' })
    ).toBeNull()
  })

  it('selects a row into the URL and marks it seen', async () => {
    renderWithClient(<InboxPage />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Stuck query on demo' })
    )
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        search: expect.objectContaining({ item: 1 }),
        replace: true,
      })
    )
    await waitFor(async () =>
      expect(
        (await getInbox({ state: 'open' })).items.find((item) => item.id === 1)
          ?.state
      ).toBe('seen')
    )
  })

  it('lists Closed by day with the retention note', async () => {
    routerState.search = { state: 'closed' }
    renderWithClient(<InboxPage />)
    for (const [name, count] of [
      ['Today', 3],
      ['Yesterday', 3],
      ['Earlier this week', 2],
    ] as const)
      expect(within(await region(name)).getAllByRole('button').length).toBe(
        count
      )
    expect(screen.getByText('Closed items stay 7 days.')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Act now' })).toBeNull()
  })

  it('scrolls the queue in its own scroll area under a fixed header, keeping j in view', async () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    routerState.search = { item: 1 }
    renderWithClient(<InboxPage />)
    await region('Stuck query on demo')
    const queue = screen.getByRole('region', { name: 'Queue' })
    const viewport = queue.querySelector(
      '[data-radix-scroll-area-viewport]'
    ) as HTMLElement
    expect(viewport).toBeTruthy()
    expect(
      viewport.contains(screen.getByRole('heading', { name: 'Inbox' }))
    ).toBe(false)
    const panel = queue.closest('[data-two-pane]')?.firstElementChild
    expect(panel?.className).toContain('overflow-hidden')
    expect(panel?.className).not.toContain('overflow-y-auto')
    fireEvent.keyDown(document.body, { key: 'j' })
    const row = scrollIntoView.mock.contexts.at(-1) as HTMLElement
    expect(row.getAttribute('data-inbox-item')).toBe('2')
    expect(viewport.contains(row)).toBe(true)
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'nearest' })
    // @ts-expect-error jsdom has no scrollIntoView of its own
    delete Element.prototype.scrollIntoView
  })

  it('moves the selection with j and k, resolves with e and opens with o', async () => {
    renderWithClient(<InboxPage />)
    await screen.findByRole('button', { name: 'Stuck query on demo' })
    fireEvent.keyDown(document.body, { key: 'j' })
    expect(routerState.navigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: expect.objectContaining({ item: 1 }) })
    )
    cleanup()
    routerState.search = { item: 1 }
    renderWithClient(<InboxPage />)
    await region('Stuck query on demo')
    fireEvent.keyDown(document.body, { key: 'j' })
    expect(routerState.navigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: expect.objectContaining({ item: 2 }) })
    )
    fireEvent.keyDown(document.body, { key: 'o' })
    expect(routerState.navigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ to: '/database' })
    )
    fireEvent.keyDown(document.body, { key: 'e' })
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Stuck query on demo' })
      ).toBeNull()
    )
    await waitFor(async () =>
      expect(
        (await getInbox({ state: 'done' })).items.some((item) => item.id === 1)
      ).toBe(true)
    )
  })
})

describe('InboxPage detail', () => {
  it('reads a stuck query: steps with SQL to copy, history and related rows', async () => {
    routerState.search = { item: 1 }
    renderWithClient(<InboxPage />)
    const detail = await region('Stuck query on demo')
    expect(within(detail).getByText('Act now')).toBeTruthy()
    expect(within(detail).getByText('demo, filed 9:55 AM')).toBeTruthy()
    const open = within(detail).getByRole('link', { name: 'Open query' })
    expect(solid(open)).toBe(true)
    expect(open.getAttribute('href')).toContain('/database')
    expect(within(detail).getByRole('button', { name: 'Resolve' })).toBeTruthy()
    expect(
      within(detail).getByRole('button', {
        name: 'More actions for Stuck query on demo',
      })
    ).toBeTruthy()
    expect(
      within(detail).getByRole('heading', {
        level: 2,
        name: 'Stuck query on demo',
      })
    ).toBeTruthy()
    expect(within(detail).getByText('pid 48213')).toBeTruthy()
    const steps = within(detail).getByRole('region', { name: 'What to do' })
    expect(
      within(steps).getByText('SELECT pg_cancel_backend(48213);')
    ).toBeTruthy()
    expect(within(steps).getAllByRole('button', { name: 'Copy' }).length).toBe(
      3
    )
    expect(
      within(steps).getByText('Closes by itself when the statement ends.')
    ).toBeTruthy()
    expect(
      within(within(detail).getByRole('region', { name: 'History' })).getByText(
        /Statement passed/
      )
    ).toBeTruthy()
    const related = within(detail).getByRole('region', { name: 'Related' })
    const rows = within(related).getAllByRole('link')
    expect(rows.length).toBe(2)
    for (const row of rows)
      expect(row.style.gridTemplateColumns).toBe('1fr auto auto')
    expect(within(related).getByText('Connections 8 of 100')).toBeTruthy()
  })

  it('resolves from the header and the item leaves the queue', async () => {
    routerState.search = { item: 1 }
    renderWithClient(<InboxPage />)
    const detail = await region('Stuck query on demo')
    fireEvent.click(within(detail).getByRole('button', { name: 'Resolve' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Stuck query on demo' })
      ).toBeNull()
    )
    expect(await region('Nothing selected')).toBeTruthy()
  })

  it('reads Changes ready: the top changes and one cream Open Actions that closes it', async () => {
    routerState.search = { item: 2 }
    renderWithClient(<InboxPage />)
    const detail = await region('5 changes ready on demo')
    expect(within(detail).getByText('Watch')).toBeTruthy()
    expect(within(detail).getByText('demo, filed 9:51 AM')).toBeTruthy()
    expect(within(detail).getByText('2 to do first')).toBeTruthy()
    const top = within(detail).getByRole('region', { name: 'Top changes' })
    expect(
      within(top).getByText('Index posts (creationdate, owneruserid, score)')
    ).toBeTruthy()
    expect(within(top).getAllByText('Do first').length).toBe(2)
    expect(within(top).getByText('3 of 5')).toBeTruthy()
    const open = within(detail).getByRole('link', { name: 'Open Actions' })
    expect(solid(open)).toBe(true)
    expect(open.getAttribute('href')).toBe('/actions?db=demo')
    expect(
      within(detail).queryByRole('button', { name: 'Copy fix #1' })
    ).toBeNull()
    fireEvent.click(open)
    expect(routerState.navigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ to: '/actions', search: { db: 'demo' } })
    )
    await waitFor(async () =>
      expect(
        (await getInbox({ state: 'done' })).items.find((item) => item.id === 2)
          ?.resolved_by
      ).toBe('code')
    )
  })

  it('offers Resolve, Reopen and Mute, never snooze or dismiss', async () => {
    routerState.search = { item: 1 }
    renderWithClient(<InboxPage />)
    const detail = await region('Stuck query on demo')
    fireEvent.keyDown(
      within(detail).getByRole('button', {
        name: 'More actions for Stuck query on demo',
      }),
      { key: 'ArrowDown' }
    )
    expect(
      await screen.findByRole('menuitem', { name: 'Mark resolved' })
    ).toBeTruthy()
    expect(screen.queryByRole('menuitem', { name: /Snooze/ })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: /Dismiss/ })).toBeNull()
  })

  it('reads the report item: the verdict, three numbers and the next report', async () => {
    setFixtureOverride('ca-inbox-report')
    routerState.search = { item: 3 }
    renderWithClient(<InboxPage />)
    const detail = await region('Daily report for October 7 is ready')
    expect(
      solid(within(detail).getByRole('link', { name: 'Open report' }))
    ).toBe(true)
    expect(within(detail).queryByRole('button', { name: 'Resolve' })).toBeNull()
    expect(
      await within(detail).findByText('demo needs two things today:')
    ).toBeTruthy()
    for (const label of [
      'Fixes ready',
      'Checks needing you',
      'Queries analyzed',
    ])
      expect(within(detail).getByText(label)).toBeTruthy()
    expect(
      await within(detail).findByText('Next report at 12:00 AM.')
    ).toBeTruthy()
    expect(within(detail).getByRole('region', { name: 'History' })).toBeTruthy()
  })

  it('reads a confirmed fix: before and after, its place in cream and Reopen', async () => {
    routerState.search = { state: 'closed', item: 4 }
    renderWithClient(<InboxPage />)
    const detail = await region('Fix confirmed: SELECT on comments by userid')
    expect(within(detail).getByText('Closed')).toBeTruthy()
    expect(within(detail).getByText('demo, closed 8:12 AM')).toBeTruthy()
    expect(
      solid(within(detail).getByRole('link', { name: 'Open query' }))
    ).toBe(true)
    expect(within(detail).queryByRole('button', { name: 'Resolve' })).toBeNull()
    expect(within(detail).getByRole('button', { name: 'Reopen' })).toBeTruthy()
    expect(within(detail).getByText('Before')).toBeTruthy()
    expect(within(detail).getByText('After')).toBeTruthy()
    expect(within(detail).getByText('14x faster over 40 calls')).toBeTruthy()
    expect(within(detail).getByRole('region', { name: 'Related' })).toBeTruthy()
  })

  it('reads No change yet as a closed, neutral row that opens Applied', async () => {
    routerState.search = { state: 'closed', item: 13 }
    renderWithClient(<InboxPage />)
    const row = await screen.findByRole('button', {
      name: 'No change yet: SELECT on users by reputation',
    })
    expect(
      within(row).getByText('index applied October 6, still 14 ms after 2 days')
    ).toBeTruthy()
    const detail = await region('No change yet: SELECT on users by reputation')
    expect(within(detail).getByText('Closed')).toBeTruthy()
    expect(within(detail).getByText('Before')).toBeTruthy()
    expect(within(detail).getByText('over 40 calls')).toBeTruthy()
    const applied = within(detail).getByRole('link', { name: 'Open Applied' })
    expect(applied.getAttribute('href')).toBe('/actions?db=demo&scope=applied')
    fireEvent.click(within(detail).getByRole('button', { name: 'Reopen' }))
    await waitFor(async () =>
      expect(
        (await getInbox({ state: 'open' })).items.some((item) => item.id === 13)
      ).toBe(true)
    )
  })

  it('lands a link to an older closed item on Closed', async () => {
    routerState.search = { item: 6 }
    renderWithClient(<InboxPage />)
    await region('Act now')
    await waitFor(() =>
      expect(routerState.navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          search: expect.objectContaining({ state: 'closed', item: 6 }),
        })
      )
    )
  })
})

describe('InboxPage muted items', () => {
  const muteDemo = (type: string) =>
    createMuteRule({
      target: 'demo',
      query_key: null,
      type,
      until: null,
      note: null,
    })
  // The stub starts with three rules that silence nothing in the Inbox fixture.
  const clearRules = async () => {
    for (const id of [1, 2, 3]) await deleteMuteRule(id)
  }

  it('shows no Muted chip when nothing is muted, snoozed or ruled out', async () => {
    await clearRules()
    renderWithClient(<InboxPage />)
    await region('Act now')
    expect(screen.queryByRole('button', { name: /^Muted/ })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Muted' })).toBeNull()
  })

  it('counts muted and snoozed items on the Muted chip and lists them with the rules', async () => {
    // The seeded expiring rule is timed against the fixture clock.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(CA_NOW * 1000)
    await muteDemo('stuck_query')
    await inboxAction(2, 'snooze', CA_NOW + 3600)
    renderWithClient(<InboxPage />)
    const filters = screen.getByRole('group', { name: 'Filters' })
    const chip = await within(filters).findByRole('button', { name: 'Muted 2' })
    expect(chip.getAttribute('aria-pressed')).toBe('false')
    expect(screen.queryByRole('region', { name: 'Muted' })).toBeNull()
    fireEvent.click(chip)
    const muted = await region('Muted')
    expect(
      within(muted).getByRole('button', { name: 'Stuck query on demo' })
    ).toBeTruthy()
    expect(
      within(muted).getByRole('button', { name: '5 changes ready on demo' })
    ).toBeTruthy()
    const rules = await region('Mute rules')
    expect(
      within(rules).getAllByRole('button', { name: 'Unmute' }).length
    ).toBe(4)
    expect(screen.queryByRole('region', { name: 'Act now' })).toBeNull()
    fireEvent.click(chip)
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Mute rules' })).toBeNull()
    )
    expect(chip.getAttribute('aria-pressed')).toBe('false')
  })

  it('keeps Muted reachable after muting the only items, and Unmute brings them back', async () => {
    await clearRules()
    await muteDemo('stuck_query')
    await muteDemo('changes_ready')
    renderWithClient(<InboxPage />)
    expect(await screen.findByRole('radio', { name: 'Open 0' })).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Muted 2' }))
    const rules = await region('Mute rules')
    const unmute = within(rules).getAllByRole('button', { name: 'Unmute' })
    expect(unmute.length).toBe(2)
    fireEvent.click(unmute[0])
    fireEvent.click(unmute[1])
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /^Muted/ })).toBeNull()
    )
    expect(await screen.findByRole('radio', { name: 'Open 2' })).toBeTruthy()
  })

  it('shows Muted while a rule is active even when it silences nothing in view', async () => {
    await clearRules()
    await createMuteRule({
      target: 'elsewhere',
      query_key: null,
      type: 'stuck_query',
      until: null,
      note: null,
    })
    renderWithClient(<InboxPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Muted' }))
    expect(await region('Mute rules')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Muted' })).toBeNull()
  })

  it('keeps muted and snoozed items out of the Open groups', async () => {
    await inboxAction(2, 'snooze', CA_NOW + 3600)
    renderWithClient(<InboxPage />)
    await region('Act now')
    expect(screen.queryByRole('region', { name: 'Watch' })).toBeNull()
  })
})

describe('InboxPage with no database', () => {
  it('offers to add a database in place of the queue hint', async () => {
    setFixtureOverride('ca-home-empty')
    renderWithClient(<InboxPage />)
    const empty = await region('Nothing selected')
    expect(await within(empty).findByText('No database yet')).toBeTruthy()
    expect(within(empty).queryByText(/Pick one on the left/)).toBeNull()
    fireEvent.click(
      within(empty).getByRole('button', { name: 'Add a database' })
    )
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '.' })
    )
  })
})
