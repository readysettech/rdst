import { type ChildProcess, spawn } from 'node:child_process'
import {
  createWriteStream,
  existsSync,
  readdirSync,
  readFileSync,
} from 'node:fs'
import { createServer } from 'node:net'
import { basename, join } from 'node:path'
import {
  _electron,
  type Browser,
  type BrowserContext,
  chromium,
  type ElectronApplication,
  type Page,
} from '@playwright/test'

import { paths } from './paths.ts'
import { startDisplay, startTrayHost } from './tray.ts'

export type Mode = 'browser' | 'desktop'

export interface AppOptions {
  mode: Mode
  runDir: string
  runId: string
  home: string
  /** Packaged desktop executable, e.g. dist/linux-unpacked/rdst-desktop. */
  desktopExecutable?: string
  headless?: boolean
  /** Session bus of the run's own Secret Service. */
  busAddress: string
  /** A non-production keyservice for account and email flows. */
  keyserviceUrl?: string
  attestationSecret?: string
  /** Point outbound HTTP(S) at a dead proxy, so the internet is unreachable. */
  offline?: boolean
}

/** One launch of the application: the page and the context that owns it. */
export interface Launch {
  context: BrowserContext
  page: Page
  baseUrl: string
}

/** Runs against a new context before the application's first navigation. */
export type PrepareContext = (context: BrowserContext) => Promise<void>

export interface AppUnderTest {
  readonly mode: Mode
  launch(prepare: PrepareContext): Promise<Launch>
  /** Stop the whole application, including its backend. */
  shutdown(): Promise<ShutdownResult>
  /** SIGKILL the backend without telling the shell, as a crash would. */
  killBackend(): Promise<number[]>
  resize(width: number, height: number): Promise<void>
  describe(): Record<string, unknown>
  /** List the native menu, or click the item at a "View > Actual Size" path. */
  menu?(path?: string): Promise<unknown>
  /** Launch a second instance with a URL, as the OS does for rdst:// links. */
  deepLink?(url: string): Promise<{ exit_code: number | null; output: string }>
  setOffline(offline: boolean): void
  /** Window state from the main process, readable while a renderer is gone. */
  windows?(): Promise<{ crashed: boolean; url: string; visible: boolean }[]>
  /** List the tray icon's menu, or click the item at a "Monitoring > Pause all" path. */
  tray?(path?: string): Promise<unknown>
  /** Stop what outlives application restarts, once the session ends. */
  dispose?(): void
  /** The backend's own HTTP address, when the page reaches it some other way. */
  backendUrl?(): string | undefined
}

const READY_TIMEOUT_MS = 120_000
const SHUTDOWN_TIMEOUT_MS = 15_000

/** How the application stopped; a forced stop is evidence, not a crash. */
export interface ShutdownResult {
  clean: boolean
  waited_ms: number
  forced_pids?: number[]
}

/** A process and all its descendants, leaves first. Linux only. */
function processTree(root: number): number[] {
  if (process.platform !== 'linux') return [root]
  const children = new Map<number, number[]>()
  for (const entry of readdirSync('/proc')) {
    const pid = Number(entry)
    if (!Number.isInteger(pid)) continue
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, 'utf8')
      const ppid = Number(stat.slice(stat.lastIndexOf(')') + 2).split(' ')[1])
      children.set(ppid, [...(children.get(ppid) ?? []), pid])
    } catch {}
  }
  const tree: number[] = []
  const visit = (pid: number) => {
    for (const child of children.get(pid) ?? []) visit(child)
    tree.push(pid)
  }
  visit(root)
  return tree
}

function forceKill(root: number) {
  const pids = processTree(root)
  for (const pid of pids) {
    try {
      process.kill(pid, 'SIGKILL')
    } catch {}
  }
  return pids
}

/** Wait for a clean stop, then kill the process tree rooted at `root`. */
async function boundedStop(
  stop: () => Promise<unknown>,
  root: number | undefined
): Promise<ShutdownResult> {
  const started = Date.now()
  const clean = await Promise.race([
    Promise.resolve()
      .then(stop)
      .then(
        () => true,
        () => true
      ),
    new Promise<boolean>((resolve) =>
      setTimeout(() => resolve(false), SHUTDOWN_TIMEOUT_MS)
    ),
  ])
  const waited_ms = Date.now() - started
  if (clean || root === undefined) return { clean, waited_ms }
  return { clean, waited_ms, forced_pids: forceKill(root) }
}

