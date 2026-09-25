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
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { useQueryDiscoveryTransport } from '@/lib/useQueryDiscovery'
import {
  createTestQueryClient,
  jsonResponse,
  renderWithClient,
} from '@/test-utils'
import { DatabasePage, rangeHolding, widerRange } from './DatabasePage'

const polling = vi.hoisted(() => ({
  enabled: false,
  receive: undefined as ((name: string, raw: string) => void) | undefined,
}))
vi.mock('@/lib/sse/sharedEventSource', () => ({
  subscribeShared: (
    _url: string,
    _events: string[],
    receive: typeof polling.receive
  ) => {
    polling.receive = receive
    return () => {
      polling.receive = undefined
    }
  },
}))
vi.mock('@/lib/monitor/contract', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('@/lib/monitor/contract')>()
  return {
    ...original,
    usesFixtures: (endpoint: Parameters<typeof original.usesFixtures>[0]) =>
      polling.enabled && endpoint === 'report'
        ? false
        : original.usesFixtures(endpoint),
  }
})

const navigate = vi.fn()
const routerSearch = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
}))
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => null,
  useNavigate: () => navigate,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: routerSearch.value, pathname: '/database' } }),
  Link: ({ children }: { children: unknown }) => children,
}))
vi.mock('@/lib/useSystemStatus', () => ({
  useSystemStatus: () => ({
    data: { targets: [{ name: 'localdev' }], default_target: 'localdev' },
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
}))
vi.mock('@/features/database/queries', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('@/features/database/queries')>()
  return {
    QuerySection: (props: Parameters<typeof original.QuerySection>[0]) =>
      polling.enabled ? (
        <original.QuerySection {...props} />
      ) : (
        <div data-testid="query-slot" />
      ),
  }
})
vi.mock('@/features/database/reports', () => ({
  ReportView: () => <div data-testid="report-slot" />,
  ReportPicker: () => null,
  AnalyzeDatabaseButton: () => <button type="button">Analyze database</button>,
  useReportRun: () => ({ active: false, status: 'idle', view: null }),
  useRunCompletion: () => undefined,
}))
vi.mock('@/features/database/reports/reportNav', () => ({
  useOpenReport: () => vi.fn(),
}))

describe('DatabasePage', () => {
  let client: QueryClient
  const renderPage = () => renderWithClient(<DatabasePage />, client)
  beforeEach(() => {
    client = createTestQueryClient()
    setFixtureTiming(0)
    resetStubState()
    localStorage.setItem('rdst_selected_target', 'localdev')
  })
  afterEach(async () => {
    cleanup()
    await client.cancelQueries()
    client.clear()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    polling.enabled = false
    await new Promise((resolve) => setTimeout(resolve, 0))
    routerSearch.value = {}
    navigate.mockReset()
    setFixtureOverride(undefined)
    localStorage.clear()
  })

  it.each([
    false,
    true,
  ])('bounds idle requests with assessment updates %s', async (updates) => {
    vi.useFakeTimers()
    polling.enabled = true
    setFixtureOverride('localdev')
    const registryTimes: number[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/report/latest'))
        return jsonResponse({ detail: 'Report not found' }, 404)
      if (url.includes('/api/query-registry')) {
        registryTimes.push(Date.now())
        return jsonResponse({ queries: [], total: 0 })
      }
      if (url.includes('/api/targets')) return jsonResponse({ targets: [] })
      return jsonResponse({ docker_installed: false, docker_running: false })
    })
    vi.stubGlobal('fetch', fetchMock)
    function Discovery() {
      useQueryDiscoveryTransport('localdev')
      return <DatabasePage />
    }
    renderWithClient(<Discovery />, client)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    const count = (path: string) =>
      fetchMock.mock.calls.filter(([input]) =>
        (input instanceof Request ? input.url : String(input)).includes(path)
      ).length
    expect(count('/api/query-registry')).toBe(1)
    expect(count('/report/latest')).toBe(1)
    for (let i = 0; i < 80; i++) {
      await act(async () => {
        if (updates) polling.receive?.('assessment_update', '{}')
        await vi.advanceTimersByTimeAsync(125)
      })
    }
    expect(count('/api/query-registry')).toBe(updates ? 3 : 1)
    for (let i = 1; i < registryTimes.length; i++)
      expect(registryTimes[i] - registryTimes[i - 1]).toBeGreaterThanOrEqual(
        5_000
      )
    expect(count('/report/latest')).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(49_000)
    })
    expect(count('/report/latest')).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(count('/report/latest')).toBe(2)
  })

  it('renders the live overview for the global target', async () => {
    setFixtureOverride('localdev')
    renderPage()
    expect(
      await screen.findByRole('heading', { name: 'localdev' })
    ).toBeTruthy()
    expect(
      await screen.findByRole('heading', { name: /^Queries per second, / })
    ).toBeTruthy()
    const health = await screen.findByRole('region', {
      name: 'Database health',
    })
    expect(within(health).getAllByRole('button')).toHaveLength(3)
    expect(screen.getByText('PostgreSQL 16.4')).toBeTruthy()
    expect(
      screen.getByRole('tab', { name: 'Live' }).getAttribute('aria-selected')
    ).toBe('true')
    expect(screen.getByRole('tab', { name: 'Report' })).toBeTruthy()
    expect(screen.getByTestId('query-slot')).toBeTruthy()
    expect(screen.getByTestId('pulse-halo')).toBeTruthy()
  })

  it('shows the first-run steps and waits for the first queries', async () => {
    setFixtureOverride('empty')
    renderPage()
    const strip = await screen.findByTestId('first-run')
    // Collapsed to the step under way; Details shows all three.
    expect(strip.textContent).toContain('Finding your queries')
    expect(strip.textContent).not.toContain('Nightly report')
    fireEvent.click(within(strip).getByRole('button', { name: /Details/ }))
    expect(strip.textContent).toContain('Analyzing the most expensive queries')
    expect(strip.textContent).toContain('Nightly report')
    // The noisy counts and timers are gone.
    expect(strip.textContent).not.toMatch(/classified|First check|of 5 min/)
    // Readings are in and nothing has run: the waiting card stands in for chart and list.
    const waiting = await screen.findByTestId('waiting-for-queries')
    expect(waiting.textContent).toContain('Waiting for queries')
    expect(waiting.textContent).toContain('test suite')
    expect(screen.queryByTestId('load-hero')).toBeNull()
    expect(screen.queryByTestId('query-section')).toBeNull()
  })

  it('asks for a target when none is selected', async () => {
    localStorage.clear()
    setFixtureOverride('localdev')
    renderPage()
    expect(await screen.findByText('Choose a target to monitor')).toBeTruthy()
  })

  it('widens the range for a linked query the range does not hold', async () => {
    setFixtureOverride('localdev')
    routerSearch.value = { range: '15m', query: 'not-in-this-window' }
    renderPage()
    await screen.findByRole('heading', { name: /^Queries per second, / })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          search: expect.objectContaining({ range: '24h' }),
        })
      )
    )
  })

  it('steps a range out to 24 hours, then everything', () => {
    expect(widerRange('5m')).toBe('24h')
    expect(widerRange('1h')).toBe('24h')
    expect(widerRange('24h')).toBe('all')
    expect(widerRange('7d')).toBe('all')
    expect(widerRange('all')).toBeNull()
  })

  it('picks the smallest range that reaches back to a time frame', () => {
    const now = 1_000_000
    expect(rangeHolding(now - 1800, now)).toBe('1h')
    expect(rangeHolding(now - 5 * 3600, now)).toBe('24h')
    expect(rangeHolding(now - 5 * 86400, now)).toBe('7d')
    expect(rangeHolding(now - 60 * 86400, now)).toBe('90d')
    expect(rangeHolding(now - 200 * 86400, now)).toBe('all')
  })
})
