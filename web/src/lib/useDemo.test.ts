import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { __resetDemoProvisionForTests, useDemo } from './useDemo'

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

// The backend allocates ports and reports provisioned:true partway through the
// deploy (well before the final "complete" frame), so /status keeps a ready page
// ready and re-attaches a mid-deploy return. Mirror that instead of pretending
// the demo is unprovisioned the whole time.
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

// Routes /provision to a controllable SSE stream and everything else to plain
// JSON, so the provision lifecycle can be exercised end to end. `statusHolder`
// lets a test flip the backend's provisioned answer mid-deploy, as the real
// backend does once it has allocated ports.
function mockProvisionFetch(
  sse: ReturnType<typeof sseResponse>,
  statusHolder: { status: Record<string, unknown> }
) {
  return vi.fn((url: RequestInfo | URL) => {
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

    // Start the deploy and stream a progress frame; the backend now reports
    // provisioned (ports allocated) as it does mid-deploy.
    await act(async () => {
      void first.result.current.provision()
    })
    await waitFor(() => expect(first.result.current.phase).toBe('provisioning'))
    holder.status = provisionedStatus
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
    holder.status = provisionedStatus

    // Leave, THEN the deploy finishes on the module-scoped stream.
    first.unmount()
    await act(async () => {
      sse.push('complete', { percent: 100, message: 'Environment ready.' })
      sse.close()
    })

    // Returning derives ready from the settled engine state.
    const second = renderHook(() => useDemo())
    await waitFor(() => expect(second.result.current.phase).toBe('ready'), {
      timeout: 2000,
    })
    second.unmount()
  })

  it('surfaces a provision error and returns to idle so Start can retry', async () => {
    const sse = sseResponse()
    const holder = { status: idleStatus as Record<string, unknown> }
    vi.stubGlobal('fetch', mockProvisionFetch(sse, holder))

    const { result } = renderHook(() => useDemo())
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    await act(async () => {
      void result.current.provision()
    })
    await waitFor(() => expect(result.current.phase).toBe('provisioning'))

    await act(async () => {
      sse.push('error', { message: 'Postgres failed to start' })
      sse.close()
    })
    await waitFor(() => expect(result.current.phase).toBe('idle'))
    expect(result.current.error).toBe('Postgres failed to start')
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
})
