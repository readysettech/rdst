import type { QueryClient } from '@tanstack/react-query'
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionsPage, IndexPlan } from '@/lib/monitor/actions'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import type { AnalyzeJobPage } from '@/lib/monitor/types'
import {
  createTestQueryClient,
  jsonResponse,
  renderWithClient,
} from '@/test-utils'
import { DatabasePage } from './DatabasePage'

vi.mock('@/lib/sse/sharedEventSource', () => ({
  subscribeShared: () => () => {},
}))

const navigate = vi.fn()
const routerSearch = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
}))
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => null,
  useNavigate: () => navigate,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: routerSearch.value, pathname: '/database' } }),
  Link: ({
    children,
    to,
    search,
    className,
  }: {
    children: unknown
    to?: string
    search?: Record<string, string>
    className?: string
  }) => (
    <a
      href={search ? `${to}?${new URLSearchParams(search)}` : (to ?? '/')}
      className={className}
    >
      {children as never}
    </a>
  ),
}))
const actionsState = vi.hoisted(() => ({
  page: undefined as ActionsPage | undefined,
  plan: null as IndexPlan | null,
}))
vi.mock('@/lib/monitor/actions', () => ({
  actionKeys: { actions: (target: string) => ['monitor', target, 'actions'] },
  useActions: () => ({ data: actionsState.page, isError: false }),
  useIndexPlan: () => ({ data: actionsState.plan }),
  useActionVerb: () => ({ mutate: vi.fn(), isPending: false }),
  resetActionStubState: () => {},
}))

/** An Actions page: the first plan being built, or one finished with open changes. */
function actionsPage(state: 'first_pass' | 'done', open = 0): ActionsPage {
  return {
    target: 'demo',
    computed_at: state === 'done' ? 100 : null,
    card_count: 0,
    checkup: {
      state,
      analyzed: 3,
      total: 10,
      due_by: null,
      report_id: null,
      finished_at: null,
    },
    actions: [],
    more: [],
    open_count: open,
    recently_done: [],
    snoozed: 0,
    dismissed: 0,
    set_aside: [],
    window: { observed_s: 7200, counter_age_s: 7200 },
    sections: {},
    coverage: {},
  } as unknown as ActionsPage
}

const statusTargets = vi.hoisted(() => ({ value: [{ name: 'demo' }] }))
vi.mock('@/lib/useSystemStatus', () => ({
  useSystemStatus: () => ({
    data: { targets: statusTargets.value, default_target: 'demo' },
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
}))
const runState = vi.hoisted(() => ({
  value: { active: false, status: 'idle', view: null } as Record<
    string,
    unknown
  >,
}))
vi.mock('@/features/database/reports', () => ({
  ReportView: () => <div data-testid="report-slot" />,
  useReportRun: () => runState.value,
  useRunCompletion: () => undefined,
}))
const jobsState = vi.hoisted(() => ({
  value: undefined as AnalyzeJobPage | undefined,
}))
// The turn-on dialog's prerequisites check has no answer here.
vi.mock('@/features/prerequisites', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/prerequisites')>()),
  usePrerequisites: () => ({
    result: undefined,
    phase: 'connecting',
    checking: false,
    error: null,
    interval: false,
    updatedAt: 0,
    recheck: () => {},
  }),
}))
// Fields laid over the fixture's monitoring settings, such as why AI is off.
const settingsPatch = vi.hoisted(() => ({
  value: null as Record<string, unknown> | null,
}))
vi.mock('@/lib/monitor/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/monitor/hooks')>()
  return {
    ...actual,
    useAnalyzeJobs: (target: string) =>
      jobsState.value
        ? { data: jobsState.value }
        : actual.useAnalyzeJobs(target),
    useMonitorSettings: (target: string) => {
      const query = actual.useMonitorSettings(target)
      return settingsPatch.value && query.data
        ? { ...query, data: { ...query.data, ...settingsPatch.value } }
        : query
    },
  }
})

