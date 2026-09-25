import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { monitorKeys } from '@/lib/monitor/keys'
import {
  INCIDENT_REFRESH_MS,
  inboxRefresher,
  incidentInboxKey,
} from './useInboxUnread'

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  })
}

afterEach(() => {
  vi.useRealTimers()
  setVisibility('visible')
})

describe('inboxRefresher', () => {
  it('refetches once for a burst of stream events', () => {
    vi.useFakeTimers()
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const refresher = inboxRefresher(client, 400)
    for (let i = 0; i < 12; i++) refresher.schedule()
    vi.advanceTimersByTime(400)
    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: monitorKeys.inboxAll,
        refetchType: 'active',
      })
    )
  })

  it('refreshes incident lists at most once a minute', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const client = new QueryClient()
    const plain = monitorKeys.inbox({ state: 'open' })
    const measured = incidentInboxKey({ state: 'open', incidents: true })
    const listed = { items: [{ id: 1, state: 'new' }] }
    client.setQueryData(plain, listed)
    client.setQueryData(measured, listed)
    const refresher = inboxRefresher(client, 400)
    refresher.schedule({ item_id: 1, state: 'new' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(plain)?.isInvalidated).toBe(true)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(false)
    vi.advanceTimersByTime(INCIDENT_REFRESH_MS)
    refresher.schedule({ item_id: 1, state: 'new' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(true)
  })

  it('refreshes an incident list right away for a new alert or a state change', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const client = new QueryClient()
    const measured = incidentInboxKey({ state: 'open', incidents: true })
    client.setQueryData(measured, { items: [{ id: 1, state: 'new' }] })
    const refresher = inboxRefresher(client, 400)
    refresher.schedule({ item_id: 1, state: 'new' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(false)
    refresher.schedule({ item_id: 2, state: 'new' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(true)
    client.setQueryData(measured, { items: [{ id: 1, state: 'new' }] })
    refresher.schedule({ item_id: 1, state: 'snoozed' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(true)
  })

  it('refreshes an incident list right away when the stream (re)connects', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const client = new QueryClient()
    const measured = incidentInboxKey({ state: 'open', incidents: true })
    client.setQueryData(measured, { items: [{ id: 1, state: 'new' }] })
    const refresher = inboxRefresher(client, 400)
    // An alert filed between the list fetch and the stream opening arrives only as
    // the connect event, which names no item.
    refresher.schedule({})
    refresher.schedule({ item_id: 1, state: 'new' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(true)
    client.setQueryData(measured, { items: [{ id: 1, state: 'new' }] })
    refresher.schedule({ item_id: 1, state: 'new' })
    vi.advanceTimersByTime(400)
    expect(client.getQueryState(measured)?.isInvalidated).toBe(false)
  })

  it('only marks inbox queries stale in a hidden tab', () => {
    vi.useFakeTimers()
    setVisibility('hidden')
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const refresher = inboxRefresher(client, 400)
    refresher.schedule()
    vi.advanceTimersByTime(400)
    expect(invalidate).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: monitorKeys.inboxAll,
        refetchType: 'none',
      })
    )
  })
})
