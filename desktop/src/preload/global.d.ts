import type { UpdateStatePayload } from '../main/update-policy.js'

export {}

declare global {
  interface Window {
    rdstDesktop?: {
      isDesktop: true
      platform: NodeJS.Platform
      oauth: {
        registerProtocol: () => Promise<boolean>
      }
      files: {
        selectSshKey: () => Promise<string | null>
      }
      windowControls: {
        minimize: () => void
        toggleMaximize: () => void
        close: () => void
        isMaximized: () => Promise<boolean>
        onMaximizedChange: (
          callback: (maximized: boolean) => void
        ) => () => void
      }
      updates: {
        getState: () => Promise<UpdateStatePayload | null>
        install: () => void
        onStateChange: (
          callback: (state: UpdateStatePayload) => void
        ) => () => void
      }
    }
  }
}
