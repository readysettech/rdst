import { type ChildProcess, execFile, spawn } from 'node:child_process'
import { createWriteStream } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * The desktop's system tray and a screen for it. A headless run gets its own
 * Xvfb, so the app runs on a real X display instead of the headless platform,
 * and `tray_host.py` stands in for the desktop panel on the run's private D-Bus
 * session: the app registers its tray icon there, and `rt tray` reads and
 * clicks the icon's menu the way a panel does. What a panel would draw is not
 * tested; what each item does is.
 */
const HOST = join(dirname(fileURLToPath(import.meta.url)), 'tray_host.py')
const READY_TIMEOUT_MS = 10_000

async function started(child: ChildProcess, ready: (text: string) => boolean) {
  return await new Promise<string>((resolve, reject) => {
    let output = ''
    const timer = setTimeout(
      () => reject(new Error('did not start in time')),
      READY_TIMEOUT_MS
    )
    const read = (chunk: Buffer) => {
      output += chunk
      if (ready(output)) {
        clearTimeout(timer)
        resolve(output)
      }
    }
    child.stdout?.on('data', read)
    child.stdio[3]?.on('data', read)
    child.once('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    child.once('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`exited with code ${code}`))
    })
  })
}

/** An X display of its own, or why there is none. */
export async function startDisplay(
  env: NodeJS.ProcessEnv,
  logFile: string
): Promise<{ display: string; stop: () => void } | { unavailable: string }> {
  const xvfb = spawn(
    'Xvfb',
    ['-displayfd', '3', '-screen', '0', '1440x900x24', '-nolisten', 'tcp'],
    { env, stdio: ['ignore', 'ignore', 'pipe', 'pipe'] }
  )
  xvfb.stderr?.pipe(createWriteStream(logFile, { flags: 'a' }))
  try {
    const output = await started(xvfb, (text) => text.includes('\n'))
    return { display: `:${output.trim()}`, stop: () => xvfb.kill() }
  } catch (error) {
    xvfb.kill()
    return { unavailable: `Xvfb: ${(error as Error).message}` }
  }
}

export interface TrayHost {
  menu(path?: string): Promise<unknown>
  stop(): void
}

/** The tray host on the session bus in `env`, or why there is none. */
export async function startTrayHost(
  env: NodeJS.ProcessEnv,
  runDir: string
): Promise<TrayHost | { unavailable: string }> {
  const items = join(runDir, 'tray-items.json')
  const host = spawn('python3', [HOST, 'watch', items], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  host.stderr?.pipe(createWriteStream(join(runDir, 'tray.log'), { flags: 'a' }))
  try {
    await started(host, (text) => text.includes('ready'))
  } catch (error) {
    host.kill()
    return { unavailable: `tray host: ${(error as Error).message}` }
  }
  return {
    menu: (path) =>
      new Promise((resolve, reject) => {
        execFile(
          'python3',
          [HOST, 'menu', items, ...(path ? [path] : [])],
          { env, timeout: 15_000 },
          (error, stdout) => {
            if (error) reject(error)
            else resolve(JSON.parse(stdout))
          }
        )
      }),
    stop: () => host.kill(),
  }
}
