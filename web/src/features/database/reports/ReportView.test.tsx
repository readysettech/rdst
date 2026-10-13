import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { aiCanRun } from '@/features/monitor-kit/aiMonitoring'
import {
  getReportViewPreference,
  putReportViewPreference,
  putSettings,
  resetStubState,
  setFixtureTiming,
} from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { LinkStub, renderWithClient } from '@/test-utils'
import { emptyRunView } from '../analyze/runModel'
import { ReportView } from './ReportView'
import type { ReportRun } from './useReportRun'

const navigate = vi.fn()

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return { ...actual, Link: LinkStub, useNavigate: () => navigate }
})

vi.mock('@/lib/monitor/actions', () => ({
  useActions: () => ({ data: undefined, isError: false }),
  useIndexPlan: () => ({ data: null }),
  useActionVerb: () => ({ mutate: vi.fn(), isPending: false }),
  resetActionStubState: () => {},
}))

function idleRun(overrides: Partial<ReportRun> = {}): ReportRun {
  return {
    status: 'idle',
    reportId: null,
    view: null,
    error: null,
    changedAt: 0,
    active: false,
    start: vi.fn(async () => {}),
    cancel: vi.fn(async () => {}),
    dismiss: vi.fn(),
    ...overrides,
  }
}

function renderReport(
  scenario: string,
  run: ReportRun = idleRun(),
  reportId: number | 'latest' = 'latest',
  onOpenActions?: () => void,
  can?: Parameters<typeof ReportView>[0]['can']
) {
  setFixtureOverride(scenario)
  const onPick = vi.fn()
  const view = renderWithClient(
    <ReportView
      target="demo"
      reportId={reportId}
      can={can}
      run={run}
      onShowLatest={vi.fn()}
      onPick={onPick}
      onOpenActions={onOpenActions}
    />
  )
  return { ...view, onPick }
}

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  navigate.mockReset()
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } })
})
afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

it('reads the report in one pass: head, verdict, the day, fix first, health, footer', async () => {
  renderReport('ca-report')
  const doc = await screen.findByTestId('report-document')
  expect(doc.getAttribute('data-report-id')).toBe('31')
  expect(screen.getByRole('heading', { name: 'Daily report' })).toBeTruthy()
  expect(
    screen.getByText('Wednesday, October 7, run today at 12:00 AM')
  ).toBeTruthy()
  expect(screen.getByText('demo needs two things today:')).toBeTruthy()

  const day = screen.getByRole('region', { name: 'The day' })
  expect(within(day).getByText('October 7, vs the day before')).toBeTruthy()
  expect(within(day).getByText('Queries per second')).toBeTruthy()
  expect(within(day).getByText('11.7')).toBeTruthy()
  expect(within(day).getByText('shared_buffers is the default')).toBeTruthy()
  expect(within(day).getByRole('link', { name: 'All 12 queries' })).toBeTruthy()

  const fix = screen.getByRole('region', { name: 'Fix first' })
  expect(
    within(fix).getByText(
      'in this order, one index on posts covers three queries'
    )
  ).toBeTruthy()
  expect(within(fix).getAllByTestId('fix-row')).toHaveLength(6)
  expect(within(fix).getByText('Needs attention')).toBeTruthy()
  expect(within(fix).getByText('Worth doing')).toBeTruthy()
  expect(within(fix).getByRole('button', { name: 'Copy change' })).toBeTruthy()
  expect(within(fix).getAllByRole('button', { name: 'Copy fix' })).toHaveLength(
    3
  )
  expect(within(fix).getByText('Fixed 8:12 AM, 27.6 ms to 1.9 ms')).toBeTruthy()
  expect(
    within(fix).getByRole('button', { name: 'Copy all 8 as SQL' })
  ).toBeTruthy()

  fireEvent.click(within(fix).getByRole('button', { name: '4 smaller fixes' }))
  expect(within(fix).getAllByTestId('fix-row')).toHaveLength(10)
  expect(
    within(fix).getByText('Drop unused index votes_creationdate_idx')
  ).toBeTruthy()
  expect(
    within(fix).getByRole('button', { name: 'Hide smaller fixes' })
  ).toBeTruthy()

  const health = screen.getByRole('region', { name: 'Health' })
  expect(within(health).getByText('3 of 8 checks need you')).toBeTruthy()
  expect(within(health).getByText('Cache hit fell to 11.5%')).toBeTruthy()
  expect(within(health).getAllByText(/^Fix \d$/)).toHaveLength(3)
  expect(within(health).getByText('5 checks fine')).toBeTruthy()
  expect(
    within(health).getByText(
      'Connections, vacuum and bloat, indexes, replication, locks'
    )
  ).toBeTruthy()

  expect(screen.getByText('Run at 12:00 AM by RDST')).toBeTruthy()
  expect(screen.getByText('12 analyses, 2 rewrites tested')).toBeTruthy()
  expect(screen.getByText('Report #31')).toBeTruthy()

  fireEvent.click(within(fix).getByRole('button', { name: 'Copy change' }))
  await waitFor(() =>
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "ALTER SYSTEM SET shared_buffers = '2GB';"
    )
  )
})

