export interface DesktopWindowControls {
  minimize: () => void
  toggleMaximize: () => void
  close: () => void
  isMaximized: () => Promise<boolean>
  onMaximizedChange: (callback: (maximized: boolean) => void) => () => void
}

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
      windowControls?: DesktopWindowControls
      updates?: DesktopUpdates
      notifications?: DesktopNotifications
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

export function isDesktopRuntime(): boolean {
  return window.rdstDesktop?.isDesktop === true
}

/**
 * True when the desktop shell uses a frameless window and the renderer draws
 * its own window controls.
 */
export function isDesktopFrameless(): boolean {
  return (
    window.rdstDesktop?.isDesktop === true &&
    window.rdstDesktop.platform !== 'darwin'
  )
}

export function getWindowControls(): DesktopWindowControls | undefined {
  return window.rdstDesktop?.windowControls
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
