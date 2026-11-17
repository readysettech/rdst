import { afterEach, describe, expect, it, vi } from 'vitest'

const updater = vi.hoisted(() => ({
  autoUpdater: {
    on: vi.fn(),
    checkForUpdates: vi.fn(() => Promise.resolve()),
    autoDownload: false,
    autoInstallOnAppQuit: true,
  },
}))

vi.mock('node:fs', () => ({
  readFileSync: () => JSON.stringify({ rdstUpdates: 'enabled' }),
}))
vi.mock('electron', () => ({
  app: { getAppPath: () => '', getVersion: () => '0.1.800', on: vi.fn() },
  BrowserWindow: { getAllWindows: () => [] },
  ipcMain: { handle: vi.fn(), on: vi.fn() },
  autoUpdater: { on: vi.fn(), once: vi.fn() },
  net: { fetch: vi.fn() },
}))
vi.mock('electron-updater', () => ({ default: updater }))

afterEach(() => {
  vi.resetModules()
})

async function setUp(installOnQuit?: boolean) {
  // Neither value, so each test proves the updater set it.
  updater.autoUpdater.autoInstallOnAppQuit = undefined as unknown as boolean
  const { setupAutoUpdates } = await import('./updater.js')
  setupAutoUpdates(
    {
      isPackaged: true,
      platform: 'win32',
      appImagePath: undefined,
      packageName: 'rdst-desktop',
      installOnQuit,
    },
    vi.fn(),
    () => true
  )
}

describe('installing a downloaded update', () => {
  it('waits for the restart in a machine-wide Store install', async () => {
    await setUp(false)
    expect(updater.autoUpdater.autoInstallOnAppQuit).toBe(false)
  })

  it('installs on quit everywhere else', async () => {
    await setUp()
    expect(updater.autoUpdater.autoInstallOnAppQuit).toBe(true)
  })
})
