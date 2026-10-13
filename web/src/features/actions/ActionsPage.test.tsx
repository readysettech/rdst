import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetActionStubState } from '@/lib/monitor/actions'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { settingsFixture } from '@/lib/monitor/fixtures'
import {
  appliedFixture,
  globalActionsFixture,
} from '@/lib/monitor/fixturesActions'
import { renderWithClient } from '@/test-utils'

const toasts = vi.hoisted(() => ({
  toast: vi.fn((_options: { title: string; action?: ReactElement }) => ({
    dismiss: vi.fn(),
  })),
}))
vi.mock('@rs/ui-new/use-toast', () => ({
  useToast: () => ({ toast: toasts.toast }),
}))

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

const signIn = vi.hoisted(() => ({ held: new Set<string>() }))

vi.mock('@/features/monitor-kit/signInHold', () => ({
  useSignInHeld: () => signIn.held,
}))

const claim = vi.hoisted(() => vi.fn())
const configured = vi.hoisted(() => ({
  databases: [] as { name: string }[],
  settled: false,
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useConfiguredDatabases: () => ({
    databases: configured.databases,
    doFirst: null,
    isPending: false,
    isError: false,
    settled: configured.settled,
  }),
  useClaimAiMonitoring: () => ({ mutate: claim, isPending: false }),
  useMonitoringLimit: () => ({ data: undefined }),
}))

import { ActionsPage } from './ActionsPage'
import { planOrder } from './actionsModel'

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  resetActionStubState()
  setFixtureOverride('ca-database')
  routerState.search = {}
  routerState.navigate.mockReset()
  configured.databases = []
  configured.settled = false
  signIn.held = new Set()
  writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
  localStorage.clear()
})

const rows = () => screen.getAllByTestId('plan-row')
const groupTitles = () =>
  screen
    .getAllByTestId('plan-group')
    .map((group) => group.querySelector('h2')?.textContent)
/** The search the last navigate wrote, from an empty one. */
const lastSearch = () =>
  (
    routerState.navigate.mock.calls.at(-1)?.[0].search as (
      s: object
    ) => Record<string, unknown>
  )({})
const solid = (el: HTMLElement) =>
  el.className.includes('bg-surface-primary-solid')
/** The plan's cards across every database, in rank order. */
const planCards = () =>
  globalActionsFixture('ca-database', null, new Map(), new Map()).cards
const pane = () => screen.getByRole('region', { name: planCards()[0].title })
/** The `action` each navigate wrote, in order. */
const actionsWritten = () =>
  routerState.navigate.mock.calls.map(
    ([options]) =>
      (options.search as (s: object) => { action?: number })({}).action
  )

describe('ActionsPage with no database', () => {
  it('offers to add a database instead of saying checks run', async () => {
    setFixtureOverride('ca-home-empty')
    renderWithClient(<ActionsPage />)
    expect(await screen.findByText('No database yet')).toBeTruthy()
    expect(screen.queryByText(/checks each database/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add a database' }))
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '.' })
    )
  })
})

describe('ActionsPage with report checks that have no change here', () => {
  it('names them under the plan and opens the report', async () => {
    renderWithClient(<ActionsPage />)
    expect(
      await screen.findByText(
        'The report flags 1 check on demo with no change here'
      )
    ).toBeTruthy()
    expect(screen.getByText(/shared_buffers\./)).toBeTruthy()
    expect(
      screen.getByRole('link', { name: 'Open the report' }).getAttribute('href')
    ).toContain('view=report')
  })
})

