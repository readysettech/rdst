import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MonitorWork } from '@/lib/monitor/types'

const state = vi.hoisted(() => ({
  work: undefined as MonitorWork | undefined,
  navigate: vi.fn(),
  setTarget: vi.fn(),
  pathname: '/inbox',
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => state.navigate,
  useRouterState: ({
    select,
  }: {
    select: (value: { location: { pathname: string } }) => unknown
  }) => select({ location: { pathname: state.pathname } }),
}))
vi.mock('@/hooks/useTarget', () => ({
  useTarget: () => ({ target: 'localdev', setTarget: state.setTarget }),
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useMonitorWork: () => ({ data: state.work }),
}))

const { MonitorWorkList, analysisSummary, workCount } = await import(
  './MonitorWorkList'
)

const job = (
  id: number,
  target: string,
  extra: Partial<MonitorWork['running'][number]> = {}
) => ({
  id,
  target,
  query_key: `q${id}`,
  display_name: `Query ${id}`,
  state: 'queued' as const,
  source: 'monitoring' as const,
  enqueued_at: 1,
  started_at: null,
  finished_at: null,
  ...extra,
})

const work: MonitorWork = {
  running: [job(1, 'demoshop', { state: 'running', started_at: 1 })],
  queued: [
    job(2, 'demoshop', { queue_position: 1, queue_state: 'today' }),
    job(3, 'localdev', { queue_position: 1, source: 'you' }),
  ],
  retrying: [],
  done_today: [
    job(4, 'localdev', { state: 'done', finished_at: 5, efficiency_score: 85 }),
  ],
  done_today_count: 6,
  reports: [{ id: 9, target: 'demoshop', trigger: 'nightly', started_at: 1 }],
}

afterEach(() => {
  cleanup()
  state.navigate.mockReset()
  state.setTarget.mockReset()
})

describe('MonitorWorkList', () => {
  it('counts only running and queued work, per database or everywhere', () => {
    expect(workCount(work)).toBe(4)
    expect(workCount(work, 'localdev')).toBe(1)
    expect(analysisSummary(work)).toBe('1 running, 2 queued')
    expect(analysisSummary(work, 'localdev')).toBe('1 queued')
    expect(analysisSummary(undefined)).toBe('Nothing queued')
  })

  it('groups analyses, lists reports, and keeps finished ones behind a count', () => {
    state.work = work
    render(<MonitorWorkList defaultOpen />)
    expect(screen.getByTestId('work-analyses').textContent).toContain(
      '1 running, 2 queued'
    )
    const entries = screen.getAllByTestId('work-entry')
    expect(entries.map((entry) => entry.textContent)).toEqual([
      expect.stringContaining('Analyzing now'),
      expect.stringContaining('Queued · demoshop'),
      expect.stringContaining('you started it'),
    ])
    expect(screen.getByTestId('work-report').textContent).toContain(
      'Nightly report for demoshop: running'
    )
    // Finished work shows as a count until opened.
    expect(screen.getByTestId('work-done').textContent).toContain('6')
    expect(screen.queryByText('Query 4')).toBeNull()
  })

  it('opens a query on its own database, switching target first', () => {
    state.work = work
    const opened = vi.fn()
    render(<MonitorWorkList defaultOpen onOpened={opened} />)
    fireEvent.click(screen.getByText('Query 2'))
    expect(state.setTarget).toHaveBeenCalledWith('demoshop')
    expect(opened).toHaveBeenCalled()
    const call = state.navigate.mock.calls[0][0]
    expect(call.to).toBe('/database')
    // From another page, only the query to bring into view.
    expect(call.search({ inboxFilter: 'x' })).toEqual({ focus: 'q2' })
  })

  it("brings a waiting query into view without touching the list's filters", () => {
    state.work = work
    state.pathname = '/database'
    render(<MonitorWorkList defaultOpen />)
    fireEvent.click(screen.getByText('Query 2'))
    const search = state.navigate.mock.calls[0][0].search
    expect(search({ filter: 'attention', sort: 'qps', q: 'orders' })).toEqual({
      filter: 'attention',
      sort: 'qps',
      q: 'orders',
      focus: 'q2',
    })
    state.pathname = '/inbox'
  })

  it('filters to one database without repeating its name', () => {
    state.work = work
    render(<MonitorWorkList target="localdev" defaultOpen />)
    const entries = screen.getAllByTestId('work-entry')
    expect(entries).toHaveLength(1)
    expect(entries[0].textContent).not.toContain('localdev')
    expect(screen.queryByTestId('work-report')).toBeNull()
  })
})
