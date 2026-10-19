import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import type { Mode } from './driver/app.ts'
import { call, startDriver } from './driver/client.ts'
import { paths } from './driver/paths.ts'
import { databaseTargets } from './driver/targets.ts'
import { weeklyQuota } from './quota.ts'
import { changeBrief, changePacket } from './selector/change.ts'

/**
 * Run one relentless area: start an isolated app, hand it to a Claude agent
 * that may only use `rt` and read its own run directory, and decide the
 * verdict from what the run produced rather than from the agent's say-so.
 */
const { values } = parseArgs({
  options: {
    area: { type: 'string' },
    // A mission: comma-separated flow ids from flows/, run instead of an area.
    flows: { type: 'string' },
    // A mission written for this run alone, such as a baseline's verify pass.
    mission: { type: 'string' },
    // A change mission: probe what a change could break. `BASE..HEAD` is a
    // whole stack, one revision its own change; `--change-dir` is a change
    // `selector/change.ts` already wrote (change.md and change.diff).
    change: { type: 'string' },
    'change-dir': { type: 'string' },
    mode: { type: 'string', default: 'browser' },
    'desktop-exec': { type: 'string' },
    // Default: gpt-6.1-sol under pi, opus under Claude Code.
    model: { type: 'string' },
    // pi: the pi agent, its models through CLIProxyAPI. claude: Claude Code
    // with the Claude login. openrouter: Claude Code against OpenRouter's
    // Anthropic-compatible API, such as z-ai/glm-5.3-flash[1m].
    'agent-provider': { type: 'string', default: 'pi' },
    // pi's thinking level.
    thinking: { type: 'string', default: 'medium' },
    // Optional caps on the agent's spend in USD and, under pi and OpenRouter,
    // its turns; the timeout bounds a run without them.
    budget: { type: 'string' },
    'max-turns': { type: 'string' },
    'timeout-min': { type: 'string', default: '60' },
    'run-dir': { type: 'string' },
    headed: { type: 'boolean', default: false },
    'home-from': { type: 'string' },
    // The app holds its first-run pop-ups back under automation; runs show them
    // as a new user sees them unless this is set.
    'no-first-run-popups': { type: 'boolean', default: false },
  },
})

type Verdict =
  | 'passed'
  | 'failed'
  | 'blocked'
  | 'incomplete'
  | 'timeout'
  | 'budget_exhausted'
  | 'agent_error'
  | 'infra_error'

interface Flow {
  id: string
  path: string
  modes: string[]
  minutes: number
}

/** A flow file's header: `Modes:` and `Minutes:` lines under its title. */
function loadFlow(id: string): Flow {
  const path = join(paths.relentlessDir, 'flows', `${id}.md`)
  if (!existsSync(path)) throw new Error(`no flow file at ${path}`)
  const text = readFileSync(path, 'utf8')
  const field = (name: string) =>
    text.match(new RegExp(`^${name}: (.+)$`, 'm'))?.[1] ?? ''
  return {
    id,
    path,
    modes: field('Modes')
      .split(/[\s,]+/)
      .filter(Boolean),
    minutes: Number(field('Minutes')) || 10,
  }
}

const flows = (values.flows ?? '')
  .split(',')
  .map((id) => id.trim())
  .filter(Boolean)
  .map(loadFlow)
const changeMission = !!values.change || !!values['change-dir']
if (
  [!!values.area, flows.length > 0, changeMission, !!values.mission].filter(
    Boolean
  ).length !== 1
) {
  throw new Error(
    'pass one of --area, --flows, --mission, or --change/--change-dir'
  )
}
const area = values.area ?? (changeMission ? 'change' : 'mission')
// The change as the mission reads it, and its whole diff for the run directory.
const change = values['change-dir']
  ? {
      brief: readFileSync(join(values['change-dir'], 'change.md'), 'utf8'),
      diff: readFileSync(join(values['change-dir'], 'change.diff'), 'utf8'),
    }
  : values.change
    ? (() => {
        const packet = changePacket(values.change, paths.repoRoot)
        return { brief: changeBrief(packet), diff: packet.diff }
      })()
    : undefined