describe('ActionsPage with nothing to change yet', () => {
  it('says the first plan is building, with the analyses done so far', async () => {
    setFixtureOverride('ca-first-run')
    renderWithClient(<ActionsPage />)
    expect(await screen.findByText('Building your first plan')).toBeTruthy()
    expect(
      screen.getByText(
        'Analyzing your busiest queries: 4 of 10 done. Changes show up as each one finishes.'
      )
    ).toBeTruthy()
    expect(screen.queryByText('Nothing to change right now')).toBeNull()
  })

  it('asks for a sign-in while the analyses wait for one', async () => {
    setFixtureOverride('ca-first-run')
    signIn.held = new Set(['demo'])
    renderWithClient(<ActionsPage />)
    expect(
      await screen.findByText('Sign in to run the first analyses')
    ).toBeTruthy()
    expect(screen.queryByText('Nothing to change right now')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(routerState.navigate).toHaveBeenCalledWith({ to: '/account-login' })
  })
})

describe('ActionsPage, the plan', () => {
  it('groups every database by category under the first change, ranks kept across the plan', async () => {
    renderWithClient(<ActionsPage />)
    expect(await screen.findByText('7 to do across 2 databases')).toBeTruthy()
    expect(screen.getByTestId('actions-lead').textContent).toBe(
      "Start with the index on posts: one change speeds up 3 queries that take 46% of demo's database time."
    )
    expect(groupTitles()).toEqual([
      'Indexes',
      'Server settings',
      'Maintenance',
      'Query rewrites',
    ])
    const groups = screen.getAllByTestId('plan-group')
    expect(groups[0].textContent).toContain('Indexes66 to add, 2 to keep')
    // An empty category stays as its one quiet line.
    expect(groups[1].textContent).toBe('Server settings0Nothing to change')
    expect(within(groups[1]).queryAllByTestId('plan-row')).toHaveLength(0)
    expect(groups[1].querySelector('h2')?.className).toContain(
      'text-content-layout-2'
    )
    expect(groups[2].textContent).toContain('Maintenance11 to run, no downtime')
    // Rows keep their place in the whole plan: Vacuum is 5th, after the indexes ranked 6 and 7.
    expect(rows().map((row) => row.dataset.rank)).toEqual([
      '1',
      '2',
      '3',
      '4',
      '6',
      '7',
      '5',
    ])
    // The group names the kind, so the line under the name starts at the database.
    expect(rows()[1].textContent).toContain('analytics, composite, 2 columns')
    expect(rows()[1].textContent).not.toContain('Index, analytics')
    expect(rows()[0].textContent).toContain('14 s')
    // One cream button: the first change's Copy SQL.
    const copies = screen.getAllByRole('button', { name: 'Copy SQL' })
    expect(copies.filter(solid)).toEqual([copies[0]])
    expect(within(rows()[0]).getByRole('button', { name: 'Copy SQL' })).toBe(
      copies[0]
    )
    // Copy all SQL waits for one database.
    expect(screen.queryByRole('button', { name: 'Copy all SQL' })).toBeNull()
    expect(screen.queryByText('Keep 2 indexes on demo')).toBeNull()
    expect(screen.getByText('2 smaller wins left out')).toBeTruthy()
    expect(
      screen.getByText(
        "Each group is ranked by database time saved a minute. The number is the change's place in the whole plan."
      )
    ).toBeTruthy()
  })

  it('starts each row with its verb and names the change without it', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const verbs = rows().map((row) => within(row).getByTestId('plan-verb'))
    expect(verbs.map((verb) => verb.textContent)).toEqual([
      'Add',
      'Add',
      'Add',
      'Add',
      'Add',
      'Add',
      'Vacuum',
    ])
    expect(verbs[0].className).toContain('text-content-positive-soft')
    expect(verbs[6].className).toContain('text-content-layout-2')
    // The first row's verb reads one size larger, with its saving.
    expect(verbs[0].className).toContain('text-body-medium')
    expect(verbs[1].className).toContain('text-body-small')
    const name = (row: HTMLElement) =>
      row.querySelector('[id$="-open"]') as HTMLElement
    expect(name(rows()[0]).textContent).toContain(
      'posts (creationdate, owneruserid, score)demo, composite, 3 columns'
    )
    expect(
      screen.getByRole('button', {
        name: 'Add posts (creationdate, owneruserid, score)',
      })
    ).toBe(name(rows()[0]))
    expect(screen.getByRole('button', { name: 'Vacuum sessions' })).toBe(
      name(rows()[6])
    )
    expect(screen.getByText('Dismissed (2)')).toBeTruthy()
  })

  it('narrows to a kind a link names, keeping its rank, with one chip that clears it', async () => {
    routerState.search = { kind: 'maintenance' }
    const { rerender } = renderWithClient(<ActionsPage />)
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(rows()[0].textContent).toContain('Vacuumsessions')
    expect(rows()[0].dataset.rank).toBe('5')
    expect(screen.getByText('1 of 7 to do')).toBeTruthy()
    expect(groupTitles()).toEqual(['Maintenance'])
    const filters = screen.getByTestId('actions-filters')
    expect(within(filters).queryByRole('group')).toBeNull()
    const chip = within(filters).getByRole('button', {
      name: 'Maintenance, clear',
    })
    expect(chip.textContent).toBe('Maintenance')
    expect(chip.getAttribute('aria-pressed')).toBeNull()
    // Kept indexes are indexes: their toggle leaves with another kind.
    expect(
      within(filters).queryByRole('button', { name: /^Kept indexes/ })
    ).toBeNull()
    fireEvent.click(chip)
    expect(lastSearch()).toEqual({})

    routerState.search = { group: 'database' }
    rerender(<ActionsPage />)
    await waitFor(() => expect(groupTitles()).toEqual(['demo', 'analytics']))
    expect(rows()).toHaveLength(7)
  })

  it('groups by priority from the URL, with the kind word back under the name', async () => {
    routerState.search = { group: 'priority' }
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    expect(groupTitles()).toEqual(['Do first', 'Then', 'Later'])
    expect(rows().map((row) => row.dataset.rank)).toEqual([
      '1',
      '2',
      '3',
      '4',
      '5',
      '6',
      '7',
    ])
    expect(rows()[1].textContent).toContain(
      'Index, analytics, composite, 2 columns'
    )
    expect(rows()[4].textContent).toContain('Maintenance, analytics')
    expect(
      screen
        .getByRole('radio', { name: 'Priority' })
        .getAttribute('aria-checked')
    ).toBe('true')
    expect(
      screen.getByText(
        'Ranked by database time saved a minute, so changes on different databases compare.'
      )
    ).toBeTruthy()
  })

  it('remembers the grouping and writes it to the URL on arrival', async () => {
    localStorage.setItem('rdst_actions_group', 'priority')
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    expect(groupTitles()).toEqual(['Do first', 'Then', 'Later'])
    expect(lastSearch()).toEqual({ group: 'priority' })
  })

  it('adds the kept indexes on request: the first, then the rest behind one row', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const toggle = screen.getByRole('button', { name: 'Kept indexes 2' })
    expect(toggle.getAttribute('aria-pressed')).toBe('false')
    expect(toggle.className).toContain('ml-auto')
    expect(screen.queryByTestId('kept-row')).toBeNull()
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    const [kept] = await screen.findAllByTestId('kept-row')
    expect(screen.getAllByTestId('kept-row')).toHaveLength(1)
    // Under the Indexes group's changes, with no rank, button or saving.
    const indexes = screen.getAllByTestId('plan-group')[0]
    expect(within(indexes).getByTestId('kept-row')).toBe(kept)
    expect(kept.textContent).toBe(
      'Keepposts_pkey on postsdemo, primary key, read 412,000 times in the last 7 daysRead 412,000 timesin the last 7 days'
    )
    expect(within(kept).queryByRole('button')).toBeNull()
    expect(within(kept).queryByTestId('plan-rank')).toBeNull()
    const more = screen.getByTestId('kept-more')
    expect(more.textContent).toBe(
      '1 more to keep, your queries read these or they enforce a key'
    )
    expect(more.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(more)
    await waitFor(() =>
      expect(screen.getAllByTestId('kept-row')).toHaveLength(2)
    )
    expect(screen.getAllByTestId('kept-row')[1].textContent).toContain(
      'users_pkey on users'
    )
    // Kept indexes never count as changes.
    expect(screen.getByText('7 to do across 2 databases')).toBeTruthy()
  })

  it('lists the kept indexes as a last Keep group by priority', async () => {
    routerState.search = { group: 'priority' }
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    fireEvent.click(screen.getByRole('button', { name: 'Kept indexes 2' }))
    await screen.findAllByTestId('kept-row')
    expect(groupTitles()).toEqual(['Do first', 'Then', 'Later', 'Keep'])
    const keep = screen.getAllByTestId('plan-group')[3]
    expect(keep.textContent).toContain('Keep2, no change needed')
    expect(within(keep).getByTestId('kept-row').textContent).toContain(
      'Index, demo, primary key'
    )
  })

  it('drops a remembered database that no longer exists for all databases', async () => {
    routerState.search = { db: 'retired' }
    configured.databases = [{ name: 'demo' }, { name: 'shop' }]
    configured.settled = true
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const bar = document.querySelector('[data-page-bar]') as HTMLElement
    expect(
      within(bar).getByRole('button', { name: 'Database: All databases' })
    ).toBeTruthy()
    expect(lastSearch()).toEqual({})
    expect(screen.getByText('7 to do across 2 databases')).toBeTruthy()
  })

  it('keeps a database that still exists', async () => {
    routerState.search = { db: 'demo' }
    configured.databases = [{ name: 'demo' }, { name: 'shop' }]
    configured.settled = true
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    expect(screen.getByRole('button', { name: 'Database: demo' })).toBeTruthy()
  })

  it('copies one script for the database picked', async () => {
    routerState.search = { db: 'demo' }
    renderWithClient(<ActionsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Copy all SQL' }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    const script = String(writeText.mock.calls[0][0])
    expect(script.startsWith('-- CONCURRENTLY keeps writes flowing')).toBe(true)
    expect(script).toContain('\n\n-- 1. Add an index on posts')
    expect(script).toContain('-- To undo these changes, run these:')
    expect(script).not.toContain('events')
    expect(screen.getByText('5 of 7 to do')).toBeTruthy()
  })

  it('opens a change in the side pane on its evidence, what if, SQL and rollback', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    expect(
      document.querySelector('[data-two-pane]')?.hasAttribute('data-open')
    ).toBe(false)
    expect(screen.queryByTestId('plan-detail')).toBeNull()
    fireEvent.click(
      screen.getByRole('button', {
        name: /Add posts \(creationdate/,
      })
    )
    const region = await screen.findByRole('region', {
      name: planCards()[0].title,
    })
    expect(
      document.querySelector('[data-two-pane]')?.hasAttribute('data-open')
    ).toBe(true)
    expect(actionsWritten()).toEqual([planCards()[0].id])
    // The header: the rank mark, then Do first, Index and the database.
    expect(within(region).getByTestId('plan-pane-rank').textContent).toBe('1')
    expect(region.textContent).toContain('Do firstIndexdemo')
    const detail = within(region).getByTestId('plan-detail')
    expect(within(detail).getByRole('heading', { level: 2 }).textContent).toBe(
      planCards()[0].title
    )
    expect(within(detail).getByTestId('plan-pane-saves').textContent).toBe(
      '14 sa min'
    )
    expect(detail.textContent).toContain('What it doesSpeeds up 3 queries')
    expect(detail.textContent).toContain('Evidence, the 3 queries it speeds up')
    const open = within(detail).getAllByRole('link', { name: 'Open query' })
    expect(open[0].getAttribute('href')).toBe(
      '/database?target=demo&query=ca0000000001'
    )
    expect(within(detail).getByTestId('plan-what-if').textContent).toContain(
      '2.6x faster'
    )
    expect(within(detail).getByTestId('plan-sql').textContent).toContain(
      'CREATE INDEX CONCURRENTLY idx_posts_creationdate_owneruserid_score'
    )
    expect(detail.textContent).toContain('Builds without blocking writes.')
    expect(
      within(detail).getByTestId('plan-rollback-sql').textContent
    ).toContain('DROP INDEX CONCURRENTLY')
    expect(detail.textContent).toContain('Drops it without blocking writes.')
    expect(
      within(detail)
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent)
    ).toEqual([
      'Saves',
      'What it does',
      'Evidence, the 3 queries it speeds up',
      'What if, measured',
      'SQL',
      'Rollback',
    ])
    // The pane holds the change's actions: the list keeps rank, name and saving, so the one
    // solid on the page is I did this.
    expect(screen.getAllByRole('button', { name: 'Copy SQL' })).toHaveLength(1)
    expect(
      screen.getAllByRole('button').filter((button) => solid(button))
    ).toHaveLength(1)
    expect(screen.queryByText('What it does', { selector: 'span' })).toBeNull()
    // Copy SQL and Copy as migration sit under the SQL; the footer holds the verbs.
    fireEvent.click(within(detail).getByRole('button', { name: 'Copy SQL' }))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(planCards()[0].sql)
    )
    fireEvent.click(
      within(detail).getByRole('button', { name: 'Copy rollback' })
    )
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(planCards()[0].rollback_sql)
    )
    expect(
      within(detail).getByRole('button', { name: 'Copy as migration' })
    ).toBeTruthy()
    const verbs = within(region).getByTestId('plan-verbs')
    expect(
      within(verbs)
        .getAllByRole('button')
        .map((button) => [button.textContent, solid(button)])
    ).toEqual([
      ['I did this', true],
      ['Set aside', false],
      ['Dismiss', false],
    ])
  })

  it('marks the opened row raised with the info border', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const name = (row: HTMLElement) =>
      row.querySelector('[id$="-open"]') as HTMLElement
    fireEvent.click(name(rows()[1]))
    await screen.findByTestId('plan-detail')
    const cells = (row: HTMLElement) => row.firstElementChild as HTMLElement
    expect(cells(rows()[1]).className).toContain('border-border-info-soft')
    expect(cells(rows()[1]).className).toContain('bg-surface-primary-soft')
    expect(cells(rows()[1]).className).not.toContain('before:bg-series-1')
    expect(name(rows()[1]).getAttribute('aria-current')).toBe('true')
    expect(cells(rows()[0]).className).not.toContain('border-border-info-soft')
    expect(name(rows()[0]).getAttribute('aria-current')).toBeNull()
  })

  it('walks the plan with up and down, and with j and k', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    // Up and down follow the rows as listed: by category, Indexes first.
    const cards = planOrder(planCards(), 'category')
    const title = () =>
      within(screen.getByTestId('plan-detail')).getByRole('heading', {
        level: 2,
      }).textContent
    fireEvent.keyDown(document.body, { key: 'j' })
    await waitFor(() => expect(title()).toBe(cards[0].title))
    // The first row has nothing above it.
    expect(screen.queryByRole('button', { name: 'Previous' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(title()).toBe(cards[1].title))
    fireEvent.keyDown(document.body, { key: 'j' })
    await waitFor(() => expect(title()).toBe(cards[2].title))
    fireEvent.keyDown(document.body, { key: 'k' })
    await waitFor(() => expect(title()).toBe(cards[1].title))
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    await waitFor(() => expect(title()).toBe(cards[0].title))
    expect(actionsWritten()).toEqual([
      cards[0].id,
      cards[1].id,
      cards[2].id,
      cards[1].id,
      cards[0].id,
    ])
    // The last row has nothing below it.
    for (let step = 0; step < cards.length; step++)
      fireEvent.keyDown(document.body, { key: 'j' })
    await waitFor(() => expect(title()).toBe(cards.at(-1)?.title))
    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull()
  })

  it('closes on Escape or Close and hands focus back to the row', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const name = (row: HTMLElement) =>
      row.querySelector('[id$="-open"]') as HTMLElement
    fireEvent.click(name(rows()[2]))
    await screen.findByTestId('plan-detail')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('plan-detail')).toBeNull())
    expect(document.activeElement).toBe(name(rows()[2]))
    expect(actionsWritten().at(-1)).toBeUndefined()

    fireEvent.click(name(rows()[1]))
    await screen.findByTestId('plan-detail')
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByTestId('plan-detail')).toBeNull())
    expect(document.activeElement).toBe(name(rows()[1]))
  })

  it('keeps Escape for an open popover', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    fireEvent.click(rows()[0].querySelector('[id$="-open"]') as HTMLElement)
    const verbs = await screen.findByTestId('plan-verbs')
    fireEvent.click(within(verbs).getByRole('button', { name: 'Dismiss' }))
    const popover = await screen.findByRole('dialog', { name: 'Dismiss' })
    fireEvent.keyDown(within(popover).getByLabelText('Reason'), {
      key: 'Escape',
    })
    expect(screen.getByTestId('plan-detail')).toBeTruthy()
  })

  it('opens the change a link names and scrolls it into view', async () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    const third = globalActionsFixture(
      'ca-database',
      'demo',
      new Map(),
      new Map()
    ).cards[2]
    routerState.search = { db: 'demo', action: third.id }
    renderWithClient(<ActionsPage />)
    const detail = await screen.findByTestId('plan-detail')
    expect(within(detail).getByRole('heading', { level: 2 }).textContent).toBe(
      third.title
    )
    const row = rows().find((each) => each.dataset.rank === '3') as HTMLElement
    expect(row.textContent).toContain('Addcomments (postid)')
    expect(
      row.querySelector('[id$="-open"]')?.getAttribute('aria-current')
    ).toBe('true')
    expect(screen.getAllByTestId('plan-detail')).toHaveLength(1)
    expect(scrollIntoView.mock.contexts).toContain(row)
    expect(routerState.navigate).not.toHaveBeenCalled()
    // @ts-expect-error jsdom has no scrollIntoView of its own
    delete Element.prototype.scrollIntoView
  })

  it('leaves the pane closed for a change the view does not list', async () => {
    routerState.search = { kind: 'maintenance', action: planCards()[0].id }
    renderWithClient(<ActionsPage />)
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(screen.queryByTestId('plan-detail')).toBeNull()
  })

  it('dismisses a change with a reason, and brings it back', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    fireEvent.click(screen.getByRole('button', { name: /Add votes/ }))
    const verbs = await screen.findByTestId('plan-verbs')
    fireEvent.click(within(verbs).getByRole('button', { name: 'Dismiss' }))
    const popover = await screen.findByRole('dialog', { name: 'Dismiss' })
    fireEvent.change(within(popover).getByLabelText('Reason'), {
      target: { value: 'votes takes too many writes' },
    })
    fireEvent.click(within(popover).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(rows()).toHaveLength(6))
    fireEvent.click(screen.getByText('Dismissed (3)'))
    const dismissed = screen.getByTestId('plan-dismissed')
    expect(dismissed.textContent).toContain('votes takes too many writes')
    const back = within(dismissed).getAllByRole('button', {
      name: 'Bring back',
    })
    fireEvent.click(back[0])
    await waitFor(() => expect(rows()).toHaveLength(7))
  })

  it('sets a change aside until a date, and marks one done', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    fireEvent.click(screen.getByRole('button', { name: /Vacuum sessions/ }))
    let verbs = await screen.findByTestId('plan-verbs')
    fireEvent.click(within(verbs).getByRole('button', { name: 'Set aside' }))
    const popover = await screen.findByRole('dialog', { name: 'Set aside' })
    // A week from the fixture's Thursday, October 8.
    expect(
      (within(popover).getByLabelText('Until') as HTMLInputElement).value
    ).toBe('2026-10-15')
    fireEvent.click(within(popover).getByRole('button', { name: 'Set aside' }))
    expect(await screen.findByText('Set aside (1)')).toBeTruthy()
    expect(screen.getByTestId('plan-set-aside').textContent).toContain(
      'Vacuum sessions, until October 15'
    )
    // The toast's Undo brings the change back.
    const undo = toasts.toast.mock.calls.at(-1)?.[0]
    expect(undo?.title).toBe('Set aside')
    render(undo?.action as ReactElement)
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(screen.queryByText('Set aside (1)')).toBeNull())

    fireEvent.click(screen.getByRole('button', { name: /Add comments/ }))
    verbs = await screen.findByTestId('plan-verbs')
    fireEvent.click(within(verbs).getByRole('button', { name: 'I did this' }))
    // Vacuum sessions came back with Undo, so one row fewer than before.
    await waitFor(() => expect(rows()).toHaveLength(6))
  })
})

