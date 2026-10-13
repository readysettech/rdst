import { onlineManager, QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  backendDown,
  backendState,
  installBackendReachability,
  isUnreachableError,
  isUnreachableResponse,
  keepDataWhileDown,
  PROBE_INTERVAL_MS,
  retryQuery,
  setDesktopBackendState,
  UNREACHABLE_HEADER,
} from './backendReachability'
import type { DesktopBackendState } from './desktop'

const response = (status: number, headers: Record<string, string> = {}) =>
  new Response(null, { status, headers })

describe('what counts as the backend being unreachable', () => {
  it('reads gateway errors that the backend did not send', () => {
    expect(isUnreachableResponse(response(502))).toBe(true)
    expect(
      isUnreachableResponse(response(504, { 'content-type': 'text/plain' }))
    ).toBe(true)
    expect(
      isUnreachableResponse(
        response(200, { [UNREACHABLE_HEADER]: 'unreachable' })
      )
    ).toBe(true)
    // The backend's own JSON errors (a provider being down, say) are not an outage.
    expect(
      isUnreachableResponse(
        response(503, { 'content-type': 'application/json' })
      )
    ).toBe(false)
    expect(isUnreachableResponse(response(500))).toBe(false)
    expect(isUnreachableResponse(response(200))).toBe(false)
  })

  it('reads fetch network errors, not aborts or other errors', () => {
    expect(isUnreachableError(new TypeError('Failed to fetch'))).toBe(true)
    expect(isUnreachableError(new TypeError('fetch failed'))).toBe(true)
    expect(isUnreachableError(new DOMException('aborted', 'AbortError'))).toBe(
      false
    )
    expect(isUnreachableError(new Error('Failed to fetch'))).toBe(false)
  })
})

describe('installBackendReachability', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let uninstall: () => void
  let desktopListener: ((state: DesktopBackendState) => void) | undefined

  beforeEach(() => {
    vi.useFakeTimers()
    fetchMock = vi.fn()
    window.fetch = fetchMock as unknown as typeof fetch
    window.rdstDesktop = undefined
    uninstall = installBackendReachability()
  })

  afterEach(() => {
    uninstall()
    window.rdstDesktop = undefined
    desktopListener = undefined
    vi.useRealTimers()
  })

  it('goes offline when /api fails to connect and back online when a probe answers', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(window.fetch('/api/monitor/work')).rejects.toThrow(
      'Failed to fetch'
    )
    expect(backendState()).toBe('restarting')
    expect(onlineManager.isOnline()).toBe(false)
    expect(retryQuery(5)).toBe(true)

    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    await vi.advanceTimersByTimeAsync(PROBE_INTERVAL_MS)
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/settings/preferences',
      undefined
    )
    expect(backendDown()).toBe(false)
    expect(onlineManager.isOnline()).toBe(true)
    expect(retryQuery(0)).toBe(true)
    expect(retryQuery(1)).toBe(false)
  })

  it('stays online when the machine has no network, since the backend is local', () => {
    uninstall()
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    try {
      uninstall = installBackendReachability()
      window.dispatchEvent(new Event('offline'))
      expect(onlineManager.isOnline()).toBe(true)
    } finally {
      onLine.mockRestore()
    }
  })

  it('leaves requests outside /api alone', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(window.fetch('https://readyset.io/x')).rejects.toThrow()
    expect(backendDown()).toBe(false)
  })

  it('follows the desktop app and keeps the data pages already have', async () => {
    uninstall()
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'linux',
      backend: {
        getState: async () => 'running',
        onStateChange: (callback) => {
          desktopListener = callback
          return () => undefined
        },
      },
    }
    uninstall = installBackendReachability()
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: retryQuery, structuralSharing: keepDataWhileDown },
      },
    })
    client.setQueryData(['targets'], ['prod'])

    desktopListener?.('restarting')
    expect(backendState()).toBe('restarting')
    // A fallback result that arrives while it is down doesn't replace what was shown.
    client.setQueryData(['targets'], [])
    expect(client.getQueryData(['targets'])).toEqual(['prod'])

    desktopListener?.('failed')
    expect(backendState()).toBe('failed')
    desktopListener?.('running')
    expect(onlineManager.isOnline()).toBe(true)
    client.setQueryData(['targets'], [])
    expect(client.getQueryData(['targets'])).toEqual([])
  })

  it('pauses a query that fails while the backend is down and resumes it when back', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: retryQuery, retryDelay: 10 } },
    })
    // QueryClientProvider mounts the client, which resumes paused queries when online.
    client.mount()
    let calls = 0
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const query = client.fetchQuery({
      queryKey: ['work'],
      queryFn: async () => {
        calls += 1
        if (calls === 1) {
          await window.fetch('/api/monitor/work')
          return 'unreachable'
        }
        return 'ok'
      },
    })
    await vi.advanceTimersByTimeAsync(1_000)
    expect(calls).toBe(1)
    expect(client.getQueryState(['work'])?.fetchStatus).toBe('paused')

    setDesktopBackendState('running')
    await vi.advanceTimersByTimeAsync(20)
    await expect(query).resolves.toBe('ok')
    client.unmount()
  })
})