/** Marks every process the driver starts so cleanup can find survivors. */
export const RUN_MARKER = 'RDST_RELENTLESS_RUN_ID'

export function appEnvironment(options: AppOptions): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    HOME: options.home,
    XDG_CONFIG_HOME: join(options.home, '.config'),
    XDG_DATA_HOME: join(options.home, '.local', 'share'),
    XDG_CACHE_HOME: join(options.home, '.cache'),
    RDST_TESTING: '1',
    RDST_TELEMETRY: 'off',
    DBUS_SESSION_BUS_ADDRESS: options.busAddress,
  }
  // The app starts the way a new user's does: no AI credentials, and no
  // provider endpoint or keyring override inherited from the test host.
  for (const name of Object.keys(env)) {
    if (
      /^(ANTHROPIC_|CLAUDE|RDST_RELENTLESS_|RDST_KEYSERVICE_URL$|RDST_ATTESTATION_SECRET$)/.test(
        name
      )
    ) {
      delete env[name]
    }
  }
  delete env.PYTHON_KEYRING_BACKEND
  if (options.keyserviceUrl) env.RDST_KEYSERVICE_URL = options.keyserviceUrl
  if (options.attestationSecret) {
    env.RDST_ATTESTATION_SECRET = options.attestationSecret
  }
  env[RUN_MARKER] = options.runId
  // The renderer must come from the app's own static server, and a parent
  // Electron host (an editor, a terminal) must not turn the app into Node.
  delete env.ELECTRON_RENDERER_URL
  delete env.ELECTRON_RUN_AS_NODE
  delete env.RDST_DESKTOP_SMOKE
  if (options.offline) {
    const dead = 'http://127.0.0.1:9'
    for (const name of [
      'HTTP_PROXY',
      'HTTPS_PROXY',
      'http_proxy',
      'https_proxy',
    ]) {
      env[name] = dead
    }
    // Local traffic still works: the app's own API, the databases, Docker.
    env.NO_PROXY = env.no_proxy =
      '127.0.0.1,localhost,::1,postgres,mysql,bastion'
  }
  // Keep uv and Playwright caches shared; only RDST state is isolated.
  env.UV_CACHE_DIR ??= join(process.env.HOME ?? '', '.cache', 'uv')
  env.PLAYWRIGHT_BROWSERS_PATH ??= join(
    process.env.HOME ?? '',
    '.cache',
    'ms-playwright'
  )
  return env
}

export async function freePort(): Promise<number> {
  const server = createServer()
  return await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}

async function waitForHealth(url: string, child: ChildProcess) {
  const deadline = Date.now() + READY_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(
        `backend exited before becoming ready (code=${child.exitCode}, signal=${child.signalCode})`
      )
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) })
      if (response.ok) return
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`timed out waiting for ${url}`)
}

/**
 * The page's scheme and host. An app scheme such as rdst-app://ui has an opaque
 * origin ("null"), so it is built from the URL's parts instead.
 */
function appBase(pageUrl: string) {
  const url = new URL(pageUrl)
  return `${url.protocol}//${url.host}`
}

/**
 * Resolve once React has rendered something, not merely when HTML loaded.
 * First-run gates replace the app shell, so this cannot wait for the shell.
 */
async function waitForShell(page: Page) {
  await page
    .locator('#root > *')
    .first()
    .waitFor({ state: 'visible', timeout: READY_TIMEOUT_MS })
}

/**
 * The web product as `rdst web` serves it: the production bundle and FastAPI
 * app on one origin, started by the same command a person runs. The origin stays fixed across restarts, as it does for a
 * person who reruns `rdst web`.
 */
class BrowserApp implements AppUnderTest {
  readonly mode = 'browser' as const
  private browser: Browser | null = null
  private context: BrowserContext | null = null
  private backend: ChildProcess | null = null
  private port = 0
  private readonly options: AppOptions