describe('ActionsPage, the page bar and the rows', () => {
  it('reads Plan and Applied as the page tabs with their counts', async () => {
    renderWithClient(<ActionsPage />)
    const tabs = await screen.findByRole('tablist', { name: 'Plan or applied' })
    await waitFor(() =>
      expect(
        within(tabs).getByRole('tab', { name: 'Plan 7' }).ariaSelected
      ).toBe('true')
    )
    const appliedTab = await within(tabs).findByRole('tab', {
      name: 'Applied 4',
    })
    expect(appliedTab.ariaSelected).toBe('false')
    expect(within(appliedTab).getByText('4').className).toContain(
      'text-content-layout-3'
    )
    expect(
      screen.queryByRole('radiogroup', { name: 'Plan or applied' })
    ).toBeNull()
    fireEvent.click(appliedTab)
    const { search } = routerState.navigate.mock.calls.at(-1)?.[0] ?? {}
    expect(search({})).toEqual({ scope: 'applied' })
  })

  it('puts the database picker in the bar and Group by over the table', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const bar = document.querySelector('[data-page-bar]') as HTMLElement
    expect(screen.getByText('7 to do across 2 databases')).toBeTruthy()
    expect(
      within(bar).getByRole('button', { name: 'Database: All databases' })
    ).toBeTruthy()
    const filters = screen.getByTestId('actions-filters')
    expect(filters.textContent?.startsWith('Group by')).toBe(true)
    expect(
      within(filters).queryByRole('group', { name: 'Kind of change' })
    ).toBeNull()
    expect(within(filters).queryByRole('button', { name: /^Sort/ })).toBeNull()
    const group = within(filters).getByRole('radiogroup', { name: 'Group by' })
    expect(
      within(group)
        .getAllByRole('radio')
        .map((radio) => [radio.textContent, radio.getAttribute('aria-checked')])
    ).toEqual([
      ['Category', 'true'],
      ['Priority', 'false'],
      ['Database', 'false'],
    ])
    fireEvent.click(within(group).getByRole('radio', { name: 'Priority' }))
    expect(lastSearch()).toEqual({ group: 'priority' })
    expect(localStorage.getItem('rdst_actions_group')).toBe('priority')
    await waitFor(() =>
      expect(groupTitles()).toEqual(['Do first', 'Then', 'Later'])
    )
  })

  it('offers Database grouping only while the plan spans databases', async () => {
    routerState.search = { db: 'demo' }
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    expect(
      within(screen.getByRole('radiogroup', { name: 'Group by' }))
        .getAllByRole('radio')
        .map((radio) => radio.textContent)
    ).toEqual(['Category', 'Priority'])
  })

  it('marks Do first rows in the series colour, the first one largest, and dims Later', async () => {
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('plan-row')
    const tier = (name: string) =>
      rows().filter((row) => row.dataset.tier === name)
    const mark = (row: HTMLElement) => within(row).getByTestId('plan-rank')
    const figure = (row: HTMLElement) =>
      within(row).getByTestId('plan-saves').firstElementChild?.className ?? ''
    const first = tier('first')
    expect(first.length).toBeGreaterThan(1)
    expect(first[0]).toBe(rows()[0])
    for (const row of first) {
      expect(mark(row).className).toContain('bg-surface-info-soft')
      expect(row.querySelector('[data-open], .grid')?.className).toContain(
        'before:bg-series-1'
      )
    }
    expect(figure(first[0])).toContain('text-headline-2')
    expect(figure(first[1])).toContain('text-headline-3')
    const [then] = tier('then')
    expect(mark(then).className).toContain('bg-surface-primary-soft')
    expect(then.innerHTML).not.toContain('bg-series-1')
    const [later] = tier('later')
    expect(mark(later).className).toContain('border-border-layout-2')
    expect(
      later.querySelector('[id$="-open"]')?.firstElementChild?.className
    ).toContain('text-content-layout-3')
  })

  it('opens the first change in the pane from its name in the lead sentence', async () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    renderWithClient(<ActionsPage />)
    const link = await screen.findByRole('link', {
      name: 'the index on posts',
    })
    expect(screen.queryByTestId('plan-detail')).toBeNull()
    fireEvent.click(link)
    expect(await screen.findByTestId('plan-detail')).toBe(
      within(pane()).getByTestId('plan-detail')
    )
    expect(
      rows()[0].querySelector('[id$="-open"]')?.getAttribute('aria-current')
    ).toBe('true')
    expect(scrollIntoView.mock.contexts).toContain(rows()[0])
    fireEvent.click(within(pane()).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByTestId('plan-detail')).toBeNull())
    fireEvent.click(link)
    expect(await screen.findByTestId('plan-detail')).toBeTruthy()
    expect(actionsWritten()).toEqual([
      planCards()[0].id,
      undefined,
      planCards()[0].id,
    ])
    // @ts-expect-error jsdom has no scrollIntoView of its own
    delete Element.prototype.scrollIntoView
  })
})

