import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { trackEvent } from '../lib/analytics'
import type { ConfiguredDatabase } from '../lib/monitor/hooks'
import { renderWithClient } from '../test-utils'
import { SHOWN_DATABASES, Sidebar } from './Sidebar'

const routerState = vi.hoisted(() => ({ pathname: '/' }))

vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { pathname: routerState.pathname } }),
  useNavigate: () => vi.fn(),
  Link: ({
    to,
    search,
    children,
    ...rest
  }: {
    to: string
    search?: Record<string, string>
    children?: ReactNode
  } & Record<string, unknown>) => (
    <a href={search ? `${to}?${new URLSearchParams(search)}` : to} {...rest}>
      {children}
    </a>
  ),
}))

const inboxUnread = vi.hoisted(() => ({ count: 0, actNow: 0, loading: false }))
vi.mock('../features/inbox/useInboxUnread', () => ({
  useInboxUnread: () => inboxUnread,
}))

const runs = vi.hoisted(() => ({ list: [] as { status: string }[] }))
const reattach = vi.hoisted(() => vi.fn())
vi.mock('../lib/backgroundRuns', () => ({
  useBackgroundRuns: () => runs.list,
  reattachBackgroundRuns: reattach,
}))

vi.mock('./SidebarAccountMenu', () => ({
  SidebarAccountMenu: () => <a href="/account-login">khalid@readyset.io</a>,
}))

const selection = vi.hoisted(() => ({ target: 'demo' as string | null }))
vi.mock('../hooks/useTarget', () => ({
  useDefaultTarget: () => selection.target,
}))

const configured = vi.hoisted(() => ({
  databases: [] as ConfiguredDatabase[],
  doFirst: null as number | null,
}))
vi.mock('../lib/monitor/hooks', () => ({
  configuredTargetsKey: ['configure-targets-hosts'],
  useConfiguredDatabases: () => ({
    databases: configured.databases,
    doFirst: configured.doFirst,
    isPending: false,
    isError: false,
  }),
  useMonitorSettings: () => ({ data: { enabled: true } }),
}))

const pulse = vi.hoisted(() => vi.fn())
vi.mock('../features/monitor-kit/useMonitorPulse', () => ({
  useMonitorPulse: pulse,
  useNow: () => Date.now() / 1000,
}))

const openAdd = vi.hoisted(() => vi.fn())
vi.mock('../components/configure/addDatabase', () => ({
  useOpenAddDatabase: () => openAdd,
  onDatabasesChanged: () => () => {},
}))

vi.mock('../lib/analytics', () => ({
  trackEvent: vi.fn(),
}))

const db = (
  name: string,
  overrides: Partial<ConfiguredDatabase> = {}
): ConfiguredDatabase => ({
  name,
  state: 'watching',
  monitored: true,
  actNow: 0,
  toDo: 0,
  ...overrides,
})

const renderSidebar = () => renderWithClient(<Sidebar />)
const databaseNav = () => screen.getByRole('navigation', { name: 'Databases' })

afterEach(() => {
  cleanup()
  routerState.pathname = '/'
  Object.assign(inboxUnread, { count: 0, actNow: 0 })
  runs.list = []
  selection.target = 'demo'
  configured.databases = []
  configured.doFirst = null
  openAdd.mockClear()
})

