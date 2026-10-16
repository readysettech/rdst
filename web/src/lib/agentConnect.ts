// Connect a database through the user's coding agent: RDST hands out a paste-ready prompt
// with its local MCP address, and the agent's calls to that MCP server report
// back on a progress stream, so the add-database dialog can follow along.

export type AgentStep =
  | 'connected'
  | 'database_added'
  | 'tested'
  | 'awaiting_user'
  | 'monitoring_enabled'
  /** The agent changed a database's monitoring settings, such as turning it off. */
  | 'settings_changed'
  | 'needs_setup'
  | 'error'

export interface AgentEvent {
  step: AgentStep
  target?: string | null
  message?: string | null
  /** When RDST sent it, epoch seconds. */
  ts?: number | null
  /** With `needs_setup`: the prerequisite that failed, such as `query_stats`. */
  check?: string | null
  /** With `needs_setup`: what to change on the server, in order. */
  instructions?: string[]
}

export interface AgentPrompt {
  prompt: string
  /** When the prompt was made, epoch seconds: the progress stream replays from here. */
  issuedAt: number | null
  /** The nonce naming this prompt in telemetry; its MCP URL carries it. */
  attempt: string | null
}

/** How an agent reaches RDST's MCP server, as Settings shows it. */
export interface McpConfig {
  mcpUrl: string
  /** The Claude Code command that registers RDST. */
  command: string
}

const PROMPT_URL = '/api/agent-connect/prompt'
const RECHECK_URL = '/api/agent-connect/recheck'
const MCP_CONFIG_URL = '/api/agent-connect/mcp-config'
export const AGENT_EVENTS_URL = '/api/agent-connect/events'

/** The steps the dialog ticks through, in order. */
export const AGENT_STEPS = [
  'connected',
  'database_added',
  'tested',
  'monitoring_enabled',
] as const satisfies readonly AgentStep[]

export type AgentProgressStep = (typeof AGENT_STEPS)[number]

const STEP_SET = new Set<string>([
  ...AGENT_STEPS,
  'needs_setup',
  'awaiting_user',
  'settings_changed',
  'error',
])

/** A fresh prompt: each call starts a new attempt. */
export async function fetchAgentPrompt(
  signal?: AbortSignal
): Promise<AgentPrompt> {
  let response: Response
  try {
    response = await fetch(PROMPT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-RDST-Via': 'agent' },
      body: '{}',
      signal,
    })
  } catch (caught) {
    if (signal?.aborted) throw caught
    // A dropped connection reads "Failed to fetch"; say what it means instead.
    throw new Error("RDST didn't answer. Check that it's still running.")
  }
  if (!response.ok) {
    let message =
      response.status === 404
        ? "This version of RDST can't connect through an agent yet."
        : `RDST couldn't make a prompt (${response.status}).`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      /* keep the generic message */
    }
    throw new Error(message)
  }
  const type = response.headers?.get('content-type') ?? ''
  if (!type.includes('json'))
    return { prompt: await response.text(), issuedAt: null, attempt: null }
  const body = (await response.json()) as Record<string, unknown>
  const prompt = [body.prompt, body.text].find(
    (value): value is string => typeof value === 'string' && value.length > 0
  )
  if (!prompt) throw new Error("RDST couldn't make a prompt.")
  return {
    prompt,
    issuedAt: typeof body.issued_at === 'number' ? body.issued_at : null,
    attempt: typeof body.attempt === 'string' ? body.attempt : null,
  }
}

async function mcpConfigFrom(response: Response): Promise<McpConfig> {
  const body = (await response.json().catch(() => ({}))) as Record<
    string,
    unknown
  >
  if (!response.ok)
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `RDST couldn't read its MCP settings (${response.status}).`
    )
  if (typeof body.mcp_url !== 'string' || typeof body.command !== 'string')
    throw new Error("RDST couldn't read its MCP settings.")
  return { mcpUrl: body.mcp_url, command: body.command }
}

/** RDST's MCP address and the command that registers it with Claude Code. */
export async function fetchMcpConfig(): Promise<McpConfig> {
  return mcpConfigFrom(await fetch(MCP_CONFIG_URL))
}

/**
 * Tests the agent's database again after the user changed its setup. The verdict arrives
 * on the progress stream as `tested` or `needs_setup`.
 */
export async function recheckAgentTarget(target: string): Promise<void> {
  let response: Response
  try {
    response = await fetch(RECHECK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target }),
    })
  } catch {
    throw new Error("RDST didn't answer. Check that it's still running.")
  }
  if (!response.ok) {
    let message = `RDST couldn't check again (${response.status}).`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      /* keep the generic message */
    }
    throw new Error(message)
  }
}

/**
 * One progress event out of a stream frame. The step is the payload's `step`, or the
 * SSE event name when the payload leaves it out. Null for keep-alives and anything else.
 */