describe('ActionsPage, applied', () => {
  it('reads the week, the days and each change with its status', async () => {
    routerState.search = { scope: 'applied' }
    renderWithClient(<ActionsPage />)
    expect((await screen.findByTestId('applied-lead')).textContent).toBe(
      'This week: 3 changes applied, 41% of database time saved. 2 verified, 1 measuring.'
    )
    const applied = screen.getAllByTestId('applied-row')
    expect(applied.map((row) => row.dataset.status)).toEqual([
      'measuring',
      'verified',
      'verified',
      'no_change',
    ])
    expect(applied[0].textContent).toContain(
      'demo, by you, applied from the plan'
    )
    expect(applied[0].textContent).toContain('38 ms before, measuring')
    expect(applied[0].textContent).toContain(
      '96 calls so far, next check in 40 min'
    )
    expect(applied[1].textContent).toContain('detected in the database')
    expect(applied[1].textContent).toContain('64 ms to 4.1 ms')
    expect(screen.getByText('Today')).toBeTruthy()
    expect(screen.getByText('Undone (1)')).toBeTruthy()
    expect(screen.getByText('Dismissed (2)')).toBeTruthy()
    // Grouping is the plan's: Applied reads by day, with no filters over it.
    expect(screen.queryByTestId('actions-filters')).toBeNull()
  })

  it('keeps a linked kind on Applied as its chip', async () => {
    routerState.search = { scope: 'applied', kind: 'index' }
    renderWithClient(<ActionsPage />)
    await screen.findAllByTestId('applied-row')
    const filters = screen.getByTestId('actions-filters')
    expect(within(filters).queryByRole('radiogroup')).toBeNull()
    expect(
      within(filters).getByRole('button', { name: 'Index, clear' })
    ).toBeTruthy()
  })

  it('shows the rollback SQL behind Undo and marks the change undone', async () => {
    routerState.search = { scope: 'applied' }
    renderWithClient(<ActionsPage />)
    const [first] = await screen.findAllByTestId('applied-row')
    fireEvent.click(within(first).getByRole('button', { name: 'Undo' }))
    const popover = await screen.findByRole('dialog', {
      name: /Undo Add an index on tags/,
    })
    expect(within(popover).getByTestId('undo-sql').textContent).toContain(
      'DROP INDEX CONCURRENTLY public.idx_tags_count_tagname;'
    )
    fireEvent.change(within(popover).getByLabelText('Reason'), {
      target: { value: 'Locks during the build' },
    })
    fireEvent.click(
      within(popover).getByRole('button', { name: 'I undid this' })
    )
    expect(await screen.findByText('Undone (2)')).toBeTruthy()
    expect(screen.getAllByTestId('applied-row')).toHaveLength(3)
  })
})