  constructor(options: AppOptions) {
    this.options = options
  }

  async launch(prepare: PrepareContext): Promise<Launch> {
    if (!existsSync(join(paths.distDir, 'index.html'))) {
      throw new Error(
        `missing web bundle at ${paths.distDir}; run pnpm --filter rdst-web build`
      )
    }
    this.port ||= await freePort()
    await this.startBackend()
    this.browser ??= await chromium.launch({
      headless: this.options.headless ?? true,
    })
    if (!this.context) {
      this.context = await this.browser.newContext({
        viewport: { width: 1280, height: 820 },
      })
      await prepare(this.context)
    }
    const page = this.context.pages()[0] ?? (await this.context.newPage())
    const baseUrl = `http://127.0.0.1:${this.port}`
    await page.goto(baseUrl)
    await waitForShell(page)
    return { context: this.context, page, baseUrl }
  }

  private async startBackend() {
    if (this.backend && this.backend.exitCode === null) return
    const log = createWriteStream(join(this.options.runDir, 'backend.log'), {
      flags: 'a',
    })
    const child = spawn(
      'uv',
      [
        'run',
        '--directory',
        paths.rdstDir,
        'python',
        'rdst.py',
        'web',
        '--ui',
        'dist',
        '--no-browser',
        '--host',
        '127.0.0.1',
        '--port',
        String(this.port),
      ],
      {
        env: {
          ...appEnvironment(this.options),
          RDST_WEB_DIST_DIR: paths.distDir,
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: true,
      }
    )
    child.stdout?.pipe(log)
    child.stderr?.pipe(log)
    this.backend = child
    await waitForHealth(`http://127.0.0.1:${this.port}/health`, child)
  }

  private async stopBackend(signal: NodeJS.Signals): Promise<ShutdownResult> {
    const child = this.backend
    this.backend = null
    if (!child?.pid || child.exitCode !== null) {
      return { clean: true, waited_ms: 0 }
    }
    const exited = new Promise((resolve) => child.once('exit', resolve))
    try {
      process.kill(-child.pid, signal)
    } catch {}
    return boundedStop(() => exited, child.pid)
  }

  async shutdown() {
    return this.stopBackend('SIGTERM')
  }

  async killBackend() {
    const pid = this.backend?.pid
    await this.stopBackend('SIGKILL')
    return pid ? [pid] : []
  }

  setOffline(offline: boolean) {
    this.options.offline = offline
  }

  /** Tear down the browser too; only the daemon's final stop does this. */
  async closeBrowser() {
    await this.context?.close().catch(() => {})
    await this.browser?.close().catch(() => {})
    this.context = null
    this.browser = null
  }

  async resize(width: number, height: number) {
    for (const page of this.context?.pages() ?? []) {
      await page.setViewportSize({ width, height })
    }
  }

  describe() {
    return {
      backend: 'rdst web --ui dist, run from the rdst source tree',
      bundle: paths.distDir,
      port: this.port,
    }
  }
}

/**
 * The packaged desktop app: Electron shell, bundled sidecar, and the local
 * static server that proxies /api. Nothing is bypassed.
 */
class DesktopApp implements AppUnderTest {
  readonly mode = 'desktop' as const
  private electron: ElectronApplication | null = null
  // A headless run's own X display and the tray host; both outlive restarts.
  private screen: Awaited<ReturnType<typeof startDisplay>> | null = null
  private trayHost: Awaited<ReturnType<typeof startTrayHost>> | null = null
  private readonly options: AppOptions
  private readonly executable: string

  constructor(options: AppOptions) {
    this.options = options
    const executable = options.desktopExecutable
    if (!executable || !existsSync(executable)) {
      throw new Error(
        `desktop mode needs --desktop-exec pointing at a packaged build (got ${executable ?? 'nothing'}); ` +
          'build one with pnpm --filter rdst-desktop package:linux and use dist/linux-unpacked/rdst-desktop'
      )
    }
    this.executable = executable
  }

  private get headless() {
    return this.options.headless ?? true
  }

