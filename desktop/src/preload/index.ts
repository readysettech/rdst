import { contextBridge } from 'electron'

contextBridge.exposeInMainWorld('rdstDesktop', {
  isDesktop: true,
})