describe('ActionsPage against the backend', () => {
  it('posts actions/opened once per visit for the database picked', async () => {
    setFixtureOverride(null)
    routerState.search = { db: 'demo' }
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const body = url.startsWith('/api/monitor/actions/applied')
        ? appliedFixture('ca-database', 'demo', new Map(), new Map())
        : url.startsWith('/api/monitor/actions')
          ? globalActionsFixture('ca-database', 'demo', new Map(), new Map())
          : init?.method === 'POST'
            ? { target: 'demo', closed: true }
            : {}
      return new Response(JSON.stringify(body))
    })
    vi.stubGlobal('fetch', fetch)
    const { rerender } = renderWithClient(<ActionsPage />)
    expect(await screen.findByText('5 of 7 to do')).toBeTruthy()
    rerender(<ActionsPage />)
    const opened = () =>
      fetch.mock.calls.filter(
        ([url]) => url === '/api/monitor/targets/demo/actions/opened'
      )
    await waitFor(() => expect(opened()).toHaveLength(1))
    expect(
      fetch.mock.calls.map(([url]) => url).filter((url) => url.includes('?db='))
    ).toEqual(
      expect.arrayContaining([
        '/api/monitor/actions?db=demo',
        '/api/monitor/actions/applied?db=demo',
      ])
    )
  })
})

