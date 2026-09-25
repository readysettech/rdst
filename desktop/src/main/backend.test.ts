import type { spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterAll, describe, expect, it, vi } from 'vitest'

import {
  type BackendHandle,
  backendEnvironment,
  backendExecutablePath,
  parseStartupMarker,
  StartupWatch,
  startBackend,
  stopBackend,
  terminateWindowsProcessTree,
} from './backend.js'
import { BackendLog } from './backend-log.js'

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

describe('startup markers', () => {
  it("reads the backend's RDST_STARTUP lines and nothing else", () => {
    expect(
      parseStartupMarker(
        'RDST_STARTUP {"state": "migrating", "store": "library.db"}'
      )
    ).toEqual({ state: 'migrating', store: 'library.db' })
    expect(parseStartupMarker('INFO:     Started server process')).toBeNull()
    expect(parseStartupMarker('RDST_STARTUP not json')).toBeNull()
  })

  it('never times out while a migration is reported', () => {
    let now = 0
    const watch = new StartupWatch(1_000, () => now)
    now = 900
    expect(watch.apply({ state: 'migrating', store: 'library.db' })).toBe(
      'migrating'
    )
    now = 60_000
    expect(watch.timedOut()).toBe(false)
    expect(
      watch.apply({ state: 'migrated', store: 'library.db', pending: 1 })
    ).toBeNull()
    expect(watch.migrating).toBe(true)
    expect(
      watch.apply({ state: 'migrated', store: 'cache.db', pending: 0 })
    ).toBe('starting')
    now = 60_500
    expect(watch.timedOut()).toBe(false)
    now = 61_001
    expect(watch.timedOut()).toBe(true)
  })
})

// A stand-in for `rdst web`: prints markers, optionally migrates for a while, then serves
// /api/init/status. Modes: serve, migrate, hang, crash.
const FAKE_BACKEND = `
import http from 'node:http'
const [mode, ms, ...rest] = process.argv.slice(2)
const arg = (name) => rest[rest.indexOf(name) + 1]
const say = (marker) => console.log('RDST_STARTUP ' + JSON.stringify(marker))
const serve = () => http.createServer((_req, res) => { res.writeHead(200); res.end('{}') })
  .listen(Number(arg('--port')), arg('--host'))
say({ state: 'starting' })
if (mode === 'crash') { console.error('boom'); process.exit(3) }
if (mode === 'hang') setInterval(() => {}, 1000)
if (mode === 'serve') serve()
if (mode === 'migrate') {
  say({ state: 'migrating', store: 'library.db' })
  setTimeout(() => { say({ state: 'migrated', store: 'library.db', pending: 0 }); serve() }, Number(ms))
  setInterval(() => {}, 1000)
}
`
const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-backend-test-'))
const fakeBackend = path.join(tempDir, 'fake-backend.mjs')
fs.writeFileSync(fakeBackend, FAKE_BACKEND)
afterAll(() => fs.rmSync(tempDir, { recursive: true, force: true }))

function fake(mode: string, ms = 0) {
  return { file: process.execPath, args: [fakeBackend, mode, String(ms)] }
}

describe('startBackend', () => {
  it('waits for a migration that outlasts the startup timeout, then is ready', async () => {
    const phases: string[] = []
    const log = new BackendLog(path.join(tempDir, 'logs-migrate'))
    const handle = await startBackend({
      resourcesPath: tempDir,
      command: fake('migrate', 1_500),
      startupTimeoutMs: 400,
      log,
      onPhase: (phase) => phases.push(phase),
    })
    try {
      expect(phases).toEqual(['migrating', 'starting'])
      expect(handle.migrating()).toBe(false)
      expect(handle.process.exitCode).toBeNull()
    } finally {
      await stopBackend(handle)
      log.close()
    }
    const written = fs.readFileSync(log.file, 'utf8')
    expect(written).toContain(
      '[stdout] RDST_STARTUP {"state":"migrating","store":"library.db"}'
    )
    expect(written).toContain('[shell] backend is ready')
  }, 15_000)

  it('stops a start that makes no progress and reports the timeout', async () => {
    let spawned: BackendHandle | null = null
    await expect(
      startBackend({
        resourcesPath: tempDir,
        command: fake('hang'),
        startupTimeoutMs: 500,
        onSpawn: (handle) => {
          spawned = handle
        },
      })
    ).rejects.toThrow('Timed out waiting for RDST backend')
    expect(
      spawned!.process.exitCode !== null || spawned!.process.signalCode !== null
    ).toBe(true)
  }, 15_000)

  it('reports a crash with the process output', async () => {
    await expect(
      startBackend({ resourcesPath: tempDir, command: fake('crash') })
    ).rejects.toThrow(/exited before it became ready \(code=3[\s\S]*boom/)
  }, 15_000)

  it('lets a migration finish before stopping the process on quit', async () => {
    let spawned: BackendHandle | null = null
    let migratingWhenStopped: boolean | null = null
    const started = startBackend({
      resourcesPath: tempDir,
      command: fake('migrate', 800),
      onSpawn: (handle) => {
        spawned = handle
      },
      onPhase: (phase) => {
        if (phase !== 'migrating') return
        const handle = spawned!
        void stopBackend(handle, 10_000).then(() => {
          migratingWhenStopped = handle.migrating()
        })
      },
    })
    const handle = await started.catch(() => spawned!)
    await vi.waitFor(() => expect(migratingWhenStopped).not.toBeNull(), {
      timeout: 10_000,
    })
    expect(migratingWhenStopped).toBe(false)
    await stopBackend(handle)
  }, 15_000)
})

describe('backendEnvironment keyservice', () => {
  it('points a change build at its packaged keyservice unless one is set', () => {
    const staging = 'https://keyservice-staging.readyset.io'
    expect(backendEnvironment({}, staging).RDST_KEYSERVICE_URL).toBe(staging)
    expect(
      backendEnvironment({ RDST_KEYSERVICE_URL: 'https://x.test' }, staging)
        .RDST_KEYSERVICE_URL
    ).toBe('https://x.test')
    expect(backendEnvironment({}).RDST_KEYSERVICE_URL).toBeUndefined()
  })
})
