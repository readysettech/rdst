import { contextBridge, ipcRenderer, webFrame } from 'electron'

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
  windowControls: {
    minimize: () => ipcRenderer.send('window:minimize'),
    toggleMaximize: () => ipcRenderer.send('window:toggle-maximize'),
    close: () => ipcRenderer.send('window:close'),
    isMaximized: (): Promise<boolean> =>
      ipcRenderer.invoke('window:is-maximized'),
    onMaximizedChange: (callback: (maximized: boolean) => void) => {
      const listener = (_event: unknown, maximized: boolean) =>
        callback(maximized)
      ipcRenderer.on('window:maximized-changed', listener)
      return () =>
        ipcRenderer.removeListener('window:maximized-changed', listener)
    },
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
})

// A non-passive listener suppresses Chromium's default gesture zoom and scrolling.
window.addEventListener(
  'wheel',
  (event) => {
    const modifier =
      event.ctrlKey || (process.platform === 'darwin' && event.metaKey)
    if (!modifier || event.deltaY === 0) return
    event.preventDefault()
    webFrame.setZoomLevel(
      Math.max(
        -3,
        Math.min(5, webFrame.getZoomLevel() - Math.sign(event.deltaY) * 0.5)
      )
    )
  },
  { passive: false, capture: true }
)