const mode = values.mode as Mode
let prompt: string
if (values.area) {
  const areaFile = join(paths.relentlessDir, 'areas', `${area}.md`)
  if (!existsSync(areaFile)) throw new Error(`no area file at ${areaFile}`)
  prompt = readFileSync(areaFile, 'utf8')
  // An area can declare `Modes: desktop` when it only makes sense there.
  const areaModes = prompt.match(/^Modes: (.+)$/m)?.[1]
  if (areaModes && !areaModes.split(/[\s,]+/).includes(mode)) {
    throw new Error(`area ${area} runs only in: ${areaModes}`)
  }
} else if (change) {
  prompt = changePrompt(change.brief)
} else if (values.mission) {
  prompt = readFileSync(values.mission, 'utf8')
} else {
  for (const flow of flows) {
    if (!flow.modes.includes(mode)) {
      throw new Error(`flow ${flow.id} runs only in: ${flow.modes.join(', ')}`)
    }
  }
  prompt = missionPrompt(flows)
}
const timeoutMin = Number(values['timeout-min'])
const runName =
  values.area ??
  (changeMission
    ? 'change'
    : values.mission
      ? basename(values.mission, '.md')
      : `mission-${flows.map((f) => f.id.split('/').pop()).join('+')}`)
const runDir = resolve(
  values['run-dir'] ??
    join(
      paths.runsDir,
      `${new Date().toISOString().replace(/[:.]/g, '-')}-${mode}-${runName}`
    )
)

function missionPrompt(chosen: Flow[]) {
  const bodies = chosen.map((f) => readFileSync(f.path, 'utf8').trim())
  return `# Mission

This run tests ${chosen.length === 1 ? 'one flow' : `${chosen.length} flows`}, not a whole area: ${chosen.map((f) => `\`${f.id}\``).join(', ')}. Work through them in order. Each says what to do, what to prove, and how to leave the app. Stay on the mission: go beyond a flow only to understand a failure you found in it.

As you work through a flow, mark its phase, so the evidence tells setup apart from checking:

    rt phase ID setup     getting the app to where the flow starts
    rt phase ID action    doing what the flow tests
    rt phase ID observe   checking what Prove asks
    rt phase ID recover   breaking things on purpose and watching recovery
    rt phase ID cleanup   leaving the app as the flow asks

After each flow, record its outcome:

    rt flow ID --status passed|failed|blocked --summary '...'

with the flow's full id as written above, such as \`${chosen[0].id}\`.

\`passed\`: everything under Prove held. \`failed\`: it did not; file a finding for each problem. \`blocked\`: you could not reach the flow; say why. Then call \`rt finish\` once, with \`failed\` if any flow failed.

${bodies.join('\n\n---\n\n')}`
}
/**
 * A change mission: the app already contains the change; find what it could
 * break for a user, starting from what it touches. The UX map goes into the
 * run directory, the only place the agent may read.
 */
function changePrompt(brief: string) {
  return `# Change mission

This run probes a code change, not an area. The app you are testing already contains it. Find what it could break for a user: the behavior it changes, and the pages, flows, and states that depend on what it touches, including reloads, restarts, errors, and odd input. \`UX_MAP.md\` in your run directory maps every page, flow, and piece of state; use it to decide where the change reaches. Spend your time where the change is most likely to hurt someone, file a finding for each problem, and call \`rt finish\` once, with \`failed\` if you filed any P1 or P2.

${brief}`
}
const startedAt = Date.now()

function readJson<T>(file: string): T | null {
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as T
  } catch {
    return null
  }
}

