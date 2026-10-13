import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentActivityItem } from '@/lib/agentActivity'
import {
  __resetBackgroundRunsForTests,
  upsertLocalRun,
} from '@/lib/backgroundRuns'
import { at, CA_NOW } from '@/lib/monitor/fixturesCa'
import { jsonResponse, renderWithClient } from '@/test-utils'

const routerState = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routerState.navigate,
  useSearch: () => routerState.search,
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

import { AgentActivityPage } from './AgentActivityPage'

function entry(overrides: Partial<AgentActivityItem>): AgentActivityItem {
  return {
    key: `entry:${overrides.id ?? 1}`,
    kind: 'entry',
    id: 1,
    ts: CA_NOW,
    first_ts: CA_NOW,
    count: 1,
    done: 1,
    failed: 0,
    skipped: 0,
    target: 'demo',
    action: 'deep_analysis',
    subject: '',
    outcome: 'done',
    summary: '',
    source: 'live',
    statement_count: 0,
    ai_call_count: 0,
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    items_filed: 0,
    ...overrides,
  }
}

/** The canonical timeline: Thursday October 8, the report for October 7 at midnight. */
const ITEMS: AgentActivityItem[] = [
  entry({
    id: 1,
    ts: at(9, 50),
    subject: 'SELECT on badges by userid',
    query_key: 'q1',
    finding: 'fix suggested',
  }),
  entry({
    id: 2,
    kind: 'rollup',
    key: 'checks:demo:today',
    ts: at(9, 50),
    first_ts: at(0, 5),
    action: 'attention_check',
    fold: 'attention_check',
    group: 'day',
    count: 4,
    done: 2,
    failed: 2,
    bucket_start: at(0, 0),
    bucket_end: at(0, 0, 0, 1),
  }),
  entry({
    id: 3,
    ts: at(0, 0),
    action: 'daily_snapshot',
    subject: 'Nightly',
    period_start: at(0, 0, 0, -1),
    period_end: at(0, 0),
    finding: '9 fixes ready',
    report_id: 31,
  }),
  entry({
    id: 4,
    ts: at(15, 15, 0, -1),
    subject: 'COUNT on posts by posttypeid',
    query_key: 'q4',
    finding: 'fix suggested',
  }),
  entry({
    id: 5,
    ts: at(16, 10, 0, -2),
    action: 'ask',
    question: 'Which posts have no owner?',
    subject: 'posts, users',
  }),
]

