import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import {
  __resetTargetSwitchLockForTests,
  useTargetSwitchLockState,
} from './targetSwitchLock'
import { type TopRunSnapshot, topLastRunQK, useTop } from './useTop'

function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

// useTop reads/writes the last-run react-query cache, so every render needs a
// QueryClient in context. A fresh client per test isolates the persistence
// cache; passing a shared client lets a test simulate a remount that restores.
function createWrapper(client: QueryClient = new QueryClient()) {
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children),
  }
}

describe('useTop unknown events', () => {
  it('ignores unknown events without setting error', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
        )
    )

    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useTop('prod'), { wrapper })

    await act(async () => {
      result.current.startRealtime('prod')
      await Promise.resolve()
    })

    expect(result.current.error).toBeNull()
    expect(result.current.state).not.toBe('error')
  })

  it('activates the target switch lock while top streaming is active', async () => {
    __resetTargetSwitchLockForTests()

    let resolveFetch: (response: Response) => void = () => {}
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })

    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise))

    const { wrapper } = createWrapper()
    const { result } = renderHook(
      () => {
        const top = useTop('prod')
        const lock = useTargetSwitchLockState()
        return { top, lock }
      },
      { wrapper }
    )

    act(() => {
      result.current.top.startRealtime('prod')
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(true)
      expect(result.current.lock.message).toBe(
        'Target switching is disabled while Top Queries is in progress.'
      )
    })

    resolveFetch(
      sseResponse('event: complete\ndata: {"queries":[],"newly_saved":0}\n\n')
    )
    await act(async () => {
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(false)
      expect(result.current.lock.message).toBe('')
    })
  })
})

describe('useTop run persistence', () => {
  const historicalResponse = () =>
    new Response(
      JSON.stringify({
        success: true,
        target: 'prod',
        engine: 'postgres',
        source: 'pg_stat',
        queries: [
          { query_hash: 'h1', query_text: 'SELECT 1' },
          { query_hash: 'h2', query_text: 'SELECT 2' },
        ],
        newly_saved: 1,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )

  it('writes the completed run to the last-run cache', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(historicalResponse()))

    const { client, wrapper } = createWrapper()
    const { result } = renderHook(() => useTop('prod'), { wrapper })

    await act(async () => {
      await result.current.getTop('prod')
    })

    await waitFor(() => expect(result.current.state).toBe('complete'))

    const snapshot = client.getQueryData<TopRunSnapshot>(topLastRunQK('prod'))
    expect(snapshot?.queries).toHaveLength(2)
    expect(snapshot?.connectionInfo?.source).toBe('pg_stat')
    expect(snapshot?.newlySaved).toBe(1)
  })

  it('restores the last run on a remount (survives navigation)', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(historicalResponse()))

    // Same client across both renders = the QueryClient that outlives the route.
    const { wrapper } = createWrapper()
    const first = renderHook(() => useTop('prod'), { wrapper })

    await act(async () => {
      await first.result.current.getTop('prod')
    })
    await waitFor(() => expect(first.result.current.state).toBe('complete'))

    // Simulate leaving /top: unmount, then mount a fresh hook on the same client.
    first.unmount()
    const second = renderHook(() => useTop('prod'), { wrapper })

    expect(second.result.current.state).toBe('complete')
    expect(second.result.current.queries).toHaveLength(2)
    expect(second.result.current.connectionInfo?.target).toBe('prod')
  })

  it('scopes the snapshot by target: switching A→B does not restore A', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(historicalResponse()))

    const { client, wrapper } = createWrapper()

    // Run on target A ('prod'), then leave /top.
    const a = renderHook(() => useTop('prod'), { wrapper })
    await act(async () => {
      await a.result.current.getTop('prod')
    })
    await waitFor(() => expect(a.result.current.state).toBe('complete'))
    a.unmount()

    // Return to /top with a *different* selected target — B restores nothing,
    // so A's rows never appear under B's header. [FIX-4]
    const b = renderHook(() => useTop('staging'), { wrapper })
    expect(b.result.current.state).toBe('idle')
    expect(b.result.current.queries).toHaveLength(0)

    // A's snapshot is untouched under its own key; B's key is empty.
    expect(client.getQueryData(topLastRunQK('prod'))).toBeDefined()
    expect(client.getQueryData(topLastRunQK('staging'))).toBeUndefined()
  })

  it('does not persist in-progress streaming state', async () => {
    __resetTargetSwitchLockForTests()

    let resolveFetch: (response: Response) => void = () => {}
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise))

    const { client, wrapper } = createWrapper()
    const { result } = renderHook(() => useTop('prod'), { wrapper })

    act(() => {
      result.current.startRealtime('prod')
    })
    await waitFor(() => expect(result.current.state).toBe('streaming'))

    // Mid-stream: nothing cached yet.
    expect(client.getQueryData(topLastRunQK('prod'))).toBeUndefined()

    resolveFetch(
      sseResponse('event: complete\ndata: {"queries":[],"newly_saved":0}\n\n')
    )
    await act(async () => {
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.state).toBe('complete'))

    // Only after completion is the run cached.
    expect(client.getQueryData(topLastRunQK('prod'))).toBeDefined()
  })

  it('clears the cache on reset so a stale run is not restored', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(historicalResponse()))

    const { client, wrapper } = createWrapper()
    const { result } = renderHook(() => useTop('prod'), { wrapper })

    await act(async () => {
      await result.current.getTop('prod')
    })
    await waitFor(() => expect(result.current.state).toBe('complete'))
    expect(client.getQueryData(topLastRunQK('prod'))).toBeDefined()

    act(() => {
      result.current.reset()
    })

    expect(client.getQueryData(topLastRunQK('prod'))).toBeUndefined()
    expect(result.current.state).toBe('idle')
    expect(result.current.queries).toHaveLength(0)
  })
})