function readFindings() {
  const file = join(runDir, 'findings.jsonl')
  if (!existsSync(file)) return []
  return readFileSync(file, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map(
      (line) =>
        JSON.parse(line) as {
          id: string
          severity: string
          title: string
          flow?: string
          category?: string
          url?: string
          steps?: string[]
          expected?: string
          actual?: string
          reproduced?: string
          evidence?: { screenshots?: string[] }
        }
    )
}

type Finding = ReturnType<typeof readFindings>[number]

// The agent records screenshots by path; they are uploaded from the run's own
// screenshots directory.
const screenshotsOf = (finding: Finding) =>
  (finding.evidence?.screenshots ?? []).map((p) => `screenshots/${basename(p)}`)

/** One readable page per finding, `findings/ID.md`, for reviewers. */
function writeFindingPages(findings: Finding[]) {
  const dir = join(runDir, 'findings')
  mkdirSync(dir, { recursive: true })
  for (const f of findings) {
    const section = (heading: string, text?: string) =>
      text ? [`## ${heading}`, '', text, ''] : []
    const page = [
      `# ${f.id} ${f.severity}: ${f.title}`,
      '',
      `${mode} / ${area}${f.flow ? `, flow ${f.flow}` : ''}${f.category ? `; ${f.category}` : ''}${f.url ? `; at ${f.url}` : ''}`,
      '',
      ...section(
        'Steps',
        f.steps?.map((step, i) => `${i + 1}. ${step}`).join('\n')
      ),
      ...section('Expected', f.expected),
      ...section('Actual', f.actual),
      ...section('Reproduced', f.reproduced),
      ...section(
        'Screenshots',
        screenshotsOf(f)
          .map((p) => `- ../${p}`)
          .join('\n')
      ),
    ]
    writeFileSync(join(dir, `${f.id}.md`), page.join('\n'))
  }
}

/** The `rt` the agent sees: the CLI bound to this run's directory. */
function writeRtWrapper() {
  const bin = join(runDir, 'bin')
  mkdirSync(bin, { recursive: true })
  const wrapper = join(bin, 'rt')
  writeFileSync(
    wrapper,
    `#!/bin/sh\nexec "${process.execPath}" "${paths.cli}" "$@"\n`
  )
  chmodSync(wrapper, 0o755)
  return bin
}

function environmentSection(status: Record<string, unknown>) {
  const { postgres, mysql } = databaseTargets()
  const describe = (db: typeof postgres | typeof mysql) =>
    `${db.engine}: host ${db.host}, port ${db.port}, database ${db.database}, user ${db.user}, password ${db.password}`
  return `## This run

- Mode: ${mode}
- Run directory (screenshots land here; you may Read files under it, except home/ and the .log files, which \`rt logs\` reads): ${runDir}
- Application dependencies, as the driver reports them:
\`\`\`json
${JSON.stringify(status.dependencies, null, 2)}
\`\`\`
- Disposable databases you may connect as targets (IMDb-style sample schema,
  tables title_basics and title_ratings):
  - ${describe(postgres)}
  - ${describe(mysql)}
- An SSH jump host in front of both: \`rt db list\` has its address, user, and
  key path.

The application is already running. Start with \`rt status\` and \`rt snapshot\`.`
}

interface AgentOutcome {
  exitCode: number | null
  timedOut: boolean
  output: Record<string, any> | null
  /** Spend reported by OpenRouter for this run's key, when used. */
  providerCostUsd?: number
}

const OPENROUTER = 'https://openrouter.ai/api'
const openRouter = values['agent-provider'] === 'openrouter'
const piAgent = values['agent-provider'] === 'pi'
if (!openRouter && !piAgent && values['agent-provider'] !== 'claude') {
  throw new Error(`unknown --agent-provider ${values['agent-provider']}`)
}

function openRouterKey() {
  const file = process.env.RDST_RELENTLESS_OPENROUTER_API_KEY_FILE
  const key = (
    file
      ? readFileSync(file, 'utf8')
      : (process.env.RDST_RELENTLESS_OPENROUTER_API_KEY ?? '')
  ).trim()
  if (!key) {
    throw new Error(
      '--agent-provider openrouter needs RDST_RELENTLESS_OPENROUTER_API_KEY or its _FILE'
    )
  }
  return key
}

function cliProxyKey() {
  const file = process.env.RDST_RELENTLESS_CLIPROXY_API_KEY_FILE
  const key = (
    file
      ? readFileSync(file, 'utf8')
      : (process.env.RDST_RELENTLESS_CLIPROXY_API_KEY ?? '')
  ).trim()
  const url = process.env.RDST_RELENTLESS_CLIPROXY_URL
  const provider = process.env.RDST_RELENTLESS_PI_PROVIDER
  if (!key || !url || !provider) {
    throw new Error(
      '--agent-provider pi needs RDST_RELENTLESS_CLIPROXY_URL, RDST_RELENTLESS_CLIPROXY_API_KEY (or its _FILE), and RDST_RELENTLESS_PI_PROVIDER, the CLIProxyAPI pi extension'
    )
  }
  return { key, url, provider }
}

/**
 * The tester under pi: only the `rt` and read tools of `agent/pi-harness.ts`,
 * in a pi directory of its own so no operator settings, logins, or
 * extensions reach the session. Its JSON event stream is summarized into
 * the fields the verdict reads from Claude Code's result.
 */
async function runPi(prompt: string, env: NodeJS.ProcessEnv) {
  const proxy = cliProxyKey()
  const agentDir = mkdtempSync(join(tmpdir(), 'rt-pi-'))
  const stopFile = join(agentDir, 'stop.json')
  // The proxy can lose its upstream for minutes and fail requests until it
  // recovers; backing off 5 s doubling over 7 tries waits out about 10 minutes.
  writeFileSync(
    join(agentDir, 'settings.json'),
    JSON.stringify({
      retry: { enabled: true, maxRetries: 7, baseDelayMs: 5_000 },
    })
  )
  Object.assign(env, {
    PI_CODING_AGENT_DIR: agentDir,
    CLIPROXYAPI_BASE_URL: proxy.url,
    CLIPROXYAPI_API_KEY: proxy.key,
    CLIPROXYAPI_TRANSPORT: 'websocket-cached',
    RDST_RELENTLESS_MAX_TURNS: values['max-turns'] ?? '',
    RDST_RELENTLESS_BUDGET_USD: values.budget ?? '',
    RDST_RELENTLESS_PI_STOP_FILE: stopFile,
  })
  delete env.CLAUDE_CODE_OAUTH_TOKEN
  delete env.RDST_RELENTLESS_CLIPROXY_API_KEY
  delete env.RDST_RELENTLESS_CLIPROXY_API_KEY_FILE
  delete env.RDST_RELENTLESS_CLIPROXY_MANAGEMENT_KEY
  delete env.RDST_RELENTLESS_CLIPROXY_MANAGEMENT_KEY_FILE
  // The task goes in a file: a mission with a large diff can exceed the
  // length of one command-line argument.
  const task = join(agentDir, 'task.md')
  writeFileSync(task, prompt)
  const args = [
    '-p',
    '--mode',
    'json',
    '--no-session',
    '--no-extensions',
    '--no-skills',
    '--no-prompt-templates',
    '--no-themes',
    '--no-context-files',
    '-e',
    proxy.provider,
    '-e',
    join(paths.relentlessDir, 'agent', 'pi-harness.ts'),
    '--tools',
    'read,rt',
    '--model',
    `cliproxyapi/${values.model ?? 'gpt-6.1-sol'}`,
    '--thinking',
    values.thinking ?? 'medium',
    '--system-prompt',
    readFileSync(join(paths.relentlessDir, 'prompts', 'tester.md'), 'utf8'),
    `@${task}`,
    'Carry out the task in the attached file.',
  ]
  // The login's weekly quota around the run, when the proxy's management key
  // is at hand; without it the run goes ahead unmeasured.
  const quota = () => weeklyQuota().catch(() => null)
  const quotaBefore = await quota()
  const outcome = await spawnAgent('pi', args, env)
  const quotaAfter = await quota()
  let stop: { reason?: string } = {}
  try {
    stop = JSON.parse(readFileSync(stopFile, 'utf8'))
  } catch {}
  rmSync(agentDir, { recursive: true, force: true })
  writeFileSync(join(runDir, 'agent.jsonl'), outcome.stdout)
  const assistant: Record<string, any>[] = []
  for (const line of outcome.stdout.split('\n')) {
    try {
      const event = JSON.parse(line)
      if (event.type === 'message_end' && event.message?.role === 'assistant')
        assistant.push(event.message)
    } catch {}
  }
  const last = assistant.at(-1)
  const output = {
    subtype:
      stop.reason === 'budget'
        ? 'error_max_budget_usd'
        : (stop.reason ?? last?.stopReason ?? null),
    is_error:
      !stop.reason &&
      (last?.stopReason === 'error' || last?.stopReason === 'aborted'),
    num_turns: assistant.length,
    total_cost_usd: assistant.reduce(
      (n, m) => n + (m.usage?.cost?.total ?? 0),
      0
    ),
    usage: {
      input_tokens: assistant.reduce((n, m) => n + (m.usage?.input ?? 0), 0),
      output_tokens: assistant.reduce((n, m) => n + (m.usage?.output ?? 0), 0),
      cache_read_input_tokens: assistant.reduce(
        (n, m) => n + (m.usage?.cacheRead ?? 0),
        0
      ),
    },
    quota: { before: quotaBefore, after: quotaAfter },
    error: last?.errorMessage ?? null,
    result:
      last?.content
        ?.filter((c: { type: string }) => c.type === 'text')
        .map((c: { text: string }) => c.text)
        .join('\n') ?? null,
  }
  writeFileSync(
    join(runDir, 'agent.json'),
    `${JSON.stringify(output, null, 2)}\n`
  )
  writeFileSync(join(runDir, 'agent.stderr.log'), outcome.stderr)
  return { exitCode: outcome.exitCode, timedOut: outcome.timedOut, output }
}

/** Runs the agent CLI in the run directory, killed with its children on timeout. */
async function spawnAgent(cmd: string, args: string[], env: NodeJS.ProcessEnv) {
  const child = spawn(cmd, args, {
    cwd: runDir,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  })
  let stdout = ''
  child.stdout.on('data', (chunk) => {
    stdout += chunk
  })
  let stderr = ''
  child.stderr.on('data', (chunk) => {
    stderr += chunk
  })
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    try {
      process.kill(-(child.pid ?? 0), 'SIGTERM')
    } catch {}
  }, timeoutMin * 60_000)
  const exitCode = await new Promise<number | null>((resolveExit) => {
    child.on('exit', (code) => resolveExit(code))
    child.on('error', () => resolveExit(null))
  })
  clearTimeout(timer)
  return { exitCode, timedOut, stdout, stderr }
}

