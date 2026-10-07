import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MonitoredTargets } from '../../hooks/useMonitoredTargets'

const navigate = vi.hoisted(() => vi.fn())
const monitored = vi.hoisted(() => ({
  current: null as unknown as MonitoredTargets,
}))
const inboxItems = vi.hoisted(() => ({ current: [] as unknown[] }))
const openCounts = vi.hoisted(() => ({ current: {} as Record<string, number> }))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return {
    ...(await importOriginal<typeof import('@tanstack/react-router')>()),
    ...fileRouteModuleMock({
      useNavigate: () => navigate,
      Navigate: ({ to, search }: { to: string; search?: unknown }) => (
        <div data-testid="redirect">{`${to} ${JSON.stringify(search)}`}</div>
      ),
    }),
  }
})

vi.mock('../../components/AnimatedSurfaceBackdrop', () => ({
  AnimatedSurfaceBackdrop: () => null,
}))

vi.mock('../../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({ isError: false, data: { error: null } }),
}))

vi.mock('../../hooks/useMonitoredTargets', () => ({
  useMonitoredTargets: () => monitored.current,
}))

vi.mock('../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders', setTarget: vi.fn() }),
}))

vi.mock('../../lib/monitor/hooks', () => ({
  useInbox: () => ({ data: { items: inboxItems.current } }),
  useMuteRules: () => ({ data: { rules: [] } }),
  useMonitorHealth: (target: string) => ({
    data: target
      ? { engine: target === 'billing' ? 'mysql' : 'postgresql', now: {} }
      : undefined,
  }),
}))

vi.mock('@tanstack/react-query', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-query')>()),
  useQueries: ({ queries }: { queries: { queryKey: unknown[] }[] }) =>
    queries.map((query) => {
      const count = openCounts.current[query.queryKey[1] as string]
      return { data: count === undefined ? undefined : { change_count: count } }
    }),
}))

vi.mock('../../components/TargetMonitorStatus', () => ({
  useTargetMonitorLines: (targets: string[]) =>
    new Map(
      targets.map((target) => [
        target,
        target === 'broken'
          ? { tone: 'negative', label: "Can't reach the database" }
          : { tone: 'positive', label: 'AI monitoring on' },
      ])
    ),
  TargetMonitorStatus: ({ line }: { line?: { label: string } }) => (
    <span>{line?.label}</span>
  ),
}))

import { HomePage } from './HomePage'

function setTargets(targets: string[], on: string[]) {
  monitored.current = {
    targets,
    monitored: on,
    settings: new Map(),
    isPending: false,
  }
}

afterEach(() => {
  cleanup()
  inboxItems.current = []
  openCounts.current = {}
})

describe('HomePage', () => {
  it('opens Connect with no database', () => {
    setTargets([], [])
    render(<HomePage />)
    expect(screen.getByTestId('redirect').textContent).toBe(
      '/onboarding undefined'
    )
  })

  it('opens the Database page of the one monitored database', () => {
    setTargets(['orders', 'billing'], ['billing'])
    render(<HomePage />)
    expect(screen.getByTestId('redirect').textContent).toBe(
      '/database {"target":"billing"}'
    )
  })

  it('opens the Database page when nothing is monitored yet', () => {
    setTargets(['orders'], [])
    render(<HomePage />)
    expect(screen.getByTestId('redirect').textContent).toBe('/database {}')
  })

  it('lists every database with its glow, open actions and new Inbox items', () => {
    setTargets(['orders', 'billing', 'broken'], ['orders', 'billing', 'broken'])
    openCounts.current = { orders: 0, billing: 3, broken: 0 }
    inboxItems.current = [
      {
        id: 1,
        target: 'orders',
        type: 'latency_regression',
        state: 'new',
        severity: 0.9,
        severity_band: 'act_now',
        muted_by: null,
        query_keys: ['q'],
      },
    ]
    render(<HomePage />)

    const cards = screen.getAllByTestId('home-database-card')
    expect(cards.map((card) => card.getAttribute('data-glow'))).toEqual([
      'negative',
      'warning',
      'negative',
    ])
    expect(cards[0].getAttribute('href')).toBe('/database')
    expect(within(cards[0]).getByText('Urgent')).toBeTruthy()
    expect(within(cards[0]).getByText('1')).toBeTruthy()
    expect(within(cards[1]).getByText('MySQL')).toBeTruthy()
    expect(within(cards[1]).getByText('3')).toBeTruthy()
    expect(within(cards[2]).getByText("Can't reach the database")).toBeTruthy()

    expect(screen.getByText('Ask about your data')).toBeTruthy()
    expect(screen.getByText('Analyze a query')).toBeTruthy()
    expect(screen.queryByText('Run a checkup')).toBeNull()
    expect(
      screen.getByRole('link', { name: 'Try the end-to-end demo' })
    ).toBeTruthy()
    expect(screen.queryByText('Continue where you left off')).toBeNull()
  })

  it('glows green when nothing is open', () => {
    setTargets(['orders', 'billing'], ['orders', 'billing'])
    openCounts.current = { orders: 0, billing: 0 }
    render(<HomePage />)
    expect(
      screen
        .getAllByTestId('home-database-card')
        .map((card) => card.getAttribute('data-glow'))
    ).toEqual(['positive', 'positive'])
    expect(screen.getAllByText('All clear')).toHaveLength(2)
  })
})
