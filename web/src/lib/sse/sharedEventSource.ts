// One set of server-sent-event connections for every open tab of the app.
//
// Browsers allow six HTTP/1.1 connections per origin. Each open stream holds one for
// good, so a few tabs with a pulse, inbox and discovery stream each starve every other
// request. Here one tab (the leader, elected with a Web Lock) opens each distinct
// stream once and relays its events to the other tabs over a BroadcastChannel. When
// the leader closes, the next tab in the lock queue takes over and reopens the streams
// its peers still want. A tab hidden for a while stops asking for its streams, except
// background ones (the inbox, which feeds the badge and desktop alerts), so a stack of
// idle tabs never holds connections the visible one needs. Without Web Locks or
// BroadcastChannel each tab opens its own streams and closes all of them while hidden.
//
// Usage:
//   const stop = subscribeShared('/api/monitor/targets/db/stream', ['pulse'],
//     (name, data) => onPulse(JSON.parse(data)))
//   stop()

export type SharedHandler = (
  name: string,
  data: string,
  lastEventId: string
) => void

export interface SubscribeOptions {
  /** Keep the stream while the tab is hidden (shared mode only). */
  background?: boolean
}

interface Listener {
  events: Set<string>
  handler: SharedHandler
  background: boolean
}

type Message =
  | { t: 'subs'; from: string; urls: [string, string[]][] }
  | { t: 'bye'; from: string }
  | { t: 'who' }
  | { t: 'event'; url: string; name: string; data: string; id: string }

export interface ChannelLike {
  postMessage: (message: Message) => void
  onmessage: ((event: { data: Message }) => void) | null
  close: () => void
}

export interface EventSourceLike {
  addEventListener: (
    name: string,
    listener: (event: MessageEvent<string>) => void
  ) => void
  close: () => void
}

export interface HubOptions {
  /** A BroadcastChannel (or an in-memory stand-in); null disables sharing. */
  channel: ChannelLike | null
  /**
   * Calls `onLeader` once this tab holds the leader lock; the lock is held until the
   * returned promise's tab goes away. Null means every tab leads itself.
   */
  requestLeadership: ((onLeader: () => void) => void) | null
  createSource: (url: string) => EventSourceLike
  /** Heartbeat for subscription announcements (ms). */
  heartbeatMs?: number
  /**
   * Peers silent for this long are forgotten (ms). Longer than the one-minute timer
   * throttling browsers apply to background tabs, so a hidden peer is not dropped and
   * re-added (closing and reopening its streams) every minute.
   */
  peerTimeoutMs?: number
  /** Let go of foreground streams after the tab has been hidden this long. */
  hiddenCloseMs?: number
  now?: () => number
}

interface OpenSource {
  source: EventSourceLike
  events: Set<string>
}

export class SharedEventHub {
  readonly tabId = Math.random().toString(36).slice(2)
  private readonly local = new Map<string, Set<Listener>>()
  private readonly peers = new Map<
    string,
    { seen: number; urls: Map<string, Set<string>> }
  >()
  private readonly sources = new Map<string, OpenSource>()
  private leader = false
  private hidden = false
  /** Hidden past the grace period: only background listeners count. */
  private dormant = false
  private hiddenTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeat: ReturnType<typeof setInterval> | null = null
  private readonly options: Required<
    Omit<HubOptions, 'channel' | 'requestLeadership'>
  > &
    Pick<HubOptions, 'channel' | 'requestLeadership'>

  constructor(options: HubOptions) {
    this.options = {
      heartbeatMs: 10_000,
      peerTimeoutMs: 150_000,
      hiddenCloseMs: 30_000,
      now: () => Date.now(),
      ...options,
    }
    const { channel, requestLeadership } = this.options
    if (channel && requestLeadership) {
      channel.onmessage = (event) => this.receive(event.data)
      requestLeadership(() => this.becomeLeader())
      this.heartbeat = setInterval(() => {
        this.announce()
        if (this.leader) this.reconcile()
      }, this.options.heartbeatMs)
    } else {
      this.leader = true
    }
  }

  get isLeader() {
    return this.leader
  }

  /** Streams this tab has open (for tests and diagnostics). */
  get openUrls(): string[] {
    return [...this.sources.keys()]
  }

  get shared() {
    return Boolean(this.options.channel && this.options.requestLeadership)
  }

  subscribe(
    url: string,
    events: string[],
    handler: SharedHandler,
    options: SubscribeOptions = {}
  ): () => void {
    const listener: Listener = {
      events: new Set(events),
      handler,
      background: Boolean(options.background),
    }
    const set = this.local.get(url) ?? new Set()
    set.add(listener)
    this.local.set(url, set)
    this.changed()
    return () => {
      set.delete(listener)
      if (!set.size) this.local.delete(url)
      this.changed()
    }
  }

