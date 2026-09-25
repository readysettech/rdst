import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DesktopTelemetry,
  type DirectTelemetryConfig,
  detectInstallChange,
  type EventPoster,
  errorClass,
  loadDirectTelemetryConfig,
  scrubMessage,
  startFailureClass,
  telemetryDisabled,
} from './telemetry.js'

describe('desktop message scrubbing', () => {
  it('matches the web scrubber in both workspace layouts', async () => {
    const sources = [
      new URL('../../../rdst/src/lib/appErrors.ts', import.meta.url),
      new URL('../../../web/src/lib/appErrors.ts', import.meta.url),
    ]
    const source = sources.find((url) => fs.existsSync(url))
    expect(source).toBeDefined()
    const { scrubMessage: webScrubMessage } = await import(
      /* @vite-ignore */ source!.href
    )
    const messages: unknown[] = [
      null,
      undefined,
      42,
      '',
      '  failure\n  with spaces  ',
      'near SELECT id FROM orders WHERE id = 1',
      'failed select id from orders',
      'INSERT INTO orders VALUES (1)',
      'UPDATE orders SET id = 1',
      'DELETE FROM orders',
      'WITH orders AS (SELECT 1) SELECT * FROM orders',
      'CREATE TABLE orders (id int)',
      'ALTER TABLE orders',
      'DROP TABLE orders',
      'TRUNCATE orders',
      'person@example.com https://example.com/private',
      'host=db.internal port=5432 database=orders user=owner password=secret',
      'schema=private table=orders relation=orders target=production',
      `quoted 'value' "value" \`value\``,
      'address 127.0.0.1:5432 and [2001:db8::1]',
      String.raw`file C:\Users\owner\private.db`,
      'files /home/owner/private.db ~/private/data ../private/data',
      'db.internal localhost:5432 example.com',
      'x'.repeat(300),
    ]
    for (const message of messages) {
      for (const limit of [20, 80, 200]) {
        expect(scrubMessage(message, limit)).toBe(
          webScrubMessage(message, limit)
        )
      }
      expect(scrubMessage(message)).toBe(webScrubMessage(message))
    }
  })
})

let dir: string

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-telemetry-'))
})

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true })
})

describe('telemetryDisabled', () => {
  it('reports only from a packaged build without an opt-out', () => {
    expect(telemetryDisabled({}, true)).toBe(false)
    expect(telemetryDisabled({}, false)).toBe(true)
    expect(telemetryDisabled({ RDST_TELEMETRY: '0' }, true)).toBe(true)
    expect(telemetryDisabled({ RDST_TELEMETRY: 'off' }, true)).toBe(true)
    expect(telemetryDisabled({ RDST_DESKTOP_SMOKE: '1' }, true)).toBe(true)
  })
})