/** Total spend on the key so far; runs sharing a key concurrently overlap. */
async function openRouterUsage(key: string): Promise<number | undefined> {
  try {
    const response = await fetch(`${OPENROUTER}/v1/key`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(15_000),
    })
    const body = (await response.json()) as { data?: { usage?: number } }
    return body.data?.usage
  } catch {
    return undefined
  }
}

async function runAgent(prompt: string, bin: string): Promise<AgentOutcome> {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PATH: `${bin}:${process.env.PATH}`,
    RDST_RELENTLESS_RUN_DIR: runDir,
  }
  // The tester authenticates with the Claude login or OpenRouter. The
  // application's key stays with the driver, which types it without the
  // agent seeing it.
  delete env.ANTHROPIC_API_KEY
  delete env.RDST_RELENTLESS_ANTHROPIC_API_KEY
  delete env.RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE
  delete env.RDST_RELENTLESS_OPENROUTER_API_KEY
  delete env.RDST_RELENTLESS_OPENROUTER_API_KEY_FILE
  delete env.ELECTRON_RUN_AS_NODE
  if (piAgent) return runPi(prompt, env)
  const model = values.model ?? 'opus'
  let limit = values.budget ? ['--max-budget-usd', values.budget] : []
  let key: string | undefined
  let configDir: string | undefined
  if (openRouter) {
    key = openRouterKey()
    // A config directory of its own keeps the Claude login and the
    // operator's settings out of the agent's session.
    configDir = mkdtempSync(join(tmpdir(), 'rt-agent-'))
    delete env.CLAUDE_CODE_OAUTH_TOKEN
    Object.assign(env, {
      ANTHROPIC_BASE_URL: OPENROUTER,
      ANTHROPIC_AUTH_TOKEN: key,
      ANTHROPIC_API_KEY: '',
      ANTHROPIC_DEFAULT_HAIKU_MODEL: model.replace(/\[1m\]$/, ''),
      CLAUDE_CONFIG_DIR: configDir,
    })
    limit = values['max-turns'] ? ['--max-turns', values['max-turns']] : []
  }
  const usageBefore = key ? await openRouterUsage(key) : undefined
  const args = [
    '-p',
    '--model',
    model,
    ...limit,
    '--system-prompt-file',
    join(paths.relentlessDir, 'prompts', 'tester.md'),
    '--output-format',
    'json',
    '--no-session-persistence',
    '--strict-mcp-config',
    '--permission-mode',
    'dontAsk',
    '--allowedTools',
    'Bash(rt:*)',
    `Read(/${runDir}/**)`,
    // The app's HOME holds what it was issued: its keyring and account tokens.
    // Its raw logs may too; `rt logs` reads them redacted.
    '--disallowedTools',
    `Read(/${runDir}/home/**)`,
    `Read(/${runDir}/**/*.log)`,
    '--',
    prompt,
  ]
  const { exitCode, timedOut, stdout, stderr } = await spawnAgent(
    'claude',
    args,
    env
  )
  if (configDir) rmSync(configDir, { recursive: true, force: true })
  // OpenRouter's usage counter settles a few seconds after the last call.
  if (key) await new Promise((r) => setTimeout(r, 20_000))
  const usageAfter = key ? await openRouterUsage(key) : undefined
  writeFileSync(join(runDir, 'agent.json'), stdout)
  writeFileSync(join(runDir, 'agent.stderr.log'), stderr)
  // The result is the last JSON line; the CLI may print warnings before it.
  let output: Record<string, any> | null = null
  for (const line of stdout.trim().split('\n').reverse()) {
    try {
      output = JSON.parse(line)
      break
    } catch {}
  }
  const providerCostUsd =
    usageBefore !== undefined && usageAfter !== undefined
      ? usageAfter - usageBefore
      : undefined
  return { exitCode, timedOut, output, providerCostUsd }
}