it('opens the day menu with every kept day and picks one', async () => {
  const { onPick } = renderReport('ca-report')
  await screen.findByTestId('report-document')
  fireEvent.keyDown(screen.getByTestId('day-picker-trigger'), { key: 'Enter' })
  const items = await screen.findAllByRole('menuitem')
  expect(items.map((item) => item.textContent)).toEqual([
    'Oct 7Latest9 fixes',
    'Oct 67 fixes',
    'Oct 58 fixes',
    'Oct 43 fixes',
    'Oct 3quiet',
    'Oct 21 fix',
    'Oct 1first report',
    'All reports',
  ])
  fireEvent.click(items[1])
  expect(onPick).toHaveBeenCalledWith(30)
})

it('offers Print, Download CSV and Copy as Markdown from the kebab', async () => {
  renderReport('ca-report', idleRun())
  await screen.findByTestId('report-document')
  fireEvent.keyDown(screen.getByRole('button', { name: 'Report menu' }), {
    key: 'Enter',
  })
  const items = await screen.findAllByRole('menuitem')
  // Reports run on their schedule: the menu has no manual run.
  expect(items.map((item) => item.textContent)).toEqual([
    'Print',
    'Download CSV',
    'Copy as Markdown',
  ])
})

it('signed out, keeps the numbers and turns every copy into a sign-in link', async () => {
  renderReport('ca-signed-out')
  await screen.findByTestId('report-document')
  expect(
    screen.getByText('Sign in to get the written report and fixes.')
  ).toBeTruthy()
  expect(screen.queryByText('demo needs two things today:')).toBeNull()
  expect(screen.getByText('11.7')).toBeTruthy()
  const fix = screen.getByRole('region', { name: 'Fix first' })
  expect(within(fix).queryByRole('button', { name: /Copy/ })).toBeNull()
  expect(within(fix).getAllByRole('link', { name: 'Sign in' })).toHaveLength(5)
  // A health row's "Fix N" may lead to a smaller fix: signed out they are listed, and the
  // section says what they wait on.
  expect(within(fix).getByText('waiting for sign-in')).toBeTruthy()
  expect(within(fix).queryByText('4 smaller fixes')).toBeNull()
  expect(
    within(fix)
      .getAllByTestId('fix-row')
      .filter((row) => row.dataset.state === 'smaller')
  ).toHaveLength(4)
  expect(screen.getByText('Numbers only')).toBeTruthy()
  expect(screen.getAllByRole('link', { name: 'Sign in' }).length).toBe(6)
})

it('points at Actions on the current day only', async () => {
  const latest = renderReport('ca-report')
  await screen.findByTestId('report-document')
  expect(await screen.findByTestId('report-actions-now')).toBeTruthy()
  expect(
    within(screen.getByTestId('report-actions-now')).getByRole('button', {
      name: 'Open Actions',
    })
  ).toBeTruthy()
  latest.unmount()
  renderReport('ca-report', idleRun(), 30)
  const doc = await screen.findByTestId('report-document')
  await waitFor(() => expect(doc.getAttribute('data-report-id')).toBe('30'))
  expect(screen.queryByTestId('report-actions-now')).toBeNull()
})

it('offers Actions before the first report when the page can open it', async () => {
  const open = vi.fn()
  renderReport('ca-first-run', idleRun(), 'latest', open)
  const first = await screen.findByTestId('report-first')
  fireEvent.click(within(first).getByRole('button', { name: 'Open Actions' }))
  expect(open).toHaveBeenCalled()
})

