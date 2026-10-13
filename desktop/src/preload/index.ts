import { contextBridge, ipcRenderer, webFrame } from 'electron'
import type { BackendRunState } from '../main/backend-supervisor.js'
import type { LoginStatus } from '../main/login.js'
import type { NotificationHealth } from '../main/notification-health.js'
import type { UpdateStatePayload } from '../main/update-policy.js'

contextBridge.exposeInMainWorld('rdstDesktop', {
  isDesktop: true,
  platform: process.platform,
  // Renderer analytics honor the same kill switch as the Python backend,
  // so automated launches (CI smoke tests) never register as users.
  telemetryDisabled: [
    'off',
    'false',
    '0',
    'no',
    'disable',
    'disabled',
  ].includes((process.env.RDST_TELEMETRY ?? '').toLowerCase()),
  oauth: {
    registerProtocol: (): Promise<boolean> =>
      ipcRenderer.invoke('oauth:register-protocol'),
  },
  files: {
    selectSshKey: (): Promise<string | null> =>
      ipcRenderer.invoke('ssh:select-key'),
  },
  updates: {
    getState: (): Promise<UpdateStatePayload | null> =>
      ipcRenderer.invoke('updates:get-state'),
    install: () => ipcRenderer.send('updates:install'),
    onStateChange: (callback: (state: UpdateStatePayload) => void) => {
      const listener = (_event: unknown, state: UpdateStatePayload) =>
        callback(state)
      ipcRenderer.on('updates:state-changed', listener)
      return () => ipcRenderer.removeListener('updates:state-changed', listener)
    },
  },
  notifications: {
    getHealth: (): Promise<NotificationHealth | null> =>
      ipcRenderer.invoke('desktop:get-notification-health'),
    onHealthChange: (callback: (health: NotificationHealth) => void) => {
      const listener = (_event: unknown, health: NotificationHealth) =>
        callback(health)
      ipcRenderer.on('desktop:notification-health', listener)
      return () =>
        ipcRenderer.removeListener('desktop:notification-health', listener)
    },
    openSettings: () => ipcRenderer.send('desktop:open-notification-settings'),
  },
  startup: {
    openLogs: () => ipcRenderer.send('desktop:open-logs'),
    retry: () => ipcRenderer.send('desktop:retry-backend'),
    reload: () => ipcRenderer.send('desktop:reload-window'),
  },
  backend: {
    getState: (): Promise<BackendRunState> =>
      ipcRenderer.invoke('desktop:get-backend-state'),
    onStateChange: (callback: (state: BackendRunState) => void) => {
      const listener = (_event: unknown, state: BackendRunState) =>
        callback(state)
      ipcRenderer.on('desktop:backend-state', listener)
      return () => ipcRenderer.removeListener('desktop:backend-state', listener)
    },
  },
  getStartAtLogin: (): Promise<LoginStatus> =>
    ipcRenderer.invoke('desktop:get-start-at-login'),
  setStartAtLogin: (enabled: boolean): Promise<LoginStatus> =>
    ipcRenderer.invoke('desktop:set-start-at-login', enabled),
  getNotificationsEnabled: (): Promise<boolean> =>
    ipcRenderer.invoke('desktop:get-notifications-enabled'),
  setNotificationsEnabled: (enabled: boolean): Promise<boolean> =>
    ipcRenderer.invoke('desktop:set-notifications-enabled', enabled),
})

// Pixels of wheel or pinch travel per zoom step. A trackpad pinch sends dozens of small
// wheel events a second, so the zoom steps on accumulated travel, not on each event.
const ZOOM_WHEEL_STEP = 60
const ZOOM_MIN_INTERVAL_MS = 120
let zoomTravel = 0
let lastZoomAt = 0

// A non-passive listener suppresses Chromium's default gesture zoom and scrolling.
window.addEventListener(
  'wheel',
  (event) => {
    const modifier =
      event.ctrlKey || (process.platform === 'darwin' && event.metaKey)
    if (!modifier || event.deltaY === 0) return
    event.preventDefault()
    zoomTravel += event.deltaY
    const now = performance.now()
    if (Math.abs(zoomTravel) < ZOOM_WHEEL_STEP) return
    if (now - lastZoomAt < ZOOM_MIN_INTERVAL_MS) return
    const direction = -Math.sign(zoomTravel)
    zoomTravel = 0
    lastZoomAt = now
    webFrame.setZoomLevel(
      Math.max(-3, Math.min(5, webFrame.getZoomLevel() + direction * 0.5))
    )
  },
  { passive: false, capture: true }
)
