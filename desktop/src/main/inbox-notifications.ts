export interface InboxNotification {
  title: string
  body: string
  /** Renderer path the notification opens: the item in the Inbox. */
  path: string
  /** One key per item episode, so a replayed or repeated push shows once. */
  key: string
  itemId: number
  target: string
  /** True for "Can't reach <target>" and "Monitoring resumed" pushes. */
  connection: boolean
  /** Stays on screen until dismissed: a lost connection or an Act now item. */
  critical: boolean
}

export interface InboxEvent {
  id: number
  item_id: number
  ts: number
  kind: string
  title: string
  target: string
  type?: string
  subject?: string
  state?: string
  severity?: number
  body_json: string
  /** The Inbox's one-line summary, sent with notified rows. */
  summary?: string | null
}

// The Inbox's Act now band: these types, or a severity of 0.85 and up.
const ACT_NOW_TYPES = new Set(['stuck_query', 'monitoring_issue'])
const ACT_NOW_SEVERITY = 0.85

/** An open item in the Inbox's Act now band. */
export function isActNow(
  event: Pick<InboxEvent, 'type' | 'severity' | 'state'>
): boolean {
  if (event.state === 'resolved' || event.state === 'dismissed') return false
  return (
    ACT_NOW_TYPES.has(event.type ?? '') ||
    (typeof event.severity === 'number' && event.severity >= ACT_NOW_SEVERITY)
  )
}

export interface SystemNotificationOptions {
  title: string
  body: string
  /** Windows and Linux: stay until dismissed. */
  timeoutType?: 'default' | 'never'
  /** Linux: critical urgency stays until dismissed. */
  urgency?: 'normal' | 'critical' | 'low'
  /** macOS: buttons on an alert-style notification. */
  actions?: { type: 'button'; text: string }[]
  closeButtonText?: string
}

/**
 * Electron options for a push. Critical ones stay on screen until dismissed (Windows
 * `timeoutType: 'never'`, Linux critical urgency, a macOS alert with Open and Dismiss);
 * ordinary ones keep the OS default timeout.
 */
export function systemNotificationOptions(
  notification: Pick<InboxNotification, 'title' | 'body' | 'critical'>,
  platform: NodeJS.Platform
): SystemNotificationOptions {
  const base = { title: notification.title, body: notification.body }
  if (!notification.critical) return base
  if (platform === 'darwin')
    return {
      ...base,
      actions: [{ type: 'button', text: 'Open' }],
      closeButtonText: 'Dismiss',
    }
  if (platform === 'linux')
    return { ...base, timeoutType: 'never', urgency: 'critical' }
  return { ...base, timeoutType: 'never' }
}

function parseBody(event: InboxEvent): Record<string, unknown> {
  try {
    const body: unknown = JSON.parse(event.body_json)
    if (typeof body === 'string') return { message: body }
    if (body && typeof body === 'object') return body as Record<string, unknown>
  } catch {
    // The title and target remain useful if an older event has malformed JSON.
  }
  return {}
}

function bodyText(event: InboxEvent, values: Record<string, unknown>): string {
  if (event.summary) return `${event.target}: ${event.summary}`
  for (const key of ['message', 'summary', 'description', 'detail', 'error']) {
    if (typeof values[key] === 'string' && values[key])
      return values[key] as string
  }
  const numbers = values.numbers
  if (numbers && typeof numbers === 'object') {
    const metrics = numbers as Record<string, unknown>
    if (
      typeof metrics.mean_ms === 'number' &&
      Number.isFinite(metrics.mean_ms)
    ) {
      return `${event.target}: ${Math.round(metrics.mean_ms)} ms mean latency.`
    }
  }
  return `Database: ${event.target}`
}

/**
 * The system notification for one inbox stream row, or null when the row isn't a push.
 * The backend decides what pushes (per-event settings, the daily cap, which never applies
 * to a lost connection); this only words it and names its episode.
 */
export function notificationFromEvent(raw: unknown): InboxNotification | null {
  if (!raw || typeof raw !== 'object') return null
  const event = raw as Partial<InboxEvent>
  if (
    event.kind !== 'notified' ||
    typeof event.title !== 'string' ||
    typeof event.target !== 'string' ||
    typeof event.item_id !== 'number'
  )
    return null
  const full = event as InboxEvent
  const values = parseBody(full)
  const path = `/inbox?item=${full.item_id}`
  const base = { path, itemId: full.item_id, target: full.target }
  if (full.type === 'monitoring_issue' && full.subject === 'lost_connection') {
    if (
      full.state === 'dismissed' ||
      (typeof values.failed_since === 'number' && full.ts < values.failed_since)
    )
      return null
    const resolved =
      full.state === 'resolved' || typeof values.resumed_at === 'number'
    const episode =
      typeof values.failed_since === 'number' ? values.failed_since : full.id
    const message =
      typeof values.message === 'string' && values.message
        ? values.message
        : null
    return {
      ...base,
      connection: true,
      critical: !resolved,
      key: `${full.item_id}:${resolved ? 'resumed' : 'lost'}:${episode}`,
      title: resolved
        ? `Monitoring resumed on ${full.target}`
        : `Can't reach ${full.target}`,
      body:
        message ??
        (resolved
          ? `RDST can reach ${full.target} again.`
          : `RDST can't reach ${full.target}. Monitoring is paused until it's back.`),
    }
  }
  return {
    ...base,
    connection: false,
    critical: isActNow(full),
    key: `${full.item_id}:${full.id}`,
    title: full.title,
    body: bodyText(full, values),
  }
}

