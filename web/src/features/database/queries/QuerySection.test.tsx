import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { at, caFixtures } from '@/lib/monitor/fixturesCa'
import { QuerySection, type QuerySectionProps } from './QuerySection'

afterEach(cleanup)

const rows = caFixtures('ca-database').queries('demo', '1h', 500, 0).queries

function props(extra: Partial<QuerySectionProps> = {}): QuerySectionProps {
  return {
    rows,
    loading: false,
    sort: 'share',
    openKey: null,
    onOpen: vi.fn(),
    hoverKey: null,
    onHoverKey: vi.fn(),
    paneOpen: false,
    mode: 'live',
    filters: { status: 'all', table: null, q: '' },
    onFiltersChange: vi.fn(),
    onAnalyzeQuery: vi.fn(),
    onBenchmark: vi.fn(),
    ...extra,
  }
}

describe('QuerySection', () => {
  it('lists ten rows with chip, fix line, numbers, score and trend, then pages', () => {
    const onOpen = vi.fn()
    render(<QuerySection {...props({ onOpen })} />)
    expect(screen.getByText('16, 9 with a fix')).toBeTruthy()
    expect(screen.getByText('Plan score')).toBeTruthy()
    expect(screen.getByText('Latency, 24 h')).toBeTruthy()
    const list = screen.getAllByTestId('query-row')
    expect(list).toHaveLength(10)
    const first = list[0]
    expect(
      within(first).getByText('SELECT on posts and users by creationdate')
    ).toBeTruthy()
    expect(within(first).getByText('Fix suggested')).toBeTruthy()
    expect(
      within(first).getByText('posts (creationdate, owneruserid, score)')
    ).toBeTruthy()
    expect(within(first).getByText('0.64')).toBeTruthy()
    expect(within(first).getByText('+12% vs yesterday')).toBeTruthy()
    expect(within(first).getByText('19%')).toBeTruthy()
    expect(within(first).getByText('42')).toBeTruthy()
    expect(screen.getAllByText('Healthy').length).toBeGreaterThan(0)
    expect(screen.getByText('1 to 10 of 16')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('11 to 16 of 16')).toBeTruthy()
    expect(screen.getAllByTestId('query-row')).toHaveLength(6)
    fireEvent.click(screen.getAllByTestId('query-row')[0])
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('keeps four columns while the pane is open and marks the open row', () => {
    render(
      <QuerySection {...props({ paneOpen: true, openKey: rows[0].key })} />
    )
    expect(screen.queryByText('Plan score')).toBeNull()
    expect(screen.queryByText('Latency, 24 h')).toBeNull()
    expect(
      screen.getAllByTestId('query-row')[0].getAttribute('aria-current')
    ).toBe('true')
  })

  it.each([
    [1000, true, true],
    [880, true, false],
    [760, false, false],
  ])('fits a %i px list: score %s, trend %s', (width, score, trend) => {
    const measure = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({ width } as DOMRect)
    render(<QuerySection {...props()} />)
    expect(Boolean(screen.queryByText('Plan score'))).toBe(score)
    expect(Boolean(screen.queryByText('Latency, 24 h'))).toBe(trend)
    measure.mockRestore()
  })

  it('names the Status menu with a capital, as its other options', () => {
    render(<QuerySection {...props()} />)
    expect(screen.getByTestId('status-menu').textContent).toBe('Status: All')
  })

  it('writes the Status filter to the URL and reads it back', async () => {
    const onFiltersChange = vi.fn()
    const { rerender } = render(
      <QuerySection {...props({ onFiltersChange })} />
    )
    fireEvent.keyDown(screen.getByTestId('status-menu'), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Healthy' }))
    expect(onFiltersChange).toHaveBeenCalledWith({ status: 'healthy' })
    rerender(
      <QuerySection
        {...props({
          onFiltersChange,
          filters: { status: 'healthy', table: 'users', q: '' },
        })}
      />
    )
    expect(screen.getByTestId('status-menu').textContent).toBe(
      'Status: Healthy'
    )
    expect(screen.getByTestId('tables-menu').textContent).toBe('Tables: users')
    expect(screen.getAllByTestId('query-row')).toHaveLength(1)
  })

  it('focuses the search box on slash, filters as you type and writes q after a pause', async () => {
    vi.useFakeTimers()
    const onFiltersChange = vi.fn()
    render(<QuerySection {...props({ onFiltersChange })} />)
    fireEvent.keyDown(document.body, { key: '/' })
    expect(document.activeElement).toBe(
      screen.getByRole('textbox', { name: 'Search queries' })
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Search queries' }), {
      target: { value: 'users by id' },
    })
    expect(screen.getAllByTestId('query-row')).toHaveLength(1)
    expect(onFiltersChange).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })
    expect(onFiltersChange).toHaveBeenCalledWith({ q: 'users by id' })
    vi.useRealTimers()
  })

  it('sorts by latency with the Latency series', () => {
    render(<QuerySection {...props({ sort: 'latency' })} />)
    expect(screen.getByText('16, sorted by latency')).toBeTruthy()
    expect(
      within(screen.getAllByTestId('query-row')[0]).getByText(
        'COUNT on posts by creationdate'
      )
    ).toBeTruthy()
  })

  it('says Queued in the first minutes and waits for score and trend', () => {
    const first = caFixtures('ca-first-run').queries(
      'demo',
      '1h',
      500,
      0
    ).queries
    render(<QuerySection {...props({ rows: first, mode: 'first_run' })} />)
    expect(screen.getByText('3 seen so far')).toBeTruthy()
    // The first analyses wait in the queue; each row says so until it starts.
    expect(screen.getAllByText('Queued for analysis')).toHaveLength(3)
    expect(screen.getAllByText('no day before yet')).toHaveLength(3)
  })

  it('marks queued and running analyses, and analyzed rows, so you can watch them finish', () => {
    const first = caFixtures('ca-first-run').queries(
      'demo',
      '1h',
      500,
      0
    ).queries
    const base = {
      finished_at: null,
      analysis_ref: null,
      mode: '',
      param_source: null,
      reason: null,
    }
    const states = [
      {
        ...first[0],
        status: 'analyzing' as const,
        last_analysis: { ...base, state: 'running' as const },
      },
      {
        ...first[1],
        status: 'analyzing' as const,
        last_analysis: {
          ...base,
          state: 'queued' as const,
          queue_state: 'today' as const,
        },
      },
      {
        ...first[2],
        status: 'healthy' as const,
        last_analysis: {
          ...base,
          state: 'done' as const,
          summary: {
            analysis_ref: 'r',
            analysis_id: 'a',
            analyzed_at: 1,
            mode: 'explain',
            mode_label: null,
            headline: 'Already efficient',
            efficiency_score: 91,
          } as never,
        },
      },
    ]
    render(<QuerySection {...props({ rows: states, mode: 'live' })} />)
    const [running, queued, done] = screen.getAllByTestId('query-row')
    expect(
      running.querySelector('[data-activity="running"] .mk-breathe')
    ).toBeTruthy()
    expect(queued.querySelector('[data-activity="queued"]')).toBeTruthy()
    expect(within(done).getByTestId('row-analyzed').textContent).toBe(
      'Analyzed, 91/100'
    )
    expect(within(running).queryByTestId('row-analyzed')).toBeNull()
  })

  it('shows the polled jobs on the rows before the list refreshes, scored or not', () => {
    const first = caFixtures('ca-first-run').queries(
      'demo',
      '1h',
      500,
      0
    ).queries
    const scored = first.map((row) => ({
      ...row,
      status: 'healthy' as const,
      last_analysis: null,
    }))
    const job = (id: number, key: string, state: 'queued' | 'running') =>
      ({ id, query_key: key, state, queue_state: null }) as never
    render(
      <QuerySection
        {...props({
          rows: scored,
          mode: 'live',
          jobs: [
            job(2, scored[0].key, 'running'),
            job(1, scored[1].key, 'queued'),
          ],
        })}
      />
    )
    const row = (index: number) =>
      document.getElementById(`query-${scored[index].key}`) as HTMLElement
    const [running, queued, idle] = [row(0), row(1), row(2)]
    expect(within(running).getByTestId('row-activity').textContent).toBe(
      'Analyzing\u2026'
    )
    expect(
      running.querySelector('[data-activity="running"] .mk-breathe')
    ).toBeTruthy()
    expect(within(queued).getByTestId('row-activity').textContent).toBe(
      'Queued for analysis'
    )
    expect(within(idle).queryByTestId('row-activity')).toBeNull()
    expect(within(idle).getByText('Healthy')).toBeTruthy()
  })

  it('says Waiting for sign-in on rows whose analysis waits for one', () => {
    const first = caFixtures('ca-first-run').queries(
      'demo',
      '1h',
      500,
      0
    ).queries
    const held = first.map((row) => ({
      ...row,
      status: 'analyzing' as const,
      last_analysis: {
        state: 'queued' as const,
        finished_at: null,
        analysis_ref: null,
        mode: '',
        param_source: null,
        reason: 'Sign in to turn on AI monitoring',
        queue_state: 'held' as const,
        held_reason: 'sign_in' as const,
      },
    }))
    const { rerender } = render(
      <QuerySection {...props({ rows: held, mode: 'first_run' })} />
    )
    expect(screen.getAllByText('Waiting for sign-in')).toHaveLength(3)
    expect(screen.queryByText('Analyzing')).toBeNull()
    // Monitoring off: nothing runs, so no row waits or analyzes.
    rerender(<QuerySection {...props({ rows: first, mode: 'scan' })} />)
    expect(screen.queryByText('Analyzing')).toBeNull()
    expect(screen.queryByText('Waiting for sign-in')).toBeNull()
  })

  it("says how many more are on their way in the pager's place on a first run", () => {
    const page = caFixtures('ca-first-run').queries('demo', '1h', 500, 0)
    const { rerender } = render(
      <QuerySection
        {...props({ rows: page.queries, mode: 'first_run', seenTotal: 16 })}
      />
    )
    expect(screen.getByText('13 more as they are seen')).toBeTruthy()
    expect(screen.queryByText(/of 3$/)).toBeNull()
    rerender(
      <QuerySection
        {...props({ rows: page.queries, mode: 'live', seenTotal: 16 })}
      />
    )
    expect(screen.queryByText(/more as they are seen/)).toBeNull()
  })

  it('counts the matches while filtering, and offers Clear filters when none match', () => {
    const onFiltersChange = vi.fn()
    const page = caFixtures('ca-first-run').queries('demo', '1h', 500, 0)
    const { rerender } = render(
      <QuerySection
        {...props({
          rows: page.queries,
          mode: 'first_run',
          seenTotal: 16,
          filters: { status: 'analyzing', table: null, q: '' },
        })}
      />
    )
    expect(screen.getByText('3 of 3 match')).toBeTruthy()
    expect(screen.queryByText(/more as they are seen/)).toBeNull()
    rerender(
      <QuerySection
        {...props({
          onFiltersChange,
          filters: { status: 'stuck', table: 'nowhere', q: '' },
        })}
      />
    )
    expect(screen.getByText('No queries match these filters.')).toBeTruthy()
    expect(screen.queryAllByTestId('query-row')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(onFiltersChange).toHaveBeenCalledWith({
      status: 'all',
      table: null,
      q: '',
    })
  })

  it('lists a scan as totals without the toolbar, and says when there is none', () => {
    const scan = caFixtures('ca-unmonitored').queries(
      'reports',
      '1h',
      500,
      0
    ).queries
    const { rerender } = render(
      <QuerySection
        {...props({ rows: scan, mode: 'scan', scanAt: at(0, 0, 0, -2) })}
      />
    )
    expect(screen.getByText('3, from a scan on Oct 6')).toBeTruthy()
    expect(screen.getByText('Calls a day (est.)')).toBeTruthy()
    expect(screen.queryByText('Calls/s')).toBeNull()
    expect(screen.getByText(/^48,2\d\d$/)).toBeTruthy()
    expect(screen.queryByTestId('status-menu')).toBeNull()
    rerender(<QuerySection {...props({ rows: [], mode: 'scan' })} />)
    expect(screen.getByText('No queries recorded yet.')).toBeTruthy()
  })
})
