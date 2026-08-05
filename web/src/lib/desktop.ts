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

declare global {
  interface Window {
    rdstDesktop?: {
      isDesktop: true
      platform: string
      oauth?: {
        registerProtocol: () => Promise<boolean>
      }
      files?: {
        selectSshKey: () => Promise<string | null>
      }
      windowControls?: DesktopWindowControls
      updates?: DesktopUpdates
    }
  }
}

/**
 * True when running inside the rdst-desktop Electron shell on macOS, where the
 * window is transparent and native liquid glass shows through the layout.
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
