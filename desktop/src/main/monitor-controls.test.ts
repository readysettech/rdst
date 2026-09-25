import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'

import { DesktopSettings } from './desktop-settings.js'
import {
  allPaused,
  applyPauseAction,
  listTrayDatabases,
  pauseMenuModel,
} from './monitor-controls.js'

const temporary: string[] = []
afterEach(() => {
  for (const dir of temporary.splice(0))
    fs.rmSync(dir, { recursive: true, force: true })
})

function tempSettings(): DesktopSettings {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-monitor-'))
  temporary.push(dir)
  return new DesktopSettings(dir)
}

/** A backend with per-target `enabled` flags that PUTs update. */
function fakeBackend(enabled: Record<string, boolean>) {
  const requests: { url: string; method: string; body?: string }[] = []
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    requests.push({ url, method, body: init?.body as string | undefined })
    if (url.endsWith('/api/configure/targets')) {
      return Response.json({
        targets: Object.keys(enabled).map((name) => ({ name })),
      })
    }
    const name = decodeURIComponent(
      url.split('/targets/')[1]?.split('/')[0] ?? ''
    )
    if (method === 'PUT')
      enabled[name] = JSON.parse(init?.body as string).enabled
    return Response.json({ enabled: enabled[name] })
  }) as typeof fetch
  return { fetcher, requests }
}

it('lists one entry per database, then Pause all and Resume all', () => {
  expect(pauseMenuModel(null)).toEqual([
    { label: 'Loading databases', enabled: false },
  ])
  expect(pauseMenuModel([])).toEqual([
    { label: 'No databases are monitored', enabled: false },
  ])

  const items = pauseMenuModel([
    { name: 'staging', enabled: false },
    { name: 'prod orders', enabled: true },
  ])
  expect(items.map((item) => (item.separator ? '---' : item.label))).toEqual([
    'Turn monitoring off for prod orders',
    'Turn monitoring on for staging',
    '---',
    'Turn all monitoring off',
    'Turn all monitoring on',
  ])
  expect(items[0]?.action).toEqual({ kind: 'pause', target: 'prod orders' })
  expect(items[1]?.action).toEqual({ kind: 'resume', target: 'staging' })

  const allRunning = pauseMenuModel([{ name: 'a', enabled: true }])
  expect(allRunning.map((item) => item.label)).toEqual([
    'Turn monitoring off for a',
    '',
    'Turn all monitoring off',
  ])
  const nonePaused = pauseMenuModel([{ name: 'a', enabled: false }])
  expect(
    nonePaused.find((item) => item.label === 'Turn all monitoring off')?.enabled
  ).toBe(false)
})

it('turns one database off, then all, and turns them on through backend settings', async () => {
  const settings = tempSettings()
  const backend = fakeBackend({ 'prod orders': true, staging: true })
  const base = 'http://127.0.0.1:1234'

  let databases = await listTrayDatabases(base, backend.fetcher)
  expect(databases).toEqual([
    { name: 'prod orders', enabled: true },
    { name: 'staging', enabled: true },
  ])

  await applyPauseAction(
    base,
    { kind: 'pause', target: 'prod orders' },
    databases,
    backend.fetcher
  )
  expect(settings.pausedTargets).toEqual([])
  expect(backend.requests.at(-1)).toEqual({
    url: `${base}/api/monitor/targets/prod%20orders/settings`,
    method: 'PUT',
    body: '{"enabled":false}',
  })

  databases = await listTrayDatabases(base, backend.fetcher)
  expect(databases).toEqual([
    { name: 'prod orders', enabled: false },
    { name: 'staging', enabled: true },
  ])
  expect(allPaused(databases)).toBe(false)

  await applyPauseAction(
    base,
    { kind: 'pause-all' },
    databases,
    backend.fetcher
  )
  expect(settings.pausedTargets).toEqual([])
  databases = await listTrayDatabases(base, backend.fetcher)
  expect(allPaused(databases)).toBe(true)
  expect(new DesktopSettings(temporary[0] ?? '').pausedTargets).toEqual([])

  await applyPauseAction(
    base,
    { kind: 'resume-all' },
    databases,
    backend.fetcher
  )
  expect(settings.pausedTargets).toEqual([])
  databases = await listTrayDatabases(base, backend.fetcher)
  expect(databases.every((database) => database.enabled)).toBe(true)
})

it('uses backend enabled state even when legacy desktop settings disagree', async () => {
  const settings = tempSettings()
  settings.setPausedTargets(['staging', 'gone'])
  const backend = fakeBackend({ staging: true })
  const databases = await listTrayDatabases('http://x', backend.fetcher)
  expect(databases).toEqual([{ name: 'staging', enabled: true }])
  expect(settings.pausedTargets).toEqual(['staging', 'gone'])
  expect(allPaused(null)).toBe(false)
  expect(allPaused([])).toBe(false)
})

it('uses backend off state without a desktop-only paused list', async () => {
  const settings = tempSettings()
  const backend = fakeBackend({ staging: false })
  const databases = await listTrayDatabases('http://x', backend.fetcher)
  expect(databases).toEqual([{ name: 'staging', enabled: false }])
  expect(allPaused(databases)).toBe(true)
  await applyPauseAction(
    'http://x',
    { kind: 'resume', target: 'staging' },
    databases,
    backend.fetcher
  )
  await applyPauseAction(
    'http://x',
    { kind: 'pause', target: 'staging' },
    databases,
    backend.fetcher
  )
  expect(backend.requests.at(-1)?.body).toBe('{"enabled":false}')
  expect(settings.pausedTargets).toEqual([])
})
