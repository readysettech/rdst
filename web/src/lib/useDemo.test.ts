import { toast } from '@rs/ui-new/use-toast'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { __resetDemoProvisionForTests, useDemo } from './useDemo'

vi.mock('@rs/ui-new/use-toast', () => ({ toast: vi.fn() }))

function jsonResponse(body: unknown) {
  return {
    ok: true,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    body: null,
  } as unknown as Response
}

function mockFetch(status: Record<string, unknown>) {
  return vi.fn((url: RequestInfo | URL) => {
    const u = String(url)
    if (u.includes('/status')) return Promise.resolve(jsonResponse(status))
    if (u.includes('/history'))
      return Promise.resolve(jsonResponse({ samples: [], events: [] }))
    if (u.includes('/patterns'))
      return Promise.resolve(jsonResponse({ patterns: [] }))
    return Promise.resolve(jsonResponse({}))
  })
}

const querypilot = {
  enabled: false,
  mode: 'count_star',
  next_pass_eta_s: null,
  schedule: '15s',
  cache_budget: 10,
}

const idleStatus = {
  provisioned: false,
  ports: null,
  health: {
    pg: 'absent',
    readyset: 'absent',
    sqp: 'absent',
    'qp-cron': 'absent',
  },
  querypilot,
  load_running: false,
  cache_budget: 10,
  last_error: null,
}

// While a start runs the backend reports provisioning, and provisioned only once
// the start has completed.
const provisioningStatus = { ...idleStatus, provisioning: true }

const provisionedStatus = {
  ...idleStatus,
  provisioned: true,
  ports: { pg: 5432 },
  health: {
    pg: 'running',
    readyset: 'running',
    sqp: 'running',
    'qp-cron': 'running',
  },
}

// A push-driven SSE response body so a test can stream provision frames on
// demand — mirrors the backend's EventSourceResponse (event:/data: lines).
function sseResponse() {
  let controller: ReadableStreamDefaultController<Uint8Array>
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  return {
    response: {
      ok: true,
      status: 200,
      statusText: 'OK',
      body: stream,
    } as unknown as Response,
    push(type: string, data: Record<string, unknown>) {
      controller.enqueue(
        encoder.encode(
          `event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`
        )
      )
    },
    close() {
      controller.close()
    },
  }
}

// Routes /provision (and its re-attach stream) to a controllable SSE stream and
// everything else to plain JSON, so the provision lifecycle can be exercised end
// to end. `statusHolder` lets a test change the backend's answer as the start
// progresses.
function mockProvisionFetch(
  sse: ReturnType<typeof sseResponse>,
  statusHolder: { status: Record<string, unknown> }
) {
  return vi.fn((url: RequestInfo | URL, _init?: RequestInit) => {
    const u = String(url)
    if (u.includes('/provision')) return Promise.resolve(sse.response)
    if (u.includes('/status'))
      return Promise.resolve(jsonResponse(statusHolder.status))
    if (u.includes('/history'))
      return Promise.resolve(jsonResponse({ samples: [], events: [] }))
    if (u.includes('/patterns'))
      return Promise.resolve(jsonResponse({ patterns: [] }))
    return Promise.resolve(jsonResponse({}))
  })
}

describe('useDemo provisioned derivation', () => {
  afterEach(() => {
    cleanup()
    __resetDemoProvisionForTests()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('stays idle when the backend reports unprovisioned despite stale running health', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch({
        provisioned: false,
        ports: null,
        health: {
          pg: 'failed',
          readyset: 'failed',
          sqp: 'failed',
          'qp-cron': 'running',
        },
        querypilot,
        load_running: false,
        cache_budget: 10,
        last_error: null,
      })
    )
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
  })

  it('becomes ready when the backend reports provisioned', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch({
        provisioned: true,
        ports: { pg: 5432 },
        health: {
          pg: 'running',
          readyset: 'running',
          sqp: 'running',
          'qp-cron': 'running',
        },
        querypilot,
        load_running: false,
        cache_budget: 10,
        last_error: null,
      })
    )
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('ready'))
  })
})

