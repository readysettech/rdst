import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  type MenuItemConstructorOptions,
  shell,
} from 'electron'

import { type BackendHandle, startBackend, stopBackend } from './backend.js'
import { type StaticServerHandle, startStaticServer } from './static-server.js'
import { setupAutoUpdates } from './updater.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const RENDERER_DIR = path.resolve(__dirname, '../renderer')
const OAUTH_PROTOCOL = app.isPackaged ? 'rdst' : 'rdst-dev'

// CI smoke mode boots the full shell (sidecar backend, static server,
// renderer), records SMOKE_OK when requested, and exits.
const SMOKE_MODE = process.env.RDST_DESKTOP_SMOKE === '1'
const SMOKE_TIMEOUT_MS = 120_000

// Development and an installed build may run together. Keep Electron's
// profile and single-instance lock separate, just as their OAuth protocols are
// separate; RDST's own ~/.rdst configuration remains shared intentionally.
if (!app.isPackaged && !SMOKE_MODE) {
  app.setPath(
    'userData',
    path.join(app.getPath('appData'), `${app.getName()}-dev`)
  )
}

let mainWindow: BrowserWindow | null = null
let backend: BackendHandle | null = null
let webServer: StaticServerHandle | null = null
let quitCleanupStarted = false
let cleanupPromise: Promise<void> | null = null
let deepLinkPending = false

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
  ipcMain.handle('oauth:register-protocol', () => {
    if (process.defaultApp && process.argv[1]) {
      return app.setAsDefaultProtocolClient(OAUTH_PROTOCOL, process.execPath, [
        path.resolve(process.argv[1]),
      ])
    }
    return app.setAsDefaultProtocolClient(OAUTH_PROTOCOL)
  })
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

function isLocalRendererUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost'
  } catch {
    return false
  }
}

// The renderer is laid out for the browser at 100%; the desktop shell shows
// it two zoom steps smaller so more of a page fits without scrolling. A
// person's own zoom changes (Cmd/Ctrl +/-) persist per origin on top of this.
const DEFAULT_ZOOM_FACTOR = 0.8

// Electron's stock View menu resets zoom to 100 percent. This shell's actual
// size is the default factor above, so Actual Size returns there while Zoom
// In and Zoom Out keep stepping from wherever the person has taken it.
function installApplicationMenu(): void {
  const isMac = process.platform === 'darwin'
  const view: MenuItemConstructorOptions = {
    label: 'View',
    submenu: [
      { role: 'reload' },
      { role: 'forceReload' },
      { role: 'toggleDevTools' },
      { type: 'separator' },
      {
        label: 'Actual Size',
        accelerator: 'CommandOrControl+0',
        click: (_item, window) => {
          const target =
            window instanceof BrowserWindow ? window : (mainWindow ?? null)
          target?.webContents.setZoomFactor(DEFAULT_ZOOM_FACTOR)
        },
      },
      { role: 'zoomIn' },
      { role: 'zoomOut' },
      { type: 'separator' },
      { role: 'togglefullscreen' },
    ],
  }
  const template: MenuItemConstructorOptions[] = [
    ...(isMac ? [{ role: 'appMenu' } as MenuItemConstructorOptions] : []),
    { role: 'fileMenu' },
    { role: 'editMenu' },
    view,
    { role: 'windowMenu' },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
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
    // Centered in the renderer's dedicated traffic-light strip.
    trafficLightPosition: isMac ? { x: 22, y: 18 } : undefined,
    // Frameless on Linux; the renderer draws its own window controls in the
    // header and marks drag regions with -webkit-app-region.
    frame: isMac,
    transparent: false,
    // Matches --surface-layout-2 in the renderer's dark theme so there is no
    // flash before first paint.
    backgroundColor: '#171616',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      zoomFactor: DEFAULT_ZOOM_FACTOR,
    },
  })
  // Handle unshifted Ctrl+= as well as Ctrl++ before menu accelerators.
  window.webContents.on('before-input-event', (event, input) => {
    const modifier = isMac ? input.meta : input.control
    if (input.type !== 'keyDown' || !modifier || input.alt) return
    const direction =
      input.key === '=' || input.key === '+'
        ? 1
        : input.key === '-' || input.key === '_'
          ? -1
          : 0
    if (!direction && input.key !== '0') return
    event.preventDefault()
    const level = direction
      ? Math.max(
          -3,
          Math.min(5, window.webContents.getZoomLevel() + direction * 0.5)
        )
      : 0
    window.webContents.setZoomLevel(level)
  })

  mainWindow = window
  if (deepLinkPending) {
    deepLinkPending = false
    focusMainWindow()
  }

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isLocalRendererUrl(url)) {
      return { action: 'allow' }
    }
    void shell.openExternal(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (isLocalRendererUrl(url)) return
    event.preventDefault()
    void shell.openExternal(url)
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

  if (isMac) {
    window.setWindowButtonVisibility(true)
  }

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
  if (!url.startsWith(`${OAUTH_PROTOCOL}://`)) return
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

  registerWindowControlHandlers()
  installApplicationMenu()

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
    const deepLink = argv.find((value) =>
      value.startsWith(`${OAUTH_PROTOCOL}://`)
    )
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
  app.on('second-instance', () => {
    const win = BrowserWindow.getAllWindows()[0]
    if (win) {
      if (win.isMinimized()) win.restore()
      win.focus()
    }
  })
  configurePrimaryInstance()
} else {
  console.error(
    'Another RDST Desktop instance already holds the single-instance lock; quitting.'
  )
  app.quit()
}
