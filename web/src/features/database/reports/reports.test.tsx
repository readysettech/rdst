import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { FIXTURE_NOW, reportSummaries } from '@/lib/monitor/fixtures'
import { LinkStub, renderWithClient } from '@/test-utils'
import { DayPicker } from './DayPicker'
import { ReportView } from './ReportView'
import { dayList } from './snapshotModel'

const navigate = vi.fn()
let search: Record<string, unknown> = {}

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    Link: LinkStub,
    useNavigate: () => navigate,
    useSearch: () => search,
    useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
      select({ location: { search, pathname: '/database' } }),
  }
})

beforeEach(() => {
  setFixtureTiming(0)
  setFixtureOverride('localdev')
  resetStubState()
  navigate.mockReset()
  search = {}
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
  setFixtureOverride(undefined)
})

function ReportTab({ reportId = 'latest' as number | 'latest' }) {
  return (
    <ReportView
      target="localdev"
      reportId={reportId}
      onShowLatest={() => undefined}
      onPick={(id) => navigate(id)}
    />
  )
}

it('opens on the day, titled by its date alone, with a bar to jump between sections', async () => {
  // The latest fixture report ran a few hours before now: at midday it is today's.
  const midday = new Date()
  midday.setHours(12, 0, 0, 0)
  vi.useFakeTimers({ toFake: ['Date'], now: midday })
  renderWithClient(<ReportTab />)
  const title = await screen.findByTestId('snapshot-title')
  expect(title.textContent).toMatch(
    /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), \w+ \d+$/
  )
  // The latest is today so far; a whole day needs no line under its title.
  expect(screen.getByTestId('snapshot-period').textContent).toMatch(
    /^Today so far, as of \d+:\d\d [AP]M$/
  )
  expect(document.body.textContent).not.toMatch(/written at|night after/)
  const nav = screen.getByRole('navigation', { name: 'Report sections' })
  expect(
    within(nav)
      .getAllByRole('button')
      .map((button) => button.dataset.section)
  ).toEqual([
    'snapshot-health',
    'snapshot-numbers',
    'snapshot-brief',
    'snapshot-actions',
    'snapshot-chart',
    'snapshot-spikes',
    'snapshot-events',
    'snapshot-look-at',
    'snapshot-all',
  ])
  // One section is current at a time (jsdom has no layout, so which one is up to the click).
  expect(nav.querySelectorAll('[aria-current="location"]')).toHaveLength(1)
  fireEvent.click(within(nav).getByRole('button', { name: /^Queries/ }))
  expect(
    within(nav)
      .getByRole('button', { name: /^Queries/ })
      .getAttribute('aria-current')
  ).toBe('location')
  expect(screen.queryByTestId('report-verdict')).toBeNull()
  for (const name of [
    'The day in brief',
    'Database health',
    'The day in numbers',
    'What to change',
    'Queries per second',
    'What happened',
    'Queries to look at',
  ])
    expect(screen.getByRole('heading', { name })).toBeTruthy()
  const prose = [...document.querySelectorAll('p, h1, h2, h3, li')]
    .map((node) => node.textContent ?? '')
    .join('\n')
  expect(prose).not.toMatch(/\{\{q:/)
  expect(prose).not.toMatch(
    /Fix first|One fix, several queries|Also noted|Verdict/
  )
  expect(screen.getAllByTestId('day-number')).toHaveLength(6)
})

it('shows each query to look at with its grade and status, and opens it on the day', async () => {
  renderWithClient(<ReportTab />)
  const rows = await screen.findAllByTestId('look-at-row')
  expect(rows[0].dataset.status).toBe('needs_attention')
  expect(within(rows[0]).getByText('Needs attention')).toBeTruthy()
  fireEvent.click(within(rows[0]).getByRole('button', { name: /^Open / }))
  const call = navigate.mock.calls.at(-1)?.[0] as {
    to: string
    search: Record<string, string>
  }
  expect(call.to).toBe('/database')
  expect(call.search.query).toBe(
    rows[0].querySelector('[data-query-key]')?.getAttribute('data-query-key')
  )
  expect(call.search.zoom).toMatch(/^\d+-\d+$/)
  expect(call.search.target).toBe('localdev')
})

it('puts health first, then the brief and what to change, before the chart, events and queries', async () => {
  renderWithClient(<ReportTab />)
  await screen.findByTestId('snapshot-title')
  const order = [...document.querySelectorAll('h2')].map(
    (node) => node.textContent
  )
  expect(order.slice(0, 8)).toEqual([
    'Database health',
    'The day in numbers',
    'The day in brief',
    'What to change',
    'Queries per second',
    'Spikes',
    'What happened',
    'Queries to look at',
  ])
  expect(screen.getByTestId('health-overview').textContent).toContain(
    'Worth a look'
  )
  // Each priority in the brief says what it means; the numbers name their day.
  expect(screen.getAllByTestId('brief-item').length).toBeLessThanOrEqual(3)
  expect(screen.getAllByTestId('brief-why')[0].textContent).toContain(
    'more work for the same server'
  )
  expect(screen.getByTestId('day-numbers-against').textContent).toMatch(
    /^Against \w+day\. Pick one/
  )
  // What to change lives on Actions; the report points there.
  const now = screen.getByTestId('report-actions-now')
  expect(within(now).getByRole('button', { name: 'Open Actions' })).toBeTruthy()
})

it('opens a query from what happened in place, with the Database page second', async () => {
  renderWithClient(<ReportTab />)
  const timeline = await screen.findByTestId('snapshot-timeline')
  const chip = timeline.querySelector('[data-query-key]') as HTMLElement
  fireEvent.click(chip)
  const panel = await screen.findByTestId('query-panel')
  expect(panel.dataset.queryKey).toBe(chip.dataset.queryKey)
  expect(navigate).not.toHaveBeenCalled()
  fireEvent.click(
    within(panel).getByRole('button', { name: /Open in Database/ })
  )
  const call = navigate.mock.calls.at(-1)?.[0] as {
    search: Record<string, string>
  }
  expect(call.search.query).toBe(chip.dataset.queryKey)
  expect(screen.getByText(/Back to normal /)).toBeTruthy()
})

it('opens each query to look at on its fix, and a tile switches the chart', async () => {
  renderWithClient(<ReportTab />)
  const rows = await screen.findAllByTestId('look-at-row')
  const withFix = rows.find((row) => row.textContent?.includes('Suggested fix'))
  expect(withFix).toBeTruthy()
  expect(within(withFix as HTMLElement).getByText('Copy the fix')).toBeTruthy()
  expect(screen.getByTestId('day-chart').dataset.view).toBe('calls')
  fireEvent.click(screen.getAllByTestId('day-number')[3])
  expect(screen.getByTestId('day-chart').dataset.view).toBe('cache_hit_ratio')
  fireEvent.click(screen.getByRole('radio', { name: 'Time spent' }))
  expect(screen.getByTestId('day-chart').dataset.view).toBe('time')
  expect(screen.getByTestId('download-csv')).toBeTruthy()
})

it('says the first daily report arrives tonight', async () => {
  setFixtureOverride('empty')
  renderWithClient(<ReportTab />)
  expect(
    await screen.findByText('Your first daily report arrives tonight')
  ).toBeTruthy()
  // No promise about how long it takes, and no report before the day is in.
  expect(screen.queryByText(/minute or two/)).toBeNull()
  expect(screen.queryByRole('button', { name: 'See today so far' })).toBeNull()
})

it('says a missing day is gone', async () => {
  renderWithClient(<ReportTab reportId={9999} />)
  expect(await screen.findByText('This day is gone')).toBeTruthy()
})

it('lists one clean row per day in the day picker, with prev and next', async () => {
  const days = dayList(reportSummaries(FIXTURE_NOW))
  const onPick = vi.fn()
  renderWithClient(
    <DayPicker days={days} selectedId={days[1].id} onPick={onPick} />
  )
  const trigger = screen.getByTestId('day-picker-trigger')
  expect(trigger.textContent).toMatch(/^\w{3}, \w{3} \d+$/)
  fireEvent.click(screen.getByRole('button', { name: /^Previous day/ }))
  expect(onPick).toHaveBeenCalledWith(days[2].id)
  fireEvent.click(screen.getByRole('button', { name: /^Next day/ }))
  expect(onPick).toHaveBeenCalledWith(days[0].id)
})
