import type { spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { afterAll, describe, expect, it, vi } from 'vitest'

import {
  type BackendHandle,
  backendEnvironment,
  backendExecutablePath,
  discoveryFilePath,
  findAvailablePort,
  PREFERRED_PORT,
  parseStartupMarker,
  preferredPort,
  removeDiscoveryFile,
  reportsListening,
  StartupWatch,
  startBackend,
  stopBackend,
  TOKEN_HEADER,
  terminateWindowsProcessTree,
  tokenFetch,
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

describe('discovery file', () => {
  it('lives in the data directory the backend environment selects', () => {
    expect(discoveryFilePath({}, '/home/u')).toBe(
      path.join('/home/u', '.rdst', 'desktop-server.json')
    )
    expect(discoveryFilePath({ RDST_INSTANCE: 'staging' }, '/home/u')).toBe(
      path.join('/home/u', '.rdst-staging', 'desktop-server.json')
    )
    expect(discoveryFilePath({ RDST_DATA_DIR: '~/data' }, '/home/u')).toBe(
      path.join('/home/u/data', 'desktop-server.json')
    )
  })

  it("removes the stopped backend's file and leaves a newer one", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-discovery-'))
    const file = path.join(dir, 'desktop-server.json')
    fs.writeFileSync(file, JSON.stringify({ port: 4100 }))
    removeDiscoveryFile(file, 4200)
    expect(fs.existsSync(file)).toBe(true)
    removeDiscoveryFile(file, 4100)
    expect(fs.existsSync(file)).toBe(false)
    expect(() => removeDiscoveryFile(file, 4100)).not.toThrow()
  })
})

describe('backend port', () => {
  it('prefers a fixed port per build so agent registrations survive restarts', () => {
    expect(preferredPort()).toBe(PREFERRED_PORT)
    expect(preferredPort('')).toBe(PREFERRED_PORT)
    expect(preferredPort('staging')).toBe(preferredPort('staging'))
    expect(preferredPort('staging')).not.toBe(PREFERRED_PORT)
  })

  it('uses the preferred port when free and another free port when taken', async () => {
    const preferred = await findAvailablePort('127.0.0.1')
    expect(await findAvailablePort('127.0.0.1', preferred)).toBe(preferred)

    const holder = net.createServer()
    await new Promise<void>((resolve) =>
      holder.listen(preferred, '127.0.0.1', resolve)
    )
    try {
      const fallback = await findAvailablePort('127.0.0.1', preferred)
      expect(fallback).not.toBe(preferred)
      expect(fallback).toBeGreaterThan(0)
    } finally {
      await new Promise((resolve) => holder.close(resolve))
    }
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
    expect(environment.RDST_HOSTED_REPORTS).toBe('disabled')
    expect(environment.CUSTOM).toBe('kept')
  })

  it('requires a distribution to explicitly enable hosted report uploads', () => {
    const environment = backendEnvironment(
      { PATH: '/usr/bin' },
      undefined,
      undefined,
      {
        consent: 'opt_out',
        hostedReports: true,
      }
    )

    expect(environment.RDST_HOSTED_REPORTS).toBe('enabled')
  })

  it("tells the backend its distribution's consent rule, over an inherited one", () => {
    const environment = backendEnvironment(
      { RDST_CONSENT: 'opt_out' },
      undefined,
      undefined,
      { consent: 'opt_in', hostedReports: false }
    )

    expect(environment.RDST_CONSENT).toBe('opt_in')
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
// /api/init/status, answering 401 without RDST_API_TOKEN when that is set, and reports it is
// listening as uvicorn does. Modes: serve, migrate, hang, crash.
const FAKE_BACKEND = `
import http from 'node:http'
const [mode, ms, ...rest] = process.argv.slice(2)
const arg = (name) => rest[rest.indexOf(name) + 1]
const say = (marker) => console.log('RDST_STARTUP ' + JSON.stringify(marker))
const token = process.env.RDST_API_TOKEN
const serve = () => http.createServer((req, res) => {
  res.writeHead(token && req.headers['x-rdst-token'] !== token ? 401 : 200); res.end('{}')
}).listen(Number(arg('--port')), arg('--host'), () => console.error(
  'INFO:     Uvicorn running on http://' + arg('--host') + ':' + arg('--port') + ' (Press CTRL+C to quit)'))
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

describe('backend identity', () => {
  it("accepts only the server's own report that it listens on the backend's address", () => {
    const base = 'http://127.0.0.1:47787'
    expect(
      reportsListening(
        'INFO:     Uvicorn running on http://127.0.0.1:47787 (Press CTRL+C to quit)\r',
        base
      )
    ).toBe(true)
    expect(
      reportsListening(
        'Uvicorn running on http://127.0.0.1:47787 (Press CTRL+C to quit)',
        base
      )
    ).toBe(true)
    expect(
      reportsListening(
        'INFO:     Uvicorn running on http://127.0.0.1:477870 (Press CTRL+C to quit)',
        base
      )
    ).toBe(false)
    // Printed before the port is bound.
    expect(
      reportsListening(
        'Starting RDST web server on http://127.0.0.1:47787',
        base
      )
    ).toBe(false)
    expect(
      reportsListening(
        'INFO:     127.0.0.1:5 - "GET /Uvicorn running on http://127.0.0.1:47787 (x HTTP/1.1" 404',
        base
      )
    ).toBe(false)
  })

  it('adds the launch token to every request a fetcher sends', async () => {
    const seen: Headers[] = []
    const fetcher = tokenFetch('secret', async (_input, init) => {
      seen.push(new Headers(init?.headers))
      return new Response('{}')
    })
    await fetcher('http://127.0.0.1:1/api/x')
    await fetcher('http://127.0.0.1:1/api/y', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [TOKEN_HEADER]: 'other' },
    })
    expect(seen.map((headers) => headers.get(TOKEN_HEADER))).toEqual([
      'secret',
      'secret',
    ])
    expect(seen[1]?.get('content-type')).toBe('application/json')
  })

  it('is ready only when its own process serves the port, with the token', async () => {
    const handle = await startBackend({
      resourcesPath: tempDir,
      command: fake('serve'),
      token: 'launch-secret',
    })
    try {
      const status = `${handle.apiBaseUrl}/api/init/status`
      expect((await fetch(status)).status).toBe(401)
      expect((await tokenFetch('launch-secret')(status)).status).toBe(200)
    } finally {
      await stopBackend(handle)
    }
  }, 15_000)

  it('never takes another program on its port for the backend', async () => {
    const port = await findAvailablePort('127.0.0.1')
    let probes = 0
    const squatter = http.createServer((_req, res) => {
      probes++
      res.writeHead(200)
      res.end('{}')
    })
    await new Promise<void>((resolve) =>
      squatter.listen(port, '127.0.0.1', resolve)
    )
    try {
      // The backend can't bind and exits; until then the squatter's answers don't count.
      await expect(
        startBackend({
          resourcesPath: tempDir,
          command: fake('serve'),
          port,
          token: 't',
        })
      ).rejects.toThrow('exited before it became ready')
      // A backend still starting up is never mistaken for the squatter either.
      await expect(
        startBackend({
          resourcesPath: tempDir,
          command: fake('hang'),
          port,
          token: 't',
          startupTimeoutMs: 600,
        })
      ).rejects.toThrow('Timed out waiting for RDST backend')
      expect(probes).toBe(0)
    } finally {
      await new Promise((resolve) => squatter.close(resolve))
    }
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

describe('backendEnvironment identity', () => {
  it('tells the sidecar its instance and the scheme the shell registered', () => {
    const cl = backendEnvironment(
      { RDST_INSTANCE: 'staging', RDST_DESKTOP_PROTOCOL: 'rdst' },
      undefined,
      { instance: 'cl', protocol: 'rdst-cl' }
    )
    expect(cl.RDST_INSTANCE).toBe('cl')
    expect(cl.RDST_DESKTOP_PROTOCOL).toBe('rdst-cl')

    // An inherited instance never moves production onto another build's data.
    const production = backendEnvironment(
      { RDST_INSTANCE: 'staging' },
      undefined,
      { instance: '', protocol: 'rdst' }
    )
    expect(production.RDST_INSTANCE).toBeUndefined()
    expect(production.RDST_DESKTOP_PROTOCOL).toBe('rdst')

    const dev = backendEnvironment({}, undefined, {
      instance: '',
      protocol: 'rdst-dev',
    })
    expect(dev.RDST_DESKTOP_PROTOCOL).toBe('rdst-dev')
    expect(backendEnvironment({}).RDST_DESKTOP_PROTOCOL).toBeUndefined()
  })
})

describe('startBackend identity', () => {
  it('passes the instance and scheme to the backend process', async () => {
    const script = path.join(tempDir, 'print-identity.mjs')
    fs.writeFileSync(
      script,
      'const { RDST_INSTANCE, RDST_DESKTOP_PROTOCOL } = process.env\n' +
        "console.error('identity=' + RDST_INSTANCE + '/' + RDST_DESKTOP_PROTOCOL)\n" +
        'process.exit(3)\n'
    )
    await expect(
      startBackend({
        resourcesPath: tempDir,
        command: { file: process.execPath, args: [script] },
        identity: { instance: 'staging', protocol: 'rdst-staging' },
      })
    ).rejects.toThrow('identity=staging/rdst-staging')
  }, 15_000)
})
