import { type ChildProcess, execFile, spawn } from 'node:child_process'
import {
  closeSync,
  mkdtempSync,
  openSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/**
 * A real Secret Service (gnome-keyring on a private D-Bus session) for one
 * run, so the application stores and restores secrets the way it does on a
 * desktop, without touching the keychain of the person running the tests.
 * It outlives application restarts, as a login session does, and is
 * restarted if it crashes; its keyrings live on disk in the run's home.
 */
export interface SecretService {
  busAddress: string
  /** Null while the keychain is available; how it ended once it is down for good. */
  exited(): KeychainExit | null
  /** Called on every crash, with whether a restart brought it back. */
  onExit(listener: (exit: KeychainExit & { restarted: boolean }) => void): void
  stop(): Promise<void>
}

export interface KeychainExit {
  code: number | null
  signal: string | null
  at: string
}

const READY_TIMEOUT_MS = 15_000
const MAX_RESTARTS = 5

async function waitForExit(child: ChildProcess, timeoutMs: number) {
  if (child.exitCode !== null || child.signalCode !== null) return
  await Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    new Promise((resolve) => setTimeout(resolve, timeoutMs)),
  ])
}

async function readBusAddress(dbus: ChildProcess): Promise<string> {
  return await new Promise((resolve, reject) => {
    let output = ''
    const timer = setTimeout(
      () => reject(new Error('dbus-daemon did not print its address')),
      READY_TIMEOUT_MS
    )
    dbus.stdout?.on('data', (chunk) => {
      output += chunk
      const line = output.split('\n')[0]
      if (output.includes('\n') && line) {
        clearTimeout(timer)
        resolve(line.trim())
      }
    })
    dbus.once('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`dbus-daemon exited with code ${code}`))
    })
  })
}

// Asynchronous on purpose: a blocking probe would starve the event loop that
// delivers the unlock password to gnome-keyring's stdin.
function secretsReady(env: NodeJS.ProcessEnv): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(
      'dbus-send',
      [
        '--session',
        '--dest=org.freedesktop.secrets',
        '--print-reply',
        '/org/freedesktop/secrets/aliases/default',
        'org.freedesktop.DBus.Properties.Get',
        'string:org.freedesktop.Secret.Collection',
        'string:Label',
      ],
      { env, timeout: 2_000 },
      (error) => resolve(!error)
    )
  })
}

// The bus activates nothing: a session bus with the system's service files
// would start a new gnome-keyring when this one is down, and that daemon
// hands over to the keychain of the person running the tests.
function busConfig(dir: string) {
  return `<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:dir=${dir}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
`
}

/**
 * `env` must already point HOME and XDG_DATA_HOME at the run's home. The
 * keyring daemon's stderr goes to `logPath`.
 */
export async function startSecretService(
  env: NodeJS.ProcessEnv,
  logPath: string
): Promise<SecretService> {
  if (process.platform !== 'linux') {
    throw new Error(
      'an isolated keychain is only implemented on Linux; other platforms need a dedicated test account'
    )
  }
  // Unix socket paths are limited to about 100 bytes, which a run directory
  // can exceed, so the bus and the daemon's control socket live in a short
  // temp path.
  const controlDir = mkdtempSync(join(tmpdir(), 'rt-keyring-'))
  const configPath = join(controlDir, 'bus.conf')
  writeFileSync(configPath, busConfig(controlDir))
  const dbus = spawn(
    'dbus-daemon',
    [`--config-file=${configPath}`, '--nofork', '--print-address=1'],
    { env, stdio: ['ignore', 'pipe', 'ignore'] }
  )
  const busAddress = await readBusAddress(dbus)
  const busEnv = {
    ...env,
    DBUS_SESSION_BUS_ADDRESS: busAddress,
    XDG_RUNTIME_DIR: controlDir,
  }
  const log = openSync(logPath, 'a')
  // Every daemon started, so stopping catches one a restart is still starting.
  const daemons = new Set<ChildProcess>()

  const launch = async () => {
    const child = spawn(
      'gnome-keyring-daemon',
      [
        '--foreground',
        '--unlock',
        '--components=secrets',
        `--control-directory=${controlDir}`,
      ],
      // A crash dump holds the unlocked secrets; the private directory is
      // removed when the service stops.
      { cwd: controlDir, env: busEnv, stdio: ['pipe', 'ignore', log] }
    )
    daemons.add(child)
    child.once('exit', () => daemons.delete(child))
    if (stopping) child.kill('SIGKILL')
    // --unlock reads the login keyring password from stdin; a non-empty one
    // makes the daemon create the default collection, or unlocks it.
    child.stdin?.end('relentless')
    const deadline = Date.now() + READY_TIMEOUT_MS
    while (!(await secretsReady(busEnv))) {
      if (Date.now() > deadline || child.exitCode !== null) {
        child.kill('SIGKILL')
        return null
      }
      await new Promise((resolve) => setTimeout(resolve, 200))
    }
    return child
  }

  let stopping = false
  let down: KeychainExit | null = null
  let restarts = 0
  const listeners: ((exit: KeychainExit & { restarted: boolean }) => void)[] =
    []

  const stop = async () => {
    stopping = true
    for (const child of [...daemons, dbus]) {
      child.kill('SIGTERM')
      await waitForExit(child, 3_000)
      if (child.exitCode === null && child.signalCode === null) {
        child.kill('SIGKILL')
      }
    }
    closeSync(log)
    rmSync(controlDir, { recursive: true, force: true })
  }

  const supervise = (child: ChildProcess) => {
    child.once('exit', async (code, signal) => {
      if (stopping) return
      const exit = { code, signal, at: new Date().toISOString() }
      const next = restarts < MAX_RESTARTS ? await launch() : null
      restarts += 1
      if (next && !stopping) supervise(next)
      else down = exit
      for (const listener of listeners) listener({ ...exit, restarted: !!next })
    })
  }

  const first = await launch()
  if (!first) {
    await stop()
    throw new Error('gnome-keyring did not provide a default collection')
  }
  supervise(first)
  return {
    busAddress,
    exited: () => down,
    onExit: (listener) => listeners.push(listener),
    stop,
  }
}