it('before the first report, says when it lands and shows the day so far', async () => {
  renderReport('ca-first-run', idleRun())
  const first = await screen.findByTestId('report-first')
  expect(
    within(first).getByRole('heading', { name: 'Daily report' })
  ).toBeTruthy()
  expect(within(first).getByText('Thursday, October 8')).toBeTruthy()
  await within(first).findByText(
    'Your first daily report runs tonight, just after midnight. After that, one runs every night. Monitoring since 9:45 AM.'
  )
  expect(
    within(first).getByText('since 9:45 AM, comparisons start tomorrow')
  ).toBeTruthy()
  expect(await within(first).findByText('Queries per second')).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Fix first' })).toBeNull()
  expect(screen.queryByText(/Report #/)).toBeNull()
  expect(within(first).queryByRole('button', { name: 'Run it now' })).toBeNull()
})

it('before the first report, says the saved report hour', async () => {
  setFixtureOverride('ca-first-run')
  await putSettings('demo', { nightly_hour: 6 })
  renderReport('ca-first-run', idleRun())
  const first = await screen.findByTestId('report-first')
  // The fixture's clock is 9:56 AM, so the next 6 AM report is tomorrow's.
  await within(first).findByText(
    'Your first daily report runs tomorrow at 6 AM. After that, one runs every night. Monitoring since 9:45 AM.'
  )
  expect(first.textContent).not.toMatch(/midnight/)
})

it('signed out, before the first report, says it waits for a sign-in', async () => {
  setFixtureOverride('ca-first-run')
  await putSettings('demo', { nightly_hour: 6 })
  const settings = {
    enabled: true,
    ai_monitoring: 'pending' as const,
    ai_reason: 'signed_out' as const,
  }
  renderReport(
    'ca-first-run',
    idleRun(),
    'latest',
    vi.fn(),
    aiCanRun({ settings })
  )
  const first = await screen.findByTestId('report-first')
  await within(first).findByText(
    'Your first daily report runs tomorrow at 6 AM once you sign in. Monitoring since 9:45 AM.'
  )
  expect(first.textContent).not.toMatch(/one runs every night/)
  expect(
    within(first).getByRole('link', { name: 'Sign in' }).getAttribute('href')
  ).toBe('/account-login')
  expect(
    within(first).queryByRole('button', { name: 'Open Actions' })
  ).toBeNull()
})

it('while the report runs, keeps the document dimmed under the running line', async () => {
  const run = idleRun({ status: 'running', reportId: 32, active: true })
  renderReport('ca-report', run)
  const doc = await screen.findByTestId('report-document')
  expect(doc.getAttribute('aria-busy')).toBe('true')
  expect(screen.getByText('Running the report now')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(run.cancel).toHaveBeenCalled()
})

it('says how far the running report has got', async () => {
  const view = emptyRunView()
  const run = idleRun({
    status: 'running',
    reportId: 32,
    active: true,
    view: {
      ...view,
      steps: {
        ...view.steps,
        narrative: { ...view.steps.narrative, state: 'running' },
      },
    },
  })
  renderReport('ca-report', run)
  await screen.findByTestId('report-document')
  expect(screen.getByText('. Writing the report.')).toBeTruthy()
})

it('says when the browser blocks a copy instead of failing quietly', async () => {
  Object.assign(navigator, {
    clipboard: {
      writeText: vi.fn(async () => Promise.reject(new Error('denied'))),
    },
  })
  renderReport('ca-report')
  const fix = await screen.findByRole('region', { name: 'Fix first' })
  fireEvent.click(within(fix).getByRole('button', { name: 'Copy change' }))
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled())
})

function viewTab(name: 'Quick' | 'Detailed') {
  return within(screen.getByRole('tablist', { name: 'Report view' })).getByRole(
    'tab',
    { name }
  )
}

async function openDetailed() {
  setFixtureOverride('ca-report')
  await putReportViewPreference('detailed')
  renderReport('ca-report')
  await waitFor(() =>
    expect(
      screen.getByTestId('report-document').getAttribute('data-view')
    ).toBe('detailed')
  )
}

it('opens on Quick and keeps the Detailed choice as the report_view preference', async () => {
  const first = renderReport('ca-report')
  const doc = await screen.findByTestId('report-document')
  expect(doc.getAttribute('data-view')).toBe('quick')
  expect(viewTab('Quick').getAttribute('aria-selected')).toBe('true')
  expect(screen.queryByRole('region', { name: 'What changed' })).toBeNull()

  fireEvent.click(viewTab('Detailed'))
  await waitFor(() =>
    expect(
      screen.getByTestId('report-document').getAttribute('data-view')
    ).toBe('detailed')
  )
  expect(viewTab('Detailed').getAttribute('aria-selected')).toBe('true')
  expect(await getReportViewPreference()).toBe('detailed')
  // The same numbers on both views: the verdict and the copy count.
  expect(screen.getByText('demo needs two things today:')).toBeTruthy()
  const fix = screen.getByRole('region', { name: 'Fix first' })
  expect(
    within(fix).getByRole('button', { name: 'Copy all 8 as SQL' })
  ).toBeTruthy()
  first.unmount()

  // A fresh page reads the preference and opens on Detailed.
  renderReport('ca-report')
  await waitFor(() =>
    expect(
      screen.getByTestId('report-document').getAttribute('data-view')
    ).toBe('detailed')
  )
  fireEvent.click(viewTab('Quick'))
  await waitFor(() =>
    expect(
      screen.getByTestId('report-document').getAttribute('data-view')
    ).toBe('quick')
  )
  expect(await getReportViewPreference()).toBe('quick')
})

it('Detailed: the day with the day before under each number, and the busiest stretches', async () => {
  await openDetailed()
  const day = screen.getByRole('region', { name: 'The day' })
  for (const label of [
    'Queries per second',
    'Average latency',
    'p95 latency',
    'Peak connections',
    'Lowest cache hit',
    'Rollbacks, deadlocks',
  ])
    expect(within(day).getByText(label)).toBeTruthy()
  for (const was of [
    'was 9.8',
    'was 12.4 ms',
    'was 51.2 ms',
    'was 6',
    'was 93.8%',
    'was 21 and 0',
  ])
    expect(within(day).getByText(was)).toBeTruthy()
  expect(within(day).getByText('69.7')).toBeTruthy()
  expect(within(day).getByText('143')).toBeTruthy()
  expect(within(day).getByText('and 0')).toBeTruthy()
  expect(
    within(day).getByText(/No spikes, the two busiest stretches marked\./)
  ).toBeTruthy()

  const spikes = within(day).getByRole('region', { name: 'Spikes' })
  expect(
    within(spikes).getByText(
      'None crossed the spike line. The two busiest stretches, with the queries behind them.'
    )
  ).toBeTruthy()
  const rows = within(spikes).getAllByTestId('spike-row')
  expect(rows).toHaveLength(2)
  expect(within(rows[0]).getByText('9:40 to 10:05 AM')).toBeTruthy()
  expect(
    within(rows[0]).getByText("15.9 queries/s, 1.4 times the day's median")
  ).toBeTruthy()
  expect(
    within(rows[0]).getByText(
      'SELECT on votes by postid 31%, SELECT on posts by id 18%, COUNT on comments by postid 14%'
    )
  ).toBeTruthy()
  expect(within(rows[0]).getByText('121 of 143 rollbacks')).toBeTruthy()
  expect(within(rows[1]).getByText('2:30 to 2:50 PM')).toBeTruthy()
  expect(within(rows[1]).getByText('0 rollbacks')).toBeTruthy()
  expect(
    within(spikes).getAllByRole('link', { name: 'Open queries' })
  ).toHaveLength(2)
})

it('Detailed: what changed, query by query', async () => {
  await openDetailed()
  const changed = screen.getByRole('region', { name: 'What changed' })
  expect(
    within(changed).getByText(
      '12 queries against October 6, average time per call'
    )
  ).toBeTruthy()
  const rows = within(changed).getAllByTestId('changed-row')
  expect(rows.map((row) => row.getAttribute('data-kind'))).toEqual([
    'slower',
    'slower',
    'faster',
    'new_heavy',
    'plan_changed',
  ])
  expect(within(rows[0]).getByText('Got slower')).toBeTruthy()
  expect(rows[0].textContent).toContain('48.7 ms to 65.0 ms')
  expect(
    within(rows[0]).getByText(
      '17% of time, reads went to disk as cache hit fell'
    )
  ).toBeTruthy()
  expect(within(rows[2]).getByText('Got faster')).toBeTruthy()
  expect(rows[2].textContent).toContain('27.6 ms to 1.9 ms')
  expect(within(rows[3]).getByText('New heavy')).toBeTruthy()
  expect(rows[3].textContent).toContain('new, 63.3 ms')
  expect(
    within(rows[3]).getByText('First seen 9:46 AM, 9.6% of time')
  ).toBeTruthy()
  expect(within(rows[4]).getByText('Plan changed')).toBeTruthy()
  expect(within(rows[4]).getByText('8:12 AM')).toBeTruthy()
  expect(within(rows[4]).getByRole('link', { name: 'Open plans' })).toBeTruthy()
  expect(
    within(changed).getAllByRole('link', { name: 'Open query' })
  ).toHaveLength(4)
})

it('Detailed: every fix in one list, then the fixes applied and confirmed', async () => {
  await openDetailed()
  const fix = screen.getByRole('region', { name: 'Fix first' })
  expect(within(fix).getAllByTestId('fix-row')).toHaveLength(9)
  expect(
    within(fix).getByText('Drop unused index votes_creationdate_idx')
  ).toBeTruthy()
  expect(within(fix).queryByText('4 smaller fixes')).toBeNull()
  const confirmed = within(fix).getByRole('region', {
    name: 'Fixes applied and confirmed',
  })
  expect(within(confirmed).getAllByTestId('confirmed-row')).toHaveLength(2)
  expect(within(confirmed).getAllByText('Confirmed')).toHaveLength(2)
  expect(
    within(confirmed).getByText(
      'Applied 8:12 AM, confirmed 9:12 AM, 27.6 ms to 1.9 ms'
    )
  ).toBeTruthy()
  expect(
    within(confirmed).getByText(
      'Applied October 6 at 4:30 PM, confirmed 12:40 AM, 41.3 ms to 3.4 ms'
    )
  ).toBeTruthy()
})

it('Detailed: every health check with its number, then the settings audit', async () => {
  await openDetailed()
  const health = screen.getByRole('region', { name: 'Health' })
  expect(within(health).getByText('3 of 8 checks need you')).toBeTruthy()
  const checks = within(health).getAllByTestId('check-row')
  expect(checks).toHaveLength(8)
  expect(checks.map((row) => row.getAttribute('data-state'))).toEqual([
    'bad',
    'warn',
    'warn',
    'ok',
    'ok',
    'ok',
    'ok',
    'ok',
  ])
  expect(within(checks[0]).getByText('11.5% lowest')).toBeTruthy()
  expect(within(checks[3]).getByText('8 of 100')).toBeTruthy()
  expect(within(checks[7]).getByText('120 ms longest')).toBeTruthy()
  const links = within(health).getAllByRole('link', { name: /^Fix \d$/ })
  expect(links.map((link) => link.getAttribute('href'))).toEqual([
    '#report-fix-first',
    '#report-settings-audit',
    '#report-fix-first',
  ])

  const audit = within(health).getByRole('region', { name: 'Settings audit' })
  expect(
    within(audit).getByText('4 off recommendation, 1 on Actions')
  ).toBeTruthy()
  const rows = within(audit).getAllByTestId('audit-row')
  expect(rows.map((row) => row.firstChild?.textContent)).toEqual([
    'shared_bufferson Actions',
    'work_mem',
    'effective_cache_size',
    'random_page_cost',
  ])
  expect(rows[0].textContent).toBe(
    'shared_bufferson Actions128 MB2 GBafter a restart'
  )
  expect(rows[3].textContent).toBe('random_page_cost41.1on reload')
})

it('Detailed: index changes, what RDST did and the totals', async () => {
  await openDetailed()
  const indexes = screen.getByRole('region', { name: 'Index changes seen' })
  expect(within(indexes).getByText('2 on October 7')).toBeTruthy()
  expect(
    within(indexes).getByText('Added idx_comments_userid_creationdate')
  ).toBeTruthy()
  expect(within(indexes).getByText('Confirmed')).toBeTruthy()
  expect(
    within(indexes).getByText('Not from RDST. No query read it by midnight.')
  ).toBeTruthy()
  expect(within(indexes).getByText('0 scans')).toBeTruthy()

  const did = screen.getByRole('region', { name: 'What RDST did' })
  const rows = within(did).getAllByTestId('did-row')
  expect(rows).toHaveLength(7)
  expect(within(rows[0]).getByRole('link', { name: 'Report #30' })).toBeTruthy()
  expect(within(rows[3]).getByText('9:47 to 9:49 AM')).toBeTruthy()
  expect(within(rows[5]).getByRole('link', { name: 'Open' })).toBeTruthy()
  expect(within(rows[6]).getByText('all day')).toBeTruthy()
  expect(within(did).getByRole('link', { name: 'Activity' })).toBeTruthy()

  const totals = screen.getByTestId('report-totals')
  expect(totals.textContent).toContain('12 queries seen')
  expect(totals.textContent).toContain('1,440 readings')
  expect(totals.textContent).not.toContain('one a minute')
  expect(totals.textContent).toContain('Data kept 7 days, 18 MB')
  expect(totals.textContent).toContain('Run at 12:00 AM by RDST')
  expect(totals.textContent).toContain('Report #31')
})
