import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { app, BrowserWindow, dialog, shell } from 'electron'

import { type BackendHandle, startBackend, stopBackend } from './backend.js'
import { type StaticServerHandle, startStaticServer } from './static-server.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const RENDERER_DIR = path.resolve(__dirname, '../renderer')

let mainWindow: BrowserWindow | null = null
let backend: BackendHandle | null = null
let webServer: StaticServerHandle | null = null
let quitCleanupStarted = false
let cleanupPromise: Promise<void> | null = null

function getResourcesPath(): string {
  return app.isPackaged
    ? process.resourcesPath
    : path.resolve(__dirname, '../..')
}

async function resolveRendererUrl(): Promise<string> {
  const devRendererUrl = process.env.ELECTRON_RENDERER_URL
  if (devRendererUrl) {
    return devRendererUrl
  }

  backend = await startBackend({ resourcesPath: getResourcesPath() })
  webServer = await startStaticServer({
    rootDir: RENDERER_DIR,
    apiBaseUrl: backend.apiBaseUrl,
  })
  return webServer.url
}

async function createWindow(rendererUrl: string): Promise<BrowserWindow> {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 620,
    title: 'RDST',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition:
      process.platform === 'darwin' ? { x: 12, y: 12 } : undefined,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  mainWindow = window

  window.webContents.setWindowOpenHandler(({ url }) => {
    const parsed = new URL(url)
    if (parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost') {
      return { action: 'allow' }
    }
    void shell.openExternal(url)
    return { action: 'deny' }
  })

  window.once('closed', () => {
    if (mainWindow === window) mainWindow = null
  })
  await window.loadURL(rendererUrl)
  return window
}

function cleanup(): Promise<void> {
  cleanupPromise ??= (async () => {
    const server = webServer
    webServer = null
    if (server) {
      await server.close().catch((error) => {
        console.warn('[rdst-desktop] failed to close web server:', error)
      })
    }

    const backendHandle = backend
    backend = null
    await stopBackend(backendHandle)
  })()
  return cleanupPromise
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function focusMainWindow(): void {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

async function startApplication(): Promise<void> {
  await app.whenReady()

  try {
    const rendererUrl = await resolveRendererUrl()
    await createWindow(rendererUrl)

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        void createWindow(rendererUrl).catch((error) => {
          console.error('[rdst-desktop] failed to create window:', error)
          dialog.showErrorBox(
            'RDST Desktop',
            `Unable to open the RDST window.\n\n${errorMessage(error)}`
          )
        })
        return
      }
      focusMainWindow()
    })
  } catch (error) {
    console.error('[rdst-desktop] failed to start:', error)
    await cleanup()
    dialog.showErrorBox('RDST Desktop failed to start', errorMessage(error))
    app.quit()
  }
}

function configurePrimaryInstance(): void {
  app.on('second-instance', focusMainWindow)

  app.on('before-quit', (event) => {
    if (quitCleanupStarted) return
    quitCleanupStarted = true
    event.preventDefault()
    void cleanup().finally(() => app.quit())
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
      app.quit()
    }
  })

  void startApplication()
}

if (app.requestSingleInstanceLock()) {
  configurePrimaryInstance()
} else {
  app.quit()
}
