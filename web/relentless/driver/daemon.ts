import { randomBytes } from 'node:crypto'
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import type { Mode } from './app.ts'
import { mailConfig } from './mail.ts'
import {
  CommandError,
  type CommandInput,
  type SecretName,
  Session,
} from './session.ts'

/**
 * Owns one application for the length of a run and serves commands over
 * loopback HTTP. driver.json in the run directory tells clients where it is.
 */
const { values } = parseArgs({
  options: {
    'run-dir': { type: 'string' },
    'run-id': { type: 'string' },
    mode: { type: 'string', default: 'browser' },
    'desktop-exec': { type: 'string' },
    headed: { type: 'boolean', default: false },
    'home-from': { type: 'string' },
    'no-first-run-popups': { type: 'boolean', default: false },
    'page-scripts': { type: 'boolean', default: false },
    deadline: { type: 'string' },
  },
})

const runDir = values['run-dir']
const runId = values['run-id']
if (!runDir || !runId) throw new Error('--run-dir and --run-id are required')
const mode = values.mode as Mode
if (mode !== 'browser' && mode !== 'desktop') {
  throw new Error(`unknown mode ${values.mode}`)
}

/** The key comes from the environment or a file, never from arguments. */
function anthropicKey() {
  const file = process.env.RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE
  const key = file
    ? readFileSync(file, 'utf8').trim()
    : process.env.RDST_RELENTLESS_ANTHROPIC_API_KEY?.trim()
  return key || undefined
}

const secrets: Partial<Record<SecretName, string>> = {}
const key = anthropicKey()
if (key) secrets['anthropic-api-key'] = key

const driverFile = join(runDir, 'driver.json')
const token = randomBytes(16).toString('hex')
let session: Session | null = null

let queue: Promise<unknown> = Promise.resolve()
let stopping = false

// Longer than the slowest legitimate command (a restart: bounded shutdown
// plus a full launch), so only a genuinely stuck command trips it.
const COMMAND_TIMEOUT_MS = 420_000

class CommandTimeout extends Error {}

/** One line per command in commands.jsonl, so a stuck run names its cause. */
async function logged<T>(
  name: string,
  input: CommandInput,
  work: () => Promise<T>
): Promise<T> {
  const log = (fields: Record<string, unknown>) => {
    const line = JSON.stringify({
      ts: new Date().toISOString(),
      name,
      ...fields,
    })
    appendFileSync(
      join(runDir as string, 'commands.jsonl'),
      `${session ? session.redact(line) : line}\n`
    )
  }
  const started = Date.now()
  log({
    event: 'start',
    args: input.pos.map((arg) => arg.slice(0, 200)),
    flags: Object.fromEntries(
      Object.entries(input.flags).map(([key, value]) => [
        key,
        String(value).slice(0, 200),
      ])
    ),
  })
  try {
    const result = await work()
    const target = session?.takeResolvedTarget()
    log({
      event: 'end',
      ok: true,
      ms: Date.now() - started,
      ...(target ? { target } : {}),
    })
    return result
  } catch (error) {
    log({
      event: 'end',
      ok: false,
      ms: Date.now() - started,
      error: errorText(error),
    })
    throw error
  }
}

function withTimeout<T>(work: Promise<T>, name: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new CommandTimeout(
            `${name} did not finish in ${COMMAND_TIMEOUT_MS / 1000}s`
          )
        ),
      COMMAND_TIMEOUT_MS
    )
  })
  return Promise.race([work, timeout]).finally(() => clearTimeout(timer))
}

function errorText(error: unknown) {
  const text = error instanceof Error ? error.message : String(error)
  return text.length > 2_000 ? `${text.slice(0, 2_000)}...` : text
}

async function stop() {
  if (stopping) return
  stopping = true
  const cleanup = await session
    ?.stop()
    .catch((error) => ({ error: errorText(error) }))
  server.close()
  return cleanup
}

const server = createServer((req, res) => {
  const reply = (status: number, body: unknown) => {
    res.writeHead(status, { 'content-type': 'application/json' })
    const text = JSON.stringify(body)
    res.end(session ? session.redact(text) : text)
  }
  if (
    req.method !== 'POST' ||
    req.headers.authorization !== `Bearer ${token}`
  ) {
    reply(403, { ok: false, error: 'forbidden' })
    return
  }
  let raw = ''
  req.on('data', (chunk) => {
    raw += chunk
  })
  req.on('end', () => {
    const { name, input } = JSON.parse(raw) as {
      name: string
      input: CommandInput
    }
    // One command at a time: the agent's view of the page stays coherent.
    queue = queue.then(async () => {
      try {
        if (name === 'stop') {
          reply(200, { ok: true, data: await stop() })
          setTimeout(() => process.exit(0), 100)
          return
        }
        if (!session) throw new Error('driver is not ready')
        const active = session
        const data = await logged(name, input, () =>
          withTimeout(active.run(name, input), name)
        )
        reply(200, { ok: true, data })
      } catch (error) {
        reply(200, {
          ok: false,
          error: errorText(error),
          kind:
            error instanceof CommandError
              ? 'usage'
              : error instanceof CommandTimeout
                ? 'timeout'
                : 'action',
        })
      }
    })
  })
})

let ready = false

// Playwright can reject outside the awaited launch; report it like any other
// startup failure instead of dying without a driver.json. Once serving, a
// stray rejection is logged and the run keeps its application.
function fatal(error: unknown) {
  if (ready) {
    console.error(`[daemon] unhandled: ${errorText(error)}`)
    return
  }
  writeFileSync(
    driverFile,
    JSON.stringify({ ready: false, error: errorText(error), pid: process.pid })
  )
  void stop().finally(() => process.exit(1))
}
process.on('uncaughtException', fatal)
process.on('unhandledRejection', fatal)

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    void stop().finally(() => process.exit(1))
  })
}

server.listen(0, '127.0.0.1', async () => {
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  try {
    session = await Session.open({
      mode,
      runDir,
      runId,
      desktopExecutable:
        values['desktop-exec'] && resolve(values['desktop-exec']),
      headless: !values.headed,
      secrets,
      mail: mailConfig(),
      homeFrom: values['home-from'] && resolve(values['home-from']),
      firstRunPopups: !values['no-first-run-popups'],
      pageScripts: values['page-scripts'],
      deadline: values.deadline ? Number(values.deadline) : undefined,
    })
    await session.launch()
    writeFileSync(
      driverFile,
      JSON.stringify({ ready: true, port, token, pid: process.pid, mode }),
      { mode: 0o600 }
    )
    ready = true
  } catch (error) {
    fatal(error)
  }
})