/** Remembers which episodes already notified, bounded so a long session stays small. */
export class NotificationDeduper {
  private readonly seen = new Set<string>()

  constructor(private readonly limit = 500) {}

  /** True the first time a key is offered. */
  first(key: string): boolean {
    if (this.seen.has(key)) return false
    this.seen.add(key)
    if (this.seen.size > this.limit) {
      const oldest = this.seen.values().next().value
      if (oldest !== undefined) this.seen.delete(oldest)
    }
    return true
  }
}

export const MAX_FRAME_BYTES = 256 * 1024
const HEALTHY_CONNECTION_MS = 60_000

function waitForRetry(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve()
    const timer = setTimeout(done, ms)
    function done() {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    signal.addEventListener('abort', done, { once: true })
  })
}

export function startInboxNotifications(options: {
  apiBaseUrl: string
  enabled: () => boolean
  notify: (notification: InboxNotification) => void
  fetcher?: typeof fetch
  startedAt?: number
  onEvent?: (id: number) => void
  /** Any other inbox change (filed, updated, resolved): the tray re-reads its state. */
  onChange?: () => void
}): () => void {
  const controller = new AbortController()
  const fetcher = options.fetcher ?? fetch
  const startedAt = options.startedAt ?? Math.floor(Date.now() / 1000)
  let lastId = 0
  const deduper = new NotificationDeduper()

  async function run(): Promise<void> {
    let retryMs = 1000
    while (!controller.signal.aborted) {
      try {
        const response = await fetcher(
          `${options.apiBaseUrl}/api/monitor/inbox/stream?since=${startedAt}`,
          {
            headers: {
              Accept: 'text/event-stream',
              ...(lastId ? { 'Last-Event-ID': String(lastId) } : {}),
            },
            signal: controller.signal,
          }
        )
        if (!response.ok || !response.body)
          throw new Error(`Inbox stream returned HTTP ${response.status}`)
        const reader = response.body.getReader()
        const connectedAt = Date.now()
        const cancelReader = () => {
          void reader.cancel().catch(() => {})
        }
        controller.signal.addEventListener('abort', cancelReader, {
          once: true,
        })
        const decoder = new TextDecoder()
        let buffer = ''
        try {
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break
            if (Date.now() - connectedAt >= HEALTHY_CONNECTION_MS)
              retryMs = 1000
            for (
              let offset = 0;
              offset < value.byteLength;
              offset += 64 * 1024
            ) {
              buffer = (
                buffer +
                decoder.decode(value.subarray(offset, offset + 64 * 1024), {
                  stream: true,
                })
              ).replace(/\r\n/g, '\n')
              let boundary = buffer.indexOf('\n\n')
              while (boundary >= 0) {
                const frame = buffer.slice(0, boundary)
                if (Buffer.byteLength(frame) > MAX_FRAME_BYTES)
                  throw new Error('Inbox stream frame is too large')
                buffer = buffer.slice(boundary + 2)
                const lines = frame.split('\n')
                const eventType = lines
                  .find((line) => line.startsWith('event:'))
                  ?.slice(6)
                  .trim()
                const data = lines
                  .filter((line) => line.startsWith('data:'))
                  .map((line) => line.slice(5).trimStart())
                  .join('\n')
                if (eventType === 'notified' && data) {
                  try {
                    const event = JSON.parse(data) as InboxEvent
                    if (typeof event.id === 'number' && event.id > lastId) {
                      lastId = event.id
                      const notification = notificationFromEvent(event)
                      if (
                        notification &&
                        event.ts >= startedAt &&
                        deduper.first(notification.key) &&
                        options.enabled()
                      )
                        options.notify(notification)
                      options.onChange?.()
                    }
                  } catch (error) {
                    console.warn('[rdst-desktop] invalid inbox event:', error)
                  }
                } else {
                  const id = Number(
                    lines
                      .find((line) => line.startsWith('id:'))
                      ?.slice(3)
                      .trim()
                  )
                  if (Number.isFinite(id) && id > lastId) lastId = id
                  if (eventType === 'upsert') options.onChange?.()
                }
                options.onEvent?.(lastId)
                boundary = buffer.indexOf('\n\n')
              }
              if (Buffer.byteLength(buffer) > MAX_FRAME_BYTES)
                throw new Error('Inbox stream frame is too large')
            }
          }
        } finally {
          controller.signal.removeEventListener('abort', cancelReader)
          await reader.cancel().catch(() => {})
        }
      } catch (error) {
        if (!controller.signal.aborted)
          console.warn('[rdst-desktop] inbox stream disconnected:', error)
      }
      if (!controller.signal.aborted) {
        await waitForRetry(retryMs, controller.signal)
        retryMs = Math.min(retryMs * 2, 30_000)
      }
    }
  }

  void run()
  return () => controller.abort()
}
