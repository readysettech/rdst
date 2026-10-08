import { execFile } from 'node:child_process'
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
  Notification,
  nativeImage,
  shell,
  Tray,
} from 'electron'

import { type BackendHandle, startBackend, stopBackend } from './backend.js'
import { BackendLog } from './backend-log.js'
import {
  BackendStartCancelled,
  type BackendStartState,
  startupTooltip,
  superviseBackendStart,
} from './backend-supervisor.js'
import { DesktopSettings } from './desktop-settings.js'
import {
  startInboxNotifications,
  systemNotificationOptions,
} from './inbox-notifications.js'
import {
  appId,
  dataDir,
  packageName,
  parseInstance,
  productName,
  urlScheme,
} from './instance.js'
import { getStartAtLogin, isHiddenLaunch, setStartAtLogin } from './login.js'
import {
  allPaused,
  applyPauseAction,
  listTrayDatabases,
  type PauseAction,
  pauseMenuModel,
  type TrayDatabase,
} from './monitor-controls.js'
import {
  detectNotificationBlock,
  healthFor,
  type NotificationHealth,
  watchNotificationHealth,
} from './notification-health.js'
import { checkPackagedWorkflow } from './packaged-smoke.js'
import { startupPageUrl } from './startup-page.js'
import { type StaticServerHandle, startStaticServer } from './static-server.js'
import {
  DesktopTelemetry,
  detectInstallChange,
  type EventProperties,
  errorClass,
  loadDirectTelemetryConfig,
  startFailureClass,
  telemetryDisabled,
} from './telemetry.js'
import { trayBitmap } from './tray-icons.js'
import {
  type TrayState,
  type TrayStatus,
  type TrayStatusWatcher,
  watchTrayStatus,
} from './tray-state.js'
import { setupAutoUpdates } from './updater.js'
import { hideWindowOnClose, stopBeforeQuit } from './window-lifecycle.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const RENDERER_DIR = path.resolve(__dirname, '../renderer')
const PACKAGED = packagedMetadata()
// A staging or change build's own identity; unpackaged development is production's.
const INSTANCE = app.isPackaged ? parseInstance(PACKAGED.instance) : ''
const OAUTH_PROTOCOL = urlScheme(INSTANCE, app.isPackaged)
// Windows toasts are attributed through the AppUserModelID on the Start menu shortcut, which
// the NSIS installer sets to the electron-builder appId; the process must claim the same one.
const APP_USER_MODEL_ID = appId(INSTANCE)
const PRODUCT_NAME = productName(INSTANCE)
const LOGIN_IDENTITY = { appId: APP_USER_MODEL_ID, productName: PRODUCT_NAME }
const DATA_DIR = dataDir(INSTANCE)

// CI smoke mode boots the full shell (sidecar backend, static server,
// renderer), records SMOKE_OK when requested, and exits.
const SMOKE_MODE = process.env.RDST_DESKTOP_SMOKE === '1'
const SMOKE_TIMEOUT_MS = 180_000
const LIFECYCLE_MODE = SMOKE_MODE && process.env.RDST_DESKTOP_LIFECYCLE === '1'

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
let tray: Tray | null = null
let backend: BackendHandle | null = null
// The process of a start still in progress, so a quit during startup stops it too.
let startingBackend: BackendHandle | null = null
let backendLog: BackendLog | null = null
let startState: BackendStartState = { phase: 'starting', attempt: 1 }
const startPhases: BackendStartState['phase'][] = []
let retryRequested: (() => void) | null = null
let webServer: StaticServerHandle | null = null
const quitState = { started: false, completed: false }
let cleanupPromise: Promise<void> | null = null
let deepLinkPending = false
// An inbox route clicked before the backend was ready, opened once it is.
let pendingInboxRoute: string | null = null
let stopInboxStream: (() => void) | null = null
let settings: DesktopSettings
let rendererUrl = ''
let starting = true
let trayMenu: Menu | null = null
// The databases the monitoring submenu lists; null until the backend answers.
let trayDatabases: TrayDatabase[] | null = null
let trayDatabasesLoading: Promise<void> | null = null
// Whether the OS will actually show RDST's notifications; null until the first check.
let notificationWatch: ReturnType<typeof watchNotificationHealth> | null = null
let notificationHealth: NotificationHealth | null = null
let lastToastFailed = false
// Test-only backend override; packaged builds never honor it.
const E2E_SOURCE_ROOT = app.isPackaged
  ? undefined
  : process.env.RDST_DESKTOP_E2E_SOURCE_ROOT
