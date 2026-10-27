import { describe, expect, it, vi } from 'vitest'

const electron = vi.hoisted(() => ({
  handlers: new Map<string, (...args: unknown[]) => unknown>(),
  listeners: new Map<string, (...args: unknown[]) => unknown>(),
}))

vi.mock('electron', () => ({
  app: {
    getAppPath: () => '',
    getVersion: () => '0.1.800',
    exit: vi.fn(),
    on: vi.fn(),
  },
  BrowserWindow: { getAllWindows: () => [] },
  ipcMain: {
    handle: (channel: string, handler: (...args: unknown[]) => unknown) =>
      electron.handlers.set(channel, handler),
    on: (channel: string, listener: (...args: unknown[]) => unknown) =>
      electron.listeners.set(channel, listener),
  },
  autoUpdater: { on: vi.fn(), once: vi.fn() },
  net: { fetch: vi.fn() },
}))

vi.mock('electron-updater', () => ({
  default: { autoUpdater: { on: vi.fn() } },
}))

describe('update IPC', () => {
  it('asks the sender check about every update message', async () => {
    const { setupAutoUpdates } = await import('./updater.js')
    const trustedSender = vi.fn(() => false)
    const beforeInstall = vi.fn()
    setupAutoUpdates(
      {
        isPackaged: false,
        platform: 'linux',
        appImagePath: undefined,
        packageName: 'rdst-desktop',
      },
      beforeInstall,
      trustedSender
    )

    const event = { senderFrame: { url: 'https://attacker.example/' } }
    expect(electron.handlers.get('updates:get-state')?.(event)).toBeNull()
    electron.listeners.get('updates:install')?.(event)

    expect(trustedSender).toHaveBeenCalledTimes(2)
    expect(trustedSender).toHaveBeenCalledWith(event)
    expect(beforeInstall).not.toHaveBeenCalled()
  })
})
