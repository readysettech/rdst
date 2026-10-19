import { existsSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import type { Mode } from './driver/app.ts'
import { call, startDriver } from './driver/client.ts'
import { paths } from './driver/paths.ts'
import type { CommandInput } from './driver/session.ts'

/**
 * Scripted proof that the driver works end to end, with no agent and no
 * fakes: pass the AI gate with a real Anthropic key, connect a live Postgres
 * target through the add dialog, get an answer from Ask, then reload, restart,
 * crash the backend, recover, and stop with no surviving processes. Product
 * behavior the script observes but does not gate on is reported under
 * `probes`.
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
const db = {
  host: process.env.RDST_E2E_DB_HOST ?? '127.0.0.1',
  port: process.env.RDST_E2E_DB_PORT ?? '15432',
  database: 'rdst_test',
  user: 'rdst_test',
  password: process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password',
}
const target = 'relentless-selftest'
if (
  !process.env.RDST_RELENTLESS_ANTHROPIC_API_KEY &&
  !process.env.RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE
) {
  console.error(
    'set RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE (or RDST_RELENTLESS_ANTHROPIC_API_KEY); the self-test uses the real AI path'
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

async function targetNames() {
  const response = await rt('api', ['GET', '/api/configure/targets'])
  return (response.json.targets as { name: string }[]).map(({ name }) => name)
}

async function aiRequirement() {
  const response = await rt('api', ['GET', '/api/env/requirements'])
  const requirement = (
    response.json.requirements as { kind: string; satisfied: boolean }[]
  ).find(({ kind }) => kind === 'anthropic_api_key')
  return { keyring_available: response.json.keyring_available, ...requirement }
}

/** The recorded summary of the first finished stream from an endpoint. */
async function finishedStream(path: string, timeoutMs: number) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const { events } = await rt('evidence', [], { all: true, kinds: 'stream' })
    const stream = events.find((event: { path: string }) =>
      event.path.startsWith(path)
    )
    if (stream) return stream
    await new Promise((resolve) => setTimeout(resolve, 1_000))
  }
  throw new Error(`no finished ${path} stream in ${timeoutMs}ms`)
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
  })
  checks.push({ name: 'driver starts the app', ok: true })

  const ownKey = 'role=button[name="Use my own Anthropic key instead"]'
  await check('a new install opens into sign-in', async () => {
    const dialog = 'role=dialog[name="Sign in or sign up to get started"]'
    await rt('wait', [], { selector: dialog, timeout: '30000' })
    await rt('press', ['Escape'])
    await rt('wait', [], { selector: dialog, state: 'hidden' })
    return true
  })

  await check('the AI gate accepts a real Anthropic key', async () => {
    await rt('click', [ownKey])
    const input = 'role=dialog >> input[name^="rdst-secret-"]'
    await rt('wait', [], { selector: input })
    await rt('fill-secret', [input, 'anthropic-api-key'])
    await rt('click', ['role=dialog >> role=button[name="Save secrets"]'])
    await rt('wait', [], {
      selector: ownKey,
      state: 'hidden',
      timeout: '60000',
    })
    const requirement = await aiRequirement()
    return requirement.satisfied ? requirement : false
  })

  await check('adding a database connects a live Postgres target', async () => {
    const dialog = 'role=dialog[name="Add a database"]'
    // The first-run pop-up opens it once the AI is set; a link opens it otherwise.
    try {
      await rt('wait', [], { selector: dialog, timeout: '10000' })
    } catch {
      await rt('goto', ['/?add=1'])
      await rt('wait', [], { selector: dialog })
    }
    for (const [field, value] of Object.entries({ name: target, ...db })) {
      await rt('fill', [`${dialog} >> [name="${field}"]`, value])
    }
    await rt('click', [`${dialog} >> role=button[name="Add and monitor"]`])
    // A database monitoring cannot read yet is added with monitoring off.
    const without = `${dialog} >> role=button[name="Add without monitoring"]`
    try {
      await rt('wait', [], { selector: without, timeout: '20000' })
      await rt('click', [without])
    } catch {}
    await rt('wait', [], { url: '/database', timeout: '30000' })
    return (await targetNames()).includes(target)
  })

  await check('ask answers through the real AI path', async () => {
    await rt('goto', ['/ask'])
    await rt('fill', [
      'role=textbox[name="Question"]',
      'How many titles are in title_basics?',
    ])
    await rt('click', ['role=button[name="Ask"]'])
    const stream = await finishedStream('/api/ask', 180_000)
    await rt('screenshot', [], { label: 'ask-answer' })
    const types: string[] = stream.event_types
    if (types.includes('result') && !types.includes('error')) {
      return { event_types: types }
    }
    throw new Error(
      `no result event: ${types.join(',')} last=${stream.last_event}`
    )
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

  await check('snapshot returns refs', async () => {
    const snapshot = await rt('snapshot', [], { 'max-chars': '5000' })
    return /\[ref=e\d+\]/.test(snapshot.text)
  })

  await check('screenshot is written', async () => {
    const { path } = await rt('screenshot', [], { label: 'selftest' })
    return existsSync(path)
  })

  await check('evidence records API traffic', async () => {
    const { events } = await rt('evidence', [], { all: true, kinds: 'api' })
    return events.length > 0
  })

  await check('reload keeps localStorage', async () => {
    await rt('eval', ['localStorage.setItem("relentless_sentinel", "kept")'])
    await rt('reload')
    return (await localValue('relentless_sentinel')) === 'kept'
  })

  await check('restart relaunches with persisted targets', async () => {
    const restarted = await rt('restart')
    // An app scheme's origin is opaque, so compare scheme and host.
    const where = (url: string) =>
      `${new URL(url).protocol}//${new URL(url).host}`
    probes.origin_changed_on_restart =
      restarted.previous_url &&
      where(restarted.previous_url) !== restarted.base_url
    probes.local_storage_survives_restart =
      (await localValue('relentless_sentinel')) === 'kept'
    probes.ai_key_survives_restart = (await aiRequirement()).satisfied === true
    return (await targetNames()).includes(target)
  })

  if (mode === 'desktop') {
    // The tray menu as a desktop panel reads it; observed, not gated.
    probes.tray_menu = (await rt('tray')).tray

    await check('rt api reaches the sidecar directly', async () => {
      const { status } = await rt('api', ['GET', '/health'])
      return status === 200
    })

    await check('a crashed renderer comes back', async () => {
      await rt('crash-renderer')
      const deadline = Date.now() + 30_000
      while (Date.now() < deadline) {
        const { windows } = await rt('status')
        if (windows?.some((w: { crashed: boolean }) => !w.crashed)) {
          await rt('restart')
          return (await targetNames()).includes(target)
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
    return (await targetNames()).includes(target)
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