const E2E_MODE = Boolean(E2E_SOURCE_ROOT) || LIFECYCLE_MODE
const e2eNotifications: Notification[] = []
// Electron drops click handlers once a Notification is garbage collected, so keep
// shown toasts alive until they close (Windows keeps them in Action Center).
const liveNotifications = new Set<Notification>()
let lastInboxEventId = 0
let trayWatcher: TrayStatusWatcher | null = null
let trayStatus: TrayStatus = {
  state: 'offline',
  tooltip: 'RDST - starting',
  unreachable: [],
  actNow: 0,
}
const trayImages = new Map<TrayState, Electron.NativeImage>()
const launchedAt = Date.now()
// Product events, sent through the backend once it's up (see telemetry.ts).
let telemetry: DesktopTelemetry | null = null

function capture(event: string, properties: EventProperties = {}): void {
  try {
    telemetry?.capture(event, properties)
  } catch {
    // Telemetry never interferes with the app.
  }
}

function captureError(component: string, error: unknown, handled = true): void {
  capture('app_error', {
    component,
    error_class: errorClass(error),
    message: error instanceof Error ? error.message : '',
    handled,
  })
}

// Main-process crashes and stray rejections; the monitor listener keeps Electron's own handling.
process.on('uncaughtExceptionMonitor', (error) =>
  captureError('main', error, false)
)
process.on('unhandledRejection', (reason) => {
  console.error('[rdst-desktop] unhandled rejection:', reason)
  captureError('main', reason, false)
})

function getResourcesPath(): string {
  return app.isPackaged
    ? process.resourcesPath
    : path.resolve(__dirname, '../..')
}

async function resolveRendererUrl(): Promise<string> {
  const devRendererUrl = SMOKE_MODE
    ? undefined
    : process.env.ELECTRON_RENDERER_URL
  if (devRendererUrl && !E2E_SOURCE_ROOT) {
    return devRendererUrl
  }

  backendLog ??= new BackendLog(logsDirectory())
  const log = backendLog
  backend = await superviseBackendStart({
    start: ({ onPhase }) =>
      startBackend({
        resourcesPath: getResourcesPath(),
        sourceDir: E2E_SOURCE_ROOT,
        keyservice: PACKAGED.keyservice,
        identity: { instance: INSTANCE, protocol: OAUTH_PROTOCOL },
        log,
        onPhase,
        onSpawn: (handle) => {
          startingBackend = handle
        },
      }).finally(() => {
        startingBackend = null
      }),
    onState: showStartState,
    cancelled: () => quitState.started,
  })
  if (devRendererUrl) return devRendererUrl
  webServer = await startStaticServer({
    rootDir: RENDERER_DIR,
    apiBaseUrl: backend.apiBaseUrl,
    smokeAuth: SMOKE_MODE,
  })
  return webServer.url
}

/** The keyservice and instance a change or staging build was packaged for (CI bakes them in). */
function packagedMetadata(): { keyservice?: string; instance?: unknown } {
  try {
    const packageJson = JSON.parse(
      fs.readFileSync(path.join(app.getAppPath(), 'package.json'), 'utf8')
    ) as { rdstKeyservice?: string; rdstInstance?: unknown }
    return {
      keyservice: packageJson.rdstKeyservice || undefined,
      instance: packageJson.rdstInstance,
    }
  } catch {
    return {}
  }
}

function logsDirectory(): string {
  return app.getPath('logs')
}

function openLogsFolder(): void {
  const dir = logsDirectory()
  fs.mkdirSync(dir, { recursive: true })
  void shell.openPath(dir).then((failure) => {
    if (failure)
      dialog.showErrorBox('RDST logs', `Couldn't open ${dir}.\n\n${failure}`)
  })
}