async function main() {
  mkdirSync(runDir, { recursive: true })
  if (change) {
    copyFileSync(
      join(paths.relentlessDir, 'UX_MAP.md'),
      join(runDir, 'UX_MAP.md')
    )
    writeFileSync(join(runDir, 'change.diff'), change.diff)
  }
  const bin = writeRtWrapper()
  let verdict: Verdict
  let status: Record<string, unknown> = {}
  let agent: AgentOutcome | null = null
  let infraError: string | undefined

  try {
    await startDriver({
      runDir,
      mode,
      desktopExecutable: values['desktop-exec'],
      headed: values.headed,
      homeFrom: values['home-from'],
      noFirstRunPopups: values['no-first-run-popups'],
    })
    status = ((await call(runDir, 'status')).data ?? {}) as Record<
      string,
      unknown
    >
  } catch (error) {
    infraError = String(error)
  }

  if (!infraError) {
    agent = await runAgent(`${prompt}\n\n${environmentSection(status)}`, bin)
    const alive = await call(runDir, 'status')
    if (!alive.ok) infraError = `driver died during the run: ${alive.error}`
  }
  const stopped = await call(runDir, 'stop')
  const cleanup = stopped.data as { orphans?: unknown[] } | undefined

  const result = readJson<{ status: string; summary: string }>(
    join(runDir, 'result.json')
  )
  const findings = readFindings()
  writeFindingPages(findings)
  const serious = findings.filter(({ severity }) =>
    ['P1', 'P2'].includes(severity)
  )
  const subtype = String(agent?.output?.subtype ?? '')

  if (infraError) verdict = 'infra_error'
  else if (agent?.timedOut) verdict = 'timeout'
  else if (result) {
    verdict = result.status as Verdict
    // A P1/P2 finding fails the area whatever the agent concluded.
    if (verdict === 'passed' && serious.length > 0) verdict = 'failed'
  } else if (subtype.includes('budget') || subtype.includes('max_turns'))
    verdict = 'budget_exhausted'
  else if (agent?.exitCode !== 0 || agent?.output?.is_error)
    verdict = 'agent_error'
  else verdict = 'incomplete'

  // A mission passes only when every flow reported and none failed or was blocked.
  const flowResults: Record<string, string> = {}
  if (flows.length > 0) {
    const file = join(runDir, 'flows.jsonl')
    const reported = existsSync(file)
      ? readFileSync(file, 'utf8')
          .split('\n')
          .filter(Boolean)
          .map((line) => JSON.parse(line) as { id: string; status: string })
      : []
    // Agents sometimes report a flow by its name alone; that counts when no
    // other flow in the mission shares the name.
    const name = (id: string) => id.split('/').at(-1)
    const isFlow = (flow: Flow, id: string) =>
      id === flow.id ||
      (id === name(flow.id) &&
        flows.filter((f) => name(f.id) === id).length === 1)
    for (const flow of flows) {
      flowResults[flow.id] =
        reported.filter((r) => isFlow(flow, r.id)).at(-1)?.status ??
        'not_reported'
    }
    const outcomes = Object.values(flowResults)
    if (verdict === 'passed' && outcomes.includes('failed')) verdict = 'failed'
    if (verdict === 'passed' && outcomes.includes('blocked'))
      verdict = 'blocked'
    if (verdict === 'passed' && outcomes.includes('not_reported'))
      verdict = 'incomplete'
  }

  const bySeverity: Record<string, number> = {}
  // In a mission, findings per flow, so each can be credited to its flow.
  const byFlow: Record<string, Record<string, number>> = {}
  for (const { severity, flow: id } of findings) {
    bySeverity[severity] = (bySeverity[severity] ?? 0) + 1
    if (flows.length === 0) continue
    const key = id ?? 'unattributed'
    byFlow[key] ??= {}
    byFlow[key][severity] = (byFlow[key][severity] ?? 0) + 1
  }
  const report = {
    area,
    mode,
    verdict,
    ...(flows.length > 0 ? { flows: flowResults } : {}),
    agent_status: result?.status ?? null,
    summary: result?.summary ?? infraError ?? null,
    findings: bySeverity,
    ...(flows.length > 0 ? { findings_by_flow: byFlow } : {}),
    finding_titles: findings.map(
      ({ id, severity, title }) => `${id} ${severity} ${title}`
    ),
    // Each finding's page and screenshots, relative to the run directory.
    finding_files: findings.map((f) => ({
      id: f.id,
      severity: f.severity,
      title: f.title,
      // The mission flow it was filed under, which the selector's labels use.
      ...(flows.length > 0 ? { flow: f.flow } : {}),
      page: `findings/${f.id}.md`,
      screenshots: screenshotsOf(f),
    })),
    agent: {
      provider: values['agent-provider'],
      model: values.model ?? (piAgent ? 'gpt-6.1-sol' : 'opus'),
    },
    // Lets the selector's evidence index tell which prompt and app a run saw.
    area_prompt_sha256: createHash('sha256').update(prompt).digest('hex'),
    app_commit: process.env.BUILDKITE_COMMIT ?? null,
    // Claude Code prices every model as an Anthropic one, so OpenRouter runs
    // report OpenRouter's own figure; pi reports its catalog's list prices.
    cost_usd: openRouter
      ? (agent?.providerCostUsd ?? null)
      : (agent?.output?.total_cost_usd ?? null),
    turns: agent?.output?.num_turns ?? null,
    usage: agent?.output?.usage ?? null,
    // pi only: the proxy login's weekly quota before and after the agent.
    quota: agent?.output?.quota ?? null,
    agent_subtype: subtype || null,
    duration_s: Math.round((Date.now() - startedAt) / 1000),
    dependencies: status.dependencies ?? null,
    orphans: cleanup?.orphans ?? null,
    run_dir: runDir,
  }
  writeFileSync(
    join(runDir, 'verdict.json'),
    `${JSON.stringify(report, null, 2)}\n`
  )
  console.log(JSON.stringify(report, null, 2))
  return verdict === 'passed' ? 0 : 1
}

process.exitCode = await main()
