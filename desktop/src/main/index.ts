import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'

import { type BackendHandle, startBackend, stopBackend } from './backend.js'
import {
  createGlassFallbackLogger,
  type GlassModule,
  loadLiquidGlass,
} from './liquid-glass.js'
import { type StaticServerHandle, startStaticServer } from './static-server.js'
import { setupAutoUpdates } from './updater.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const RENDERER_DIR = path.resolve(__dirname, '../renderer')

let mainWindow: BrowserWindow | null = null
let backend: BackendHandle | null = null
let webServer: StaticServerHandle | null = null
let quitCleanupStarted = false
let cleanupPromise: Promise<void> | null = null
let liquidGlass: GlassModule | null = null
let liquidGlassFailureReason: string | null = null
let glassViewID: number | null = null
let lastGlassFocusState: boolean | null = null

const logGlassFallback = createGlassFallbackLogger(process.platform)

function tuneGlassForFocus(focused: boolean): void {
  if (!liquidGlass || glassViewID == null) return
  if (lastGlassFocusState === focused) return
  lastGlassFocusState = focused

  try {
    liquidGlass.unstable_setSubdued?.(glassViewID, focused ? 0 : 1)
    liquidGlass.unstable_setScrim?.(glassViewID, focused ? 0 : 1)
  } catch {
    // Best effort only.
  }
}

function applyGlassEffects(win: BrowserWindow): void {
  if (process.platform !== 'darwin') return

  glassViewID = null
  lastGlassFocusState = null
  let fallbackReason = liquidGlassFailureReason

  try {
    const supported = liquidGlass?.isGlassSupported?.() ?? false
    if (supported && liquidGlass?.addView) {
      glassViewID = liquidGlass.addView(win.getNativeWindowHandle(), {
        cornerRadius: 14,
        tintColor: '#121212e6',
        opaque: false,
      })
      tuneGlassForFocus(win.isFocused())
      return
    }

    if (!fallbackReason) {
      fallbackReason = liquidGlass
        ? 'electron-liquid-glass reported that native glass is unavailable on this macOS version.'
        : 'electron-liquid-glass was unavailable at runtime.'
    }
  } catch (error) {
    fallbackReason = `electron-liquid-glass threw while applying native glass: ${errorMessage(error)}`
  }

  logGlassFallback(fallbackReason ?? 'unknown liquid glass failure')

  try {
    win.setVibrancy?.('sidebar')
    win.setBackgroundMaterial?.('auto')
  } catch {
    // Ignore optional visual effect errors.
  }
}

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

function registerWindowControlHandlers(): void {
  const senderWindow = (event: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent) =>
    BrowserWindow.fromWebContents(event.sender)

  ipcMain.on('window:minimize', (event) => senderWindow(event)?.minimize())
  ipcMain.on('window:toggle-maximize', (event) => {
    const win = senderWindow(event)
    if (!win) return
    if (win.isMaximized()) {
      win.unmaximize()
    } else {
      win.maximize()
    }
  })
  ipcMain.on('window:close', (event) => senderWindow(event)?.close())
  ipcMain.handle(
    'window:is-maximized',
    (event) => senderWindow(event)?.isMaximized() ?? false
  )
}

async function createWindow(rendererUrl: string): Promise<BrowserWindow> {
  const isMac = process.platform === 'darwin'
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 620,
    title: 'RDST',
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    // Centered in the renderer's traffic-light strip, which sits inside the
    // shell's 8px window inset.
    trafficLightPosition: isMac ? { x: 22, y: 18 } : undefined,
    // Frameless on Linux; the renderer draws its own window controls in the
    // header and marks drag regions with -webkit-app-region.
    frame: isMac,
    transparent: isMac,
    // Matches --surface-layout-2 in the renderer's dark theme so there is no
    // flash before first paint.
    backgroundColor: isMac ? '#00000000' : '#171616',
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

  const sendMaximizedState = (maximized: boolean) => {
    if (!window.isDestroyed()) {
      window.webContents.send('window:maximized-changed', maximized)
    }
  }
  window.on('maximize', () => sendMaximizedState(true))
  window.on('unmaximize', () => sendMaximizedState(false))

  await window.loadURL(rendererUrl)

  applyGlassEffects(window)
  if (isMac) {
    window.setWindowButtonVisibility(true)
  }
  tuneGlassForFocus(window.isFocused())
  window.on('focus', () => tuneGlassForFocus(true))
  window.on('blur', () => tuneGlassForFocus(false))

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

  const liquidGlassResult = await loadLiquidGlass({
    platform: process.platform,
  })
  liquidGlass = liquidGlassResult.module
  liquidGlassFailureReason = liquidGlassResult.failureReason

  registerWindowControlHandlers()

  setupAutoUpdates({
    isPackaged: app.isPackaged,
    platform: process.platform,
    appImagePath: process.env.APPIMAGE,
  })

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
