import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { setFixtureOverride } from '../../lib/monitor/contract'
import { caFixtures } from '../../lib/monitor/fixturesCa'
import type { HomeModel } from '../../lib/monitor/types'
import { HomePage, HomeView } from './HomePage'
import { stripRows as stripOf } from './homeModel'

const filter = vi.hoisted(() => ({ db: null as string | null }))
const signIn = vi.hoisted(() => ({ held: new Set<string>() }))

vi.mock('../monitor-kit/signInHold', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../monitor-kit/signInHold')>()),
  useSignInHeld: (targets: readonly string[]) =>
    new Set(targets.filter((target) => signIn.held.has(target))),
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  // Links render their search, so a test can read where each one leads.
  const Link = ({
    to,
    search,
    children,
    ...rest
  }: {
    to?: string
    search?: Record<string, unknown>
    children?: ReactNode
  } & Record<string, unknown>) => {
    const query = new URLSearchParams(
      Object.entries(search ?? {}).map(([key, value]) => [key, String(value)])
    ).toString()
    return (
      <a href={query ? `${to}?${query}` : to} {...rest}>
        {children}
      </a>
    )
  }
  return {
    ...(await importOriginal<typeof import('@tanstack/react-router')>()),
    ...fileRouteModuleMock({ useNavigate: () => vi.fn(), Link }),
  }
})

vi.mock('../../hooks/useDbFilter', () => ({
  useDbFilter: () => ({ db: filter.db, setDb: vi.fn() }),
}))

vi.mock('../shared/filters/DatabaseFilter', async (importOriginal) => ({
  ...(await importOriginal<
    typeof import('../shared/filters/DatabaseFilter')
  >()),
  DatabaseFilter: ({ value }: { value: string | null }) => (
    <button type="button">Database: {value ?? 'All databases'}</button>
  ),
}))

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
  filter.db = null
  signIn.held = new Set()
})

const href = (element: HTMLElement) => element.getAttribute('href')