  /** Hidden tabs let go of their foreground streams after a grace period. */
  setHidden(hidden: boolean) {
    if (hidden === this.hidden) return
    this.hidden = hidden
    if (this.hiddenTimer) clearTimeout(this.hiddenTimer)
    this.hiddenTimer = null
    if (hidden)
      this.hiddenTimer = setTimeout(() => {
        this.dormant = true
        this.changed()
      }, this.options.hiddenCloseMs)
    else if (this.dormant) {
      this.dormant = false
      this.changed()
    }
  }

  /** Leaves: closes streams and tells peers (pagehide). */
  dispose() {
    if (this.heartbeat) clearInterval(this.heartbeat)
    if (this.hiddenTimer) clearTimeout(this.hiddenTimer)
    for (const open of this.sources.values()) open.source.close()
    this.sources.clear()
    this.options.channel?.postMessage({ t: 'bye', from: this.tabId })
    this.options.channel?.close()
  }

  private changed() {
    this.announce()
    if (this.leader) this.reconcile()
  }

  private localUrls(): Map<string, Set<string>> {
    const out = new Map<string, Set<string>>()
    for (const [url, listeners] of this.local) {
      const events = new Set<string>()
      for (const listener of listeners) {
        if (this.dormant && !(this.shared && listener.background)) continue
        for (const name of listener.events) events.add(name)
      }
      if (events.size) out.set(url, events)
    }
    return out
  }

  private announce() {
    const { channel } = this.options
    if (!channel || this.leader) return
    channel.postMessage({
      t: 'subs',
      from: this.tabId,
      urls: [...this.localUrls()].map(([url, events]) => [url, [...events]]),
    })
  }

  private becomeLeader() {
    this.leader = true
    this.options.channel?.postMessage({ t: 'who' })
    this.reconcile()
  }

  private receive(message: Message) {
    if (message.t === 'event') {
      if (!this.leader)
        this.dispatch(message.url, message.name, message.data, message.id)
      return
    }
    if (message.t === 'who') {
      this.announce()
      return
    }
    if (!this.leader) return
    if (message.t === 'bye') this.peers.delete(message.from)
    else
      this.peers.set(message.from, {
        seen: this.options.now(),
        urls: new Map(
          message.urls.map(([url, events]) => [url, new Set(events)])
        ),
      })
    this.reconcile()
  }

  private wanted(): Map<string, Set<string>> {
    const union = this.localUrls()
    const cutoff = this.options.now() - this.options.peerTimeoutMs
    for (const [id, peer] of this.peers) {
      if (peer.seen < cutoff) {
        this.peers.delete(id)
        continue
      }
      for (const [url, events] of peer.urls) {
        const set = union.get(url) ?? new Set()
        for (const name of events) set.add(name)
        union.set(url, set)
      }
    }
    return union
  }

  private reconcile() {
    if (!this.leader) return
    const wanted = this.wanted()
    for (const [url, open] of this.sources)
      if (!wanted.has(url)) {
        open.source.close()
        this.sources.delete(url)
      }
    for (const [url, events] of wanted) {
      let open = this.sources.get(url)
      if (!open) {
        open = { source: this.options.createSource(url), events: new Set() }
        this.sources.set(url, open)
      }
      for (const name of events) {
        if (open.events.has(name)) continue
        open.events.add(name)
        open.source.addEventListener(name, (event) => {
          const data = typeof event.data === 'string' ? event.data : ''
          const id = event.lastEventId ?? ''
          this.dispatch(url, name, data, id)
          this.options.channel?.postMessage({ t: 'event', url, name, data, id })
        })
      }
    }
  }

  private dispatch(url: string, name: string, data: string, id: string) {
    for (const listener of this.local.get(url) ?? [])
      if (listener.events.has(name)) listener.handler(name, data, id)
  }
}

let hub: SharedEventHub | null = null

function browserHub(): SharedEventHub {
  const canShare =
    typeof BroadcastChannel !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    'locks' in navigator &&
    Boolean((navigator as Navigator & { locks?: LockManager }).locks)
  const created = new SharedEventHub({
    channel: canShare
      ? (new BroadcastChannel('rdst-sse') as unknown as ChannelLike)
      : null,
    requestLeadership: canShare
      ? (onLeader) => {
          void navigator.locks.request(
            'rdst-sse-leader',
            () =>
              new Promise<void>(() => {
                onLeader()
              })
          )
        }
      : null,
    createSource: (url) => new EventSource(url) as unknown as EventSourceLike,
  })
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () =>
      created.setHidden(document.visibilityState === 'hidden')
    )
    window.addEventListener('pagehide', () => created.dispose())
  }
  return created
}

/**
 * Subscribes to named events of the SSE stream at `url`, sharing one connection per
 * URL across every tab. Returns an unsubscribe function.
 */
export function subscribeShared(
  url: string,
  events: string[],
  handler: SharedHandler,
  options?: SubscribeOptions
): () => void {
  if (typeof EventSource === 'undefined') return () => {}
  hub ??= browserHub()
  return hub.subscribe(url, events, handler, options)
}

/** Test hook: forget the browser hub. */
export function resetSharedHub() {
  hub?.dispose()
  hub = null
}
