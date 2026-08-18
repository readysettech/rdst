import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  type QueryDiscoveryResync,
  type QueryDiscoverySnapshot,
  queryDiscoveryQueryKey,
  useQueryDiscoveryTransport,
} from './useQueryDiscovery'
import { queryRegistryQueryKey } from './useQueryRegistry'

type Listener = (event: MessageEvent<string>) => void

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  closed = false
  private listeners = new Map<string, Set<Listener>>()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: Listener) {
    const set = this.listeners.get(type) ?? new Set<Listener>()
    set.add(listener)
    this.listeners.set(type, set)
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener)
  }

  close() {
    this.closed = true
  }

  emit(type: string, payload: unknown) {
    const event = { data: JSON.stringify(payload) } as MessageEvent<string>
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

function snapshot(
  overrides: Partial<QueryDiscoverySnapshot> = {}
): QueryDiscoverySnapshot {
  return {
    cursor: 1,
    target: 'prod',
    state: 'watching',
    updated_at: '2026-08-16T00:00:00Z',
    source: 'pg_stat_statements',
    engine: 'postgres',
    query_count: 5,
    new_hashes: [],
    error: null,
    ...overrides,
  }
}

function resyncPayload(
  overrides: Partial<QueryDiscoveryResync> = {}
): QueryDiscoveryResync {
  const { cursor: _cursor, ...state } = snapshot({ changed: true })
  return {
    cursor: 42,
    reason: 'cursor_not_retained',
    latest_seq: 42,
    state,
    ...overrides,
  }
}

function renderTransport(target = 'prod') {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  const rendered = renderHook(() => useQueryDiscoveryTransport(target), {
    wrapper,
  })
  const source = MockEventSource.instances.at(-1)
  if (!source) throw new Error('transport did not open an EventSource')
  return { client, invalidate, source, ...rendered }
}

vi.stubGlobal('EventSource', MockEventSource)

afterEach(() => {
  MockEventSource.instances = []
})

describe('useQueryDiscoveryTransport registry invalidation', () => {
  it('skips invalidation on a no-change poll but keeps freshness live', () => {
    const { client, invalidate, source } = renderTransport()
    const payload = snapshot({
      cursor: 7,
      changed: false,
      updated_at: '2026-08-16T00:01:00Z',
    })

    act(() => {
      source.emit('discovery_update', payload)
    })

    expect(invalidate).not.toHaveBeenCalled()
    expect(client.getQueryData(queryDiscoveryQueryKey('prod'))).toEqual(payload)
  })

  it('invalidates only the target-scoped registry key on a change', () => {
    const { invalidate, source } = renderTransport()

    act(() => {
      source.emit('discovery_update', snapshot({ changed: true }))
    })

    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryRegistryQueryKey('prod'),
    })
  })

  it('invalidates on a legacy payload without the changed field', () => {
    const { invalidate, source } = renderTransport()

    act(() => {
      source.emit('discovery_update', snapshot())
    })

    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryRegistryQueryKey('prod'),
    })
  })

  it('leaves other targets registry queries untouched on a change', () => {
    const { client, source } = renderTransport()
    client.setQueryData([...queryRegistryQueryKey('prod'), 150, 0], {
      queries: [],
      total: 0,
    })
    client.setQueryData([...queryRegistryQueryKey('staging'), 150, 0], {
      queries: [],
      total: 0,
    })

    act(() => {
      source.emit('discovery_update', snapshot({ changed: true }))
    })

    const state = (key: readonly unknown[]) =>
      client.getQueryState(key as unknown[])?.isInvalidated
    expect(state([...queryRegistryQueryKey('prod'), 150, 0])).toBe(true)
    expect(state([...queryRegistryQueryKey('staging'), 150, 0])).toBe(false)
  })

  it('does not invalidate while the collector is still starting', () => {
    const { invalidate, source } = renderTransport()

    act(() => {
      source.emit('discovery_snapshot', snapshot({ state: 'starting' }))
    })

    expect(invalidate).not.toHaveBeenCalled()
  })
})

describe('useQueryDiscoveryTransport resync', () => {
  it('publishes the embedded state and invalidates the registry once', () => {
    const { client, invalidate, source } = renderTransport()
    const payload = resyncPayload()

    act(() => {
      source.emit('resync', payload)
    })

    expect(client.getQueryData(queryDiscoveryQueryKey('prod'))).toEqual({
      ...payload.state,
      cursor: payload.cursor,
    })
    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryRegistryQueryKey('prod'),
    })
    expect(source.closed).toBe(false)
  })

  it('treats subscriber_overflow the same as cursor_not_retained', () => {
    const { client, invalidate, source } = renderTransport()
    const payload = resyncPayload({ reason: 'subscriber_overflow' })

    act(() => {
      source.emit('resync', payload)
    })

    expect(client.getQueryData(queryDiscoveryQueryKey('prod'))).toEqual({
      ...payload.state,
      cursor: payload.cursor,
    })
    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryRegistryQueryKey('prod'),
    })
  })

  it('adopts the resync cursor for the next stream connection', () => {
    const client = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    const { rerender } = renderHook(
      ({ target }: { target: string }) => useQueryDiscoveryTransport(target),
      { wrapper, initialProps: { target: 'prod' } }
    )
    const source = MockEventSource.instances.at(-1)
    if (!source) throw new Error('transport did not open an EventSource')

    act(() => {
      source.emit('resync', resyncPayload({ cursor: 42, latest_seq: 42 }))
    })
    rerender({ target: 'staging' })
    rerender({ target: 'prod' })

    const reopened = MockEventSource.instances.at(-1)
    expect(reopened?.url).toContain('cursor=42')
  })

  it('ignores unknown event types without disturbing the stream', () => {
    const { client, invalidate, source } = renderTransport()

    act(() => {
      source.emit('bookmark', { cursor: 99 })
    })

    expect(invalidate).not.toHaveBeenCalled()
    expect(client.getQueryData(queryDiscoveryQueryKey('prod'))).toBeUndefined()
    expect(source.closed).toBe(false)
  })

  it('keeps processing discovery updates after a resync', () => {
    const { client, invalidate, source } = renderTransport()
    const update = snapshot({
      cursor: 43,
      changed: true,
      updated_at: '2026-08-16T00:02:00Z',
    })

    act(() => {
      source.emit('resync', resyncPayload())
      source.emit('discovery_update', update)
    })

    expect(client.getQueryData(queryDiscoveryQueryKey('prod'))).toEqual(update)
    expect(invalidate).toHaveBeenCalledTimes(2)
    expect(invalidate).toHaveBeenLastCalledWith({
      queryKey: queryRegistryQueryKey('prod'),
    })
  })
})