describe('HomePage', () => {
  it('opens with the item that needs you, its database first, every database and the log', async () => {
    setFixtureOverride('ca-home')
    renderWithClient(<HomePage />)

    const needs = await screen.findByRole('link', { name: '1 item needs you' })
    expect(href(needs)).toBe('/inbox?item=1')
    expect(needs.parentElement?.textContent).toBe(
      '1 item needs you on demo: a stuck query since 9:55 AM.'
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Home' })).toBeTruthy()
    expect(screen.getByText('Thursday, October 8')).toBeTruthy()
    expect(
      screen.getByText(
        'Watching 2 of 3 databases. Last reading 9:56:30. Next reports at 12 AM.'
      )
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Database: All databases' })
    ).toBeTruthy()

    const card = screen.getByTestId('home-day-card')
    expect(within(card).getByRole('heading', { name: 'demo' })).toBeTruthy()
    expect(
      within(card).getByText('needs you first, today vs yesterday')
    ).toBeTruthy()
    expect(href(within(card).getByRole('link', { name: 'Open demo' }))).toBe(
      '/database?target=demo'
    )
    expect(href(within(card).getByRole('link', { name: '9' }))).toBe(
      '/actions?db=demo'
    )
    expect(
      await within(card).findByText(
        '3 changes applied this week saved 41% of database time'
      )
    ).toBeTruthy()
    expect(href(within(card).getByRole('link', { name: 'See Applied' }))).toBe(
      '/actions?db=demo&scope=applied'
    )
    expect(within(card).getByText('peak 15.9 at 9:50')).toBeTruthy()
    expect(within(card).getByText('54% of database time')).toBeTruthy()
    expect(within(card).getByText('shared_buffers is the default')).toBeTruthy()
    expect(
      within(card).getByRole('link', { name: 'Daily report, Oct 8' })
    ).toBeTruthy()

    const strip = screen.getByRole('region', { name: 'Across your databases' })
    expect(within(strip).getByText('3, sorted by what needs you')).toBeTruthy()
    const rows = within(strip).getAllByRole('link')
    expect(rows.map((row) => row.getAttribute('aria-label'))).toEqual([
      'Open demo',
      'Open analytics',
    ])
    expect(href(rows[1])).toBe('/database?target=analytics')
    expect(within(rows[0]).getByText('1 act now')).toBeTruthy()
    expect(rows[1].textContent).toBe('analyticsWatching023')
    expect(
      within(strip).getByRole('button', {
        name: 'Turn on monitoring for staging',
      }).textContent
    ).toBe('Turn on')
    expect(within(strip).getByText('Not monitored')).toBeTruthy()
    // Each State cell leads with the radar; demo's ring fills with its first analyses.
    const radars = within(strip).getAllByTestId('radar')
    expect(radars.map((mark) => mark.dataset.state)).toEqual([
      'watching',
      'watching',
      'off',
    ])
    expect(radars[0].style.getPropertyValue('--radar-fill')).toBe('144deg')
    fireEvent.pointerMove(radars[1])
    const tip = await screen.findByRole('tooltip')
    expect(tip.textContent).toContain('analytics, actively monitoring')
    expect(tip.textContent).toContain('Last reading 9:56:04, every minute')
    expect(tip.textContent).toContain('2 changes to do in Actions')

    const today = screen.getByRole('region', { name: 'Today' })
    expect(
      within(today).getByText(
        'since yesterday: 4 items opened and closed, 1 fix confirmed'
      )
    ).toBeTruthy()
    const log = within(today).getAllByRole('link')
    expect(log).toHaveLength(8)
    expect(within(log[0]).getByText('demo')).toBeTruthy()
    expect(within(log[0]).getByText('act now')).toBeTruthy()
    expect(within(today).getByText('demo, 2.1x for 11 minutes')).toBeTruthy()
    const analytics = within(today)
      .getByText('Report ran: 2 changes ready')
      .closest('a') as HTMLElement
    expect(href(analytics)).toBe('/actions?db=analytics')
    expect(within(analytics).getByText('analytics')).toBeTruthy()

    const recent = screen.getByRole('region', {
      name: 'Recently analyzed by you',
    })
    expect(within(recent).getAllByRole('link')).toHaveLength(3)
    expect(
      within(recent).getByText('analytics, yesterday, fix suggested')
    ).toBeTruthy()
    expect(
      within(recent).getByText('demo, 2 days ago, fix suggested')
    ).toBeTruthy()
  })

  it('narrows the day card and the log to the chosen database, never the strip', async () => {
    setFixtureOverride('ca-home')
    filter.db = 'analytics'
    renderWithClient(<HomePage />)

    expect(
      await screen.findByRole('button', { name: 'Database: analytics' })
    ).toBeTruthy()
    // The day the payload carries is demo's, so analytics has no card to show.
    expect(screen.queryByTestId('home-day-card')).toBeNull()
    const today = screen.getByRole('region', { name: 'Today' })
    expect(
      within(today)
        .getAllByRole('link')
        .map((row) => href(row))
    ).toEqual(['/actions?db=analytics', '/agent-activity'])
    expect(within(today).queryByText(/since yesterday/)).toBeNull()
    const strip = screen.getByRole('region', { name: 'Across your databases' })
    expect(within(strip).getAllByRole('link')).toHaveLength(2)
  })

  it('keeps the day card when the chosen database is the one it shows', async () => {
    setFixtureOverride('ca-home')
    filter.db = 'demo'
    renderWithClient(<HomePage />)

    const card = await screen.findByTestId('home-day-card')
    expect(within(card).getByText('today, vs yesterday')).toBeTruthy()
    const today = screen.getByRole('region', { name: 'Today' })
    expect(within(today).queryByText('Report ran: 2 changes ready')).toBeNull()
    expect(within(today).getByText('Stuck query filed')).toBeTruthy()
  })

  it('names the changes to do with one database and keeps the strip away', async () => {
    setFixtureOverride('ca-home-one')
    renderWithClient(<HomePage />)

    const changes = await screen.findByRole('link', { name: '2 changes to do' })
    expect(href(changes)).toBe('/actions?db=demo')
    expect(changes.parentElement?.textContent).toBe('2 changes to do on demo.')
    expect(
      screen.getByText(
        'Watching 1 database. Last reading 9:56:30. Next report at 12 AM.'
      )
    ).toBeTruthy()
    expect(
      screen.queryByRole('region', { name: 'Across your databases' })
    ).toBeNull()
    expect(screen.queryByRole('button', { name: /^Database:/ })).toBeNull()
    expect(screen.getByText('today, vs yesterday')).toBeTruthy()
    // With no strip, the day card carries the database's radar.
    const card = screen.getByTestId('home-day-card')
    expect(within(card).getByTestId('radar').dataset.state).toBe('watching')
    expect(screen.queryByText('shared_buffers is the default')).toBeNull()
    expect(screen.getByText('99.2')).toBeTruthy()
    expect(screen.getByText('Stuck query ended, item closed')).toBeTruthy()
    const recent = screen.getByRole('region', {
      name: 'Recently analyzed by you',
    })
    expect(within(recent).getByText('2 days ago, fix suggested')).toBeTruthy()
  })

  it('shows one line, one action and what follows when there is no database', async () => {
    setFixtureOverride('ca-home-empty')
    renderWithClient(<HomePage />)

    expect(await screen.findByText('No database yet.')).toBeTruthy()
    const solids = screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-surface-primary-solid'))
    expect(solids.map((button) => button.textContent)).toEqual([
      'Add a database',
    ])
    expect(screen.getByText('After you add one')).toBeTruthy()
    expect(screen.getByText('Fixes show up in Actions')).toBeTruthy()
    expect(screen.queryByText(/Inbox/)).toBeNull()
    expect(screen.queryByTestId('home-day-card')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Today' })).toBeNull()
  })
})

describe('HomeView sentence', () => {
  const base = () => caFixtures('ca-home').home()
  const quiet = (model: HomeModel, doFirst: number): HomeModel => ({
    ...model,
    status: {
      ...model.status,
      state: 'quiet',
      needs_you: 0,
      act_now: 0,
      do_first: doFirst,
      headline: null,
      link: null,
      since: model.status.checks_ran_at,
    },
  })

  it('counts the Do first changes and names the database to start on', () => {
    renderWithClient(<HomeView model={quiet(base(), 3)} />)

    const changes = screen.getByRole('link', { name: '3 changes to do' })
    expect(href(changes)).toBe('/actions?db=demo')
    expect(changes.parentElement?.textContent).toBe(
      '3 changes to do, demo first.'
    )
  })

  it('says AI checks wait for a sign-in under a paused sentence, with the link', () => {
    const model = base()
    const paused: HomeModel = {
      ...quiet(model, 0),
      status: { ...quiet(model, 0).status, state: 'paused', target: 'demo' },
      databases_strip: [
        { ...stripOf(model)[0], target: 'demo', state: 'ai_paused' },
      ],
    }
    renderWithClient(<HomeView model={paused} />)
    expect(screen.queryByTestId('sign-in-note')).toBeNull()
    cleanup()

    signIn.held = new Set(['demo'])
    renderWithClient(<HomeView model={paused} />)
    const note = screen.getByTestId('sign-in-note')
    expect(note.textContent).toBe('Sign in to run AI checks. Sign in')
    expect(href(within(note).getByRole('link', { name: 'Sign in' }))).toBe(
      '/account-login'
    )
  })

  it('signed out, promises no report and no analysis progress', () => {
    const model = base()
    const row = stripOf(model)[0]
    const paused: HomeModel = {
      ...quiet(model, 0),
      status: {
        ...quiet(model, 0).status,
        state: 'paused',
        target: 'demo',
        next_report_at: model.generated_at + 3600,
      },
      databases_strip: [
        {
          ...row,
          target: 'demo',
          state: 'ai_paused',
          building: false,
          activity: {
            kind: 'reading',
            label: 'Reading query statistics',
            done: 0,
            total: 10,
            last_reading_at: model.generated_at - 30,
            every_s: 60,
            next_check_at: null,
          },
        },
      ],
    }
    signIn.held = new Set(['demo'])
    renderWithClient(<HomeView model={paused} />)
    const page = document.body.textContent ?? ''
    expect(page).not.toMatch(/Next report/)
    expect(page).not.toMatch(/0 of 10/)
    expect(screen.getByTestId('sign-in-note').textContent).toBe(
      'Sign in to run AI checks. Sign in'
    )
  })

  it('names a database it cannot reach ahead of the Do first changes', () => {
    const model = quiet(base(), 3)
    renderWithClient(
      <HomeView
        model={{
          ...model,
          status: { ...model.status, state: 'cant_reach', target: 'demo' },
        }}
      />
    )

    const down = screen.getByRole('link', { name: "Can't reach demo" })
    expect(href(down)).toBe('/database?target=demo')
    expect(down.parentElement?.textContent).toBe(
      "Can't reach demo since 9:55 AM; no readings or alerts until it answers."
    )
    expect(screen.queryByText(/All quiet/)).toBeNull()
    expect(screen.queryByText(/alerts running/)).toBeNull()
  })

  it('reads all quiet with nothing to act on and nothing to do first', () => {
    renderWithClient(<HomeView model={quiet(base(), 0)} />)

    expect(screen.getByText(/All quiet on demo/)).toBeTruthy()
    expect(screen.getByText(/since 9:55 AM/)).toBeTruthy()
  })
})