  /**
   * Headless runs get no display at all, so neither the window nor anything
   * the app spawns (a browser for an external link) can reach a screen.
   * Blank rather than delete: Playwright merges the launch environment over
   * this process's own.
   */
  private environment() {
    const env = appEnvironment(this.options)
    // A packaged flavor names its managed sandbox after its instance, as
    // rdst-desktop-cl names rdst-readyset-sandbox-cl, so the host CI reaches
    // the sandbox at follows the executable's flavor.
    const instance = basename(this.executable).match(
      /^rdst-desktop-(\w+)$/
    )?.[1]
    if (instance && env.RDST_DOCKER_PUBLISHED_HOST === 'rdst-readyset-sandbox')
      env.RDST_DOCKER_PUBLISHED_HOST = `rdst-readyset-sandbox-${instance}`
    if (this.headless) {
      env.DISPLAY =
        this.screen && 'display' in this.screen ? this.screen.display : ''
      env.WAYLAND_DISPLAY = ''
      env.ELECTRON_OZONE_PLATFORM_HINT = ''
    }
    return env
  }

  private platformArgs() {
    if (!this.headless) return []
    return this.screen && 'display' in this.screen
      ? ['--ozone-platform=x11']
      : ['--ozone-platform=headless']
  }

  private async surroundings() {
    if (this.headless && !this.screen) {
      this.screen = await startDisplay(
        appEnvironment(this.options),
        join(this.options.runDir, 'xvfb.log')
      )
    }
    this.trayHost ??= await startTrayHost(
      appEnvironment(this.options),
      this.options.runDir
    )
  }

  async launch(prepare: PrepareContext): Promise<Launch> {
    const log = createWriteStream(join(this.options.runDir, 'backend.log'), {
      flags: 'a',
    })
    await this.surroundings()
    const electron = await _electron.launch({
      executablePath: this.executable,
      args: ['--no-sandbox', '--disable-dev-shm-usage', ...this.platformArgs()],
      env: this.environment() as Record<string, string>,
      timeout: READY_TIMEOUT_MS,
    })
    electron.process().stdout?.pipe(log)
    electron.process().stderr?.pipe(log)
    this.electron = electron
    // The window loads only after the sidecar is ready, so this still runs
    // before the renderer's first request.
    await prepare(electron.context())
    const page = await electron.firstWindow({ timeout: READY_TIMEOUT_MS })
    await waitForShell(page)
    return {
      context: electron.context(),
      page,
      baseUrl: appBase(page.url()),
    }
  }

  async shutdown() {
    const electron = this.electron
    this.electron = null
    if (!electron) return { clean: true, waited_ms: 0 }
    // close() quits through the app's before-quit handler, which stops the
    // sidecar. A quit that never finishes is killed and reported. An app the
    // user already quit has no process left to ask for.
    let pid: number | undefined
    try {
      pid = electron.process().pid
    } catch {}
    return boundedStop(() => electron.close(), pid)
  }

  async killBackend() {
    const pids = findMarkedProcesses(this.options.runId).filter(({ cmdline }) =>
      /\bweb\b.*--ui\s+none/.test(cmdline)
    )
    for (const { pid } of pids) {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {}
    }
    return pids.map(({ pid }) => pid)
  }

  setOffline(offline: boolean) {
    this.options.offline = offline
  }

  /** Read from the running sidecar's arguments, since a respawn may pick a new port. */
  backendUrl() {
    for (const { cmdline } of findMarkedProcesses(this.options.runId)) {
      const match = cmdline.match(/\bweb\b.*--host (\S+) --port (\d+)/)
      if (match) return `http://${match[1]}:${match[2]}`
    }
    return undefined
  }

  async deepLink(url: string) {
    if (!this.electron) throw new Error('application is not running')
    // The running instance holds the single-instance lock, so this one should
    // hand the URL over and quit.
    const child = spawn(
      this.executable,
      ['--no-sandbox', '--disable-dev-shm-usage', ...this.platformArgs(), url],
      { env: this.environment(), stdio: ['ignore', 'pipe', 'pipe'] }
    )
    let output = ''
    child.stdout?.on('data', (chunk) => {
      output += chunk
    })
    child.stderr?.on('data', (chunk) => {
      output += chunk
    })
    const exitCode = await new Promise<number | null>((resolve) => {
      const timer = setTimeout(() => {
        child.kill('SIGKILL')
        resolve(null)
      }, 20_000)
      child.once('exit', (code) => {
        clearTimeout(timer)
        resolve(code)
      })
    })
    return { exit_code: exitCode, output: output.slice(-2_000) }
  }