beforeEach(() => {
  routerState.search = {}
  routerState.navigate.mockReset()
  vi.spyOn(Date, 'now').mockReturnValue(CA_NOW * 1000)
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      Promise.resolve(
        url.includes('/api/agent-activity')
          ? jsonResponse({
              items: ITEMS,
              next_cursor: null,
              targets: ['demo', 'staging'],
              retention_days: 90,
            })
          : jsonResponse({}, 404)
      )
    )
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AgentActivityPage', () => {
  it('opens with the bar, the two menus and the kebab', async () => {
    renderWithClient(<AgentActivityPage />)
    expect(
      screen.getByRole('heading', { level: 1, name: 'Activity' })
    ).toBeTruthy()
    expect(screen.getByText('what RDST did')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Database: All databases' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Events: All events' })
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'More actions' })).toBeTruthy()
    await screen.findByText('Kept 90 days.')
  })

  it('groups rows by day with the time, the line, its detail and where it leads', async () => {
    renderWithClient(<AgentActivityPage />)
    const today = await screen.findByRole('region', { name: 'Today' })
    const rows = within(today).getAllByRole('link')
    expect(rows.length).toBe(3)
    expect(rows[0].textContent).toContain('9:50 AM')
    expect(rows[0].textContent).toContain('Analyzed SELECT on badges by userid')
    expect(rows[0].textContent).toContain('fix suggested')
    expect(rows[0].textContent).toContain('Analysis')
    expect(rows[0].getAttribute('href')).toContain('tab=analysis')
    const checks = within(today).getByText("2 of 4 didn't complete")
    expect(checks.className).toContain('text-content-warning-soft')
    expect(within(today).getByText('Daily report for October 7')).toBeTruthy()
    expect(
      within(screen.getByRole('region', { name: 'Yesterday' })).getAllByRole(
        'link'
      ).length
    ).toBe(1)
    expect(
      within(screen.getByRole('region', { name: 'October 6' })).getByText('Ask')
    ).toBeTruthy()
  })

  it('names the database on every row, folded ones too, when the log has several', async () => {
    renderWithClient(<AgentActivityPage />)
    const today = await screen.findByRole('region', { name: 'Today' })
    for (const row of within(today).getAllByRole('link'))
      expect(row.textContent).toContain('on demo')
  })

  it('leaves the name off with one database in the log', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          url.includes('/api/agent-activity')
            ? jsonResponse({
                items: ITEMS,
                next_cursor: null,
                targets: ['demo'],
              })
            : jsonResponse({}, 404)
        )
      )
    )
    renderWithClient(<AgentActivityPage />)
    const today = await screen.findByRole('region', { name: 'Today' })
    expect(today.textContent).not.toContain('on demo')
  })

  it('asks for seven days and filters by database from the menu', async () => {
    renderWithClient(<AgentActivityPage />)
    await screen.findByRole('region', { name: 'Today' })
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string
    expect(url).toContain('since=')
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Database: All databases' }),
      { key: 'ArrowDown' }
    )
    const staging = await screen.findByRole('menuitem', { name: 'staging' })
    expect(staging.querySelector('svg')).toBeNull()
    fireEvent.click(staging)
    expect(routerState.navigate).toHaveBeenCalled()
  })

  it("keeps a removed database's rows, without a link, and says it was removed", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          url.includes('/api/agent-activity')
            ? jsonResponse({
                items: ITEMS,
                next_cursor: null,
                targets: ['demo'],
              })
            : url === '/api/status'
              ? jsonResponse({ targets: [{ name: 'staging' }] })
              : jsonResponse({}, 404)
        )
      )
    )
    renderWithClient(<AgentActivityPage />)
    const today = await screen.findByRole('region', { name: 'Today' })
    expect((await within(today).findAllByText('on demo, removed')).length).toBe(
      3
    )
    expect(within(today).queryAllByRole('link')).toHaveLength(0)
  })

  it('lists the work in progress at the top, each with a Cancel', async () => {
    __resetBackgroundRunsForTests()
    const stopLoad = vi.fn()
    upsertLocalRun(
      {
        runId: 'run-1',
        kind: 'analyze',
        target: 'demo',
        status: 'running',
        message: '',
        queryLabel: 'SELECT on users by id',
      },
      { onCancel: stopLoad }
    )
    const fetchMock = vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve(
        url.includes('/api/agent-activity')
          ? jsonResponse({ items: ITEMS, next_cursor: null, targets: ['demo'] })
          : url === '/api/status'
            ? jsonResponse({ targets: [{ name: 'demo' }] })
            : url.endsWith('/targets/demo/analyze-jobs')
              ? jsonResponse({
                  jobs: [],
                  queue: {
                    running: {
                      query_key: 'q9',
                      display_name: 'SELECT on posts by id',
                    },
                  },
                })
              : url.endsWith('/analyze/cancel') && init?.method === 'POST'
                ? jsonResponse({ id: 1, state: 'skipped' })
                : jsonResponse({}, 404)
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    renderWithClient(<AgentActivityPage />)
    const running = await screen.findByRole('region', { name: 'Running now' })
    await within(running).findByText('Analyzing SELECT on posts by id')
    expect(
      within(running).getByText('Analyzing SELECT on users by id')
    ).toBeTruthy()
    const cancels = within(running).getAllByRole('button', { name: 'Cancel' })
    expect(cancels).toHaveLength(2)
    fireEvent.click(cancels[0])
    await vi.waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith('/targets/demo/queries/q9/analyze/cancel')
        )
      ).toBe(true)
    )
    fireEvent.click(cancels[1])
    expect(stopLoad).toHaveBeenCalledOnce()
    __resetBackgroundRunsForTests()
  })

  it('shows the empty state when nothing happened', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({ items: [], next_cursor: null, targets: [] })
        )
      )
    )
    renderWithClient(<AgentActivityPage />)
    expect(await screen.findByText('Nothing yet')).toBeTruthy()
  })
})
