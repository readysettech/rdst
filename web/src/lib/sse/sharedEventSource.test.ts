import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  type ChannelLike,
  type EventSourceLike,
  SharedEventHub,
} from './sharedEventSource'

/** An in-memory BroadcastChannel: delivers to every other member, never the sender. */
function bus() {
  const members = new Set<ChannelLike>()
  return () => {
    const channel: ChannelLike = {
      onmessage: null,
      postMessage: (message) => {
        for (const other of members)
          if (other !== channel)
            other.onmessage?.({ data: structuredClone(message) })
      },
      close: () => members.delete(channel),
    }
    members.add(channel)
    return channel
  }
}

/** A Web Lock: the first requester leads; the next one leads when it is released. */
function lock() {
  const queue: Array<() => void> = []
  let held = false
  return {
    request: (onLeader: () => void) => {
      if (!held) {
        held = true
        onLeader()
      } else queue.push(onLeader)
    },
    release: () => {
      const next = queue.shift()
      if (next) next()
      else held = false
    },
  }
}

class FakeSource implements EventSourceLike {
  static opened: FakeSource[] = []
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>()
  closed = false
  constructor(readonly url: string) {
    FakeSource.opened.push(this)
  }
  addEventListener(
    name: string,
    listener: (event: MessageEvent<string>) => void
  ) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener])
  }
  close() {
    this.closed = true
  }
  emit(name: string, data: string) {
    for (const listener of this.listeners.get(name) ?? [])
      listener({ data, lastEventId: '1' } as MessageEvent<string>)
  }
}

const open = () => FakeSource.opened.filter((source) => !source.closed)

afterEach(() => {
  FakeSource.opened = []
  vi.useRealTimers()
})

describe('SharedEventHub', () => {
  it('opens each stream once for every tab and relays its events', () => {
    const channel = bus()
    const leader = lock()
    const hubs = [0, 1, 2].map(
      () =>
        new SharedEventHub({
          channel: channel(),
          requestLeadership: leader.request,
          createSource: (url) => new FakeSource(url),
        })
    )
    const received: string[][] = hubs.map(() => [])
    hubs.forEach((hub, index) => {
      hub.subscribe('/api/monitor/targets/db/stream', ['pulse'], (_, data) =>
        received[index].push(data)
      )
      hub.subscribe('/api/monitor/inbox/stream', ['upsert'], () => undefined)
    })

    expect(hubs.map((hub) => hub.isLeader)).toEqual([true, false, false])
    expect(
      open()
        .map((source) => source.url)
        .sort()
    ).toEqual(['/api/monitor/inbox/stream', '/api/monitor/targets/db/stream'])

    open()
      .find((source) => source.url.endsWith('/db/stream'))
      ?.emit('pulse', '{"ts":1}')
    expect(received).toEqual([['{"ts":1}'], ['{"ts":1}'], ['{"ts":1}']])
  })

  it('keeps a stream while any tab wants it and hands over when the leader leaves', () => {
    const channel = bus()
    const leader = lock()
    const first = new SharedEventHub({
      channel: channel(),
      requestLeadership: leader.request,
      createSource: (url) => new FakeSource(url),
    })
    const second = new SharedEventHub({
      channel: channel(),
      requestLeadership: leader.request,
      createSource: (url) => new FakeSource(url),
    })
    const stopFirst = first.subscribe('/a', ['x'], () => undefined)
    const seen: string[] = []
    second.subscribe('/a', ['x'], (_, data) => seen.push(data))
    second.subscribe('/b', ['y'], () => undefined)
    expect(
      open()
        .map((source) => source.url)
        .sort()
    ).toEqual(['/a', '/b'])

    stopFirst()
    expect(
      open()
        .map((source) => source.url)
        .sort()
    ).toEqual(['/a', '/b'])

    first.dispose()
    leader.release()
    expect(second.isLeader).toBe(true)
    const reopened = open().filter((source) => source.url === '/a')
    expect(reopened).toHaveLength(1)
    reopened[0].emit('x', 'hello')
    expect(seen).toEqual(['hello'])
  })

  it('closes streams on a hidden tab when it cannot share, and reopens on return', () => {
    vi.useFakeTimers()
    const hub = new SharedEventHub({
      channel: null,
      requestLeadership: null,
      createSource: (url) => new FakeSource(url),
      hiddenCloseMs: 1000,
    })
    hub.subscribe('/a', ['x'], () => undefined)
    expect(open()).toHaveLength(1)
    hub.setHidden(true)
    vi.advanceTimersByTime(999)
    expect(open()).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(open()).toHaveLength(0)
    hub.setHidden(false)
    expect(open()).toHaveLength(1)
  })
})

describe('SharedEventHub on hidden tabs', () => {
  const shared = (
    channel: () => ChannelLike,
    leader: ReturnType<typeof lock>
  ) =>
    new SharedEventHub({
      channel: channel(),
      requestLeadership: leader.request,
      createSource: (url) => new FakeSource(url),
      hiddenCloseMs: 1000,
    })
  const urls = () =>
    open()
      .map((source) => source.url)
      .sort()

  it('lets go of foreground streams on a hidden tab and keeps background ones', () => {
    vi.useFakeTimers()
    const hub = shared(bus(), lock())
    hub.subscribe('/pulse', ['pulse'], () => undefined)
    hub.subscribe('/inbox', ['upsert'], () => undefined, { background: true })
    expect(urls()).toEqual(['/inbox', '/pulse'])

    hub.setHidden(true)
    vi.advanceTimersByTime(999)
    expect(urls()).toEqual(['/inbox', '/pulse'])
    vi.advanceTimersByTime(1)
    expect(urls()).toEqual(['/inbox'])

    hub.setHidden(false)
    expect(urls()).toEqual(['/inbox', '/pulse'])
  })

  it('keeps no more than one connection per stream across hidden and visible tabs', () => {
    vi.useFakeTimers()
    const channel = bus()
    const leader = lock()
    const tabs = [0, 1, 2].map(() => shared(channel, leader))
    tabs.forEach((tab, index) => {
      tab.subscribe(`/pulse/${index % 2}`, ['pulse'], () => undefined)
      tab.subscribe('/discovery', ['update'], () => undefined)
      tab.subscribe('/inbox', ['upsert'], () => undefined, { background: true })
    })
    expect(urls()).toEqual(['/discovery', '/inbox', '/pulse/0', '/pulse/1'])

    // The leader and one peer go to the background; the visible peer keeps its streams.
    tabs[0].setHidden(true)
    tabs[2].setHidden(true)
    vi.advanceTimersByTime(1000)
    expect(urls()).toEqual(['/discovery', '/inbox', '/pulse/1'])
  })
})
