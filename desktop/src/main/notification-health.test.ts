import { describe, expect, it, vi } from 'vitest'

import {
  type CommandRunner,
  detectNotificationBlock,
  healthFor,
  parseNameHasOwner,
  parseRegDword,
  watchNotificationHealth,
} from './notification-health.js'

const REG_OFF = '\r\nHKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications\r\n' +
  '    ToastEnabled    REG_DWORD    0x0\r\n\r\n'
const REG_ON = REG_OFF.replace('0x0', '0x1')

/** A runner that answers `reg query` by key and value name, and bus queries by command. */
function runner(answers: Record<string, { code: number | null; stdout: string } | Error>): CommandRunner {
  return vi.fn(async (command: string, args: string[]) => {
    const key = command === 'reg' ? `${args[1]}|${args[3]}` : command
    const answer = answers[key]
    if (answer instanceof Error) throw answer
    return answer ?? { code: 1, stdout: '' }
  })
}

const PUSH = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications|ToastEnabled'
const APP = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings\\com.readyset.rdst-desktop|Enabled'

describe('parsers', () => {
  it('reads a REG_DWORD and treats a missing value as unset', () => {
    expect(parseRegDword(REG_OFF, 'ToastEnabled')).toBe(0)
    expect(parseRegDword(REG_ON, 'toastenabled')).toBe(1)
    expect(parseRegDword('ERROR: The system was unable to find the specified registry key or value.', 'Enabled'))
      .toBeNull()
  })

  it('reads NameHasOwner replies from dbus-send and gdbus', () => {
    expect(parseNameHasOwner('method return time=1 sender=org.freedesktop.DBus\n   boolean true\n')).toBe(true)
    expect(parseNameHasOwner('   boolean false')).toBe(false)
    expect(parseNameHasOwner('(true,)')).toBe(true)
    expect(parseNameHasOwner('(false,)')).toBe(false)
    expect(parseNameHasOwner('')).toBeNull()
  })
})

describe('detectNotificationBlock', () => {
  it('Windows: the global toast switch, then the per-app switch', async () => {
    expect(await detectNotificationBlock({
      platform: 'win32', supported: true, run: runner({ [PUSH]: { code: 0, stdout: REG_OFF } }),
    })).toBe('windows_global')
    expect(await detectNotificationBlock({
      platform: 'win32', supported: true,
      run: runner({ [PUSH]: { code: 0, stdout: REG_ON }, [APP]: { code: 0, stdout: '    Enabled    REG_DWORD    0x0' } }),
    })).toBe('windows_app')
    // Values that were never written mean notifications are on.
    expect(await detectNotificationBlock({ platform: 'win32', supported: true, run: runner({}) })).toBeNull()
    expect(await detectNotificationBlock({
      platform: 'win32', supported: true, run: runner({ [PUSH]: new Error('reg missing') }),
    })).toBeNull()
  })

  it('macOS: no notification service or a failed toast', async () => {
    const run = runner({})
    expect(await detectNotificationBlock({ platform: 'darwin', supported: true, run })).toBeNull()
    expect(await detectNotificationBlock({ platform: 'darwin', supported: false, run })).toBe('macos_blocked')
    expect(await detectNotificationBlock({ platform: 'darwin', supported: true, lastToastFailed: true, run }))
      .toBe('macos_blocked')
    expect(run).not.toHaveBeenCalled()
  })

  it('Linux: a notification daemon on the session bus', async () => {
    expect(await detectNotificationBlock({
      platform: 'linux', supported: true, run: runner({ 'dbus-send': { code: 0, stdout: 'boolean false' } }),
    })).toBe('linux_no_daemon')
    expect(await detectNotificationBlock({
      platform: 'linux', supported: true,
      run: runner({ 'dbus-send': new Error('ENOENT'), gdbus: { code: 0, stdout: '(true,)' } }),
    })).toBeNull()
    // Neither tool answered: no claim either way.
    expect(await detectNotificationBlock({ platform: 'linux', supported: true, run: runner({}) })).toBeNull()
  })
})

describe('healthFor', () => {
  it('words the banner and tray, and links the OS settings page', () => {
    const windows = healthFor('win32', 'windows_global', 5)
    expect(windows).toMatchObject({
      blocked: true,
      message: "Windows notifications are off, so you won't see alerts like 'Can't reach the database'.",
      trayLabel: 'Notifications are blocked by Windows... Fix',
      settingsUrl: 'ms-settings:notifications',
      checkedAt: 5,
    })
    expect(healthFor('darwin', 'macos_blocked', 0).settingsUrl).toMatch(/^x-apple.systempreferences:/)
    expect(healthFor('linux', 'linux_no_daemon', 0)).toMatchObject({ settingsUrl: null, blocked: true })
    expect(healthFor('win32', null, 0)).toMatchObject({ blocked: false, message: null, trayLabel: null })
    for (const reason of ['windows_global', 'windows_app', 'macos_blocked', 'linux_no_daemon'] as const)
      expect(healthFor('win32', reason, 0).message).not.toMatch(/[–—]/)
  })
})

describe('watchNotificationHealth', () => {
  it('reports only changes', async () => {
    vi.useFakeTimers()
    try {
      const states = [null, null, 'windows_global', 'windows_global', null] as const
      let index = 0
      const changes: boolean[] = []
      const watch = watchNotificationHealth({
        check: async () => healthFor('win32', states[Math.min(index++, states.length - 1)] ?? null, index),
        onChange: (health) => changes.push(health.blocked),
        intervalMs: 1000,
      })
      await vi.advanceTimersByTimeAsync(0)
      for (let tick = 0; tick < 4; tick++) await vi.advanceTimersByTimeAsync(1000)
      expect(changes).toEqual([false, true, false])
      expect(watch.current()?.blocked).toBe(false)
      watch.stop()
    } finally {
      vi.useRealTimers()
    }
  })
})
