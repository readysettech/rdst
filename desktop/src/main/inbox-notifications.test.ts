import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  isActNow,
  NotificationDeduper,
  MAX_FRAME_BYTES,
  notificationFromEvent,
  startInboxNotifications,
  systemNotificationOptions,
} from './inbox-notifications.js'

function frame(id: number, ts: number, kind = 'notified'): string {
  return `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify({
    id, item_id: 42, ts, kind, title: 'Monitoring connection lost',
    target: 'prod-orders', body_json: JSON.stringify({ message: 'Reconnect to resume monitoring.' }),
  })}\n\n`
}

async function flush(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

afterEach(() => vi.useRealTimers())

describe('inbox notifications', () => {
  it('keeps exponential reconnect backoff after short streams receive keepalives', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockImplementation(async () => new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(': keepalive\n\n'))
        controller.close()
      },
    })))
    const stop = startInboxNotifications({ apiBaseUrl: 'http://localhost', enabled: () => true,
      notify: vi.fn(), fetcher })
    await vi.advanceTimersByTimeAsync(0)
    let calls = 1
    for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
      await vi.advanceTimersByTimeAsync(delay - 1)
      expect(fetcher).toHaveBeenCalledTimes(calls)
      await vi.advanceTimersByTimeAsync(1)
      calls += 1
      expect(fetcher).toHaveBeenCalledTimes(calls)
    }
    stop()
  })

  it('closes a stream with an oversized partial or complete frame', async () => {
    vi.useFakeTimers()
    for (const suffix of ['', '\n\n']) {
      const cancel = vi.fn()
      const notify = vi.fn()
      const fetcher = vi.fn().mockImplementation(async () => new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('x'.repeat(MAX_FRAME_BYTES + 1) + suffix))
        },
        cancel,
      })))
      const stop = startInboxNotifications({ apiBaseUrl: 'http://localhost', enabled: () => true,
        notify, fetcher })
      await vi.advanceTimersByTimeAsync(0)
      expect(cancel).toHaveBeenCalledOnce()
      expect(notify).not.toHaveBeenCalled()
      expect(fetcher).toHaveBeenCalledOnce()
      stop()
    }
  })
  it('maps notified event title and body and ignores other kinds', () => {
    const event = JSON.parse(frame(1, 100).split('data: ')[1]!)
    expect(notificationFromEvent(event)).toMatchObject({
      title: 'Monitoring connection lost', body: 'Reconnect to resume monitoring.',
      path: '/inbox?item=42', connection: false, key: '42:1',
    })
    expect(notificationFromEvent({ ...event, kind: 'upsert' })).toBeNull()
  })

  it('words a lost connection and its recovery, one key per outage', () => {
    const lost = {
      id: 7, item_id: 9, ts: 100, kind: 'notified', title: "Can't reach the database",
      target: 'prod-pg', type: 'monitoring_issue', subject: 'lost_connection', state: 'new',
      body_json: JSON.stringify({ target: 'prod-pg', failed_since: 90 }),
    }
    expect(notificationFromEvent(lost)).toEqual({
      title: "Can't reach prod-pg",
      body: "RDST can't reach prod-pg. Monitoring is paused until it's back.",
      path: '/inbox?item=9', key: '9:lost:90', itemId: 9, target: 'prod-pg', connection: true,
      critical: true,
    })
    const again = notificationFromEvent({ ...lost, id: 8 })
    expect(again?.key).toBe('9:lost:90')
    const message = 'Monitoring resumed at 11:15 AM. No readings from 9:55 to 11:15 AM.'
    expect(notificationFromEvent({
      ...lost, id: 11, state: 'resolved',
      body_json: JSON.stringify({ failed_since: 90, resumed_at: 200, message }),
    })).toMatchObject({
      title: 'Monitoring resumed on prod-pg', body: message, key: '9:resumed:90', critical: false,
    })
    expect(notificationFromEvent({
      ...lost, id: 12, ts: 360, body_json: JSON.stringify({ failed_since: 300 }),
    })?.key).toBe('9:lost:300')
  })

  it('uses recovered wording and skips dismissed or earlier outage episodes', () => {
    const event = {
      id: 7,
      item_id: 9,
      ts: 100,
      kind: 'notified',
      title: "Can't reach the database",
      target: 'prod-pg',
      type: 'monitoring_issue',
      subject: 'lost_connection',
      state: 'new',
      body_json: JSON.stringify({
        failed_since: 90,
        resumed_at: 110,
        message: 'Monitoring resumed.',
      }),
    }
    expect(notificationFromEvent(event)).toMatchObject({
      title: 'Monitoring resumed on prod-pg',
      body: 'Monitoring resumed.',
      critical: false,
      key: '9:resumed:90',
    })
    expect(notificationFromEvent({ ...event, state: 'dismissed' })).toBeNull()
    expect(
      notificationFromEvent({
        ...event,
        body_json: JSON.stringify({ failed_since: 300 }),
      })
    ).toBeNull()
  })

  it('marks lost connections and Act now items critical', () => {
    const base = { id: 1, item_id: 3, ts: 1, kind: 'notified', title: 'Slower', target: 'db', body_json: '{}' }
    expect(notificationFromEvent({ ...base, type: 'regression', severity: 0.7, state: 'new' })?.critical)
      .toBe(false)
    expect(notificationFromEvent({ ...base, type: 'regression', severity: 0.9, state: 'new' })?.critical)
      .toBe(true)
    expect(notificationFromEvent({ ...base, type: 'stuck_query', severity: 0.5, state: 'new' })?.critical)
      .toBe(true)
    expect(isActNow({ type: 'stuck_query', severity: 1, state: 'resolved' })).toBe(false)
  })

  it('keeps critical notifications on screen until dismissed, per OS', () => {
    const critical = { title: "Can't reach db", body: 'Monitoring is paused.', critical: true }
    expect(systemNotificationOptions(critical, 'win32')).toEqual({
      title: "Can't reach db", body: 'Monitoring is paused.', timeoutType: 'never',
    })
    expect(systemNotificationOptions(critical, 'linux')).toMatchObject({
      timeoutType: 'never', urgency: 'critical',
    })
    expect(systemNotificationOptions(critical, 'darwin')).toMatchObject({
      actions: [{ type: 'button', text: 'Open' }], closeButtonText: 'Dismiss',
    })
    // Ordinary pushes keep the OS default timeout.
    expect(systemNotificationOptions({ ...critical, critical: false }, 'win32')).toEqual({
      title: "Can't reach db", body: 'Monitoring is paused.',
    })
  })

  it('dedupes a repeated push for the same outage', () => {
    const deduper = new NotificationDeduper(2)
    expect(deduper.first('9:lost:90')).toBe(true)
    expect(deduper.first('9:lost:90')).toBe(false)
    expect(deduper.first('9:resumed:90')).toBe(true)
    expect(deduper.first('9:lost:300')).toBe(true)
    // The oldest key falls out once the bound is reached.
    expect(deduper.first('9:lost:90')).toBe(true)
  })

  it('shows one notification when a lost connection is pushed twice', async () => {
    const notify = vi.fn()
    const onChange = vi.fn()
    const row = (id: number) => `id: ${id}\nevent: notified\ndata: ${JSON.stringify({
      id, item_id: 9, ts: 100, kind: 'notified', title: "Can't reach the database",
      target: 'prod-pg', type: 'monitoring_issue', subject: 'lost_connection', state: 'new',
      body_json: JSON.stringify({ failed_since: 90 }),
    })}\n\n`
    const fetcher = vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          row(1) + row(2) + 'id: 3\nevent: upsert\ndata: {}\n\n'))
        controller.close()
      },
    }), { status: 200 }))
    const stop = startInboxNotifications({
      apiBaseUrl: 'http://127.0.0.1:1234', enabled: () => true,
      notify, fetcher, startedAt: 100, onChange,
    })
    await flush()
    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify.mock.calls[0]?.[0]).toMatchObject({ title: "Can't reach prod-pg" })
    expect(onChange).toHaveBeenCalledTimes(3)
    stop()
  })

  it('reconnects after a closed stream without repeating old events', async () => {
    vi.useFakeTimers()
    const notify = vi.fn()
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frame(1, 99) + frame(2, 100) + frame(3, 100, 'upsert')))
          controller.close()
        },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frame(2, 100) + frame(4, 101)))
          controller.close()
        },
      }), { status: 200 }))
    const stop = startInboxNotifications({
      apiBaseUrl: 'http://127.0.0.1:1234', enabled: () => true,
      notify, fetcher, startedAt: 100,
    })
    await flush()
    expect(notify).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0]?.[0]).toBe('http://127.0.0.1:1234/api/monitor/inbox/stream?since=100')
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(notify).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls[1]?.[1]?.headers['Last-Event-ID']).toBe('3')
    stop()
  })

  it('respects the persisted preference while still advancing the event cursor', async () => {
    const notify = vi.fn()
    const fetcher = vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frame(1, 100)))
        controller.close()
      },
    }), { status: 200 }))
    const stop = startInboxNotifications({
      apiBaseUrl: 'http://127.0.0.1:1234', enabled: () => false,
      notify, fetcher, startedAt: 100,
    })
    await flush()
    expect(notify).not.toHaveBeenCalled()
    stop()
  })
})
