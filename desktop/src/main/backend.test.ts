import type { spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'
import path from 'node:path'
import { describe, expect, it, vi } from 'vitest'

import {
  backendEnvironment,
  backendExecutablePath,
  terminateWindowsProcessTree,
} from './backend.js'

function entries(value: string | undefined): string[] {
  return value?.split(path.posix.delimiter) ?? []
}

describe('terminateWindowsProcessTree', () => {
  it('forcefully terminates the backend and all descendants', async () => {
    const taskkill = new EventEmitter()
    const spawnProcess = vi.fn(() => taskkill) as unknown as typeof spawn

    const termination = terminateWindowsProcessTree(1234, spawnProcess)
    taskkill.emit('exit', 0)
    await termination

    expect(spawnProcess).toHaveBeenCalledWith(
      'taskkill.exe',
      ['/PID', '1234', '/T', '/F'],
      { stdio: 'ignore', windowsHide: true }
    )
  })

  it('reports taskkill failures', async () => {
    const taskkill = new EventEmitter()
    const spawnProcess = vi.fn(() => taskkill) as unknown as typeof spawn

    const termination = terminateWindowsProcessTree(1234, spawnProcess)
    taskkill.emit('exit', 1)

    await expect(termination).rejects.toThrow('taskkill exited with code 1')
  })
})

describe('backendExecutablePath', () => {
  it('makes Docker Desktop discoverable from a minimal macOS GUI PATH', () => {
    expect(
      entries(backendExecutablePath('/usr/bin:/bin', 'darwin', '/Users/test'))
    ).toEqual([
      '/usr/bin',
      '/bin',
      '/usr/local/bin',
      '/Users/test/.docker/bin',
      '/opt/homebrew/bin',
      '/opt/local/bin',
      '/Applications/Docker.app/Contents/Resources/bin',
      '/Users/test/Applications/Docker.app/Contents/Resources/bin',
    ])
  })

  it('preserves custom entries and does not duplicate standard entries', () => {
    expect(
      entries(
        backendExecutablePath(
          '/custom/bin:/usr/local/bin:/custom/bin',
          'linux',
          '/home/test'
        )
      )
    ).toEqual([
      '/custom/bin',
      '/usr/local/bin',
      '/custom/bin',
      '/usr/bin',
      '/bin',
      '/home/test/.docker/bin',
    ])
  })

  it('leaves the Windows PATH untouched', () => {
    expect(backendExecutablePath('C:\\Windows;C:\\Docker', 'win32')).toBe(
      'C:\\Windows;C:\\Docker'
    )
  })
})

describe('backendEnvironment', () => {
  it('marks the sidecar as desktop-hosted', () => {
    const environment = backendEnvironment({ PATH: '/usr/bin', CUSTOM: 'kept' })

    expect(environment.RDST_DESKTOP).toBe('1')
    expect(environment.CUSTOM).toBe('kept')
  })
})
