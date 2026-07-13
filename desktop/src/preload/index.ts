import { contextBridge, ipcRenderer } from 'electron'

import type { UpdateStatePayload } from '../main/update-policy.js'

contextBridge.exposeInMainWorld('rdstDesktop', {
  isDesktop: true,
  platform: process.platform,
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
      return () =>
        ipcRenderer.removeListener('updates:state-changed', listener)
    },
  },
})
