import { afterEach, describe, expect, it, vi } from 'vitest'

import { allPaused, listTrayDatabases } from './monitor-controls.js'
import {
  type TrayInboxItem,
  trayStatus,
  watchTrayStatus,
} from './tray-state.js'

const lost: TrayInboxItem = {
  target: 'prod-pg',
  type: 'monitoring_issue',
  subject: 'lost_connection',
  state: 'new',
  severity_band: 'act_now',
  muted_by: null,
}
const stuck: TrayInboxItem = {
  target: 'demo',
  type: 'stuck_query',
  subject: 'abc',
  state: 'seen',
  severity_band: 'act_now',
  muted_by: null,
}
const watch: TrayInboxItem = {
  target: 'demo',
  type: 'latency_regression',
  subject: 'q',
  state: 'new',
  severity_band: 'watch',
  muted_by: null,
}

afterEach(() => vi.useRealTimers())

describe('tray status', () => {
  it('is running with nothing that needs the person', () => {
    expect(
      trayStatus({ backendUp: true, paused: false, items: [watch] })
    ).toEqual({
      state: 'running',
      tooltip: 'RDST is running',
      unreachable: [],
      actNow: 0,
    })
  })

  it('names an unreachable database before counting Act now items', () => {
    expect(
      trayStatus({ backendUp: true, paused: false, items: [stuck, lost] })
    ).toMatchObject({
      state: 'problem',
      tooltip: "RDST - can't reach prod-pg",
      unreachable: ['prod-pg'],
      actNow: 2,
    })
    const second = { ...lost, target: 'orders', subject: 'password_missing' }
    expect(
      trayStatus({ backendUp: true, paused: false, items: [lost, second] })
        .tooltip
    ).toBe("RDST - can't reach 2 databases")
  })

  it('counts open, unmuted Act now items', () => {
    expect(
      trayStatus({ backendUp: true, paused: false, items: [stuck] }).tooltip
    ).toBe('RDST - 1 item needs you')
    expect(
      trayStatus({
        backendUp: true,
        paused: false,
        items: [stuck, { ...stuck, subject: 'x' }],
      }).tooltip
    ).toBe('RDST - 2 items need you')
    for (const closed of [
      { ...stuck, state: 'resolved' },
      { ...stuck, state: 'dismissed' },
      { ...stuck, state: 'snoozed' },
      { ...stuck, muted_by: 3 },
      { ...lost, state: 'resolved' },
    ]) {
      expect(
        trayStatus({ backendUp: true, paused: false, items: [closed] }).state
      ).toBe('running')
    }
  })

  it('shows paused over problems, and offline over everything', () => {
    expect(
      trayStatus({ backendUp: true, paused: true, items: [lost] })
    ).toMatchObject({
      state: 'paused',
      tooltip: 'RDST - monitoring is off',
    })
    expect(
      trayStatus({ backendUp: false, paused: true, items: [lost] })
    ).toEqual({
      state: 'offline',
      tooltip: 'RDST - backend stopped',
      unreachable: [],
      actNow: 0,
    })
    expect(
      trayStatus({ backendUp: true, paused: false, items: null }).state
    ).toBe('offline')
    expect(
      trayStatus({
        backendUp: false,
        starting: true,
        paused: false,
        items: null,
      }).tooltip
    ).toBe('RDST - starting')
  })
})

describe('tray status watcher', () => {
  it('reports changes only, and goes offline when the inbox read fails', async () => {
    vi.useFakeTimers()
    const onChange = vi.fn()
    let items: TrayInboxItem[] = []
    let failing = false
    const fetcher = vi.fn(async (url: string) => {
      expect(url).toBe('http://127.0.0.1:9/api/monitor/inbox?state=open')
      if (failing) throw new Error('connection refused')
      return new Response(JSON.stringify({ items }), { status: 200 })
    })
    const watcher = watchTrayStatus({
      apiBaseUrl: () => 'http://127.0.0.1:9',
      backendUp: () => true,
      paused: () => false,
      onChange,
      fetcher: fetcher as unknown as typeof fetch,
      intervalMs: 15_000,
    })
    await vi.advanceTimersByTimeAsync(0)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0]?.[0].state).toBe('running')
    await watcher.refresh()
    expect(onChange).toHaveBeenCalledTimes(1)
    items = [lost]
    await vi.advanceTimersByTimeAsync(15_000)
    expect(onChange.mock.calls.at(-1)?.[0].state).toBe('problem')
    failing = true
    await watcher.refresh()
    expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({
      state: 'offline',
      tooltip: 'RDST - backend stopped',
    })
    watcher.stop()
    failing = false
    items = []
    await vi.advanceTimersByTimeAsync(30_000)
    expect(onChange).toHaveBeenCalledTimes(3)
  })

  it('skips the request while the backend is down', async () => {
    const fetcher = vi.fn()
    const onChange = vi.fn()
    const watcher = watchTrayStatus({
      apiBaseUrl: () => 'http://127.0.0.1:9',
      backendUp: () => false,
      paused: () => false,
      onChange,
      fetcher: fetcher as unknown as typeof fetch,
    })
    await watcher.refresh()
    expect(fetcher).not.toHaveBeenCalled()
    expect(onChange.mock.calls[0]?.[0].state).toBe('offline')
    watcher.stop()
  })
})

describe('tray status while starting', () => {
  it('says what the start is doing', () => {
    expect(
      trayStatus({
        backendUp: false,
        starting: true,
        paused: false,
        items: null,
        startingTooltip: 'RDST - upgrading your data, this can take a minute',
      })
    ).toMatchObject({
      state: 'offline',
      tooltip: 'RDST - upgrading your data, this can take a minute',
    })
    expect(
      trayStatus({
        backendUp: false,
        starting: true,
        paused: false,
        items: null,
      }).tooltip
    ).toBe('RDST - starting')
    expect(
      trayStatus({
        backendUp: false,
        starting: false,
        paused: false,
        items: null,
        startingTooltip: 'ignored',
      }).tooltip
    ).toBe('RDST - backend stopped')
  })
})

it('awaits fresh monitoring settings on every poll without opening the menu', async () => {
  vi.useFakeTimers()
  let enabled = true
  const onChange = vi.fn()
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/api/configure/targets'))
      return Response.json({ targets: [{ name: 'prod' }] })
    if (url.endsWith('/settings')) return Response.json({ enabled })
    return Response.json({ items: [] })
  }) as unknown as typeof fetch
  const paused = vi.fn(async () =>
    allPaused(await listTrayDatabases('http://x', fetcher))
  )
  const watcher = watchTrayStatus({
    apiBaseUrl: () => 'http://x',
    backendUp: () => true,
    paused,
    onChange,
    fetcher,
  })
  try {
    await vi.advanceTimersByTimeAsync(0)
    expect(onChange.mock.calls.at(-1)?.[0].state).toBe('running')
    enabled = false
    await vi.advanceTimersByTimeAsync(15_000)
    expect(onChange.mock.calls.at(-1)?.[0].state).toBe('paused')
    enabled = true
    await vi.advanceTimersByTimeAsync(15_000)
    expect(onChange.mock.calls.at(-1)?.[0].state).toBe('running')
    expect(paused).toHaveBeenCalledTimes(3)
  } finally {
    watcher.stop()
    vi.useRealTimers()
  }
})