export function parseAgentEvent(
  raw: string,
  eventName = 'message'
): AgentEvent | null {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  const step =
    typeof record.step === 'string'
      ? record.step
      : STEP_SET.has(eventName)
        ? eventName
        : null
  if (!step || !STEP_SET.has(step)) return null
  const event: AgentEvent = {
    step: step as AgentStep,
    target: typeof record.target === 'string' ? record.target : null,
    message: typeof record.message === 'string' ? record.message : null,
  }
  if (typeof record.ts === 'number') event.ts = record.ts
  if (step === 'needs_setup') {
    event.check = typeof record.check === 'string' ? record.check : null
    event.instructions = Array.isArray(record.instructions)
      ? record.instructions.filter(
          (line): line is string => typeof line === 'string'
        )
      : []
  }
  return event
}

/** A prerequisite the database is missing, which the user fixes on the server. */
export interface AgentSetupNeeded {
  check: string
  instructions: string[]
}

export interface AgentProgress {
  /** Steps done so far. */
  done: ReadonlySet<AgentProgressStep>
  /** The database the agent added, once it says. */
  target: string | null
  /** The latest error the agent hit; cleared by the next step that succeeds. */
  error: string | null
  /** Set while the database is added but missing a prerequisite; cleared once it passes. */
  setup: AgentSetupNeeded | null
  /** Counts test verdicts (`tested` and `needs_setup`), so a repeat verdict still lands. */
  verdicts: number
  /** The agent asked to turn monitoring on: the user turns it on in RDST's dialog. */
  awaitingUser: boolean
}

export const NO_PROGRESS: AgentProgress = {
  done: new Set(),
  target: null,
  error: null,
  setup: null,
  verdicts: 0,
  awaitingUser: false,
}

/**
 * Folds one event into the progress. A later step implies the ones before it, so a
 * missed frame never leaves a gap in the checklist.
 */
export function advanceAgentProgress(
  progress: AgentProgress,
  event: AgentEvent
): AgentProgress {
  if (event.step === 'settings_changed') return progress
  const target = event.target || progress.target
  if (event.step === 'error')
    return {
      ...progress,
      target,
      error: event.message || 'Your agent hit a problem.',
    }
  const verdicts =
    event.step === 'tested' || event.step === 'needs_setup'
      ? progress.verdicts + 1
      : progress.verdicts
  // Missing setup means the database was added and reached, so the steps before the test
  // are done and the test waits on the user. Waiting on the user's click means it passed.
  const last =
    event.step === 'needs_setup'
      ? 'database_added'
      : event.step === 'awaiting_user'
        ? 'tested'
        : event.step
  const index = AGENT_STEPS.indexOf(last)
  const done = new Set(progress.done)
  for (const step of AGENT_STEPS.slice(0, index + 1)) done.add(step)
  if (event.step === 'needs_setup') done.delete('tested')
  const setup =
    event.step === 'needs_setup'
      ? {
          check: event.check || 'query_stats',
          instructions: event.instructions ?? [],
        }
      : done.has('tested')
        ? null
        : progress.setup
  const awaitingUser =
    event.step === 'awaiting_user' ||
    (progress.awaitingUser && !done.has('monitoring_enabled'))
  return { done, target, error: null, setup, verdicts, awaitingUser }
}

/** The first step still to do, or null when every step is done. */
export function currentAgentStep(
  progress: AgentProgress
): AgentProgressStep | null {
  return AGENT_STEPS.find((step) => !progress.done.has(step)) ?? null
}

interface EventSourceLike {
  addEventListener: (
    name: string,
    listener: (event: MessageEvent<string>) => void
  ) => void
  onerror: ((event: Event) => void) | null
  close: () => void
}

/**
 * Follows the agent's progress until the returned function is called. EventSource
 * reconnects on its own after a dropped connection.
 */
export function subscribeAgentConnect(
  onEvent: (event: AgentEvent) => void,
  options: {
    /** Replay events from this moment on, epoch seconds (the prompt's `issuedAt`). */
    since?: number | null
    onError?: () => void
    createSource?: (url: string) => EventSourceLike
  } = {}
): () => void {
  const create =
    options.createSource ??
    (typeof EventSource === 'undefined'
      ? null
      : (url: string) => new EventSource(url) as unknown as EventSourceLike)
  if (!create) return () => {}
  const source = create(
    options.since == null
      ? AGENT_EVENTS_URL
      : `${AGENT_EVENTS_URL}?since=${encodeURIComponent(options.since)}`
  )
  for (const name of ['message', 'progress', ...STEP_SET])
    source.addEventListener(name, (event) => {
      const parsed = parseAgentEvent(event.data, name)
      if (parsed) onEvent(parsed)
    })
  if (options.onError) source.onerror = () => options.onError?.()
  return () => source.close()
}