describe('ActionsPage with nothing to change, against the backend', () => {
  const empty = (
    databases: { database: string; monitored: boolean }[],
    db: string | null = null
  ) => ({
    ...globalActionsFixture('ca-database', db, new Map(), new Map()),
    db,
    cards: [],
    set_aside: [],
    dismissed: [],
    report_checks: {},
    keep: {},
    left_out: {},
    unmonitored: databases
      .filter((row) => !row.monitored)
      .map((row) => row.database),
    databases: databases.map((row) => ({
      ...row,
      to_do: 0,
      do_first: 0,
      checkup: null,
    })),
  })
  const serve = (page: object, settings: Record<string, object> = {}) => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const target = url.match(/targets\/([^/]+)\/settings/)?.[1]
      const body = target
        ? { ...settingsFixture('localdev'), enabled: true, ...settings[target] }
        : url.startsWith('/api/monitor/actions/applied')
          ? appliedFixture('ca-database', null, new Map(), new Map())
          : url.startsWith('/api/monitor/actions')
            ? page
            : init?.method === 'POST'
              ? { target: 'demo', closed: false }
              : {}
      return new Response(JSON.stringify(body))
    })
    vi.stubGlobal('fetch', fetch)
    return fetch
  }

  it('says why AI is off and how to turn it on instead of claiming checks run', async () => {
    setFixtureOverride(null)
    serve(empty([{ database: 'demo', monitored: true }]), {
      demo: { ai_monitoring: 'local', ai_reason: 'limit' },
    })
    renderWithClient(<ActionsPage />)
    expect(
      await screen.findByText('Make an AI slot available to turn on AI here')
    ).toBeTruthy()
    expect(screen.getByTestId('actions-ai-off').textContent).toContain(
      "Your account's AI slots are all in use."
    )
    fireEvent.click(screen.getByRole('button', { name: 'Manage databases' }))
    expect(routerState.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'connections' },
    })
    expect(screen.queryByText('Nothing to change right now')).toBeNull()
  })

  it("doesn't claim RDST checks a database that isn't monitored", async () => {
    setFixtureOverride(null)
    routerState.search = { db: 'shop' }
    serve(empty([{ database: 'shop', monitored: false }], 'shop'))
    renderWithClient(<ActionsPage />)
    expect(await screen.findByText("shop isn't monitored")).toBeTruthy()
    expect(screen.queryByText(/RDST checks each database/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
    expect(routerState.navigate).toHaveBeenCalledWith({
      to: '/database',
      search: { target: 'shop', panel: 'enable' },
    })
  })

  it('posts actions/opened for every monitored database in the all-databases view', async () => {
    setFixtureOverride(null)
    const fetch = serve(
      empty([
        { database: 'demo', monitored: true },
        { database: 'analytics', monitored: true },
        { database: 'shop', monitored: false },
      ])
    )
    renderWithClient(<ActionsPage />)
    const opened = () =>
      fetch.mock.calls
        .map(([url]) => url)
        .filter((url) => url.endsWith('/actions/opened'))
    await waitFor(() =>
      expect(opened().sort()).toEqual([
        '/api/monitor/targets/analytics/actions/opened',
        '/api/monitor/targets/demo/actions/opened',
      ])
    )
  })
})