describe('Sidebar', () => {
  it('reattaches the runs this browser saved', () => {
    reattach.mockClear()
    renderSidebar()
    expect(reattach).toHaveBeenCalledTimes(1)
  })

  it('lists the global pages, the databases, the Platform card, then the secondary group', () => {
    configured.databases = [db('demo')]
    renderSidebar()
    const links = screen.getAllByRole('link').map((link) => link.textContent)
    expect(links).toEqual([
      'Home',
      'Inbox',
      'Actions',
      'Ask',
      'demoWatching',
      expect.stringContaining('Readyset Platform'),
      'Activity',
      'Settings',
      'Help',
      'khalid@readyset.io',
    ])
    const card = screen.getByTestId('platform-card')
    expect(card.getAttribute('href')).toBe('/platform')
    expect(card.querySelector('svg')).toBeTruthy()
    expect(card.querySelector('[data-testid="platform-card-art"]')).toBeTruthy()
    expect(card.innerHTML).not.toMatch(/#[0-9a-f]{6}/i)
    expect(
      screen.getByRole('link', { name: 'Help' }).getAttribute('href')
    ).toBe('/help')
    expect(screen.queryByRole('button', { name: /Switch target/ })).toBeNull()
  })

  it('opens the add dialog from the quiet plus beside the Databases caption', () => {
    renderSidebar()
    expect(screen.getByText('Databases')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add database' }))
    expect(openAdd).toHaveBeenCalledWith()
  })

  it('shows only the count beside the name; the state is read once, not shown', () => {
    configured.databases = [
      db('demo', { toDo: 5 }),
      db('analytics', { toDo: 0 }),
      db('replica', { state: 'cant_reach', toDo: 0 }),
      db('staging', {
        state: 'not_monitored',
        monitored: false,
        toDo: null,
      }),
      db('shop_main', { state: 'ai_paused', toDo: null }),
    ]
    renderSidebar()
    const rows = within(databaseNav()).getAllByRole('link')
    // Visible: the name and the count; the state word is screen-reader only.
    expect(
      rows.map((row) =>
        [...row.querySelectorAll('span:not(.sr-only)')]
          .map((span) => span.textContent)
          .filter(Boolean)
      )
    ).toEqual([
      ['demo', '5 to do'],
      ['analytics'],
      ['replica'],
      ['staging'],
      ['shop_main'],
    ])
    expect(rows[0].getAttribute('href')).toBe('/database?target=demo')
    expect(rows.map((row) => row.getAttribute('aria-label'))).toEqual([
      'demo, Watching, 5 to do',
      'analytics, Watching',
      "replica, Can't reach",
      'staging, Not monitored',
      'shop_main, AI checks paused',
    ])
    expect(
      rows.map((row) =>
        row.querySelector('[data-testid="radar"]')?.getAttribute('data-state')
      )
    ).toEqual(['watching', 'watching', 'unreachable', 'off', 'paused'])
    expect(rows[3].className).toContain('text-content-layout-3')
  })

  it("opens the database's activity from its item", async () => {
    configured.databases = [
      db('demo', {
        toDo: 10,
        activity: {
          kind: 'analyzing',
          label: 'Analyzing SELECT on orders by status',
          done: 4,
          total: 10,
          last_reading_at: Date.now() / 1000 - 30,
          every_s: 60,
        },
      }),
    ]
    renderSidebar()
    const item = within(databaseNav()).getByRole('link', { name: /^demo/ })
    expect(item.getAttribute('aria-label')).toBe('demo, Watching, 10 to do')
    expect(item.querySelector('[data-testid="radar-fill"]')).not.toBeNull()
    act(() => item.focus())
    const tip = await screen.findByRole('tooltip')
    expect(tip.textContent).toContain('demo, actively monitoring')
    expect(tip.textContent).toContain(
      'Analyzing SELECT on orders by status, 4 of 10'
    )
    expect(tip.textContent).toContain('10 changes to do in Actions')
  })

  it('lights a database only on its own page', () => {
    configured.databases = [db('demo'), db('analytics')]
    renderSidebar()
    expect(
      within(databaseNav())
        .getByRole('link', { name: /demo/ })
        .getAttribute('aria-current')
    ).toBeNull()
    cleanup()

    routerState.pathname = '/database/reports/31'
    renderSidebar()
    expect(
      within(databaseNav())
        .getByRole('link', { name: /demo/ })
        .getAttribute('aria-current')
    ).toBe('page')
    expect(
      within(databaseNav())
        .getByRole('link', { name: /analytics/ })
        .getAttribute('aria-current')
    ).toBeNull()
  })

  it('folds databases past eight under "N more"', () => {
    configured.databases = Array.from({ length: SHOWN_DATABASES + 3 }, (_, i) =>
      db(`db${i}`)
    )
    renderSidebar()
    expect(within(databaseNav()).getAllByRole('link')).toHaveLength(
      SHOWN_DATABASES
    )
    const more = screen.getByRole('button', { name: '3 more' })
    expect(more.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(more)
    expect(within(databaseNav()).getAllByRole('link')).toHaveLength(
      SHOWN_DATABASES + 3
    )
    expect(screen.getByRole('button', { name: 'Fewer' })).toBeTruthy()
  })

  it('unfolds when the database on screen is past the fold', () => {
    configured.databases = Array.from({ length: SHOWN_DATABASES + 2 }, (_, i) =>
      db(`db${i}`)
    )
    selection.target = `db${SHOWN_DATABASES + 1}`
    routerState.pathname = '/database'
    renderSidebar()
    expect(within(databaseNav()).getAllByRole('link')).toHaveLength(
      SHOWN_DATABASES + 2
    )
  })

  it('badges the Inbox only with items that need action now', () => {
    Object.assign(inboxUnread, { count: 9, actNow: 0 })
    renderSidebar()
    expect(screen.queryByTitle(/need you/)).toBeNull()
    cleanup()

    Object.assign(inboxUnread, { count: 10, actNow: 1 })
    renderSidebar()
    const badge = screen.getByTitle('1 need you')
    expect(badge.textContent).toBe('1')
    expect(badge.className).toContain('bg-surface-negative-solid')
    expect(screen.getByRole('link', { name: 'Inbox 1' })).toBeTruthy()
  })

  it('badges the do-first changes on Actions like the Inbox', () => {
    configured.doFirst = 7
    routerState.pathname = '/actions'
    renderSidebar()
    const actions = screen.getByRole('link', { name: 'Actions 7' })
    expect(actions.getAttribute('href')).toBe('/actions')
    expect(actions.getAttribute('aria-current')).toBe('page')
    const badge = screen.getByTitle('7 to do, 7 first')
    expect(badge.className).toContain('bg-surface-negative-solid')
    expect(badge.className).toContain('text-(--neutral-white)')
  })

  it('counts every change the Actions page lists, not only the ones to do first', () => {
    configured.doFirst = 3
    configured.databases = [db('demo', { toDo: 4 })]
    renderSidebar()
    expect(screen.getByRole('link', { name: 'Actions 4' })).toBeTruthy()
    expect(screen.getByTitle('4 to do, 3 first').className).toContain(
      'bg-surface-negative-solid'
    )
  })

  it('counts the changes to do on Actions when none is to do first', () => {
    configured.doFirst = 0
    configured.databases = [db('demo', { toDo: 3 }), db('shop', { toDo: 2 })]
    renderSidebar()
    expect(screen.getByRole('link', { name: 'Actions 5' })).toBeTruthy()
    const count = screen.getByTitle('5 to do')
    expect(count.className).not.toContain('bg-surface-negative-solid')
    expect(screen.queryByTestId('nav-live-dot')).toBeNull()
  })

  it('breathes on Actions while a database builds its first plan', () => {
    configured.databases = [db('demo', { building: true })]
    renderSidebar()
    const actions = screen.getByRole('link', {
      name: /Actions Building your first plan/,
    })
    expect(within(actions).getByTestId('nav-live-dot')).toBeTruthy()
  })

  it('holds the selected database readings stream open', () => {
    pulse.mockClear()
    renderSidebar()
    expect(pulse).toHaveBeenCalledWith('demo')
  })

  it('counts what is running on Activity', () => {
    runs.list = [
      { status: 'running' },
      { status: 'done' },
      { status: 'reconnecting' },
    ]
    renderSidebar()
    expect(screen.getByRole('link', { name: 'Activity 2' })).toBeTruthy()
  })

  it('has no background or border of its own', () => {
    renderSidebar()
    const aside = document.getElementById('app-sidebar') as HTMLElement
    expect(aside.className).not.toMatch(/\bbg-/)
    expect(aside.className).not.toMatch(/\bborder-r\b/)
  })

  it('gives nav links the shared focus ring and tracks clicks', () => {
    renderSidebar()
    const ask = screen.getByRole('link', { name: 'Ask' })
    expect(ask.className).toContain('focus-visible:ring-2')
    ask.addEventListener('click', (event) => event.preventDefault(), {
      once: true,
    })
    fireEvent.click(ask)
    expect(trackEvent).toHaveBeenCalledWith('nav_item_clicked', {
      label: 'Ask',
    })
  })
})