describe('DesktopTelemetry', () => {
  it('queues events until the backend is up, then posts them to its telemetry route', async () => {
    const post = vi.fn<EventPoster>(async () => true)
    const file = path.join(dir, 'queue.json')
    const telemetry = new DesktopTelemetry(file, false, post)
    telemetry.capture('desktop_startup_failed', { stage: 'start', attempts: 4 })
    expect(post).not.toHaveBeenCalled()
    expect(JSON.parse(fs.readFileSync(file, 'utf8'))).toHaveLength(1)

    telemetry.setBackend('http://127.0.0.1:9999')
    await telemetry.flush()
    expect(post).toHaveBeenCalledWith(
      'http://127.0.0.1:9999/api/telemetry/events',
      {
        source: 'desktop',
        events: [
          {
            event: 'desktop_startup_failed',
            properties: { stage: 'start', attempts: 4 },
          },
        ],
      }
    )
    expect(telemetry.pending).toHaveLength(0)
    expect(fs.existsSync(file)).toBe(false)
  })

  it('keeps events when the backend refuses them, and marks them delayed on the next launch', async () => {
    const file = path.join(dir, 'queue.json')
    const first = new DesktopTelemetry(file, false, async () => false)
    first.setBackend('http://127.0.0.1:9999')
    first.capture('tray_quit', { uptime_hours: 2 })
    await first.flush()
    expect(first.pending).toHaveLength(1)

    const post = vi.fn<EventPoster>(async () => true)
    const next = new DesktopTelemetry(file, false, post)
    expect(next.pending[0]?.properties).toEqual({
      uptime_hours: 2,
      delayed: true,
    })
    next.setBackend('http://127.0.0.1:9999')
    await next.flush()
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('does nothing when telemetry is off', async () => {
    const post = vi.fn<EventPoster>(async () => true)
    const file = path.join(dir, 'queue.json')
    const telemetry = new DesktopTelemetry(file, true, post)
    telemetry.setBackend('http://127.0.0.1:9999')
    telemetry.capture('tray_quit')
    await telemetry.flush()
    expect(post).not.toHaveBeenCalled()
    expect(fs.existsSync(file)).toBe(false)
  })
})

describe('detectInstallChange', () => {
  const marker = () => path.join(dir, 'install.json')

  it('counts the first launch of a new install, then nothing until the version changes', () => {
    expect(
      detectInstallChange({
        markerFile: marker(),
        version: '1.0.0',
        settingsExisted: false,
        rdstDataExists: false,
      })
    ).toEqual({ kind: 'installed', installKind: 'fresh' })
    expect(
      detectInstallChange({
        markerFile: marker(),
        version: '1.0.0',
        settingsExisted: true,
        rdstDataExists: true,
      })
    ).toEqual({ kind: 'same' })
    expect(
      detectInstallChange({
        markerFile: marker(),
        version: '1.1.0',
        settingsExisted: true,
        rdstDataExists: true,
      })
    ).toEqual({ kind: 'upgraded', fromVersion: '1.0.0', toVersion: '1.1.0' })
  })

  it('adopts an install from before the marker without calling it new', () => {
    expect(
      detectInstallChange({
        markerFile: marker(),
        version: '1.0.0',
        settingsExisted: true,
        rdstDataExists: true,
      })
    ).toEqual({ kind: 'adopted' })
  })

  it('tells a new desktop install on a machine that already had RDST data', () => {
    expect(
      detectInstallChange({
        markerFile: marker(),
        version: '1.0.0',
        settingsExisted: false,
        rdstDataExists: true,
      })
    ).toEqual({ kind: 'installed', installKind: 'existing_rdst_data' })
  })
})

describe('error labels', () => {
  it('names errors by class, never by message', () => {
    expect(errorClass(new TypeError('host db.example.com'))).toBe('TypeError')
    expect(errorClass('text')).toBe('Nonstring')
    expect(startFailureClass('RDST backend exited with exit code 1')).toBe(
      'exited'
    )
    expect(startFailureClass('Timed out waiting for the backend')).toBe(
      'timeout'
    )
  })
})

describe('direct startup failure capture', () => {
  const config = (): DirectTelemetryConfig => ({
    key: 'preview-key',
    host: 'https://posthog.invalid',
    internalProbeUrl: 'http://canary.invalid',
    deviceId: 'install-1',
    appVersion: '2.3.4',
    os: 'win32',
    disabled: false,
  })
  it('posts a scrubbed startup failure without a backend and clears the queue', async () => {
    const post = vi.fn<EventPoster>(async () => true)
    const file = path.join(dir, 'queue.json')
    const telemetry = new DesktopTelemetry(
      file,
      false,
      post,
      config(),
      async () => true
    )
    telemetry.capture('desktop_startup_failed', {
      stage: 'migrate',
      attempts: 4,
      error_class: 'timeout',
      message:
        'db.example.com 2001:db8::1 bob@example.com /home/bob/data SELECT secret FROM orders',
      sql: 'SELECT secret FROM orders',
      host: 'db.example.com',
    })
    await telemetry.flush()
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('https://posthog.invalid/capture/', {
      api_key: 'preview-key',
      distinct_id: 'install-1',
      event: 'desktop_startup_failed',
      properties: expect.objectContaining({
        stage: 'migrate',
        attempts: 4,
        error_class: 'timeout',
        os: 'win32',
        app_version: '2.3.4',
        installation_id: 'install-1',
        internal_user: true,
      }),
    })
    expect(JSON.stringify(post.mock.calls)).not.toMatch(
      /db\.example|bob@|2001:db8|SELECT|secret|\/home\/bob/
    )
    expect(telemetry.pending).toHaveLength(0)
    expect(fs.existsSync(file)).toBe(false)
  })
  it('keeps offline failures scrubbed on disk and retries on the next launch', async () => {
    const file = path.join(dir, 'queue.json')
    const first = new DesktopTelemetry(
      file,
      false,
      async () => {
        throw new Error('offline')
      },
      config(),
      async () => false
    )
    first.capture('desktop_startup_failed', {
      stage: 'start',
      message: 'host=db.example.com',
    })
    await first.flush()
    expect(first.pending).toHaveLength(1)
    expect(fs.readFileSync(file, 'utf8')).not.toContain('db.example.com')
    const post = vi.fn<EventPoster>(async () => true)
    const next = new DesktopTelemetry(
      file,
      false,
      post,
      { ...config(), appVersion: '3.0.0' },
      async () => false
    )
    await next.flush()
    expect(post).toHaveBeenCalledWith(
      'https://posthog.invalid/capture/',
      expect.objectContaining({
        properties: expect.objectContaining({
          app_version: '2.3.4',
          delayed: true,
          internal_user: false,
        }),
      })
    )
    expect(next.pending).toHaveLength(0)
  })
  it('uses the saved account ID and puts email only on the person profile', async () => {
    const post = vi.fn<EventPoster>(async () => true)
    const telemetry = new DesktopTelemetry(
      path.join(dir, 'queue.json'),
      false,
      post,
      { ...config(), userId: 'usr_42', email: 'person@example.com' },
      async () => false
    )
    telemetry.capture('desktop_startup_failed', {
      stage: 'shell',
      error_class: 'TypeError',
      message: 'failed',
    })
    await telemetry.flush()
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        distinct_id: 'usr_42',
        event: '$identify',
        properties: expect.objectContaining({
          $anon_distinct_id: 'install-1',
          $set: { email: 'person@example.com', account_kind: 'signed_in' },
        }),
      })
    )
    expect(post.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        distinct_id: 'usr_42',
        event: 'desktop_startup_failed',
      })
    )
    expect(JSON.stringify(post.mock.calls[1])).not.toContain(
      'person@example.com'
    )
  })

  it('honors the config opt-out even with the backend available', async () => {
    const post = vi.fn<EventPoster>(async () => true)
    const telemetry = new DesktopTelemetry(
      path.join(dir, 'queue.json'),
      false,
      post,
      { ...config(), disabled: true }
    )
    telemetry.setBackend('http://127.0.0.1:9999')
    telemetry.capture('desktop_startup_failed')
    await telemetry.flush()
    expect(post).not.toHaveBeenCalled()
    expect(telemetry.pending).toHaveLength(0)
  })
})

