// Can the OS actually show RDST's notifications? A switched-off Windows toast setting
// drops every toast silently, so the app checks on launch and every few minutes, and
// says so in the window and the tray. It only reads OS settings; it never writes them.
//
// - Windows: HKCU\...\PushNotifications ToastEnabled (the global switch) and
//   HKCU\...\Notifications\Settings\<app id> Enabled (this app's switch), via `reg query`.
// - macOS: Electron has no permission-status API in the main process, so a missing
//   notification service (`Notification.isSupported()` false) or a toast that fails to
//   show counts as blocked. The first-launch welcome toast raises the permission prompt.
// - Linux: whether a notification daemon owns org.freedesktop.Notifications on the
//   session bus (`dbus-send`, then `gdbus`).

import { appId as instanceAppId } from './instance.js'

export const APP_USER_MODEL_ID = instanceAppId('')
export const HEALTH_CHECK_INTERVAL_MS = 5 * 60_000

export type NotificationBlock =
  | 'windows_global'
  | 'windows_app'
  | 'macos_blocked'
  | 'linux_no_daemon'
  | 'unsupported'

export interface NotificationHealth {
  blocked: boolean
  reason: NotificationBlock | null
  /** One sentence for the in-app banner. */
  message: string | null
  /** The tray's menu item and tooltip line. */
  trayLabel: string | null
  /** The OS page where the user turns notifications on, when there is one. */
  settingsUrl: string | null
  checkedAt: number
}

export interface CommandResult {
  code: number | null
  stdout: string
}

export type CommandRunner = (command: string, args: string[]) => Promise<CommandResult>

const WINDOWS_PUSH_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications'
const windowsAppKey = (appId: string) =>
  `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings\\${appId}`

const SETTINGS_URL: Partial<Record<NodeJS.Platform, string>> = {
  win32: 'ms-settings:notifications',
  darwin: 'x-apple.systempreferences:com.apple.preference.notifications',
}

const OS_NAME: Partial<Record<NodeJS.Platform, string>> = {
  win32: 'Windows',
  darwin: 'macOS',
}

const MISSED = "so you won't see alerts like 'Can't reach the database'."

const BLOCK_MESSAGE: Record<NotificationBlock, string> = {
  windows_global: `Windows notifications are off, ${MISSED}`,
  windows_app: `Windows notifications are off for RDST, ${MISSED}`,
  macos_blocked: `macOS notifications are off for RDST, ${MISSED}`,
  linux_no_daemon: `No notification service is running on this desktop, ${MISSED}`,
  unsupported: `This system can't show notifications, ${MISSED}`,
}

/**
 * A DWORD from `reg query` output ("    ToastEnabled    REG_DWORD    0x0"), or null
 * when the value isn't set.
 */
export function parseRegDword(stdout: string, name: string): number | null {
  for (const line of stdout.split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/)
    if (parts.length >= 3 && parts[0]?.toLowerCase() === name.toLowerCase() && parts[1] === 'REG_DWORD') {
      const value = Number.parseInt(parts[2] ?? '', 16)
      return Number.isNaN(value) ? null : value
    }
  }
  return null
}

/** `dbus-send`/`gdbus` reply to NameHasOwner: true, false, or null when unreadable. */
export function parseNameHasOwner(stdout: string): boolean | null {
  const text = stdout.toLowerCase()
  if (/\bboolean true\b|\(true,?\)/.test(text)) return true
  if (/\bboolean false\b|\(false,?\)/.test(text)) return false
  return null
}

export function healthFor(
  platform: NodeJS.Platform,
  reason: NotificationBlock | null,
  now: number
): NotificationHealth {
  if (!reason)
    return { blocked: false, reason: null, message: null, trayLabel: null, settingsUrl: null, checkedAt: now }
  const settingsUrl = reason === 'linux_no_daemon' || reason === 'unsupported'
    ? null
    : (SETTINGS_URL[platform] ?? null)
  const blocker = OS_NAME[platform] ?? 'the system'
  return {
    blocked: true,
    reason,
    message: BLOCK_MESSAGE[reason],
    trayLabel: settingsUrl
      ? `Notifications are blocked by ${blocker}... Fix`
      : 'Notifications can\'t be shown on this system',
    settingsUrl,
    checkedAt: now,
  }
}

export async function detectNotificationBlock(options: {
  platform: NodeJS.Platform
  run: CommandRunner
  /** Electron's `Notification.isSupported()`. */
  supported: boolean
  /** A toast failed to show since the last check (Electron's `failed` event). */
  lastToastFailed?: boolean
  appId?: string
}): Promise<NotificationBlock | null> {
  const { platform, run, supported } = options
  if (platform === 'win32') {
    const global = await run('reg', ['query', WINDOWS_PUSH_KEY, '/v', 'ToastEnabled']).catch(() => null)
    if (global && global.code === 0 && parseRegDword(global.stdout, 'ToastEnabled') === 0) return 'windows_global'
    const app = await run('reg', ['query', windowsAppKey(options.appId ?? APP_USER_MODEL_ID), '/v', 'Enabled'])
      .catch(() => null)
    if (app && app.code === 0 && parseRegDword(app.stdout, 'Enabled') === 0) return 'windows_app'
    return supported ? null : 'unsupported'
  }
  if (platform === 'darwin') {
    if (!supported || options.lastToastFailed) return 'macos_blocked'
    return null
  }
  if (platform === 'linux') {
    const query = [
      ['dbus-send', ['--session', '--print-reply', '--dest=org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus.NameHasOwner', 'string:org.freedesktop.Notifications']],
      ['gdbus', ['call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus',
        '--method', 'org.freedesktop.DBus.NameHasOwner', 'org.freedesktop.Notifications']],
    ] as const
    for (const [command, args] of query) {
      const result = await run(command, [...args]).catch(() => null)
      if (!result || result.code !== 0) continue
      const owned = parseNameHasOwner(result.stdout)
      if (owned === false) return 'linux_no_daemon'
      if (owned === true) return null
    }
    // No bus tool answered: say nothing rather than guess.
    return supported ? null : 'unsupported'
  }
  return supported ? null : 'unsupported'
}

/** Checks now and on an interval, reporting each change. */
export function watchNotificationHealth(options: {
  check: () => Promise<NotificationHealth>
  onChange: (health: NotificationHealth) => void
  intervalMs?: number
}): { refresh: () => Promise<NotificationHealth | null>; current: () => NotificationHealth | null; stop: () => void } {
  let current: NotificationHealth | null = null
  let stopped = false
  const refresh = async () => {
    try {
      const next = await options.check()
      if (stopped) return current
      const changed = !current || current.blocked !== next.blocked || current.reason !== next.reason
      current = next
      if (changed) options.onChange(next)
      return next
    } catch (error) {
      console.warn('[rdst-desktop] notification check failed:', error)
      return current
    }
  }
  void refresh()
  const timer = setInterval(() => { void refresh() }, options.intervalMs ?? HEALTH_CHECK_INTERVAL_MS)
  timer.unref?.()
  return {
    refresh,
    current: () => current,
    stop: () => {
      stopped = true
      clearInterval(timer)
    },
  }
}
