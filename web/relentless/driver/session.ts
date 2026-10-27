import {
  appendFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { join, resolve } from 'node:path'
import {
  type Browser,
  type BrowserContext,
  chromium,
  type Locator,
  type Page,
} from '@playwright/test'

import {
  type AppUnderTest,
  createApp,
  findMarkedProcesses,
  type Launch,
  type Mode,
  type PrepareContext,
  RUN_MARKER,
} from './app.ts'
import { MAX_WAIT_SECONDS, minutesLeft, waitSeconds } from './budget.ts'
import {
  type EvidenceKind,
  EvidenceLog,
  redactIssuedTokens,
} from './evidence.ts'
import { type SecretService, startSecretService } from './keyring.ts'
import { type MailConfig, waitForMail } from './mail.ts'
import {
  GOTO_HOSTS,
  isDeepLink,
  linkedFromMail,
  opensFromApp,
} from './reach.ts'
import {
  databaseTargets,
  execSql,
  installBastionKey,
  installCodingAgent,
  prepareTesterRole,
  type Service,
  setPaused,
  writeFixtures,
} from './targets.ts'

/** Logs the app and driver write as they run, unredacted. */
const RAW_LOGS = [
  'backend.log',
  'daemon.log',
  'keyring.log',
  'tray.log',
  'xvfb.log',
]

export interface SessionOptions {
  mode: Mode
  runDir: string
  runId: string
  desktopExecutable?: string
  headless?: boolean
  /** Values the agent can type with fill-secret but never sees. */
  secrets: Partial<Record<SecretName, string>>
  mail: MailConfig | { unavailable: string }
  /** A directory copied into the run's HOME first, for returning-user runs. */
  homeFrom?: string
  /** Show the first-run pop-ups the app holds back under automation. */
  firstRunPopups?: boolean
  /** Allow `rt eval`, arbitrary script in the page; only the self-test does. */
  pageScripts?: boolean
  /** When the run's time limit ends, in epoch milliseconds. */
  deadline?: number
}

export const SECRET_NAMES = ['anthropic-api-key'] as const
export type SecretName = (typeof SECRET_NAMES)[number]

export interface CommandInput {
  pos: string[]
  flags: Record<string, string | boolean>
}

const DEFAULT_TIMEOUT_MS = 10_000
const MAIL_TIMEOUT_MS = 90_000
const MAX_TIMEOUT_MS = 120_000
const DEFAULT_SNAPSHOT_CHARS = 30_000
const SEVERITIES = ['P1', 'P2', 'P3', 'P4']
const VERDICTS = ['passed', 'failed', 'blocked']

export class CommandError extends Error {}

function flag(input: CommandInput, name: string) {
  const value = input.flags[name]
  return typeof value === 'string' ? value : undefined
}

const PHASES = ['setup', 'action', 'observe', 'recover', 'cleanup']

function required(input: CommandInput, index: number, what: string) {
  const value = input.pos[index]
  if (value === undefined) throw new CommandError(`missing ${what}`)
  return value
}

function timeoutOf(input: CommandInput, fallback = DEFAULT_TIMEOUT_MS) {
  const raw = Number(flag(input, 'timeout') ?? fallback)
  return Math.min(Math.max(raw, 100), MAX_TIMEOUT_MS)
}

function truncate(text: string, limit: number) {
  return text.length > limit
    ? { text: text.slice(0, limit), truncated: true, total_chars: text.length }
    : { text, truncated: false }
}

/** A bare `e12`, or `f2e12` inside a frame, is a ref from the last `snapshot`; anything else a
 * selector. */
function locate(page: Page, target: string): Locator {
  return /^(?:f\d+)?e\d+$/.test(target)
    ? page.locator(`aria-ref=${target}`)
    : page.locator(target)
}

export class Session {
  readonly options: SessionOptions
  readonly evidence: EvidenceLog
  private readonly app: AppUnderTest & { closeBrowser?: () => Promise<void> }
  private readonly secretService: SecretService
  private readonly secretValues: string[]
  // A trace records typed text and request bodies verbatim, so a run that
  // types a secret keeps screenshots and events but no trace.
  private readonly tracing: boolean
  private current: Launch | null = null
  private readonly bastion: ReturnType<typeof installBastionKey>
  private readonly startedAt = Date.now()
  // The tab commands act on; null means the application's main window.
  private activePage: Page | null = null
  // Stands in for the person's own browser when a desktop run opens a link
  // from an email, as the desktop app's sign-in expects.
  private system: { browser: Browser; context: BrowserContext } | null = null
  // Every link this run's emails held, read or opened.
  private readonly mailLinks = new Set<string>()
  private readonly paused = new Set<Service>()
  private resolvedTarget: string | null = null
  private lastSnapshot = ''
  private mailCount = 0
  private screenshotCount = 0
  private findingCount = 0
  /** The flow a mission last reported, for findings filed after its outcome. */
  private lastFlow: string | undefined
  private launchCount = 0
  private evidenceCursor = 0

  private constructor(
    options: SessionOptions,
    secretService: SecretService,
    bastion: ReturnType<typeof installBastionKey>
  ) {
    this.options = options
    this.secretService = secretService
    this.bastion = bastion
    const typed = Object.values(options.secrets).filter(
      (value): value is string => Boolean(value)
    )
    this.secretValues =
      'apiKey' in options.mail
        ? [
            ...typed,
            options.mail.apiKey,
            ...(options.mail.attestationSecret
              ? [options.mail.attestationSecret]
              : []),
          ]
        : typed
    this.tracing = typed.length === 0
    this.evidence = new EvidenceLog(
      join(options.runDir, 'events.jsonl'),
      (text) => this.redact(text)
    )
    secretService.onExit((exit) => {
      this.evidence.record('lifecycle', {
        event: exit.restarted ? 'keychain_restarted' : 'keychain_died',
        ...exit,
      })
    })
    this.app = createApp({
      ...options,
      home: join(options.runDir, 'home'),
      busAddress: secretService.busAddress,
      ...('apiKey' in options.mail
        ? {
            keyserviceUrl: options.mail.keyserviceUrl,
            attestationSecret: options.mail.attestationSecret,
          }
        : {}),
    })
  }

  static async open(options: SessionOptions) {
    const home = join(options.runDir, 'home')
    mkdirSync(join(options.runDir, 'screenshots'), { recursive: true })
    mkdirSync(home, { recursive: true })
    if (options.homeFrom) cpSync(options.homeFrom, home, { recursive: true })
    writeFixtures(join(options.runDir, 'fixtures'))
    await prepareTesterRole()
    installCodingAgent(home)
    const secretService = await startSecretService(
      {
        ...process.env,
        HOME: home,
        XDG_DATA_HOME: join(home, '.local', 'share'),
        [RUN_MARKER]: options.runId,
      },
      join(options.runDir, 'keyring.log')
    )
    return new Session(options, secretService, installBastionKey(home))
  }

  redact(text: string) {
    let redacted = text
    for (const value of this.secretValues) {
      redacted = redacted.split(value).join('[REDACTED]')
    }
    return redactIssuedTokens(redacted)
  }

  private get page(): Page {
    if (!this.current) throw new CommandError('application is not running')
    if (this.activePage && !this.activePage.isClosed()) return this.activePage
    this.activePage = null
    return this.current.page
  }

  /** Locate TARGET and remember a replayable form of it for the command log. */
  private async target(target: string): Promise<Locator> {
    this.resolvedTarget = target
    if (/^(?:f\d+)?e\d+$/.test(target)) {
      // Read the role and name from the snapshot the ref came from; taking
      // another snapshot here would renumber the refs.
      const line = this.lastSnapshot
        .split('\n')
        .find((entry) => entry.includes(`[ref=${target}]`))
      const match = line?.match(/^\s*- ([\w-]+)(?: "((?:[^"\\]|\\.)*)")?/)
      if (match) {
        this.resolvedTarget = match[2]
          ? `role=${match[1]}[name="${match[2]}"]`
          : `role=${match[1]}`
      }
    }
    return locate(this.page, target)
  }

  /** The replayable target of the last command, for the command log. */
  takeResolvedTarget() {
    const target = this.resolvedTarget
    this.resolvedTarget = null
    return target
  }

  async launch() {
    const prepare: PrepareContext = async (context) => {
      await this.evidence.attach(context)
      if (this.options.firstRunPopups) {
        await context.addInitScript(() => {
          ;(
            window as { __RDST_FIRST_RUN_POPUPS__?: boolean }
          ).__RDST_FIRST_RUN_POPUPS__ = true
        })
      }
    }
    let launch: Launch
    try {
      launch = await this.app.launch(prepare)
    } catch (error) {
      // A launch can lose its DevTools connection before the window opens. A
      // second attempt tells that apart from an app that cannot start, and the
      // evidence keeps the first failure.
      this.evidence.record('lifecycle', {
        event: 'launch_failed',
        error: String(error).split('\n')[0],
      })
      await this.app.shutdown()
      await new Promise((resolve) => setTimeout(resolve, 2_000))
      launch = await this.app.launch(prepare)
    }
    this.launchCount += 1
    this.activePage = null
    if (this.tracing) {
      await launch.context.tracing.start({ screenshots: true, snapshots: true })
    }
    this.current = launch
    this.evidence.record('lifecycle', {
      event: 'launched',
      launch: this.launchCount,
      base_url: launch.baseUrl,
    })
  }

  private async stopTracing() {
    const context = this.current?.context
    if (!context || !this.tracing) return null
    const file = join(this.options.runDir, `trace-${this.launchCount}.zip`)
    try {
      await context.tracing.stop({ path: file })
      return file
    } catch {
      return null
    }
  }

  /** Where the page is and what the action just caused. */
  private async outcome(before: number, extra: Record<string, unknown> = {}) {
    // Give responses triggered by the action a moment to be recorded.
    await new Promise((resolve) => setTimeout(resolve, 150))
    const problems = this.evidence
      .since(before, ['page_error', 'console', 'request_failed'])
      .filter((event) => event.kind !== 'console' || event.level === 'error')
      .slice(0, 3)
    const apiErrors = this.evidence
      .since(before, ['api'])
      .filter((event) => Number(event.status) >= 400)
      .slice(0, 3)
    let title = ''
    let url: string | undefined
    try {
      url = this.page.url()
      title = await this.page.title()
    } catch {}
    return {
      url,
      title,
      evidence_cursor: this.evidence.cursor,
      new_evidence: this.evidence.counts(before),
      problems: [...problems, ...apiErrors],
      ...extra,
    }
  }

  async run(name: string, input: CommandInput): Promise<unknown> {
    this.resolvedTarget = null
    const before = this.evidence.cursor
    const page = () => this.page
    const timeout = timeoutOf(input)
    switch (name) {
      case 'status':
        return {
          mode: this.options.mode,
          run_dir: this.options.runDir,
          url: this.current?.page.url(),
          base_url: this.current?.baseUrl,
          launches: this.launchCount,
          minutes_left: minutesLeft(this.options.deadline),
          // The page's zoom: 1 at actual size on the run's display.
          device_pixel_ratio: await this.current?.page
            .evaluate(() => window.devicePixelRatio)
            .catch(() => undefined),
          // An app quit from its own menu or tray leaves the driver running.
          windows: await this.app
            .windows?.()
            .catch(
              () => 'the app is not running; rt restart launches it again'
            ),
          evidence_cursor: this.evidence.cursor,
          evidence_counts: this.evidence.counts(),
          findings: this.findingCount,
          dependencies: {
            ...this.app.describe(),
            ai: this.options.secrets['anthropic-api-key']
              ? 'real Anthropic API; the app starts without a key, enter it through the UI with rt fill-secret TARGET anthropic-api-key'
              : 'no Anthropic key provided; use Readyset-hosted AI when mail is available',
            keyring: this.secretService.exited()
              ? `DOWN: the run's keychain died at ${this.secretService.exited()?.at}; saved secrets cannot persist, so do not file findings about lost secrets`
              : 'real Secret Service (gnome-keyring) on a private D-Bus session; it survives app restarts',
            trace: this.tracing
              ? 'Playwright trace per launch'
              : 'off, because this run can type a secret',
            first_run_popups: this.options.firstRunPopups
              ? 'shown as a new user sees them'
              : 'held back, as the app does under automation; open each step yourself',
            databases: 'rt db list',
            coding_agent:
              'a stand-in Claude Code in ~/.local/bin/claude; RDST can search with it, and it finds the four projects under ~/work',
            ssh_bastion: this.bastion
              ? 'running; see rt db list'
              : 'not running',
            mail:
              'unavailable' in this.options.mail
                ? `unavailable (${this.options.mail.unavailable}); do not sign up for a Readyset account`
                : `real inbox; get an address with rt mail address, keyservice ${this.options.mail.keyserviceUrl}`,
          },
          tabs: this.tabList().length,
        }
      case 'snapshot': {
        const scope = input.pos[0] ? locate(page(), input.pos[0]) : page()
        const depth = flag(input, 'depth')
        const snapshot = await scope.ariaSnapshot({
          mode: 'ai',
          timeout,
          ...(depth ? { depth: Number(depth) } : {}),
        })
        this.lastSnapshot = snapshot
        const limit = Number(flag(input, 'max-chars') ?? DEFAULT_SNAPSHOT_CHARS)
        return { url: page().url(), ...truncate(snapshot, limit) }
      }
      case 'screenshot': {
        this.screenshotCount += 1
        const label = (flag(input, 'label') ?? 'shot').replace(/[^\w-]+/g, '_')
        const file = join(
          this.options.runDir,
          'screenshots',
          `${String(this.screenshotCount).padStart(3, '0')}-${label}.png`
        )
        const target = input.pos[0] ? locate(page(), input.pos[0]) : page()
        await target.screenshot({
          path: file,
          timeout,
          ...(input.pos[0]
            ? {}
            : { fullPage: input.flags['full-page'] === true }),
        })
        return { path: file }
      }
      case 'goto': {
        const target = required(input, 0, 'path')
        const url = /^https?:/.test(target)
          ? target
          : new URL(target, this.current?.baseUrl).toString()
        this.mustOpen('goto', url)
        await page().goto(url, { timeout })
        return this.outcome(before)
      }
      case 'reload':
        await page().reload({ timeout })
        return this.outcome(before)
      case 'back':
        await page().goBack({ timeout })
        return this.outcome(before)
      case 'forward':
        await page().goForward({ timeout })
        return this.outcome(before)
      case 'click':
        await (await this.target(required(input, 0, 'target'))).click({
          timeout,
          button: flag(input, 'button') === 'right' ? 'right' : 'left',
          clickCount: input.flags.double === true ? 2 : 1,
        })
        return this.outcome(before)
      case 'hover':
        await (await this.target(required(input, 0, 'target'))).hover({
          timeout,
        })
        return this.outcome(before)
      case 'fill':
        await (await this.target(required(input, 0, 'target'))).fill(
          required(input, 1, 'text'),
          { timeout }
        )
        return this.outcome(before)
      case 'fill-secret': {
        const name = required(input, 1, 'secret name') as SecretName
        const value = this.options.secrets[name]
        if (!value) {
          throw new CommandError(
            `no secret ${name} in this run; available: ${Object.keys(this.options.secrets).join(', ') || 'none'}`
          )
        }
        await (await this.target(required(input, 0, 'target'))).fill(value, {
          timeout,
        })
        return this.outcome(before, { filled: name })
      }
      case 'type':
        await (
          await this.target(required(input, 0, 'target'))
        ).pressSequentially(required(input, 1, 'text'), { timeout })
        return this.outcome(before)
      case 'press': {
        const key = required(input, 0, 'key')
        const target = flag(input, 'target')
        if (target) await (await this.target(target)).press(key, { timeout })
        else await page().keyboard.press(key)
        return this.outcome(before)
      }
      case 'select':
        await (await this.target(required(input, 0, 'target'))).selectOption(
          required(input, 1, 'value'),
          { timeout }
        )
        return this.outcome(before)
      case 'check':
      case 'uncheck':
        await (await this.target(required(input, 0, 'target')))[name]({
          timeout,
        })
        return this.outcome(before)
      case 'text': {
        const target = input.pos[0] ?? '#main-content'
        const text = await locate(page(), target).innerText({ timeout })
        return truncate(text, Number(flag(input, 'max-chars') ?? 20_000))
      }
      case 'wait':
        return this.wait(input, before, timeout)
      case 'eval': {
        if (!this.options.pageScripts) {
          this.refuse(
            'eval',
            'eval runs only in the self-test; use rt storage, rt scroll, or rt wait --seconds'
          )
        }
        const value = await page().evaluate(required(input, 0, 'expression'))
        return { value }
      }
      case 'storage':
        return this.storage(input)
      case 'scroll':
        await (
          await this.target(required(input, 0, 'target'))
        ).scrollIntoViewIfNeeded({ timeout })
        return this.outcome(before)
      case 'api':
        return this.api(input)
      case 'evidence': {
        const since = input.flags.all
          ? 0
          : Number(flag(input, 'since') ?? this.evidenceCursor)
        const kinds = flag(input, 'kinds')?.split(',') as
          | EvidenceKind[]
          | undefined
        const events = this.evidence.since(since, kinds)
        this.evidenceCursor = this.evidence.cursor
        return { since, cursor: this.evidence.cursor, events }
      }
      case 'logs': {
        const lines = readFileSync(
          join(this.options.runDir, 'backend.log'),
          'utf8'
        )
          .split('\n')
          .filter(Boolean)
        const pattern = flag(input, 'grep')
        const matched = pattern
          ? lines.filter((line) => new RegExp(pattern, 'i').test(line))
          : lines
        return { lines: matched.slice(-Number(flag(input, 'last') ?? 60)) }
      }
      case 'viewport': {
        const width = Number(required(input, 0, 'width'))
        const height = Number(required(input, 1, 'height'))
        await this.app.resize(width, height)
        return this.outcome(before, { width, height })
      }
      case 'restart':
        if (input.flags.offline === true) this.app.setOffline(true)
        if (input.flags.online === true) this.app.setOffline(false)
        return this.restart(before)
      case 'upload': {
        const file = resolve(required(input, 1, 'file'))
        if (!file.startsWith(resolve(this.options.runDir))) {
          throw new CommandError(
            'upload files from the run directory, such as its fixtures/'
          )
        }
        await (await this.target(required(input, 0, 'target'))).setInputFiles(
          file,
          { timeout }
        )
        return this.outcome(before, { uploaded: file })
      }
      case 'deeplink': {
        if (!this.app.deepLink)
          throw new CommandError('deeplink is desktop-only')
        const url = required(input, 0, 'url')
        if (!isDeepLink(url)) {
          this.refuse('deeplink', 'deeplink opens rdst:// URLs only')
        }
        const result = await this.app.deepLink(url)
        this.evidence.record('lifecycle', {
          event: 'deep_link',
          url,
          exit_code: result.exit_code,
        })
        return this.outcome(before, result)
      }
      case 'crash-renderer': {
        const session = await this.page.context().newCDPSession(this.page)
        void session.send('Page.crash').catch(() => {})
        this.evidence.record('lifecycle', { event: 'renderer_crashed' })
        await new Promise((r) => setTimeout(r, 3_000))
        return {
          crashed: true,
          windows: await this.app.windows?.(),
          note: 'The driver cannot drive a crashed page, even after the app reloads it. windows shows whether the app recovered on its own; rt restart to continue.',
        }
      }
      case 'kill-backend': {
        const pids = await this.app.killBackend()
        this.evidence.record('lifecycle', { event: 'backend_killed', pids })
        return this.outcome(before, { killed: pids })
      }
      case 'tab':
        return this.tab(input, before)
      case 'menu': {
        if (!this.app.menu) throw new CommandError('menu is desktop-only')
        const path = input.pos.join(' ').trim()
        const result = await this.app.menu(path || undefined)
        return path ? this.outcome(before, { menu: result }) : { menu: result }
      }
      case 'tray': {
        if (!this.app.tray) throw new CommandError('tray is desktop-only')
        const path = input.pos.join(' ').trim()
        const result = await this.app.tray(path || undefined)
        return path ? this.outcome(before, { tray: result }) : { tray: result }
      }
      case 'db':
        return this.db(input, before)
      case 'mail':
        return this.mail(input, before)
      case 'finding':
        return this.finding(input)
      case 'phase':
        return this.phase(input)
      case 'flow':
        return this.flow(input)
      case 'finish':
        return this.finish(input)
      default:
        throw new CommandError(`unknown command ${name}`)
    }
  }

  private async wait(input: CommandInput, before: number, timeout: number) {
    const seconds = flag(input, 'seconds')
    if (seconds !== undefined) {
      const requested = Number(seconds)
      if (!(requested >= 0)) throw new CommandError('--seconds takes a number')
      const { deadline } = this.options
      const waited = waitSeconds(requested, deadline)
      await new Promise((resolve) => setTimeout(resolve, waited * 1000))
      return this.outcome(before, {
        waited_seconds: waited,
        minutes_left: minutesLeft(deadline),
        ...(waited < Math.min(requested, MAX_WAIT_SECONDS)
          ? {
              note: 'the run is nearly out of time: file what you found and rt finish',
            }
          : {}),
      })
    }
    const selector = flag(input, 'selector')
    const text = flag(input, 'text')
    const url = flag(input, 'url')
    const api = flag(input, 'api')
    if (!selector && !text && !url && !api) {
      throw new CommandError(
        'wait needs --seconds, --selector, --text, --url, or --api'
      )
    }
    const page = this.page
    if (selector) {
      const state = (flag(input, 'state') ?? 'visible') as
        | 'visible'
        | 'hidden'
        | 'attached'
        | 'detached'
      await locate(page, selector).waitFor({ state, timeout })
    }
    if (text) {
      await page.getByText(text).first().waitFor({ state: 'visible', timeout })
    }
    if (url) {
      await page.waitForURL((current) => current.href.includes(url), {
        timeout,
      })
    }
    if (api) {
      // Matches responses already recorded since --since (default: the
      // previous command), so a response that beat this call still counts.
      const since = Number(flag(input, 'since') ?? before)
      const deadline = Date.now() + timeout
      const matches = () =>
        this.evidence
          .since(since, ['api', 'stream'])
          .filter((event) => String(event.path).includes(api))
      while (matches().length === 0) {
        if (Date.now() > deadline) {
          throw new CommandError(
            `no /api response matching ${api} in ${timeout}ms`
          )
        }
        await new Promise((resolve) => setTimeout(resolve, 200))
      }
      return this.outcome(before, { matched: matches() })
    }
    return this.outcome(before)
  }

  private async api(input: CommandInput) {
    const method = required(input, 0, 'method').toUpperCase()
    const path = required(input, 1, 'path')
    if (!path.startsWith('/api/') && !path.startsWith('/health')) {
      throw new CommandError('api only reaches /api/* and /health')
    }
    const body = flag(input, 'json')
    const shown = this.current?.baseUrl ?? ''
    const direct = /^https?:/.test(shown)
    const base = direct ? shown : this.app.backendUrl?.()
    const token = direct ? undefined : this.app.backendToken?.()
    // Without the backend's address, an app-scheme page's own /api is the way in.
    const response = base
      ? await fetch(new URL(path, base), {
          method,
          headers: {
            ...(body ? { 'content-type': 'application/json' } : {}),
            ...(token ? { 'X-RDST-Token': token } : {}),
          },
          body,
          signal: AbortSignal.timeout(timeoutOf(input)),
        }).then(async (r) => ({
          status: r.status,
          contentType: r.headers.get('content-type'),
          text: await r.text(),
        }))
      : await this.page.evaluate(
          async ({ path, method, body, timeout }) => {
            const r = await fetch(path, {
              method,
              headers: body
                ? { 'content-type': 'application/json' }
                : undefined,
              body,
              signal: AbortSignal.timeout(timeout),
            })
            return {
              status: r.status,
              contentType: r.headers.get('content-type'),
              text: await r.text(),
            }
          },
          { path, method, body, timeout: timeoutOf(input) }
        )
    const text = response.text
    this.evidence.record('api', {
      method,
      path: path.split('?')[0],
      status: response.status,
      source: 'rt-api',
    })
    let json: unknown
    try {
      json = JSON.parse(text)
    } catch {}
    return {
      status: response.status,
      content_type: response.contentType,
      ...(json === undefined ? truncate(text, 20_000) : { json }),
    }
  }

  private tabList() {
    const own = (this.current?.context.pages() ?? []).map((page) => ({
      page,
      system: false,
    }))
    const system = (this.system?.context.pages() ?? []).map((page) => ({
      page,
      system: true,
    }))
    return [...own, ...system]
  }

  private describeTabs() {
    const active = this.current ? this.page : null
    return this.tabList().map(({ page, system }, index) => ({
      index,
      url: page.url(),
      active: page === active,
      ...(system ? { where: 'separate system browser' } : {}),
    }))
  }

  /** localStorage of the app's page, read and written as the app does. */
  private async storage(input: CommandInput) {
    const action = required(input, 0, 'get | set | remove | clear')
    const key = input.pos[1]
    if (action === 'get') {
      const value = await this.page.evaluate(
        (key) =>
          key === undefined
            ? Object.fromEntries(Object.entries(localStorage))
            : localStorage.getItem(key),
        key
      )
      return { value }
    }
    if (action === 'set') {
      const value = required(input, 2, 'value')
      await this.page.evaluate(
        ([key, value]) => localStorage.setItem(key, value),
        [required(input, 1, 'key'), value] as const
      )
      return { set: key }
    }
    if (action === 'remove') {
      await this.page.evaluate(
        (key) => localStorage.removeItem(key),
        required(input, 1, 'key')
      )
      return { removed: key }
    }
    if (action === 'clear') {
      await this.page.evaluate(() => localStorage.clear())
      return { cleared: true }
    }
    throw new CommandError(
      'storage get [KEY] | set KEY VALUE | remove KEY | clear'
    )
  }

  private mustOpen(command: string, url: string) {
    const keyservice =
      'keyserviceUrl' in this.options.mail
        ? this.options.mail.keyserviceUrl
        : undefined
    if (opensFromApp(url, this.current?.baseUrl, keyservice)) return
    // The desktop window runs with the app's privileges; a browser tab does not.
    const appWindow =
      this.options.mode !== 'browser' &&
      (this.current?.context.pages().includes(this.page) ?? false)
    if (!appWindow && linkedFromMail(url, this.mailLinks)) return
    this.refuse(
      command,
      `${command} opens the app's own pages, ${GOTO_HOSTS.join(', ')}, the run's keyservice and, outside the desktop window, the sites this run's emails link to`
    )
  }

  /** A command the tester may not run: the evidence keeps the attempt. */
  private refuse(command: string, reason: string): never {
    this.evidence.record('lifecycle', { event: 'refused', command, reason })
    throw new CommandError(reason)
  }

  private async tab(input: CommandInput, before: number) {
    const action = input.pos[0] ?? 'list'
    if (action === 'list') return { tabs: this.describeTabs() }
    if (!this.current) throw new CommandError('application is not running')
    if (action === 'new') {
      const path = input.pos[1] ?? '/'
      const url = /^https?:/.test(path)
        ? path
        : new URL(path, this.current.baseUrl).toString()
      this.mustOpen('tab new', url)
      if (this.options.mode !== 'browser') {
        throw new CommandError(
          'the desktop app has one window; tab new is browser-mode only'
        )
      }
      const page = await this.current.context.newPage()
      await page.goto(url)
      this.activePage = page
      return this.outcome(before, { tabs: this.describeTabs() })
    }
    const index = Number(required(input, 1, 'tab index'))
    const entry = this.tabList()[index]
    if (!entry) throw new CommandError(`no tab ${index}`)
    if (action === 'switch') {
      this.activePage = entry.page
      await entry.page.bringToFront()
      return this.outcome(before, { tabs: this.describeTabs() })
    }
    if (action === 'close') {
      if (entry.page === this.current.page) {
        throw new CommandError(
          'the main window cannot be closed this way; use rt restart'
        )
      }
      await entry.page.close()
      return { tabs: this.describeTabs() }
    }
    throw new CommandError('tab list | new [PATH] | switch N | close N')
  }

  private async db(input: CommandInput, before: number) {
    const action = input.pos[0] ?? 'list'
    if (action === 'list') {
      return {
        databases: databaseTargets(),
        ssh_bastion: this.bastion ?? 'not running',
        fixtures: {
          targets_csv: join(this.options.runDir, 'fixtures', 'targets.csv'),
          queries_sql: join(this.options.runDir, 'fixtures', 'queries.sql'),
        },
        paused: [...this.paused],
      }
    }
    const service = required(
      input,
      1,
      'database: postgres, mysql, or bastion'
    ) as Service
    if (!['postgres', 'mysql', 'bastion'].includes(service)) {
      throw new CommandError('database must be postgres, mysql, or bastion')
    }
    if (action === 'exec') {
      if (service === 'bastion')
        throw new CommandError('exec runs SQL on postgres or mysql')
      const sql = input.pos.slice(2).join(' ').trim()
      if (!sql) throw new CommandError('rt db exec postgres|mysql -- SQL')
      const admin = input.flags.admin === true
      const result = await execSql(service, sql, admin)
      this.evidence.record('lifecycle', {
        event: 'db_exec',
        db: service,
        admin,
        exit_code: result.code,
      })
      return {
        exit_code: result.code,
        ...truncate(result.stdout + result.stderr, 20_000),
      }
    }
    if (action === 'pause' || action === 'resume') {
      const container = await setPaused(service, action === 'pause')
      if (action === 'pause') this.paused.add(service)
      else this.paused.delete(service)
      this.evidence.record('lifecycle', {
        event: `db_${action}d`,
        db: service,
        container,
      })
      return this.outcome(before, { db: service, paused: action === 'pause' })
    }
    throw new CommandError('db list | exec DB -- SQL | pause DB | resume DB')
  }

  private async mail(input: CommandInput, before: number) {
    const config = this.options.mail
    if ('unavailable' in config) {
      throw new CommandError(`mail is unavailable: ${config.unavailable}`)
    }
    const action = required(input, 0, 'address, read, or open')
    if (action === 'address') {
      this.mailCount += 1
      const label = (input.pos[1] ?? 'rt')
        .replace(/[^a-z0-9-]/gi, '')
        .toLowerCase()
      return {
        address:
          `${label}-${this.options.runId}-${this.mailCount}@${config.domain}`.toLowerCase(),
      }
    }
    const to = required(input, 1, 'address')
    const message = await waitForMail(config, to, {
      since: this.startedAt,
      contains: flag(input, 'contains'),
      timeoutMs: timeoutOf(input, MAIL_TIMEOUT_MS),
    })
    for (const link of message.links) this.mailLinks.add(link)
    if (action === 'read') return message
    if (action !== 'open')
      throw new CommandError(
        'mail address [LABEL] | read ADDRESS | open ADDRESS'
      )
    const prefix = flag(input, 'prefix')
    const links = prefix
      ? message.links.filter((link) => link.startsWith(prefix))
      : message.links
    const link = links[Number(flag(input, 'index') ?? 0)]
    if (!link)
      throw new CommandError(
        `no matching link; links: ${message.links.join(', ')}`
      )
    if (!this.current) throw new CommandError('application is not running')
    let page: Page
    if (this.options.mode === 'browser') {
      // The web sign-in finishes in the same browser that started it.
      page = await this.current.context.newPage()
    } else {
      this.system ??= await (async () => {
        const browser = await chromium.launch({
          headless: this.options.headless ?? true,
        })
        return { browser, context: await browser.newContext() }
      })()
      page = await this.system.context.newPage()
    }
    await page.goto(link)
    this.activePage = page
    return this.outcome(before, { opened: link, subject: message.subject })
  }

  private async restart(before: number) {
    const trace = await this.stopTracing()
    const previousUrl = this.current?.page.url()
    const shutdown = await this.app.shutdown()
    this.evidence.record('lifecycle', { event: 'shutdown', trace, ...shutdown })
    await this.launch()
    return this.outcome(before, {
      previous_url: previousUrl,
      base_url: this.current?.baseUrl,
      shutdown: shutdown.clean
        ? `clean in ${shutdown.waited_ms}ms`
        : `did not quit within ${shutdown.waited_ms}ms; process tree killed`,
    })
  }

  private finding(input: CommandInput) {
    const raw = flag(input, 'json')
    if (!raw) throw new CommandError('finding needs --json')
    const finding = JSON.parse(raw) as Record<string, unknown>
    for (const field of ['title', 'severity', 'expected', 'actual', 'steps']) {
      if (!finding[field]) throw new CommandError(`finding is missing ${field}`)
    }
    if (!SEVERITIES.includes(String(finding.severity))) {
      throw new CommandError(`severity must be one of ${SEVERITIES.join(', ')}`)
    }
    this.findingCount += 1
    const id = `F-${String(this.findingCount).padStart(3, '0')}`
    const record = {
      id,
      ts: new Date().toISOString(),
      mode: this.options.mode,
      url: this.current?.page.url(),
      evidence_cursor: this.evidence.cursor,
      // In a mission, the flow the finding belongs to.
      flow: this.evidence.context.flow ?? this.lastFlow,
      ...finding,
    }
    appendFileSync(
      join(this.options.runDir, 'findings.jsonl'),
      `${JSON.stringify(record)}\n`
    )
    return { id }
  }

  /** Marks where a mission is inside a flow, so evidence can tell setup from checking. */
  private phase(input: CommandInput) {
    const flow = required(input, 0, 'flow id')
    const phase = required(input, 1, 'phase')
    if (!PHASES.includes(phase)) {
      throw new CommandError(`phase must be one of ${PHASES.join('|')}`)
    }
    this.evidence.context = { flow, phase }
    this.evidence.record('lifecycle', { event: 'phase' })
    return { flow, phase }
  }

  /** A mission's per-flow outcome; the supervisor checks every flow reported. */
  private flow(input: CommandInput) {
    const id = required(input, 0, 'flow id')
    const status = flag(input, 'status')
    if (!status || !['passed', 'failed', 'blocked'].includes(status)) {
      throw new CommandError('flow needs --status passed|failed|blocked')
    }
    const summary = flag(input, 'summary')
    if (!summary) throw new CommandError('flow needs --summary')
    const entry = { id, status, summary, at: new Date().toISOString() }
    appendFileSync(
      join(this.options.runDir, 'flows.jsonl'),
      `${JSON.stringify(entry)}\n`
    )
    this.evidence.record('lifecycle', { event: 'flow', id, status })
    this.evidence.context = {}
    this.lastFlow = id
    return entry
  }

  private finish(input: CommandInput) {
    const status = flag(input, 'status')
    if (!status || !VERDICTS.includes(status)) {
      throw new CommandError(`finish needs --status ${VERDICTS.join('|')}`)
    }
    const summary = flag(input, 'summary')
    if (!summary) throw new CommandError('finish needs --summary')
    const result = {
      status,
      summary,
      findings: this.findingCount,
      finished_at: new Date().toISOString(),
    }
    writeFileSync(
      join(this.options.runDir, 'result.json'),
      `${JSON.stringify(result, null, 2)}\n`
    )
    return result
  }

  /** Stop everything and verify no process from this run survived. */
  async stop() {
    const trace = await this.stopTracing()
    const shutdown = await this.app.shutdown()
    this.app.dispose?.()
    await this.app.closeBrowser?.()
    await this.system?.browser.close().catch(() => {})
    for (const service of this.paused) {
      await setPaused(service, false).catch(() => {})
    }
    this.current = null
    await this.secretService.stop()
    const deadline = Date.now() + 5_000
    let survivors = findMarkedProcesses(this.options.runId)
    while (survivors.length > 0 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250))
      survivors = findMarkedProcesses(this.options.runId)
    }
    for (const { pid } of survivors) {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {}
    }
    const cleanup = {
      trace,
      shutdown,
      orphans: survivors,
      orphan_check: process.platform === 'linux' ? 'performed' : 'unsupported',
    }
    // The app's and driver's own logs are written raw while the run lasts,
    // and the agent never reads them; redacted, they can become artifacts.
    for (const log of RAW_LOGS) {
      const file = join(this.options.runDir, log)
      if (existsSync(file))
        writeFileSync(file, this.redact(readFileSync(file, 'utf8')))
    }
    writeFileSync(
      join(this.options.runDir, 'cleanup.json'),
      `${JSON.stringify(cleanup, null, 2)}\n`
    )
    return cleanup
  }
}