describe('sidecar config and install identity', () => {
  const defaults = {
    key: 'preview-key',
    host: 'https://posthog.invalid',
    internalProbeUrl: '',
  }
  const load = (env: NodeJS.ProcessEnv = {}, packaged = true) =>
    loadDirectTelemetryConfig({
      env,
      packaged,
      dataDir: dir,
      appVersion: '1.2.3',
      defaults,
    })
  it('reuses the backend install ID and creates it on first launch', () => {
    const first = load()
    expect(first.deviceId).toMatch(/^[a-f0-9-]{36}$/)
    expect(load().deviceId).toBe(first.deviceId)
    expect(fs.readFileSync(path.join(dir, 'device_id'), 'utf8')).toBe(
      first.deviceId
    )
  })
  it.each([
    [{ RDST_TELEMETRY: '0' }, true],
    [{ RDST_TESTING: 'true' }, true],
    [{ RDST_POSTHOG_KEY: '' }, true],
    [{ RDST_DESKTOP_SMOKE: '1' }, true],
    [{}, false],
  ])('stays silent for %j (packaged: %s)', (env, packaged) => {
    expect(load(env, packaged).disabled).toBe(true)
    expect(fs.existsSync(path.join(dir, 'device_id'))).toBe(false)
  })
  it('reads the saved account metadata alongside the install identity', () => {
    fs.writeFileSync(
      path.join(dir, 'config.toml'),
      '[account]\nuser_id = "usr_42"\nemail = "person@example.com"\nstatus = "active"\n\n[other]\nuser_id = "wrong"\n'
    )
    const identity = load()
    expect(identity.userId).toBe('usr_42')
    expect(identity.email).toBe('person@example.com')
  })

  it('honors the config opt-out and the environment project key', () => {
    expect(load({ RDST_POSTHOG_KEY: 'override-key' }).key).toBe('override-key')
    fs.writeFileSync(
      path.join(dir, 'config.toml'),
      'telemetry_enabled = false\n'
    )
    expect(load().disabled).toBe(true)
  })
})
