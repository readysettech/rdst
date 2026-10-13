import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'

vi.mock('@rs/ui-new/use-toast', () => ({ toast: vi.fn() }))

const navigateSpy = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigateSpy }
})

import type {
  DemoFacts,
  LoadSample,
  PatternRow,
  PreflightChecks,
} from '../../lib/useDemo'
import * as useDemoMod from '../../lib/useDemo'
import { PlatformPage } from './PlatformPage'

type DemoState = ReturnType<typeof useDemoMod.useDemo>

const at = new Date(2026, 9, 8, 9, 50, 0).getTime() / 1000

function sample(t: number): LoadSample {
  return {
    t,
    direct: { qps: 100, p50_ms: 5, p95_ms: 38 },
    router: { qps: 250, p50_ms: 1, p95_ms: 2 },
  }
}

function row(overrides: Partial<PatternRow>): PatternRow {
  return {
    key: 'Q',
    title: 'Query',
    sql: 'SELECT 1',
    group: 'workload',
    status: 'pass_through',
    hits: 0,
    postgres_hits: 0,
    readyset_hits: 0,
    direct_avg_ms: null,
    router_avg_ms: null,
    reason: { kind: 'below_rank' },
    log_reason: null,
    ...overrides,
  }
}

const patterns: PatternRow[] = [
  row({
    key: 'A01',
    title: 'SELECT on orders by customer_id',
    status: 'cached_manual',
    hits: 410,
    direct_avg_ms: 41,
    router_avg_ms: 0.4,
  }),
  row({
    key: 'A02',
    title: 'SELECT on products by category',
    hits: 220,
    direct_avg_ms: 18,
  }),
  row({
    key: 'H01',
    title: 'Revenue by category',
    hits: 34,
    direct_avg_ms: 212,
  }),
  row({ key: 'H02', title: 'Cohort revenue', hits: 30, direct_avg_ms: 180 }),
]

const checks: PreflightChecks = {
  docker_installed: true,
  docker_running: true,
  images_present: false,
  missing_images: ['readyset'],
  download_mb: 1100,
  disk_space_ok: true,
  disk_free_gb: 40,
  disk_required_gb: 2,
  amd64_emulation: 'not_applicable',
}

const facts: DemoFacts = {
  rows: 1_500_000,
  tables: { orders: 1_500_000 },
  pg_version: '16',
  query_count: 44,
  download_mb: 1100,
}

function makeBase(): DemoState {
  return {
    phase: 'idle',
    containers: [],
    provisionPct: 0,
    samples: [],
    windows: [],
    events: [],
    patterns: [],
    querypilotOn: false,
    mode: 'count_star',
    nextPassEta: null,
    cacheBudget: 10,
    loadRunning: false,
    autoTeardownAt: null,
    error: null,
    notice: null,
    provision: vi.fn(),
    beginLoad: vi.fn(),
    endLoad: vi.fn(),
    cache: vi.fn(),
    uncache: vi.fn(),
    toggleQueryPilot: vi.fn(),
    setDiscoveryMode: vi.fn(),
    setCacheBudget: vi.fn(),
    tearDown: vi.fn(),
    setIntensity: vi.fn(),
    refreshPatterns: vi.fn(() => Promise.resolve()),
    refreshStatus: vi.fn(() => Promise.resolve(undefined)),
    refreshHistory: vi.fn(() => Promise.resolve()),
  } as unknown as DemoState
}

function mockDemo(overrides: Partial<DemoState>) {
  const state = { ...makeBase(), ...overrides } as DemoState
  vi.spyOn(useDemoMod, 'useDemo').mockReturnValue(state)
  return state
}

function running(overrides: Partial<DemoState> = {}) {
  return mockDemo({
    phase: 'ready',
    loadRunning: true,
    samples: [sample(at - 60), sample(at)],
    windows: [sample(at - 60), sample(at)],
    patterns,
    autoTeardownAt: new Date(2026, 9, 8, 10, 45, 0).getTime() / 1000,
    ...overrides,
  })
}