/** Reflect a start in progress in the window (while it shows the startup page) and the tray. */
function showStartState(state: BackendStartState): void {
  startState = state
  if (state.phase === 'failed') {
    capture('desktop_startup_failed', {
      stage: startPhases.includes('migrating') ? 'migrate' : 'start',
      attempts: state.attempt,
      error_class: startFailureClass(state.error),
      message: state.error,
    })
  }
  if (startPhases[startPhases.length - 1] !== state.phase)
    startPhases.push(state.phase)
  backendLog?.write('shell', [
    `startup: ${state.phase} (attempt ${state.attempt})`,
  ])
  void trayWatcher?.refresh()
  if (state.phase === 'ready') return
  const window = mainWindow
  if (
    !window ||
    window.isDestroyed() ||
    !window.webContents.getURL().startsWith('data:')
  )
    return
  if (state.phase === 'failed') focusMainWindow()
  void window.loadURL(startupPageUrl(state)).catch((error) => {
    console.warn('[rdst-desktop] could not show startup state:', error)
  })
}

function waitForRetry(): Promise<void> {
  return new Promise((resolve) => {
    retryRequested = () => {
      retryRequested = null
      resolve()
    }
  })
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
  ipcMain.on('desktop:open-logs', openLogsFolder)
  ipcMain.on('desktop:retry-backend', () => retryRequested?.())
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
/** How often a held zoom key steps. */
const ZOOM_REPEAT_MS = 150

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
      // The window's input handler does the stepping; these only list the shortcuts.
      {
        role: 'zoomIn',
        accelerator: 'CommandOrControl+=',
        registerAccelerator: false,
      },
      {
        role: 'zoomOut',
        accelerator: 'CommandOrControl+-',
        registerAccelerator: false,
      },
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

async function createWindow(
  rendererUrl: string,
  startHidden = false
): Promise<BrowserWindow> {
  const isMac = process.platform === 'darwin'
  const window = new BrowserWindow({
    show: !startHidden,
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
  // Handle unshifted Ctrl+= as well as Ctrl++ before menu accelerators. A held key
  // repeats fast, so repeats step at most every ZOOM_REPEAT_MS.
  let lastZoomAt = 0
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
    const now = Date.now()
    if (input.isAutoRepeat && now - lastZoomAt < ZOOM_REPEAT_MS) return
    lastZoomAt = now
    const level = direction
      ? Math.max(
          -3,
          Math.min(5, window.webContents.getZoomLevel() + direction * 0.5)
        )
      : 0
    window.webContents.setZoomLevel(level)
  })

  mainWindow = window
  hideWindowOnClose(window, () => quitState.started || SMOKE_MODE)
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
  window.webContents.on('render-process-gone', (_event, details) => {
    if (details.reason !== 'clean-exit') {
      capture('app_error', {
        component: 'renderer',
        error_class: details.reason,
        handled: false,
      })
    }
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
    stopInboxStream?.()
    notificationWatch?.stop()
    stopInboxStream = null
    trayWatcher?.stop()
    trayWatcher = null
    tray?.destroy()
    tray = null
    const server = webServer
    webServer = null
    if (server) {
      await server.close().catch((error) => {
        console.warn('[rdst-desktop] failed to close web server:', error)
      })
    }

    const backendHandle = backend ?? startingBackend
    backend = null
    startingBackend = null
    retryRequested?.()
    await stopBackend(backendHandle)
    backendLog?.close()
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
  if (!mainWindow) {
    deepLinkPending = true
    if (rendererUrl && !starting) {
      void createWindow(rendererUrl).catch((error) => {
        console.error('[rdst-desktop] failed to create window:', error)
        captureError('window', error)
        dialog.showErrorBox(
          PRODUCT_NAME,
          `Unable to open the RDST window.\n\n${errorMessage(error)}`
        )
      })
    }
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

async function openInbox(route = '/inbox'): Promise<void> {
  if (starting || !rendererUrl) {
    pendingInboxRoute = route
    focusMainWindow()
    return
  }
  try {
    if (!mainWindow && rendererUrl) await createWindow(rendererUrl)
    focusMainWindow()
    if (mainWindow)
      await mainWindow.loadURL(new URL(route, rendererUrl).toString())
  } catch (error) {
    console.error('[rdst-desktop] failed to open inbox:', error)
    captureError('window', error)
    dialog.showErrorBox(
      PRODUCT_NAME,
      `Unable to open the inbox.\n\n${errorMessage(error)}`
    )
  }
}

function trayIconPath(): string {
  const name = process.platform === 'darwin' ? 'iconTemplate.png' : 'tray.png'
  return app.isPackaged
    ? path.join(process.resourcesPath, 'icons', name)
    : path.resolve(__dirname, '../../resources', name)
}

function trayImage(state: TrayState): Electron.NativeImage {
  const cached = trayImages.get(state)
  if (cached) return cached
  const base = nativeImage.createFromPath(trayIconPath())
  const template = process.platform === 'darwin'
  let image = base
  if (!base.isEmpty() && state !== 'running') {
    const { width, height } = base.getSize()
    const drawn = trayBitmap(
      { data: base.toBitmap(), width, height, template },
      state
    )
    image = nativeImage.createFromBitmap(drawn.data, { width, height })
    if (drawn.template) image.setTemplateImage(true)
  } else if (template) {
    image.setTemplateImage(true)
  }
  trayImages.set(state, image)
  return image
}

function applyTrayStatus(status: TrayStatus): void {
  const changedState = status.state !== trayStatus.state
  trayStatus = status
  if (!tray || tray.isDestroyed()) return
  if (changedState) tray.setImage(trayImage(status.state))
  tray.setToolTip(trayTooltip())
}

function trayTooltip(): string {
  const blocked = notificationHealth?.blocked
    ? notificationHealth.trayLabel?.replace(/\.\.\. Fix$/, '')
    : null
  return blocked ? `${trayStatus.tooltip}\n${blocked}` : trayStatus.tooltip
}

function runCommand(
  command: string,
  args: string[]
): Promise<{ code: number | null; stdout: string }> {
  return new Promise((resolve) => {
    execFile(
      command,
      args,
      { timeout: 5_000, windowsHide: true },
      (error, stdout) => {
        const code = error
          ? typeof error.code === 'number'
            ? error.code
            : 1
          : 0
        resolve({ code, stdout: String(stdout ?? '') })
      }
    )
  })
}

async function checkNotificationHealth(): Promise<NotificationHealth> {
  const reason = await detectNotificationBlock({
    platform: process.platform,
    run: runCommand,
    supported: Notification.isSupported(),
    lastToastFailed,
    appId: APP_USER_MODEL_ID,
  })
  return healthFor(process.platform, reason, Date.now())
}

function notificationHealthChanged(health: NotificationHealth): void {
  if (health.blocked && !notificationHealth?.blocked) {
    capture('os_notifications_disabled_detected', {
      reason: health.reason,
      platform: process.platform,
    })
  }
  notificationHealth = health
  if (tray && !tray.isDestroyed()) tray.setToolTip(trayTooltip())
  buildTrayMenu()
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send('desktop:notification-health', health)
  }
}

function openNotificationSettings(): void {
  const url =
    notificationHealth?.settingsUrl ??
    (process.platform === 'win32'
      ? 'ms-settings:notifications'
      : process.platform === 'darwin'
        ? 'x-apple.systempreferences:com.apple.preference.notifications'
        : null)
  if (url) void shell.openExternal(url)
  // Coming back from the settings page, check again soon.
  setTimeout(() => {
    void notificationWatch?.refresh()
  }, 15_000).unref?.()
}

/** Re-read which databases are monitored, then rebuild the menu with them. */
function loadTrayDatabases(): Promise<void> {
  if (trayDatabasesLoading) return trayDatabasesLoading
  if (!backend) return Promise.resolve()
  trayDatabasesLoading = listTrayDatabases(backend.apiBaseUrl)
    .then((databases) => {
      const changed =
        JSON.stringify(databases) !== JSON.stringify(trayDatabases)
      trayDatabases = databases
      if (changed) buildTrayMenu()
    })
    .finally(() => {
      trayDatabasesLoading = null
    })
  return trayDatabasesLoading
}

function refreshTrayDatabases(): void {
  void loadTrayDatabases().catch((error) =>
    console.warn('[rdst-desktop] could not list monitored databases:', error)
  )
}

function runPauseAction(action: PauseAction): void {
  if (!backend) return
  void applyPauseAction(backend.apiBaseUrl, action, trayDatabases ?? [])
    .catch((error) =>
      dialog.showErrorBox('RDST monitoring', errorMessage(error))
    )
    .finally(refreshTrayMenu)
}

function refreshTrayMenu(): void {
  if (!tray) return
  void trayWatcher?.refresh()
  buildTrayMenu()
  refreshTrayDatabases()
}

function buildTrayMenu(): void {
  if (!tray) return
  const login = startAtLoginStatus()
  const pauseItems = pauseMenuModel(trayDatabases).map(
    (item): Electron.MenuItemConstructorOptions =>
      item.separator
        ? { type: 'separator' }
        : {
            label: item.label,
            enabled: item.enabled && backend !== null,
            click: item.action
              ? () => runPauseAction(item.action as PauseAction)
              : undefined,
          }
  )
  trayMenu = Menu.buildFromTemplate([
    { label: 'Open RDST', click: focusMainWindow },
    {
      label: 'Inbox',
      click: () => {
        void openInbox()
      },
    },
    { type: 'separator' },
    {
      label: 'Monitoring',
      enabled: backend !== null,
      submenu: pauseItems,
    },
    { type: 'separator' },
    {
      label: login.note ? `Start at login (${login.note})` : 'Start at login',
      type: 'checkbox',
      checked: login.enabled,
      enabled: login.available,
      click: () => {
        try {
          configureStartAtLogin(!login.enabled)
          settings.markStartAtLoginConfigured()
          capture('start_at_login_changed', {
            enabled: !login.enabled,
            by: 'user',
          })
        } catch (error) {
          dialog.showErrorBox('Start at login', errorMessage(error))
        }
        refreshTrayMenu()
      },
    },
    {
      label: 'Notifications',
      type: 'checkbox',
      checked: settings.notificationsEnabled,
      click: () => {
        settings.setNotificationsEnabled(!settings.notificationsEnabled)
        refreshTrayMenu()
      },
    },
    ...(notificationHealth?.blocked && notificationHealth.trayLabel
      ? [
          {
            label: notificationHealth.trayLabel,
            enabled: notificationHealth.settingsUrl !== null,
            click: openNotificationSettings,
          },
        ]
      : []),
    {
      label: 'Send test notification',
      click: () => {
        showNotification(
          'RDST test notification',
          'Notifications from RDST show up like this.',
          focusMainWindow,
          false,
          'test'
        )
        setTimeout(() => {
          void notificationWatch?.refresh()
        }, 2_000).unref?.()
      },
    },
    { type: 'separator' },
    { label: 'Open logs folder', click: openLogsFolder },
    { label: 'Quit RDST', click: quitFromTray },
  ])
  // Opening the menu re-reads the databases, so ones turned on in the app show up.
  trayMenu.on('menu-will-show', refreshTrayDatabases)
  tray.setContextMenu(trayMenu)
}

/** Quit from the tray menu, after telling the backend (it stops with the app). */
function quitFromTray(): void {
  capture('tray_quit', {
    uptime_hours: Math.round((Date.now() - launchedAt) / 360_000) / 10,
    monitored_count: trayDatabases
      ? trayDatabases.filter((database) => database.enabled).length
      : null,
  })
  const flushed = telemetry?.flush() ?? Promise.resolve()
  void Promise.race([
    flushed,
    new Promise((resolve) => setTimeout(resolve, 1_500)),
  ]).finally(() => app.quit())
}

function createTray(): void {
  tray = new Tray(trayImage(trayStatus.state))
  tray.setToolTip(trayStatus.tooltip)
  tray.on('double-click', focusMainWindow)
  // Windows and most Linux panels open the window on a plain click; macOS shows the menu.
  if (process.platform !== 'darwin') tray.on('click', focusMainWindow)
  trayWatcher = watchTrayStatus({
    apiBaseUrl: () => backend?.apiBaseUrl ?? null,
    backendUp: () =>
      backend !== null &&
      backend.process.exitCode === null &&
      backend.process.signalCode === null,
    starting: () => starting,
    startingTooltip: () => startupTooltip(startState),
    paused: async () => {
      await loadTrayDatabases()
      return allPaused(trayDatabases)
    },
    onChange: applyTrayStatus,
  })
  refreshTrayMenu()
}

function showNotification(
  title: string,
  body: string,
  onClick: () => void,
  critical = false,
  kind = 'other'
): void {
  if (!Notification.isSupported() && !E2E_MODE) return
  const notification = new Notification(
    systemNotificationOptions({ title, body, critical }, process.platform)
  )
  const clicked = () => {
    capture('system_notification_clicked', { kind, critical })
    onClick()
  }
  notification.on('click', clicked)
  // macOS alert buttons: Open is the first action; Dismiss only closes.
  notification.on('action', (_event, index) => {
    if (index === 0) clicked()
  })
  notification.on('show', () => {
    lastToastFailed = false
  })
  notification.on('failed', () => {
    lastToastFailed = true
    void notificationWatch?.refresh()
  })
  if (E2E_MODE) e2eNotifications.push(notification)
  else {
    liveNotifications.add(notification)
    notification.on('close', () => liveNotifications.delete(notification))
    notification.show()
  }
  capture('system_notification_shown', { kind, critical })
}

// The sidecar exiting outside a quit leaves nothing watching the databases; say so once.
function watchBackendExit(handle: BackendHandle): void {
  handle.process.once('exit', (code, signal) => {
    if (quitState.started) return
    console.error(
      `[rdst-desktop] RDST backend exited (code=${code}, signal=${signal})`
    )
    telemetry?.setBackend(null)
    capture('app_error', {
      component: 'backend_exit',
      error_class: signal ?? `exit_${code ?? 'unknown'}`,
      handled: false,
    })
    void trayWatcher?.refresh()
    if (settings.notificationsEnabled) {
      showNotification(
        'RDST stopped monitoring',
        "RDST's background service stopped, so it isn't watching your databases. " +
          'Quit RDST from the tray and open it again to restart it.',
        focusMainWindow,
        true,
        'backend_stopped'
      )
    }
  })
}

/** desktop_installed on a new install's first launch, desktop_upgraded after an update. */
function recordInstallChange(userData: string, settingsExisted: boolean): void {
  if (!telemetry || SMOKE_MODE || !app.isPackaged) return
  const change = detectInstallChange({
    markerFile: path.join(userData, 'telemetry-install.json'),
    version: app.getVersion(),
    settingsExisted,
    rdstDataExists: fs.existsSync(path.join(DATA_DIR, 'config.toml')),
  })
  if (change.kind === 'installed') {
    capture('desktop_installed', {
      os: process.platform,
      os_version: os.release(),
      arch: process.arch,
      install_kind: change.installKind,
    })
  } else if (change.kind === 'upgraded') {
    capture('desktop_upgraded', {
      from_version: change.fromVersion,
      to_version: change.toVersion,
    })
  }
}

function configureStartAtLogin(
  enabled: boolean
): ReturnType<typeof setStartAtLogin> {
  return setStartAtLogin(
    app,
    enabled,
    process.platform,
    process.env,
    process.execPath,
    app.isPackaged ? [] : [app.getAppPath()],
    LOGIN_IDENTITY
  )
}

function startAtLoginStatus(): ReturnType<typeof getStartAtLogin> {
  return getStartAtLogin(app, process.platform, process.env, LOGIN_IDENTITY)
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
  await checkPackagedWorkflow(window, rendererUrl)
  const title = await window.webContents.executeJavaScript('document.title')
  const result = `SMOKE_OK workflow=ok title=${String(title)}`
  const markerPath = process.env.RDST_DESKTOP_SMOKE_MARKER
  if (markerPath) fs.writeFileSync(markerPath, `${result}\n`, 'utf8')
  console.log(result)
  window.destroy()
  exitSmoke(0)
}

/** Start the backend; after the retries run out, wait for "Try again" and start over. */
async function startBackendUntilReady(): Promise<string> {
  for (;;) {
    try {
      return await resolveRendererUrl()
    } catch (error) {
      if (SMOKE_MODE || quitState.started || startState.phase !== 'failed')
        throw error
      await waitForRetry()
      if (quitState.started) throw new BackendStartCancelled()
    }
  }
}

async function startApplication(): Promise<void> {
  if (SMOKE_MODE && !LIFECYCLE_MODE) {
    // Self-imposed deadline so a hung boot cannot wedge a CI step.
    setTimeout(() => {
      console.error(
        '[rdst-desktop] smoke: timed out waiting for shell readiness'
      )
      exitSmoke(2)
    }, SMOKE_TIMEOUT_MS)
  }

  telemetry = new DesktopTelemetry(
    path.join(app.getPath('userData'), 'telemetry-queue.json'),
    telemetryDisabled(process.env, app.isPackaged),
    undefined,
    loadDirectTelemetryConfig({
      env: process.env,
      packaged: app.isPackaged,
      appVersion: app.getVersion(),
      dataDir: DATA_DIR,
    })
  )

  await app.whenReady()
  smokeLog('electron ready')
  if (process.platform === 'win32') {
    app.setAppUserModelId(app.isPackaged ? APP_USER_MODEL_ID : process.execPath)
  }

  registerWindowControlHandlers()
  const userData = app.getPath('userData')
  const settingsExisted = fs.existsSync(
    path.join(userData, 'desktop-settings.json')
  )
  settings = new DesktopSettings(userData)

  recordInstallChange(userData, settingsExisted)
  if (
    app.isPackaged &&
    !SMOKE_MODE &&
    !settings.startAtLoginConfigured &&
    startAtLoginStatus().available
  ) {
    try {
      configureStartAtLogin(true)
      settings.markStartAtLoginConfigured()
      capture('start_at_login_changed', { enabled: true, by: 'default' })
    } catch (error) {
      console.warn('[rdst-desktop] failed to enable start at login:', error)
    }
  }
  ipcMain.handle(
    'desktop:get-notification-health',
    async () =>
      notificationHealth ?? (await notificationWatch?.refresh()) ?? null
  )
  ipcMain.on('desktop:open-notification-settings', openNotificationSettings)
  ipcMain.handle('desktop:get-start-at-login', startAtLoginStatus)
  ipcMain.handle('desktop:set-start-at-login', (_event, enabled: boolean) => {
    const result = configureStartAtLogin(enabled)
    settings.markStartAtLoginConfigured()
    capture('start_at_login_changed', { enabled: result.enabled, by: 'user' })
    refreshTrayMenu()
    return result
  })
  ipcMain.handle(
    'desktop:get-notifications-enabled',
    () => settings.notificationsEnabled
  )
  ipcMain.handle(
    'desktop:set-notifications-enabled',
    (_event, enabled: boolean) => {
      settings.setNotificationsEnabled(enabled)
      refreshTrayMenu()
      return settings.notificationsEnabled
    }
  )
  installApplicationMenu()

  // Smoke builds expose update IPC with delivery disabled.
  if (SMOKE_MODE) {
    ipcMain.handle('updates:get-state', () => null)
  } else {
    setupAutoUpdates({
      isPackaged: app.isPackaged,
      platform: process.platform,
      appImagePath: process.env.APPIMAGE,
      packageName: packageName(INSTANCE),
    })
  }

  try {
    // The tray and window come up first, so a long data upgrade shows what it's doing.
    const hidden = !SMOKE_MODE && isHiddenLaunch()
    // The backend's web_started event tells an OS-login launch from a person opening RDST.
    process.env.RDST_DESKTOP_LAUNCHED_BY = hidden ? 'login' : 'user'
    process.env.RDST_DESKTOP_VERSION = app.getVersion()
    if (!SMOKE_MODE || LIFECYCLE_MODE) createTray()
    await createWindow(startupPageUrl(startState), hidden)
    rendererUrl = await startBackendUntilReady()
    smokeLog('backend and static server ready')

    const window = mainWindow ?? (await createWindow(rendererUrl, hidden))
    if (!window.webContents.getURL().startsWith(rendererUrl))
      await window.loadURL(rendererUrl)
    starting = false
    if (pendingInboxRoute) {
      const route = pendingInboxRoute
      pendingInboxRoute = null
      await openInbox(route)
    }
    smokeLog('window loaded')

    if (SMOKE_MODE && !LIFECYCLE_MODE) {
      await runSmokeCheck(window)
      return
    }

    refreshTrayMenu()
    notificationWatch = watchNotificationHealth({
      check: checkNotificationHealth,
      onChange: notificationHealthChanged,
    })
    // The first toast registers RDST in the OS notification list (and asks on macOS).
    if (!settings.welcomeNotificationSent && settings.notificationsEnabled) {
      showNotification(
        'RDST is running',
        "It'll let you know if a database needs you.",
        focusMainWindow,
        false,
        'welcome'
      )
      settings.markWelcomeNotificationSent()
    }
    if (backend) {
      telemetry?.setBackend(backend.apiBaseUrl)
      watchBackendExit(backend)
      stopInboxStream = startInboxNotifications({
        apiBaseUrl: backend.apiBaseUrl,
        enabled: () => settings.notificationsEnabled,
        onEvent: (id) => {
          lastInboxEventId = id
        },
        onChange: () => {
          void trayWatcher?.refresh()
        },
        notify: ({ title, body, path: route, critical, connection }) => {
          const kind = connection
            ? critical
              ? 'connection_lost'
              : 'connection_restored'
            : 'inbox_item'
          showNotification(
            title,
            body,
            () => {
              void openInbox(route)
            },
            critical,
            kind
          )
        },
      })
    }

    if (E2E_MODE) {
      Object.assign(globalThis, {
        __rdstDesktopE2E: {
          snapshot: () => ({
            backendPid: backend?.process.pid,
            backendLog: backendLog?.file ?? null,
            startPhases: [...startPhases],
            backendUrl: backend?.apiBaseUrl,
            windowVisible: mainWindow?.isVisible(),
            trayExists: tray !== null && !tray.isDestroyed(),
            trayTooltip: tray ? trayStatus.tooltip : null,
            trayState: tray ? trayStatus.state : null,
            menuItems: trayMenu?.items
              .filter((item) => item.type !== 'separator')
              .map((item) => ({
                label: item.label,
                checked: item.checked,
                enabled: item.enabled,
              })),
            notifications: e2eNotifications.map((item) => ({
              title: item.title,
              body: item.body,
              isElectronNotification: item instanceof Notification,
            })),
            lastInboxEventId,
          }),
          clickMenu: (label: string) => {
            const items = trayMenu?.items.flatMap((entry) => [
              entry,
              ...(entry.submenu?.items ?? []),
            ])
            const item = items?.find((entry) => entry.label === label)
            if (!item?.click) throw new Error(`Missing tray item: ${label}`)
            ;(item.click as () => void)()
          },
          clickNotification: (index: number) =>
            e2eNotifications[index]?.emit('click'),
          refreshTray: () => {
            loadTrayDatabases()
            return trayWatcher?.refresh()
          },
        },
      })
    }

    app.on('activate', focusMainWindow)
  } catch (error) {
    if (error instanceof BackendStartCancelled) return
    console.error('[rdst-desktop] failed to start:', error)
    capture('desktop_startup_failed', {
      stage: backend ? 'window' : 'shell',
      error_class: errorClass(error),
      message: errorMessage(error),
    })
    await cleanup()
    if (SMOKE_MODE) {
      app.exit(1)
      return
    }
    dialog.showErrorBox(`${PRODUCT_NAME} failed to start`, errorMessage(error))
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
    stopBeforeQuit(event, quitState, cleanup, () => app.quit())
  })

  app.on('window-all-closed', () => {})

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
  console.error(
    `Another ${PRODUCT_NAME} instance already holds the single-instance lock; quitting.`
  )
  app.quit()
}
