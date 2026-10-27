import { existsSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import type { Mode } from './driver/app.ts'
import { call, startDriver } from './driver/client.ts'
import { paths } from './driver/paths.ts'
import type { CommandInput } from './driver/session.ts'

/**
 * Scripted proof that the driver and what CI gives the agents work, with no
 * agent and no fakes: the app starts, snapshots, clicks, typing and secret
 * fill reach the page, the real Anthropic key is accepted by Anthropic, both
 * databases and the jump host answer, and the app survives reloads,
 * restarts, crashes and a killed backend, then stops with no surviving
 * processes. It walks no product screen: how the app behaves is for the
 * missions to judge, so a product change cannot hold them back. What it sees
 * of the product on the way is reported under `probes`.
 */
const { values } = parseArgs({
  options: {
    mode: { type: 'string', default: 'browser' },
    'desktop-exec': { type: 'string' },
    'run-dir': { type: 'string' },
    headed: { type: 'boolean', default: false },
  },
})
const mode = values.mode as Mode
const runDir = resolve(
  values['run-dir'] ?? join(paths.runsDir, `selftest-${mode}-${Date.now()}`)
)
const keyFile = process.env.RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE
const anthropicKey = (
  keyFile
    ? readFileSync(keyFile, 'utf8')
    : (process.env.RDST_RELENTLESS_ANTHROPIC_API_KEY ?? '')
).trim()
if (!anthropicKey) {
  console.error(
    'set RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE (or RDST_RELENTLESS_ANTHROPIC_API_KEY); the agents use the real AI path'
  )
  process.exit(2)
}

const checks: { name: string; ok: boolean; detail?: unknown }[] = []
const probes: Record<string, unknown> = {}

async function rt(
  name: string,
  pos: string[] = [],
  flags: CommandInput['flags'] = {}
) {
  const response = await call(runDir, name, { pos, flags })
  if (!response.ok)
    throw new Error(`${name} ${pos.join(' ')}: ${response.error}`)
  return response.data as Record<string, any>
}

async function check(name: string, body: () => Promise<unknown>) {
  try {
    const detail = await body()
    checks.push({
      name,
      ok: detail !== false,
      ...(typeof detail === 'boolean' ? {} : { detail }),
    })
  } catch (error) {
    checks.push({ name, ok: false, detail: String(error) })
  }
}

async function backendAnswers() {
  return (await rt('api', ['GET', '/health'])).status === 200
}

async function localValue(key: string) {
  return (await rt('eval', [`localStorage.getItem(${JSON.stringify(key)})`]))
    .value
}

try {
  await startDriver({
    runDir,
    mode,
    desktopExecutable: values['desktop-exec'],
    headed: values.headed,
    pageScripts: true,
  })
  checks.push({ name: 'driver starts the app', ok: true })

  try {
    const screen: string = (await rt('snapshot', [], { 'max-chars': '20000' }))
      .text
    const { json } = await rt('api', ['GET', '/api/account/status'])
    probes.first_run = {
      dialogs: [...screen.matchAll(/dialog "([^"]+)"/g)].map(
        ([, name]) => name
      ),
      signed_in: json.signed_in ?? null,
    }
  } catch (error) {
    probes.first_run = String(error)
  }

  await check('snapshot returns refs', async () => {
    const snapshot = await rt('snapshot', [], { 'max-chars': '5000' })
    return /\[ref=e\d+\]/.test(snapshot.text)
  })

  // A scratch input of the selftest's own, so no product screen is involved.
  await check('click, fill and fill-secret reach the page', async () => {
    const input = '#relentless-selftest'
    await rt('eval', [
      // Above anything the app shows, and clickable while a modal holds the page.
      `(() => { const input = document.createElement('input'); input.id = 'relentless-selftest'; input.style.cssText = 'position:fixed;top:0;left:0;width:200px;height:24px;z-index:2147483647;pointer-events:auto'; input.addEventListener('click', () => { input.dataset.clicked = 'yes' }); document.body.append(input) })()`,
    ])
    const read = async (expression: string) =>
      (await rt('eval', [`document.querySelector('${input}').${expression}`]))
        .value
    await rt('click', [input])
    await rt('fill', [input, 'typed by rt'])
    const typed = await read('value')
    await rt('fill-secret', [input, 'anthropic-api-key'])
    const secretLength = await read('value.length')
    const clicked = await read('dataset.clicked')
    await rt('eval', [`document.querySelector('${input}').remove()`])
    if (clicked !== 'yes') throw new Error('the click did not land')
    if (typed !== 'typed by rt') throw new Error(`fill typed ${typed}`)
    if (secretLength !== anthropicKey.length)
      throw new Error('fill-secret did not type the key')
    return true
  })

  await check('Anthropic accepts the real key', async () => {
    const response = await fetch('https://api.anthropic.com/v1/models', {
      headers: { 'x-api-key': anthropicKey, 'anthropic-version': '2023-06-01' },
      signal: AbortSignal.timeout(30_000),
    })
    if (response.ok) return true
    throw new Error(`Anthropic answered ${response.status}`)
  })

  await check('SQL reaches both databases', async () => {
    for (const db of ['postgres', 'mysql']) {
      const result = await rt('db', [
        'exec',
        db,
        'SELECT count(*) FROM title_basics',
      ])
      if (result.exit_code !== 0 || !/\b40\b/.test(result.text)) {
        throw new Error(`${db}: ${result.text}`)
      }
    }
    return true
  })

  await check('the jump host key is installed', async () => {
    const { ssh_bastion } = await rt('db', ['list'])
    // Without Compose there may be no jump host; under it, one always runs.
    if (typeof ssh_bastion === 'string')
      return !process.env.RDST_RELENTLESS_SSH_HOST
    const mode = statSync(ssh_bastion.key_path).mode & 0o777
    if (mode !== 0o600) throw new Error(`key mode ${mode.toString(8)}`)
    return true
  })

  await check('screenshot is written', async () => {
    const { path } = await rt('screenshot', [], { label: 'selftest' })
    return existsSync(path)
  })

  await check('evidence records API traffic', async () => {
    await backendAnswers()
    const { events } = await rt('evidence', [], { all: true, kinds: 'api' })
    return events.length > 0
  })

  await check('reload keeps localStorage', async () => {
    await rt('eval', ['localStorage.setItem("relentless_sentinel", "kept")'])
    await rt('reload')
    return (await localValue('relentless_sentinel')) === 'kept'
  })

  await check('restart relaunches the app', async () => {
    const restarted = await rt('restart')
    // An app scheme's origin is opaque, so compare scheme and host.
    const where = (url: string) =>
      `${new URL(url).protocol}//${new URL(url).host}`
    probes.origin_changed_on_restart =
      restarted.previous_url &&
      where(restarted.previous_url) !== restarted.base_url
    probes.local_storage_survives_restart =
      (await localValue('relentless_sentinel')) === 'kept'
    return backendAnswers()
  })

  if (mode === 'desktop') {
    // The tray menu as a desktop panel reads it; observed, not gated.
    probes.tray_menu = (await rt('tray')).tray

    await check('a crashed renderer comes back', async () => {
      await rt('crash-renderer')
      const deadline = Date.now() + 30_000
      while (Date.now() < deadline) {
        const { windows } = await rt('status')
        if (windows?.some((w: { crashed: boolean }) => !w.crashed)) {
          await rt('restart')
          return backendAnswers()
        }
        await new Promise((resolve) => setTimeout(resolve, 1_000))
      }
      throw new Error('the window stayed crashed')
    })
  }

  await check('killed backend is observable and recoverable', async () => {
    const killed = await rt('kill-backend')
    if (killed.killed.length === 0) return false
    await rt('restart')
    return backendAnswers()
  })
} catch (error) {
  checks.push({
    name: 'driver starts the app',
    ok: false,
    detail: String(error),
  })
} finally {
  const stopped = await call(runDir, 'stop')
  const cleanup = stopped.data as
    | { orphans?: unknown[]; trace?: string }
    | undefined
  checks.push({
    name: 'stop leaves no processes behind',
    ok: stopped.ok && (cleanup?.orphans?.length ?? 0) === 0,
    detail: stopped.ok ? cleanup : stopped.error,
  })
}

const passed = checks.every(({ ok }) => ok)
console.log(
  JSON.stringify({ mode, run_dir: runDir, passed, checks, probes }, null, 2)
)
process.exitCode = passed ? 0 : 1