describe('DatabasePage', () => {
  let client: QueryClient
  const renderPage = () => renderWithClient(<DatabasePage />, client)
  const select = (target: string) =>
    localStorage.setItem('rdst_selected_target', target)
  beforeEach(() => {
    client = createTestQueryClient()
    setFixtureTiming(0)
    resetStubState()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ targets: [] }))
    )
  })
  afterEach(async () => {
    cleanup()
    await client.cancelQueries()
    client.clear()
    vi.unstubAllGlobals()
    routerSearch.value = {}
    actionsState.page = undefined
    actionsState.plan = null
    runState.value = { active: false, status: 'idle', view: null }
    jobsState.value = undefined
    settingsPatch.value = null
    statusTargets.value = [{ name: 'demo' }]
    navigate.mockReset()
    setFixtureOverride(undefined)
    localStorage.clear()
  })

  it('opens on the live view: bar, tabs, numbers, chart card and the list', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    renderPage()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'demo' })
    ).toBeTruthy()
    const bar = screen
      .getByRole('heading', { level: 1, name: 'demo' })
      .closest('header')!
    await waitFor(() =>
      expect(bar.textContent).toContain('Actively monitoring')
    )
    expect(bar.textContent).toContain('PostgreSQL 17')
    expect(bar.textContent).not.toContain('last reading')
    // The radar leads the bar; its tooltip carries the last reading.
    const radar = within(bar).getByRole('img', { name: 'demo, Watching' })
    expect(
      radar.compareDocumentPosition(
        within(bar).getByRole('heading', { level: 1 })
      ) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    act(() => radar.focus())
    const tip = await screen.findByRole('tooltip')
    expect(tip.textContent).toContain('Actively monitoring')
    expect(tip.textContent).toContain('Last reading 9:56:30, every minute')
    // Reports run on their schedule: the bar has no manual run.
    expect(
      within(bar).queryByRole('button', { name: 'Run report now' })
    ).toBeNull()
    expect(
      within(bar).getByRole('button', { name: 'Monitoring settings' })
    ).toBeTruthy()
    expect(
      screen.getByRole('tab', { name: 'Live' }).getAttribute('aria-selected')
    ).toBe('true')
    // Actions is a page of its own: the tabs are Live and Report.
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Live',
      'Report',
    ])
    // The changes to do read from the plan, the same count the sidebar shows.
    expect(
      screen.getByRole('link', { name: '5 changes to do in Actions' })
    ).toBeTruthy()
    const health = screen.getByTestId('health-metrics')
    expect(within(health).getByText('Queries per second')).toBeTruthy()
    expect(within(health).getByText('14.2')).toBeTruthy()
    expect(
      await within(health).findByText('shared_buffers is the default')
    ).toBeTruthy()
    expect(screen.getByTestId('load-card')).toBeTruthy()
    expect(screen.getByText('12 min recorded')).toBeTruthy()
    expect(screen.getByText('16, 9 with a fix')).toBeTruthy()
    expect(
      document.querySelector('[data-two-pane]')?.hasAttribute('data-open')
    ).toBe(false)
    const solids = screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-surface-primary-solid'))
    expect(solids).toHaveLength(0)
  })

  it('stays on Live while the first plan is built, pointing to this database on Actions', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    actionsState.page = actionsPage('first_pass')
    renderPage()
    const live = await screen.findByRole('tab', { name: 'Live' })
    expect(live.getAttribute('aria-selected')).toBe('true')
    expect(screen.queryByRole('tab', { name: /Actions/ })).toBeNull()
    expect(screen.queryByTestId('actions-tab')).toBeNull()
    expect(screen.getByTestId('first-plan-pointer').textContent).toContain(
      'Building your first plan'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Go to Actions' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/actions',
      search: { db: 'demo' },
    })
  })

  it('links its changes to do to this database on Actions', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    actionsState.page = actionsPage('done', 2)
    renderPage()
    const link = await screen.findByRole('link', {
      name: '5 changes to do in Actions',
    })
    expect(link.getAttribute('href')).toBe('/actions?db=demo')
    expect(screen.queryByRole('tab', { name: /Actions/ })).toBeNull()
  })

  it('marks the first plan seen once Actions is opened from the pointer', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    actionsState.page = actionsPage('done', 2)
    renderPage()
    expect(
      (await screen.findByTestId('first-plan-pointer')).textContent
    ).toContain('Your first plan is ready')
    fireEvent.click(screen.getByRole('button', { name: 'See the actions' }))
    expect(localStorage.getItem('rdst_actions_seen:demo')).toBe('1')
    await waitFor(() =>
      expect(screen.queryByTestId('first-plan-pointer')).toBeNull()
    )
  })

  it('opens Live for a link to a query list filter', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    actionsState.page = actionsPage('done', 2)
    routerSearch.value = { status: 'fix' }
    renderPage()
    const live = await screen.findByRole('tab', { name: 'Live' })
    expect(live.getAttribute('aria-selected')).toBe('true')
  })

  it('opens a row in the pane, walks with the keys and closes on Escape', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    routerSearch.value = { query: 'ca0000000003' }
    renderPage()
    expect(
      await screen.findByRole('region', {
        name: 'SELECT on posts by owneruserid',
      })
    ).toBeTruthy()
    const twoPane = document.querySelector('[data-two-pane]')
    expect(twoPane?.hasAttribute('data-open')).toBe(true)
    // Under 1280 px the open pane takes the width.
    expect(twoPane?.className).toContain(
      'max-desktop:[&>div:first-child]:hidden'
    )
    // From 1280 px the page keeps its minimum and the pane gives up width.
    expect(twoPane?.className).toContain(
      'desktop:[&>div:first-child]:min-w-[37.5rem]'
    )
    expect(twoPane?.className).toContain('desktop:[&>div:last-child]:shrink')
    // The list's score column leaves; the pane carries the score in its header and details.
    expect(screen.queryByText('Plan score', { selector: 'span' })).toBeNull()
    expect((await screen.findByTestId('pane-header-score')).textContent).toBe(
      'Plan score 72'
    )
    expect((await screen.findByTestId('pane-plan-score')).textContent).toBe(
      'Plan score 72/100'
    )
    fireEvent.keyDown(document.body, { key: 'j' })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          search: expect.objectContaining({ query: 'ca0000000004' }),
        })
      )
    )
    // A key on the document itself, outside any element, is read too.
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          search: expect.not.objectContaining({ query: expect.anything() }),
        })
      )
    )
    // Focus goes back to the row the pane was open on.
    expect(document.activeElement?.id).toBe('query-ca0000000003')
  })

  it("shows the index plan's statement in the pane for a query with an open action", async () => {
    setFixtureOverride('ca-database')
    select('demo')
    const sql =
      'CREATE INDEX CONCURRENTLY idx_posts_owneruserid ON posts (owneruserid);'
    actionsState.plan = {
      add: [{ id: 'a', sql, helps: [{ key: 'ca0000000003', gain: 0.5 }] }],
      drop: [],
    } as unknown as IndexPlan
    routerSearch.value = { query: 'ca0000000003' }
    renderPage()
    await screen.findByRole('region', {
      name: 'SELECT on posts by owneruserid',
    })
    expect(screen.getByTestId('fix-sql').textContent).toBe(sql)
  })

  it('opens the Benchmark dialog over the query pane from the URL', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    routerSearch.value = { query: 'ca0000000003', panel: 'benchmark' }
    renderPage()
    expect(
      await screen.findByRole('heading', { name: /^Benchmark \d+ quer/ })
    ).toBeTruthy()
    // The dialog sits over the query's pane, which stays open under it.
    expect(
      await screen.findByRole('region', {
        name: 'SELECT on posts by owneruserid',
        hidden: true,
      })
    ).toBeTruthy()
  })

  it('opens the settings pane from the gear', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    routerSearch.value = { panel: 'settings' }
    renderPage()
    expect(
      await screen.findByRole('region', { name: 'Monitoring, demo' })
    ).toBeTruthy()
    expect(
      await screen.findByRole('button', { name: 'Stop monitoring' })
    ).toBeTruthy()
  })

  it('opens the Report tab and reads collecting in the first minutes', async () => {
    setFixtureOverride('ca-first-run')
    select('demo')
    renderPage()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'demo' })
    ).toBeTruthy()
    const bar = screen
      .getByRole('heading', { level: 1, name: 'demo' })
      .closest('header')!
    await waitFor(() => expect(bar.textContent).toMatch(/\d+ min recorded/))
    // The Report tab opens before the first report, on the page that says when it lands.
    expect(
      screen.getByRole('tab', { name: 'Report' }).getAttribute('aria-disabled')
    ).not.toBe('true')
    expect(screen.getAllByText('collecting')).toHaveLength(2)
    // The label names the span the axis draws, and the menu waits for 15 minutes.
    const menu = screen.getByTestId('range-menu')
    expect(menu.textContent).toBe('Last 12 min')
    expect(menu.hasAttribute('disabled')).toBe(true)
    // The line under the chart says only when the report lands.
    const chart = screen.getByTestId('first-run-chart').textContent
    expect(chart).toContain(
      'Your first daily report runs tonight, just after midnight.'
    )
    expect(chart).not.toContain('Deep analysis')
    // The first hour draws the busiest queries' bands, as later.
    expect(
      screen.getByRole('img', { name: /^Queries\/s, last 12 min\./ })
    ).toBeTruthy()
    expect(await screen.findByText('3 seen so far')).toBeTruthy()
    expect(screen.getAllByText('Queued for analysis')).toHaveLength(3)
  })

  it('opens the Report tab on the first run once a report has finished', async () => {
    setFixtureOverride('ca-first-run')
    select('demo')
    runState.value = { active: false, status: 'done', reportId: 7, view: null }
    renderPage()
    const tab = await screen.findByRole('tab', { name: 'Report' })
    await waitFor(() => expect(tab.getAttribute('aria-disabled')).toBeNull())
    fireEvent.click(tab)
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        search: expect.objectContaining({ view: 'report', report: '7' }),
      })
    )
  })

  it('asks for a sign-in where the first analyses wait for one', async () => {
    setFixtureOverride('ca-first-run')
    select('demo')
    const page = actionsPage('first_pass')
    actionsState.page = {
      ...page,
      checkup: {
        ...page.checkup,
        held: 'sign_in',
        held_text: 'Sign in to turn on AI monitoring',
      },
    }
    renderPage()
    const pointer = await screen.findByTestId('first-plan-pointer')
    expect(pointer.textContent).toContain('Sign in to run the first analyses')
    expect(pointer.textContent).not.toContain('Building your first plan')
    expect(
      within(pointer)
        .getByRole('link', { name: 'Sign in' })
        .getAttribute('href')
    ).toBe('/account-login')
    // A held job is not an analysis running.
    expect(screen.queryByTestId('tab-live-dot')).toBeNull()
  })

  it('says why AI is off in the bar, with the way to turn it on', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    settingsPatch.value = { ai_monitoring: 'local', ai_reason: 'limit' }
    renderPage()
    expect((await screen.findByTestId('ai-off-reason')).textContent).toBe(
      'Make an AI slot available to turn on AI here'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Manage databases' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'connections' },
    })
  })

  it('offers a sign-in in the bar when AI is off because the user is signed out', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    settingsPatch.value = { ai_monitoring: 'pending', ai_reason: 'signed_out' }
    renderPage()
    expect((await screen.findByTestId('ai-off-reason')).textContent).toBe(
      'Sign in to turn on AI monitoring'
    )
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })

  it('keeps the bar quiet while AI is on', async () => {
    setFixtureOverride('ca-database')
    select('demo')
    renderPage()
    await screen.findByRole('img', { name: /^demo, / })
    expect(screen.queryByTestId('ai-off-reason')).toBeNull()
  })

  it("estimates one query's fix gain from its pane", async () => {
    setFixtureOverride('ca-database')
    select('demo')
    routerSearch.value = { query: 'ca0000000003' }
    renderPage()
    await screen.findByRole('region', {
      name: 'SELECT on posts by owneruserid',
    })
    fireEvent.click(screen.getByRole('button', { name: 'Estimate fix gain' }))
    expect(
      await screen.findByRole('heading', { name: 'Estimate fix gain' })
    ).toBeTruthy()
  })

  it('shows the first run steps in the first minutes', async () => {
    setFixtureOverride('ca-first-run')
    select('demo')
    renderPage()
    const strip = await screen.findByTestId('first-run')
    expect(strip.querySelector('[data-step]')).toBeTruthy()
    // Its queries are measured, so the list shows under the chart.
    expect(await screen.findByTestId('query-section')).toBeTruthy()
    expect(screen.queryByTestId('waiting-for-queries')).toBeNull()
  })

  it('drops the first-plan pointer once monitoring is off', async () => {
    setFixtureOverride('ca-unmonitored')
    select('reports')
    actionsState.page = actionsPage('first_pass')
    renderPage()
    expect(await screen.findByText('3, from a scan on Oct 6')).toBeTruthy()
    expect(screen.queryByTestId('first-plan-pointer')).toBeNull()
    expect(screen.queryByText('Analyzing')).toBeNull()
  })

  it('offers to turn monitoring on and lists the scan when monitoring is off', async () => {
    setFixtureOverride('ca-unmonitored')
    select('reports')
    renderPage()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'reports' })
    ).toBeTruthy()
    const bar = screen
      .getByRole('heading', { level: 1, name: 'reports' })
      .closest('header')!
    await waitFor(() => expect(bar.textContent).toContain('Not monitored'))
    expect(screen.queryByRole('tab', { name: 'Live' })).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'Monitoring settings' })
    ).toBeNull()
    expect(
      screen.getByText(
        'Turn on monitoring to see live numbers, fixes and a daily report.'
      )
    ).toBeTruthy()
    const solids = screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-surface-primary-solid'))
    expect(solids.map((button) => button.textContent)).toEqual([
      'Turn on monitoring',
    ])
    expect(screen.queryByTestId('load-card')).toBeNull()
    expect(await screen.findByText('3, from a scan on Oct 6')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
    expect(
      await screen.findByRole('heading', { name: 'Monitor reports' })
    ).toBeTruthy()
  })

  it("names the outage, keeps the last numbers and offers Fix connection when it can't reach", async () => {
    setFixtureOverride('ca-unreachable')
    select('database')
    renderPage()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'database' })
    ).toBeTruthy()
    const bar = screen
      .getByRole('heading', { level: 1, name: 'database' })
      .closest('header')!
    await waitFor(() =>
      expect(bar.textContent).toContain("Can't reach, since 9:41 AM")
    )
    const fix = screen.getByRole('button', { name: 'Fix connection' })
    expect(fix.closest('.bg-surface-layout-2')).toBeTruthy()
    expect(screen.getByText('last reading 9:40:58')).toBeTruthy()
    expect(screen.getByText('no readings since 9:41')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Fix connection' }))
    expect(
      await screen.findByRole('heading', { name: "Can't reach database" })
    ).toBeTruthy()
  })

  it('asks for a database when none is selected', async () => {
    setFixtureOverride('ca-database')
    renderPage()
    expect(await screen.findByText('Choose a database to monitor')).toBeTruthy()
    expect(
      screen.getByText('Pick one under Databases in the sidebar.')
    ).toBeTruthy()
  })

  it('offers Add a database when there is none yet', async () => {
    setFixtureOverride('ca-home-empty')
    statusTargets.value = []
    renderPage()
    expect(await screen.findByText('No database yet.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a database' })).toBeTruthy()
  })
})
