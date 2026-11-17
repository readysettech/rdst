export interface DesktopUpdateLink {
  label: string
  url: string
}

export interface DesktopUpdateState {
  /**
   * "downloading" and "ready" are in-place update states; "available"
   * means the install format requires a manual download via the provided
   * links.
   */
  status: 'available' | 'downloading' | 'ready'
  version: string
  downloadLinks: DesktopUpdateLink[]
  progress?: number
}

export interface DesktopUpdates {
  getState: () => Promise<DesktopUpdateState | null>
  install: () => void
  onStateChange: (callback: (state: DesktopUpdateState) => void) => () => void
}

/** Whether the OS will show RDST's notifications (the desktop app checks every few minutes). */
export interface DesktopNotificationHealth {
  blocked: boolean
  reason: string | null
  message: string | null
  trayLabel: string | null
  settingsUrl: string | null
  checkedAt: number
}

export interface DesktopNotifications {
  getHealth: () => Promise<DesktopNotificationHealth | null>
  onHealthChange: (
    callback: (health: DesktopNotificationHealth) => void
  ) => () => void
  openSettings: () => void
  /** Resolves to why the test couldn't be shown and what to do, or null once it shows. */
  sendTest?: () => Promise<string | null>
  /** The tray's Notifications switch changed. */
  onEnabledChange?: (callback: (enabled: boolean) => void) => () => void
}

/**
 * The desktop app's background service after it first started: restarting while it comes
 * back after stopping, failed while it keeps stopping (it still tries every 30 seconds).
 */
export type DesktopBackendState = 'running' | 'restarting' | 'failed'

export interface DesktopBackend {
  getState: () => Promise<DesktopBackendState>
  onStateChange: (callback: (state: DesktopBackendState) => void) => () => void
}

export interface DesktopLoginStatus {
  enabled: boolean
  available: boolean
  note?: string
  /** This build asks before starting at login, and the user hasn't answered yet. */
  ask?: boolean
}

declare global {
  interface Window {
    rdstDesktop?: {
      isDesktop: true
      platform: string
      // Set by the preload when RDST_TELEMETRY disables telemetry, so the
      // renderer's analytics stay silent in automated launches (CI smokes).
      telemetryDisabled?: boolean
      oauth?: {
        registerProtocol: () => Promise<boolean>
      }
      files?: {
        selectSshKey: () => Promise<string | null>
      }
      data?: {
        clearStorage: () => Promise<void>
      }
      updates?: DesktopUpdates
      notifications?: DesktopNotifications
      backend?: DesktopBackend
      monitoring?: {
        /** Fires after the tray turns monitoring on or off, or pauses it. */
        onChange: (callback: () => void) => () => void
        /** Rebuilds the tray menu after monitoring changed outside the app. */
        changed?: () => void
      }
      startup?: {
        openLogs: () => void
      }
      /** Runs that showed the window, counted up to the second. */
      getWindowOpens?: () => Promise<number>
      /** Start at login, the same switch as in the tray menu. */
      getStartAtLogin?: () => Promise<DesktopLoginStatus>
      setStartAtLogin?: (enabled: boolean) => Promise<DesktopLoginStatus>
      /** The desktop app's own system notifications switch (the tray's Notifications). */
      getNotificationsEnabled?: () => Promise<boolean>
      setNotificationsEnabled?: (enabled: boolean) => Promise<boolean>
    }
  }
}

/**
 * True when running inside the rdst-desktop Electron shell on macOS, where the
 * renderer reserves space for native traffic lights.
 */
export function isDesktopMac(): boolean {
  return (
    window.rdstDesktop?.isDesktop === true &&
    window.rdstDesktop.platform === 'darwin'
  )
}

/** The machine RDST runs on, as copy names it: "this Mac" in the macOS app. */
export function thisComputer(): string {
  return isDesktopMac() ? 'this Mac' : 'this computer'
}

export function isDesktopRuntime(): boolean {
  return window.rdstDesktop?.isDesktop === true
}

export function getDesktopUpdates(): DesktopUpdates | undefined {
  return window.rdstDesktop?.updates
}

export function registerDesktopOAuthProtocol(): Promise<boolean> | undefined {
  return window.rdstDesktop?.oauth?.registerProtocol()
}

export function selectDesktopSshKey(): Promise<string | null> | undefined {
  return window.rdstDesktop?.files?.selectSshKey()
}

/** The desktop app's system notifications switch, when this page runs inside it. */
export function getDesktopSystemNotifications():
  | {
      get: () => Promise<boolean>
      set: (enabled: boolean) => Promise<boolean>
    }
  | undefined {
  const desktop = window.rdstDesktop
  if (
    !desktop?.isDesktop ||
    !desktop.getNotificationsEnabled ||
    !desktop.setNotificationsEnabled
  )
    return undefined
  return {
    get: desktop.getNotificationsEnabled,
    set: desktop.setNotificationsEnabled,
  }
}

export function getDesktopNotifications(): DesktopNotifications | undefined {
  return window.rdstDesktop?.notifications
}

export function getDesktopBackend(): DesktopBackend | undefined {
  return window.rdstDesktop?.backend
}

/** Opens the desktop app's logs folder; undefined outside the desktop app. */
export function getDesktopOpenLogs(): (() => void) | undefined {
  return window.rdstDesktop?.startup?.openLogs
}

export function clearBrowserStorage() {
  window.localStorage.clear()
  window.sessionStorage.clear()
}

export function clearDesktopStorage(): Promise<void> | undefined {
  return window.rdstDesktop?.data?.clearStorage()
}
