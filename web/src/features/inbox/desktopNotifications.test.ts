import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Push = typeof import('./desktopNotifications')
let push: Push
const fetchMock = vi.fn(
  async (_url: string, _init?: RequestInit) => new Response('{}')
)

beforeEach(async () => {
  vi.resetModules()
  localStorage.clear()
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('Notification', {
    permission: 'granted',
    requestPermission: vi.fn(),
  })
  push = await import('./desktopNotifications')
})

afterEach(() => vi.unstubAllGlobals())

const saved = () =>
  fetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body)))

describe('Push to this computer', () => {
  it('takes the backend choice on load', () => {
    const changed = vi.fn()
    const stop = push.onDesktopNotificationsChange(changed)
    push.hydrateDesktopNotifications(true)
    stop()
    expect(push.desktopNotificationsEnabled()).toBe(true)
    expect(changed).toHaveBeenCalledTimes(1)
    push.hydrateDesktopNotifications(false)
    expect(push.desktopNotificationsEnabled()).toBe(true)
  })

  it('saves an earlier browser choice when the backend has none', () => {
    localStorage.setItem('rdst_inbox_desktop_notifications', '1')
    push.hydrateDesktopNotifications(null)
    expect(saved()).toEqual([{ push_notifications: true }])
  })

  it('saves a change in the backend', async () => {
    expect(await push.setDesktopNotifications(true)).toBe(true)
    expect(await push.setDesktopNotifications(false)).toBe(false)
    expect(saved()).toEqual([
      { push_notifications: true },
      { push_notifications: false },
    ])
  })
})