// The demo owner's round-3 bug: starting a deploy, navigating away mid-load, and
// returning showed the Start card again (progress lost) and a second Start
// errored. The provision lifecycle now lives in a module-scoped engine, so it
// survives the route unmount/remount and re-attaches on return.
describe('useDemo provision persists across navigation', () => {
  afterEach(() => {
    cleanup()
    __resetDemoProvisionForTests()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('restores the in-progress deploy after leaving and returning to /demo', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const first = renderHook(() => useDemo())
    await waitFor(() => expect(first.result.current.phase).toBe('idle'))

    // Start the deploy and stream a progress frame; the backend now reports the
    // start in flight.
    await act(async () => {
      void first.result.current.provision()
    })
    await waitFor(() => expect(first.result.current.phase).toBe('provisioning'))
    holder.status = provisioningStatus
    await act(async () => {
      sse.push('container', {
        name: 'pg',
        label: 'Postgres (Orders dataset)',
        state: 'ready',
        percent: 30,
      })
    })
    await waitFor(() => expect(first.result.current.provisionPct).toBe(30))

    // Navigate away.
    first.unmount()

    // Return: a fresh mount shows the SAME in-progress state, not the Start card.
    const second = renderHook(() => useDemo())
    expect(second.result.current.phase).toBe('provisioning')
    expect(second.result.current.provisionPct).toBe(30)
    expect(
      second.result.current.containers.find((c) => c.name === 'pg')?.state
    ).toBe('ready')

    // The still-live module stream completes → the returned page reaches ready.
    await act(async () => {
      sse.push('complete', { percent: 100, message: 'Environment ready.' })
      sse.close()
    })
    holder.status = provisionedStatus
    await waitFor(() => expect(second.result.current.phase).toBe('ready'), {
      timeout: 2000,
    })
    second.unmount()
  })

  it('reaches ready when the deploy completes while the page is unmounted', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const first = renderHook(() => useDemo())
    await waitFor(() => expect(first.result.current.phase).toBe('idle'))
    await act(async () => {
      void first.result.current.provision()
    })
    await waitFor(() => expect(first.result.current.phase).toBe('provisioning'))
    holder.status = provisioningStatus

    // Leave, THEN the deploy finishes on the module-scoped stream.
    first.unmount()
    await act(async () => {
      sse.push('complete', { percent: 100, message: 'Environment ready.' })
      sse.close()
    })
    holder.status = provisionedStatus

    // Returning derives ready from the settled engine state.
    const second = renderHook(() => useDemo())
    await waitFor(() => expect(second.result.current.phase).toBe('ready'), {
      timeout: 2000,
    })
    second.unmount()
  })

  it('ends a failed provision in the failed state with its message, never ready', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    const fetchMock = mockProvisionFetch(sse, holder)
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    // A stale status read reports the demo provisioned.
    holder.status = provisionedStatus

    await act(async () => {
      sse.push('error', { message: 'ReadySet did not snapshot in time.' })
      sse.close()
    })
    await waitFor(() => expect(result.current.phase).toBe('failed'))
    expect(result.current.error).toBe('ReadySet did not snapshot in time.')

    // A status read that still says provisioned doesn't turn the failure into Running.
    await act(async () => {
      await result.current.refreshStatus()
    })
    expect(result.current.phase).toBe('failed')
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes('/load/start'))
    ).toBe(false)
  })

  it('reconciles a dropped stream to failed when the backend says the start failed', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    holder.status = {
      ...idleStatus,
      last_error: 'SQP proxy did not become ready.',
    }

    await act(async () => {
      sse.close()
    })
    await waitFor(() => expect(result.current.phase).toBe('failed'))
    expect(result.current.error).toBe('SQP proxy did not become ready.')
  })

  it('ignores a second Start while a deploy is already in flight (no double-provision)', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    const fetchMock = mockProvisionFetch(sse, holder)
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))

    const provisionPosts = () =>
      fetchMock.mock.calls.filter(([u]) => String(u).includes('/provision'))
        .length

    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    expect(provisionPosts()).toBe(1)

    // A second Start (e.g. a returning visitor clicking again) must not re-POST.
    await act(async () => {
      void result.current.provision()
    })
    expect(provisionPosts()).toBe(1)
    expect(result.current.phase).toBe('provisioning')
  })

  it('reconciles to ready via /status when the stream drops without a terminal frame', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    // The backend finished the deploy (ports allocated, containers up) even
    // though the client's stream dropped without a terminal frame.
    holder.status = provisionedStatus

    await act(async () => {
      sse.close()
    })
    await waitFor(() => expect(result.current.phase).toBe('ready'), {
      timeout: 2000,
    })
  })

  it('settles a dropped start that finishes before the next status poll', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    // The stream drops while the backend still reports the start running.
    holder.status = provisioningStatus
    await act(async () => {
      sse.close()
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(result.current.phase).toBe('provisioning')

    // The start completes before the next poll reattaches to it.
    holder.status = provisionedStatus
    await act(async () => {
      await result.current.refreshStatus()
    })
    await waitFor(() => expect(result.current.phase).toBe('ready'))
  })

  it('settles a dropped start that fails before the next status poll', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    holder.status = provisioningStatus
    await act(async () => {
      sse.close()
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })

    holder.status = { ...idleStatus, last_error: 'SQP proxy did not start.' }
    await act(async () => {
      await result.current.refreshStatus()
    })
    await waitFor(() => expect(result.current.phase).toBe('failed'))
    expect(result.current.error).toBe('SQP proxy did not start.')
  })

  it('reattaches to a start still running after its stream dropped', async () => {
    const first = sseResponse()
    const replay = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    const fetchMock = vi.fn((url: RequestInfo | URL, _init?: RequestInit) => {
      const u = String(url)
      if (u.endsWith('/provision/stream'))
        return Promise.resolve(replay.response)
      if (u.includes('/provision')) return Promise.resolve(first.response)
      if (u.includes('/status'))
        return Promise.resolve(jsonResponse(holder.status))
      if (u.includes('/history'))
        return Promise.resolve(jsonResponse({ samples: [], events: [] }))
      if (u.includes('/patterns'))
        return Promise.resolve(jsonResponse({ patterns: [] }))
      return Promise.resolve(jsonResponse({}))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    holder.status = provisioningStatus
    await act(async () => {
      first.close()
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })

    await act(async () => {
      await result.current.refreshStatus()
    })
    const replays = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith('/provision/stream')
    )
    expect(replays).toHaveLength(1)
    expect(result.current.phase).toBe('provisioning')

    await act(async () => {
      replay.push('complete', { percent: 100, message: 'Environment ready.' })
      replay.close()
    })
    holder.status = provisionedStatus
    await waitFor(() => expect(result.current.phase).toBe('ready'), {
      timeout: 2000,
    })
  })

  it('follows a start already in flight after a reload instead of showing it running', async () => {
    const sse = sseResponse()
    const holder = { status: provisioningStatus as Record<string, unknown> }
    const fetchMock = mockProvisionFetch(sse, holder)
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    const provisionCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/provision')
    )
    expect(provisionCalls).toHaveLength(1)
    expect(String(provisionCalls[0][0])).toBe('/api/demo/provision/stream')
    expect(provisionCalls[0][1]).toMatchObject({ method: 'GET' })

    // The backend replays the start so far.
    await act(async () => {
      sse.push('container', {
        name: 'pg',
        label: 'Postgres (Orders dataset)',
        state: 'ready',
        percent: 30,
      })
    })
    await waitFor(() => expect(result.current.provisionPct).toBe(30))
    expect(result.current.containers.find((c) => c.name === 'pg')?.state).toBe(
      'ready'
    )
    await act(async () => {
      await result.current.refreshStatus()
    })
    expect(result.current.phase).toBe('provisioning')

    await act(async () => {
      sse.push('complete', { percent: 100, message: 'Environment ready.' })
      sse.close()
    })
    holder.status = provisionedStatus
    await waitFor(() => expect(result.current.phase).toBe('ready'), {
      timeout: 2000,
    })
  })

  it('ends a start followed after a reload in the failed state when it fails', async () => {
    const sse = sseResponse()
    const holder = { status: provisioningStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))
    holder.status = {
      ...idleStatus,
      last_error: 'ReadySet did not snapshot in time.',
    }
    await act(async () => {
      sse.push('error', { message: 'ReadySet did not snapshot in time.' })
      sse.close()
    })
    await waitFor(() => expect(result.current.phase).toBe('failed'))
    expect(result.current.error).toBe('ReadySet did not snapshot in time.')
  })
})

