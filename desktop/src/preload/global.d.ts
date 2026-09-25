import type { UpdateStatePayload } from '../main/update-policy.js'
import type { LoginStatus } from '../main/login.js'
import type { NotificationHealth } from '../main/notification-health.js'

declare global {
  interface Window {
    rdstDesktop?: {
      isDesktop: true
      platform: NodeJS.Platform
      telemetryDisabled: boolean
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
      notifications: {
        getHealth: () => Promise<NotificationHealth | null>
        onHealthChange: (
          callback: (health: NotificationHealth) => void
        ) => () => void
        openSettings: () => void
      }
      startup: {
        openLogs: () => void
        retry: () => void
      }
      getStartAtLogin: () => Promise<LoginStatus>
      setStartAtLogin: (enabled: boolean) => Promise<LoginStatus>
      getNotificationsEnabled: () => Promise<boolean>
      setNotificationsEnabled: (enabled: boolean) => Promise<boolean>
    }
  }
}
