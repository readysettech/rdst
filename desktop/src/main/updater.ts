import { readFileSync } from 'node:fs'
import path from 'node:path'

import { app, BrowserWindow, ipcMain, net } from 'electron'
import electronUpdater from 'electron-updater'

import {
  isNewerVersion,
  manualDownloadLinks,
  parseUpdateMetadata,
  resolveUpdateMode,
  UPDATE_FEED_URLS,
  type UpdateEnvironment,
  type UpdateStatePayload,
} from './update-policy.js'

// electron-updater is CommonJS; named ESM imports are unreliable, so pull
// autoUpdater off the default export.
const { autoUpdater } = electronUpdater

const INITIAL_CHECK_DELAY_MS = 15_000
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

let currentState: UpdateStatePayload | null = null

/**
 * Reads the rdstUpdates flag the package scripts bake into the packaged
 * app's metadata via electron-builder extraMetadata. Only merged main CI
 * builds set it to "enabled"; Gerrit preview and local packages leave the
 * updater off.
 */
function packagedUpdatesEnabled(): boolean {
  try {
    const packageJson = JSON.parse(
      readFileSync(path.join(app.getAppPath(), 'package.json'), 'utf8')
    ) as { rdstUpdates?: string }
    return packageJson.rdstUpdates === 'enabled'
  } catch {
    return false
  }
}

function broadcastState(state: UpdateStatePayload): void {
  currentState = state
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send('updates:state-changed', state)
  }
}

function scheduleChecks(check: () => void): void {
  setTimeout(check, INITIAL_CHECK_DELAY_MS).unref()
  setInterval(check, CHECK_INTERVAL_MS).unref()
}

/** In-place updates through electron-updater; macOS, Windows, and AppImage installs. */
function setupInPlaceUpdates(): void {
  let downloadingVersion: string | null = null
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.on('error', (error) => {
    console.warn('[rdst-desktop] update error:', error.message)
  })
  autoUpdater.on('update-available', (info) => {
    downloadingVersion = info.version
    broadcastState({
      status: 'available',
      version: info.version,
      downloadLinks: [],
    })
  })
  autoUpdater.on('download-progress', (progress) => {
    if (!downloadingVersion) return
    broadcastState({
      status: 'downloading',
      version: downloadingVersion,
      downloadLinks: [],
      progress: Math.min(100, Math.max(0, Math.round(progress.percent))),
    })
  })
  autoUpdater.on('update-downloaded', (info) => {
    downloadingVersion = null
    broadcastState({
      status: 'ready',
      version: info.version,
      downloadLinks: [],
      progress: 100,
    })
  })

  scheduleChecks(() => {
    autoUpdater.checkForUpdates().catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error)
      console.warn('[rdst-desktop] update check failed:', message)
    })
  })
}

/**
 * Notification-only updates: fetch the channel metadata directly and
 * surface download links. Used where in-place updates are unavailable:
 * deb/rpm installs.
 */
function setupUpdateNotifications(env: UpdateEnvironment): void {
  const feedUrl = UPDATE_FEED_URLS[env.platform]
  if (!feedUrl) return
  const channel =
    env.platform === 'darwin' ? 'latest-mac.yml' : 'latest-linux.yml'

  const check = async () => {
    const response = await net.fetch(`${feedUrl}/${channel}`, {
      cache: 'no-store',
    })
    if (!response.ok) {
      throw new Error(`fetching ${channel}: HTTP ${response.status}`)
    }
    const metadata = parseUpdateMetadata(await response.text())
    if (!metadata.version) {
      throw new Error(`no version in ${channel}`)
    }
    if (!isNewerVersion(app.getVersion(), metadata.version)) return
    broadcastState({
      status: 'available',
      version: metadata.version,
      downloadLinks: manualDownloadLinks({
        platform: env.platform,
        arch: process.arch,
        version: metadata.version,
        files: metadata.files,
      }),
    })
  }

  scheduleChecks(() => {
    check().catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error)
      console.warn('[rdst-desktop] update check failed:', message)
    })
  })
}

/**
 * Wires update delivery for packaged builds and exposes update state to
 * the renderer over updates:* IPC channels. The handlers are registered
 * even when updates are disabled so the preload bridge always works.
 */
export function setupAutoUpdates(
  options: Omit<UpdateEnvironment, 'updatesEnabled'>
): void {
  const env: UpdateEnvironment = {
    ...options,
    updatesEnabled: options.isPackaged && packagedUpdatesEnabled(),
  }
  const mode = resolveUpdateMode(env)

  ipcMain.handle('updates:get-state', () => currentState)
  ipcMain.on('updates:install', () => {
    if (mode === 'auto' && currentState?.status === 'ready') {
      autoUpdater.quitAndInstall()
    }
  })

  if (mode === 'auto') {
    setupInPlaceUpdates()
  } else if (mode === 'notify') {
    setupUpdateNotifications(env)
  }
}
