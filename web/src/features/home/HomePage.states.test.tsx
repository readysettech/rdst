import { fireEvent, render, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  type MonitorPulseState,
  readingClock,
} from '../monitor-kit/useMonitorPulse'

const navigate = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return {
    ...(await importOriginal<typeof import('@tanstack/react-router')>()),
    ...fileRouteModuleMock({ useNavigate: () => navigate }),
  }
})

vi.mock('../../components/AnimatedSurfaceBackdrop', () => ({
  AnimatedSurfaceBackdrop: () => null,
}))

vi.mock('../../components/HandRaiser', () => ({
  HandRaiser: () => null,
}))

vi.mock('../monitor-kit/charts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../monitor-kit/charts')>()),
  MiniTrend: () => null,
}))

import { DatabaseHome, FirstRunHome } from './HomePage'

const NOW = 1_790_000_000

const pulse: MonitorPulseState = {
  pulses: [],
  last: null,
  lastCollect: null,
  state: 'live',
  nextAt: null,
  now: NOW,
  clock: readingClock(null, null, NOW),
  beat: 0,
  isLoading: false,
}

function history(firstTs: number | null, span = 0) {
  return {
    first_ts: firstTs,
    last_ts: firstTs == null ? null : firstTs + span,
    span_seconds: span,
    resolutions: {
      '60': { first_ts: firstTs },
      '300': { first_ts: null },
      '3600': { first_ts: null },
      '86400': { first_ts: null },
    },
  }
}

function health(firstTs: number | null, span = 0) {
  return {
    target: 'orders',
    engine: 'postgresql' as const,
    engine_version: null,
    range: '1h' as const,
    res: 60 as const,
    history: history(firstTs, span),
    now: {
      ts: NOW,
      qps: 34.7,
      mean_ms: 1.2,
      active_conns: 4,
      max_conns: 100,
      cache_hit_ratio: 0.99,
      rollbacks_per_min: 0,
    },
    period: {
      qps: 30,
      mean_ms: 1,
      db_time_ms: 100,
      active_conns_peak: 5,
      cache_hit_ratio_min: 0.9,
      xact_rollback: 0,
      deadlocks: 0,
    },
    prior: null,
    series: [],
    markers: [],
  }
}

const counts = { asked: 0, analyzed: 0, compared: 0, candidates: 0 }

describe('FirstRunHome', () => {
  it('sends people to connect a database, with the demo beside it', () => {
    navigate.mockClear()
    const { container } = render(<FirstRunHome />)
    const scope = within(container)

    expect(scope.getByText('See Readyset under real load')).toBeTruthy()
    expect(scope.getByText('Turn on monitoring')).toBeTruthy()
    expect(scope.queryByText(/semantic layer/i)).toBeNull()
    fireEvent.click(scope.getByRole('button', { name: 'Connect a database' }))
    expect(navigate).toHaveBeenCalledWith({ to: '/onboarding' })
  })
})

describe('DatabaseHome', () => {
  it('asks to turn on monitoring on the Database page, never to build a schema', () => {
    navigate.mockClear()
    const { container } = render(
      <DatabaseHome
        state="connected"
        target="orders"
        health={health(null)}
        monitorQueries={undefined}
        pulse={pulse}
        monitoringOn={false}
        recents={[]}
        counts={counts}
        historyDays={null}
      />
    )
    const scope = within(container)

    expect(scope.getByText(/Monitoring is off/)).toBeTruthy()
    expect(scope.queryByText(/semantic layer|schema/i)).toBeNull()
    fireEvent.click(
      scope.getAllByRole('button', { name: 'Turn on monitoring' })[0]
    )
    expect(navigate).toHaveBeenCalledWith({ to: '/database' })
  })

  it('shows first-reading progress while the chart has too little history', () => {
    const { container } = render(
      <DatabaseHome
        state="collecting"
        target="orders"
        health={health(NOW - 120, 120)}
        monitorQueries={undefined}
        pulse={pulse}
        monitoringOn
        recents={[]}
        counts={counts}
        historyDays={0}
      />
    )
    const scope = within(container)

    expect(scope.getByText(/Collecting the first readings/)).toBeTruthy()
    expect(scope.getByText(/The chart is drawing/)).toBeTruthy()
    expect(scope.queryByText(/deep-analyzing|analyzed the busiest/)).toBeNull()
    expect(scope.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '40'
    )
  })

  it('leads with the live database and a way into it', () => {
    navigate.mockClear()
    const { container } = render(
      <DatabaseHome
        state="active"
        target="orders"
        health={health(NOW - 3 * 86_400, 3 * 86_400)}
        monitorQueries={{
          queries: [],
          total: 8,
          counts: { all: 8, attention: 2, new: 1, fixed: 0, muted: 0 },
          composition: [],
          history: history(NOW - 3 * 86_400, 3 * 86_400),
        }}
        pulse={pulse}
        monitoringOn
        recents={[]}
        counts={counts}
        historyDays={3}
        attentionCount={3}
      />
    )
    const scope = within(container)

    expect(scope.getByText('Try the demo')).toBeTruthy()
    expect(scope.getByText('34.7')).toBeTruthy()
    // Queries with an open Inbox item, not the monitor's own attention state.
    expect(scope.getByText('Need attention')).toBeTruthy()
    expect(scope.getByText('3')).toBeTruthy()
    fireEvent.click(scope.getAllByRole('button', { name: 'Open database' })[0])
    expect(navigate).toHaveBeenCalledWith({ to: '/database' })
  })

  it('reopens the analysis an item already has instead of re-running it (A6)', () => {
    navigate.mockClear()
    const { container } = render(
      <DatabaseHome
        state="active"
        target="demo"
        health={health(NOW - 86_400, 86_400)}
        monitorQueries={undefined}
        pulse={pulse}
        monitoringOn
        recents={[
          {
            hash: 'abc123',
            label: 'select * from orders',
            kind: 'analyzed',
            nextAction: 'Review',
            sql: 'select * from orders',
            lastActivity: '2026-08-20T00:00:00Z',
            avgDurationMs: 12,
            frequency: 4,
          },
        ]}
        counts={counts}
        historyDays={1}
      />
    )

    fireEvent.click(
      within(container).getByRole('button', {
        name: 'Continue with review for select * from orders',
      })
    )

    expect(navigate).toHaveBeenCalledWith({
      to: '/database',
      search: { target: 'demo', query: 'abc123', tab: 'analysis' },
    })
  })
})