  async menu(path?: string) {
    if (!this.electron) throw new Error('application is not running')
    return this.electron.evaluate(({ Menu, BrowserWindow }, target) => {
      const root = Menu.getApplicationMenu()
      if (!root) return { error: 'the app has no native menu' }
      const name = (item: Electron.MenuItem) =>
        item.label || item.role || item.type
      const tree = (menu: Electron.Menu): unknown[] =>
        menu.items
          .filter((item) => item.type !== 'separator')
          .map((item) =>
            item.submenu ? { [name(item)]: tree(item.submenu) } : name(item)
          )
      if (!target) return tree(root)
      let items = root.items
      let found: Electron.MenuItem | undefined
      for (const part of target.split('>').map((p) => p.trim().toLowerCase())) {
        found = items.find(
          (item) =>
            item.label.toLowerCase() === part ||
            item.role?.toLowerCase() === part
        )
        if (!found)
          return { error: `no menu item "${part}"`, available: items.map(name) }
        items = found.submenu?.items ?? []
      }
      const window = BrowserWindow.getAllWindows()[0]
      found?.click(undefined, window, window?.webContents)
      return { clicked: target }
    }, path)
  }

  async windows() {
    if (!this.electron) throw new Error('application is not running')
    return this.electron.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().map((w) => ({
        crashed: w.webContents.isCrashed(),
        url: w.webContents.getURL(),
        visible: w.isVisible(),
      }))
    )
  }

  async resize(width: number, height: number) {
    await this.electron?.evaluate(
      ({ BrowserWindow }, [w, h]) => {
        BrowserWindow.getAllWindows()[0]?.setSize(w, h)
      },
      [width, height] as const
    )
  }

  async tray(path?: string) {
    if (!this.trayHost || 'unavailable' in this.trayHost) {
      return { error: `no tray in this run (${this.trayHost?.unavailable})` }
    }
    return this.trayHost.menu(path)
  }

  dispose() {
    if (this.trayHost && 'stop' in this.trayHost) this.trayHost.stop()
    if (this.screen && 'stop' in this.screen) this.screen.stop()
  }

  describe() {
    const screen = this.screen
    return {
      backend: 'bundled sidecar spawned by the Electron main process',
      display: !this.headless
        ? 'the desktop session'
        : screen && 'display' in screen
          ? `a private X display (${screen.display}); no window on your screen, native OS dialogs cannot open`
          : `headless (no window on screen; native OS dialogs cannot open; ${screen?.unavailable ?? 'not started'})`,
      tray:
        this.trayHost && 'menu' in this.trayHost
          ? 'rt tray lists the tray menu; rt tray "A > B" clicks an item'
          : `unavailable (${this.trayHost?.unavailable ?? 'not started'})`,
      executable: this.executable,
    }
  }
}

export function createApp(options: AppOptions): AppUnderTest & {
  closeBrowser?: () => Promise<void>
} {
  return options.mode === 'desktop'
    ? new DesktopApp(options)
    : new BrowserApp(options)
}

export interface MarkedProcess {
  pid: number
  cmdline: string
}

/** Processes carrying this run's marker. Linux only; elsewhere it is empty. */
export function findMarkedProcesses(runId: string): MarkedProcess[] {
  if (process.platform !== 'linux') return []
  const needle = `${RUN_MARKER}=${runId}`
  const found: MarkedProcess[] = []
  for (const entry of readdirSync('/proc')) {
    const pid = Number(entry)
    if (!Number.isInteger(pid) || pid === process.pid) continue
    try {
      const environ = readFileSync(`/proc/${pid}/environ`, 'utf8')
      if (!environ.split('\0').includes(needle)) continue
      const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf8')
        .split('\0')
        .join(' ')
        .trim()
      found.push({ pid, cmdline })
    } catch {}
  }
  return found
}
