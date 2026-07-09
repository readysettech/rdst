import { contextBridge, ipcRenderer } from 'electron'

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
})