/** A fetch that answers the page's own calls; everything else is a 404. */
function stubApi({
  preflight = checks,
  tourDone = true,
}: {
  preflight?: PreflightChecks | null
  tourDone?: boolean
} = {}) {
  const calls: Array<{ url: string; method: string }> = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET' })
    const body =
      url === '/api/demo/preflight'
        ? preflight
        : url === '/api/demo/tour'
          ? { done: tourDone }
          : url === '/api/demo/facts'
            ? facts
            : {}
    const ok = body != null
    return Promise.resolve({
      ok,
      status: ok ? 200 : 500,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    } as unknown as Response)
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

function solidButtons(scope: HTMLElement = document.body) {
  return [...scope.querySelectorAll('button')].filter(
    (el) =>
      el.className.split(/\s+/).includes('bg-surface-primary-solid') &&
      !el.closest('[role="dialog"]')
  )
}

describe('PlatformPage', () => {
  beforeAll(() => {
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>
    proto.hasPointerCapture = () => false
    proto.setPointerCapture = () => {}
    proto.releasePointerCapture = () => {}
    proto.scrollIntoView = () => {}
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('idle: the verdict, the facts card, one solid Start and things to try held until it runs', async () => {
    stubApi()
    const demo = mockDemo({})
    renderWithClient(<PlatformPage />)

    expect(
      screen.getByRole('heading', { level: 1, name: 'Readyset Platform' })
    ).toBeTruthy()
    expect(
      await screen.findByText(
        'A caching demo on your Mac, about 2 minutes. Docker is ready. The first start downloads 1.1 GB.'
      )
    ).toBeTruthy()
    expect(screen.getByText('Readyset caches your queries')).toBeTruthy()
    const facts = screen.getByTestId('platform-facts-card')
    expect(
      within(facts).getByRole('heading', { name: 'What the demo builds' })
    ).toBeTruthy()
    expect(within(facts).getByText('four local containers')).toBeTruthy()
    expect(within(facts).getByText('Orders database')).toBeTruthy()
    expect(within(facts).getByText('once, kept for next time')).toBeTruthy()
    expect(within(facts).getByText('on the orders schema')).toBeTruthy()
    expect(
      await within(facts).findByText('rows, PostgreSQL 16, under live traffic')
    ).toBeTruthy()
    expect(within(facts).getByText('1.5')).toBeTruthy()
    expect(within(facts).getByText('44')).toBeTruthy()

    const start = screen.getByRole('button', { name: 'Start the demo' })
    expect(solidButtons()).toEqual([start])

    const tryCache = screen.getByTestId('try-cache') as HTMLButtonElement
    expect(tryCache.disabled).toBe(true)
    expect(tryCache.className).toContain('w-full')
    expect(tryCache.closest('.opacity-45')).toBeTruthy()
    expect(screen.getByText('once the demo is running')).toBeTruthy()
    fireEvent.click(tryCache)
    expect(
      screen.getByText(
        /Runs in four local containers\. Nothing leaves your Mac/
      )
    ).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()

    // Starting asks first and names the download.
    fireEvent.click(start)
    const confirm = await screen.findByRole('dialog', {
      name: 'Start the demo?',
    })
    expect(
      within(confirm).getByText(
        'Downloads about 1.1 GB of images, then runs four containers on your Mac for up to an hour.'
      )
    ).toBeTruthy()
    expect(demo.provision).not.toHaveBeenCalled()
    fireEvent.click(
      within(confirm).getByRole('button', { name: 'Start the demo' })
    )
    expect(demo.provision).toHaveBeenCalledTimes(1)
  })

  it('asks before starting even with the images downloaded, and says so', async () => {
    stubApi({ preflight: { ...checks, images_present: true } })
    const demo = mockDemo({})
    renderWithClient(<PlatformPage />)

    expect(
      await screen.findByText(
        'A caching demo on your Mac, about 2 minutes. Docker is ready and the images are downloaded.'
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Start the demo' }))
    const confirm = await screen.findByRole('dialog', {
      name: 'Start the demo?',
    })
    expect(
      within(confirm).getByText(
        'Runs four containers on your Mac for up to an hour. The images are already downloaded.'
      )
    ).toBeTruthy()
    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }))
    expect(demo.provision).not.toHaveBeenCalled()
  })

  it('checks Docker again before asking to start', async () => {
    const live = { ...checks }
    stubApi({ preflight: live })
    mockDemo({})
    renderWithClient(<PlatformPage />)

    const start = await screen.findByRole('button', { name: 'Start the demo' })
    await waitFor(() =>
      expect((start as HTMLButtonElement).disabled).toBe(false)
    )
    live.docker_running = false
    fireEvent.click(start)
    expect(await screen.findByText("Docker isn't running")).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('docker missing: holds Start, offers Try again and names the fix', async () => {
    stubApi({ preflight: { ...checks, docker_running: false } })
    mockDemo({})
    renderWithClient(<PlatformPage />)

    expect(await screen.findByText("Docker isn't running")).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain(
      "Docker isn't running. Start Docker Desktop, then try again. The demo needs it for its four containers."
    )
    expect(
      screen.getByRole('button', { name: 'Get Docker Desktop' })
    ).toBeTruthy()
    expect(
      (
        screen.getByRole('button', {
          name: 'Start the demo',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    const again = screen.getByRole('button', { name: 'Try again' })
    expect(solidButtons()).toEqual([again])
    expect(screen.getByTestId('platform-facts-card')).toBeTruthy()
    const tryCache = screen.getByTestId('try-cache') as HTMLButtonElement
    expect(tryCache.closest('.opacity-45')).toBeTruthy()
    expect(tryCache.disabled).toBe(true)
  })

  it('starting: the steps card replaces the facts and Cancel tears down', async () => {
    stubApi()
    const demo = mockDemo({
      phase: 'provisioning',
      provisionPct: 20,
      containers: [
        { name: 'pg', label: 'pg', state: 'starting', percent: 0 },
        { name: 'readyset', label: 'readyset', state: 'pending', percent: 0 },
        { name: 'sqp', label: 'sqp', state: 'pending', percent: 0 },
        { name: 'qp-cron', label: 'qp-cron', state: 'pending', percent: 0 },
      ],
    })
    renderWithClient(<PlatformPage />)

    expect(screen.getByText('Starting, about 2 minutes')).toBeTruthy()
    expect(screen.getByTestId('platform-starting-card')).toBeTruthy()
    expect(screen.queryByTestId('platform-facts-card')).toBeNull()
    expect(solidButtons()).toEqual([])
    expect(screen.getByTestId('try-cache').closest('.opacity-45')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(demo.tearDown).toHaveBeenCalledTimes(1)
  })

  it('running: the proof in the verdict, the comparison, the queries and one solid QueryPilot button', async () => {
    stubApi()
    const demo = running()
    renderWithClient(<PlatformPage />)

    expect(
      screen.getByText('Running since 9:45, removes itself at 10:45')
    ).toBeTruthy()
    expect(
      screen.getByText('Readyset answers 1 of 4 queries from memory:')
    ).toBeTruthy()
    expect(
      screen.getByText(
        /0\.4 ms instead of 41 ms, fresh as orders change\. Turn on QueryPilot to let it pick the rest\./
      )
    ).toBeTruthy()
    expect(screen.getByTestId('platform-comparison-card')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Queries' })).toBeTruthy()
    expect(screen.queryByTestId('platform-facts-card')).toBeNull()

    const turnOn = screen.getByRole('button', { name: 'Turn on QueryPilot' })
    expect(solidButtons()).toEqual([turnOn])
    fireEvent.click(turnOn)
    expect(demo.toggleQueryPilot).toHaveBeenCalledWith(true)
    expect(
      (screen.getByTestId('try-cache') as HTMLButtonElement).disabled
    ).toBe(false)
  })

  it('running with QueryPilot on: a ghost Turn off and no solid button', () => {
    stubApi()
    const demo = running({ querypilotOn: true })
    renderWithClient(<PlatformPage />)
    expect(solidButtons()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Turn off QueryPilot' }))
    expect(demo.toggleQueryPilot).toHaveBeenCalledWith(false)
    expect(
      screen.getByText(
        /QueryPilot picks what to cache by policy: most frequent\./
      )
    ).toBeTruthy()
  })

  it('shuts down only through the red confirm', async () => {
    stubApi()
    const demo = running()
    renderWithClient(<PlatformPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Shut down demo' }))
    const dialog = await screen.findByRole('dialog', {
      name: 'Shut down the demo?',
    })
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Keep running' })
    )
    expect(demo.tearDown).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Shut down demo' }))
    fireEvent.click(
      within(
        await screen.findByRole('dialog', { name: 'Shut down the demo?' })
      ).getByRole('button', { name: 'Shut down' })
    )
    expect(demo.tearDown).toHaveBeenCalledTimes(1)
  })

  it('starts the orders workload itself once the demo is ready', async () => {
    const calls = stubApi()
    running({ loadRunning: false })
    renderWithClient(<PlatformPage />)
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url === '/api/demo/load/start' && call.method === 'POST'
        )
      ).toBe(true)
    )
    cleanup()
    const quiet = stubApi()
    running({ loadRunning: true })
    renderWithClient(<PlatformPage />)
    await screen.findByTestId('platform-comparison-card')
    expect(quiet.some((call) => call.url === '/api/demo/load/start')).toBe(
      false
    )
  })

  it('things to try drive the matching step: raised rows, QueryPilot, the chart', async () => {
    stubApi({ tourDone: false })
    const demo = running({
      patterns: patterns.map((p) =>
        p.key === 'A01' ? { ...p, status: 'pass_through' } : p
      ),
    })
    renderWithClient(<PlatformPage />)

    const tryCache = screen.getByTestId('try-cache')
    expect(tryCache.textContent).toContain('Pick Revenue by category')
    fireEvent.click(tryCache)
    expect(tryCache.getAttribute('aria-current')).toBe('step')
    const raised = screen
      .getAllByTestId('pattern-row')
      .filter((el) => el.getAttribute('data-open') === 'true')
      .map((el) => el.getAttribute('data-key'))
    expect(raised).toEqual(['H01', 'H02'])

    fireEvent.click(screen.getByTestId('try-querypilot'))
    expect(demo.toggleQueryPilot).toHaveBeenCalledWith(true)
    expect(
      screen.getByTestId('try-querypilot').getAttribute('aria-current')
    ).toBe('step')
    expect(tryCache.getAttribute('aria-current')).toBeNull()

    fireEvent.click(screen.getByTestId('try-compare'))
    expect(
      within(screen.getByTestId('try-compare')).getByText('done')
    ).toBeTruthy()
  })

  it('records the tour as done once all three were tried, then shows the invite', async () => {
    const calls = stubApi({ tourDone: false })
    running({
      querypilotOn: true,
      events: [{ t: at, type: 'manual_cache', label: 'you cached A' }],
    })
    renderWithClient(<PlatformPage />)
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/demo/tour')).toBe(true)
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    await act(async () => {
      fireEvent.click(screen.getByTestId('try-compare'))
    })
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url === '/api/demo/tour-done' && call.method === 'POST'
        )
      ).toBe(true)
    )
    expect(
      await screen.findByRole('dialog', {
        name: 'Try this on your own database',
      })
    ).toBeTruthy()
  })

  it('keeps the invite for a finished tour, and away while a step is open', async () => {
    stubApi({ tourDone: true })
    running({
      events: [{ t: at, type: 'manual_cache', label: 'you cached A' }],
    })
    renderWithClient(<PlatformPage />)
    const invite = await screen.findByRole('dialog', {
      name: 'Try this on your own database',
    })
    expect(
      within(invite).getByRole('button', { name: 'Sign in to Readyset' })
    ).toBeTruthy()
    fireEvent.click(screen.getByTestId('try-compare'))
    expect(
      screen.queryByRole('dialog', { name: 'Try this on your own database' })
    ).toBeNull()
  })

  it('confirms a policy switch only while QueryPilot is on', async () => {
    stubApi()
    const demo = running({ querypilotOn: true })
    renderWithClient(<PlatformPage />)
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Policy: most frequent' }),
      { key: 'Enter' }
    )
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Most expensive' })
    )
    expect(demo.setDiscoveryMode).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog', {
      name: 'Switch caching policy?',
    })
    expect(dialog.textContent).toContain(
      'This resets all counters, drops every cache and starts the comparison over.'
    )
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Switch policy' })
    )
    await waitFor(() =>
      expect(demo.setDiscoveryMode).toHaveBeenCalledWith('sum_time')
    )
  })
})
