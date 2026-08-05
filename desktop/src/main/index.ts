import fs from 'node:fs'
import os from 'node:os'
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

// CI smoke mode boots the full shell (sidecar backend, static server,
// renderer), records SMOKE_OK when requested, and exits.
const SMOKE_MODE = process.env.RDST_DESKTOP_SMOKE === '1'
const SMOKE_TIMEOUT_MS = 120_000

let mainWindow: BrowserWindow | null = null
let backend: BackendHandle | null = null
let webServer: StaticServerHandle | null = null
let quitCleanupStarted = false
let cleanupPromise: Promise<void> | null = null
let liquidGlass: GlassModule | null = null
let liquidGlassFailureReason: string | null = null
let glassViewID: number | null = null
let lastGlassFocusState: boolean | null = null
let deepLinkPending = false

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
  const senderWindow = (
    event: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent
  ) => BrowserWindow.fromWebContents(event.sender)

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
  ipcMain.handle('oauth:register-protocol', () =>
    app.setAsDefaultProtocolClient('rdst')
  )
  ipcMain.handle('ssh:select-key', async (event) => {
    const sshDirectory = path.join(os.homedir(), '.ssh')
    const options: Electron.OpenDialogOptions = {
      title: 'Select SSH private key',
      defaultPath: fs.existsSync(sshDirectory) ? sshDirectory : os.homedir(),
      // SSH keys live in ~/.ssh, which macOS and Linux dialogs hide by default.
      properties: ['openFile', 'showHiddenFiles'],
    }
    const win = senderWindow(event)
    const result = win
      ? await dialog.showOpenDialog(win, options)
      : await dialog.showOpenDialog(options)
    return result.canceled ? null : (result.filePaths[0] ?? null)
  })
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
  if (deepLinkPending) {
    deepLinkPending = false
    focusMainWindow()
  }

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

  if (SMOKE_MODE) {
    window.webContents.on('render-process-gone', (_event, details) => {
      console.error(
        `[rdst-desktop] smoke: renderer process gone: ${details.reason}`
      )
      exitSmoke(1)
    })
  }

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

// Boot-stage breadcrumbs so a CI hang pinpoints the stalled phase.
function smokeLog(stage: string): void {
  if (SMOKE_MODE) console.log(`[rdst-desktop] smoke: ${stage}`)
}

function focusMainWindow(): void {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

function handleDeepLink(url: string): void {
  if (!url.startsWith('rdst://')) return
  if (!mainWindow) deepLinkPending = true
  focusMainWindow()
}

// Stop the backend before exiting so smoke failures never orphan the
// sidecar; if teardown itself wedges, force the exit after a grace period.
function exitSmoke(code: number): void {
  const forceExit = setTimeout(() => app.exit(code), 10_000)
  void cleanup().finally(() => {
    clearTimeout(forceExit)
    app.exit(code)
  })
}

async function runSmokeCheck(window: BrowserWindow): Promise<void> {
  const title = await window.webContents.executeJavaScript('document.title')
  const result = `SMOKE_OK title=${String(title)}`
  const markerPath = process.env.RDST_DESKTOP_SMOKE_MARKER
  if (markerPath) fs.writeFileSync(markerPath, `${result}\n`, 'utf8')
  console.log(result)
  window.destroy()
  exitSmoke(0)
}

async function startApplication(): Promise<void> {
  if (SMOKE_MODE) {
    // Self-imposed deadline so a hung boot cannot wedge a CI step.
    setTimeout(() => {
      console.error(
        '[rdst-desktop] smoke: timed out waiting for shell readiness'
      )
      exitSmoke(2)
    }, SMOKE_TIMEOUT_MS)
  }

  await app.whenReady()
  smokeLog('electron ready')

  const liquidGlassResult = await loadLiquidGlass({
    platform: process.platform,
  })
  liquidGlass = liquidGlassResult.module
  liquidGlassFailureReason = liquidGlassResult.failureReason
  smokeLog('liquid glass loaded')

  registerWindowControlHandlers()

  // No updater in smoke mode: main-build packages ship with updates
  // enabled, and an update check against the production feed mid-smoke
  // would make the run nondeterministic.
  if (!SMOKE_MODE) {
    setupAutoUpdates({
      isPackaged: app.isPackaged,
      platform: process.platform,
      appImagePath: process.env.APPIMAGE,
    })
  }

  try {
    const rendererUrl = await resolveRendererUrl()
    smokeLog('backend and static server ready')

    const window = await createWindow(rendererUrl)
    smokeLog('window loaded')

    if (SMOKE_MODE) {
      await runSmokeCheck(window)
      return
    }

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
    if (SMOKE_MODE) {
      app.exit(1)
      return
    }
    dialog.showErrorBox('RDST Desktop failed to start', errorMessage(error))
    app.quit()
  }
}

function configurePrimaryInstance(): void {
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleDeepLink(url)
  })
  app.on('second-instance', (_event, argv) => {
    const deepLink = argv.find((value) => value.startsWith('rdst://'))
    if (deepLink) handleDeepLink(deepLink)
    else focusMainWindow()
  })

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

if (SMOKE_MODE) {
  // Throwaway state dir: keeps shared CI agents clean and scopes the
  // single-instance lock so stale processes cannot short-circuit the run.
  app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-smoke-')))
  // The sidecar inherits this env: CI launches must not emit telemetry.
  process.env.RDST_TELEMETRY = 'off'
}

if (app.requestSingleInstanceLock()) {
  configurePrimaryInstance()
} else {
  app.quit()
}