describe('useDemo QueryPilot and policy changes', () => {
  afterEach(() => {
    cleanup()
    __resetDemoProvisionForTests()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  function failingFetch(failing: string) {
    const ok = mockFetch(provisionedStatus)
    return vi.fn((url: RequestInfo | URL) =>
      String(url).includes(failing)
        ? Promise.resolve({
            ok: false,
            status: 500,
            statusText: 'Internal Server Error',
            text: () => Promise.resolve('Internal Server Error'),
          } as unknown as Response)
        : ok(url)
    )
  }

  it('a failed QueryPilot toggle keeps it off, adds no annotation and says why', async () => {
    vi.stubGlobal('fetch', failingFetch('/api/demo/querypilot'))
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('ready'))

    await act(async () => {
      await result.current.toggleQueryPilot(true)
    })

    expect(result.current.querypilotOn).toBe(false)
    expect(result.current.events).toEqual([])
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Couldn't turn on QueryPilot",
        description: 'Internal Server Error',
        variant: 'negative',
      })
    )
  })

  it('a failed policy switch keeps the current policy and adds no annotation', async () => {
    vi.stubGlobal('fetch', failingFetch('/api/demo/discovery-mode'))
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('ready'))

    await act(async () => {
      await result.current.setDiscoveryMode('sum_time')
    })

    expect(result.current.mode).toBe('count_star')
    expect(result.current.events).toEqual([])
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Couldn't switch the caching policy",
        variant: 'negative',
      })
    )
  })

  // A fetch whose QueryPilot and policy calls wait until `release` is called.
  function gatedFetch() {
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    const ok = mockFetch(provisionedStatus)
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url)
      if (u.includes('/querypilot') || u.includes('/discovery-mode')) await gate
      return ok(url)
    })
    const changes = () =>
      fetchMock.mock.calls.filter(([u]) =>
        /\/(querypilot|discovery-mode)/.test(String(u))
      ).length
    return { fetchMock, release, changes }
  }

  it('ignores QueryPilot and policy changes while one is pending', async () => {
    const { fetchMock, release, changes } = gatedFetch()
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.querypilotPending).toBe(false)

    let first!: Promise<void>
    await act(async () => {
      first = result.current.toggleQueryPilot(true)
    })
    expect(result.current.querypilotPending).toBe(true)
    expect(result.current.querypilotOn).toBe(false)
    await act(async () => {
      await result.current.toggleQueryPilot(true)
      await result.current.setDiscoveryMode('sum_time')
    })
    expect(changes()).toBe(1)

    await act(async () => {
      release()
      await first
    })
    expect(result.current.querypilotPending).toBe(false)
    expect(result.current.events.map((e) => e.type)).toEqual(['qp_on'])
  })

  it('takes a new change once a failed one has settled', async () => {
    vi.stubGlobal('fetch', failingFetch('/api/demo/querypilot'))
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('ready'))

    await act(async () => {
      await result.current.toggleQueryPilot(true)
    })
    expect(result.current.querypilotPending).toBe(false)
    expect(result.current.querypilotOn).toBe(false)

    await act(async () => {
      await result.current.setDiscoveryMode('sum_time')
    })
    expect(result.current.events.map((e) => e.type)).toEqual(['mode_change'])
  })

  it('a confirmed QueryPilot toggle turns it on and annotates the chart', async () => {
    vi.stubGlobal('fetch', mockFetch(provisionedStatus))
    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('ready'))

    await act(async () => {
      await result.current.toggleQueryPilot(true)
    })

    expect(result.current.events.map((e) => e.type)).toEqual(['qp_on'])
  })
})
