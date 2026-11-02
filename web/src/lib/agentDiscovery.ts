// RDST looks for the user's databases itself (a scan of their recent projects, Docker and
// the databases running here), or runs their own coding agent (Claude Code or Codex, here or
// in WSL) to look deeper. The user picks one, and the agent can set it up.

export interface DiscoveryAgent {
  /** `claude:local`, `codex:wsl` and so on: what a search names. */
  key: string
  id: 'claude' | 'codex'
  side: 'local' | 'wsl'
  label: string
  /** What a sentence calls it: "Claude found 2 databases". */
  name: string
  /** Whether it has a sign-in it can run with; null when it can't say. */
  signed_in?: boolean | null
  /** `app` for the copy the Claude or Codex desktop app ships, which RDST runs when the
   * user has no CLI of their own. */
  via?: 'cli' | 'app'
}

export type DiscoveryProvider =
  | 'local'
  | 'docker'
  | 'supabase'
  | 'neon'
  | 'digitalocean'
  | 'aws'
  | 'gcp'
  | 'azure'
  | 'planetscale'
  | 'railway'
  | 'render'
  | 'heroku'
  | 'aiven'
  | 'timescale'
  | 'crunchy'
  | 'cockroach'
  | 'xata'
  | 'tembo'
  | 'nile'
  | 'prisma'
  | 'tidb'
  | 'singlestore'
  | 'fly'
  | 'vultr'
  | 'linode'
  | 'scaleway'
  | 'ovh'
  | 'other'

/** What a hosted provider is called. */
export const PROVIDER_NAMES: Partial<Record<DiscoveryProvider, string>> = {
  supabase: 'Supabase',
  neon: 'Neon',
  digitalocean: 'DigitalOcean',
  aws: 'AWS',
  gcp: 'Google Cloud SQL',
  azure: 'Azure',
  planetscale: 'PlanetScale',
  railway: 'Railway',
  render: 'Render',
  heroku: 'Heroku',
  aiven: 'Aiven',
  timescale: 'Timescale',
  crunchy: 'Crunchy Bridge',
  cockroach: 'CockroachDB',
  xata: 'Xata',
  tembo: 'Tembo',
  nile: 'Nile',
  prisma: 'Prisma Postgres',
  tidb: 'TiDB Cloud',
  singlestore: 'SingleStore',
  fly: 'Fly.io',
  vultr: 'Vultr',
  linode: 'Linode',
  scaleway: 'Scaleway',
  ovh: 'OVHcloud',
}

export interface FoundDatabase {
  id: string
  name: string | null
  /** Unknown for a project RDST hasn't read yet, or one whose database it couldn't place. */
  engine: 'postgresql' | 'mysql' | null
  host: string | null
  port: number | null
  database: string | null
  user: string | null
  provider: DiscoveryProvider
  environment: 'development' | 'staging' | 'production' | 'unknown'
  project: string | null
  source: string | null
  env_file: string | null
  env_var: string | null
  password_file: string | null
  password_var: string | null
  note: string | null
  /** What pointed at it: "DATABASE_URL in .env", "Docker container shop-db". */
  found_by: string[]
  /** The name RDST already has it under, when it does. */
  saved_as?: string | null
  /** A file whose connection URL, password included, RDST reads when the user connects. */
  url_file?: string | null
  url_host?: string | null
  /** A Cloud SQL instance connection name. */
  instance?: string | null
  /** The secret manager the project keeps its secrets in, like "Infisical". */
  secrets?: string | null
  /** The project uses a database but doesn't say where: the agent works it out. */
  incomplete?: boolean | null
  /** A project listed by name, read only once the user picks it (a Mac's Documents). */
  deferred?: boolean | null
  /** Seen only in the user's agent sessions or shell history. */
  history?: boolean | null
  /** Whether it answered when RDST knocked, without logging in. */
  reach?: 'answering' | 'unreachable' | null
  /** It runs inside WSL, not on Windows itself. */
  wsl?: boolean | null
}

/** One way a scan looked, and what it saw. */
export interface ScanMethod {
  method: 'projects' | 'history' | 'docker' | 'ports' | 'clients' | 'reach'
  label: string
  state: 'running' | 'done' | 'failed'
  found: number
  /** The folders and places it looked in. */
  looked: string[]
  seconds: number | null
}

export interface ActivityLine {
  /** `say`: the agent's own words; `tool`: a step it ran; `rdst`: what RDST saw happen;
   * `you`: what the user answered. */
  kind: 'say' | 'tool' | 'rdst' | 'you'
  text: string
}

export interface DiscoveryRun {
  id: string
  /** None for a scan: RDST looked itself. */
  agent: DiscoveryAgent | null
  /** A scan or search finds databases; a setup connects the one the user picked. */
  kind: 'scan' | 'search' | 'setup'
  state: 'running' | 'done' | 'failed' | 'cancelled'
  message: string | null
  steps: number
  progress: string[]
  /** Seconds since the run started. */
  elapsed: number
  candidates: FoundDatabase[]
  activity: ActivityLine[]
  /** A setup's database, once the agent added it. */
  target: string | null
  /** The setup waits on the user's Turn on monitoring click. */
  waiting: boolean
  monitoring: boolean
  /** A terminal command that picks the agent's session up again. */
  resume_command: string | null
  /** The desktop app that can open this session. */
  continue_in: 'claude' | 'codex' | null
  /** It stopped because the agent isn't signed in on this computer. */
  signed_out?: boolean
  /** The desktop app the user opened this conversation in; it carries on there. */
  in_app?: 'claude' | 'codex' | 'terminal' | null
  /** Where a scan looked. */
  methods: ScanMethod[] | null
  /** The agent's own conversation id, once it started. */
  session: string | null
  /** A decision the agent stopped to ask the user for. */
  question: { question: string; options: string[] } | null
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new Error("RDST didn't answer. Check that it's still running.")
  }
  const body = (await response.json().catch(() => ({}))) as Record<
    string,
    unknown
  >
  if (!response.ok)
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `RDST couldn't do that (${response.status}).`
    )
  return body as T
}

/** The coding agents installed here (and in WSL) that RDST can run. */
export async function fetchDiscoveryAgents(): Promise<DiscoveryAgent[]> {
  const body = await call<{ agents?: DiscoveryAgent[] }>(
    '/api/agent-connect/agents'
  )
  return Array.isArray(body.agents) ? body.agents : []
}

/** Has `agent` set up the databases the user picked from a scan or search. A search's own
 * agent picks its conversation back up; any other starts a fresh one. */
export function startSetup(
  run: string,
  candidates: string[],
  agent?: string,
  /** What went wrong when RDST tried to connect it itself. */
  problem?: string
): Promise<DiscoveryRun> {
  return call(`/api/agent-connect/discover/${encodeURIComponent(run)}/setup`, {
    method: 'POST',
    body: JSON.stringify({ candidates, agent, problem }),
  })
}

/** The agents RDST can run and the desktop apps installed here; `refresh` looks again
 * instead of answering from what RDST saw in the last minute. */
export async function fetchDiscoveryOptions(refresh = false): Promise<{
  agents: DiscoveryAgent[]
  apps: ('claude' | 'codex')[]
}> {
  const body = await call<{ agents?: DiscoveryAgent[]; apps?: string[] }>(
    `/api/agent-connect/agents${refresh ? '?refresh=true' : ''}`
  )
  return {
    agents: Array.isArray(body.agents) ? body.agents : [],
    apps: (Array.isArray(body.apps) ? body.apps : []).filter(
      (app): app is 'claude' | 'codex' => app === 'claude' || app === 'codex'
    ),
  }
}

/** What the paste prompt has the user's agent do, in a sentence. */
export const PROMPT_SUMMARY =
  'Your agent finds the database this project uses, adds it to RDST and gets it ready to monitor. It only reads the project folder and asks you before changing anything.'

/** Opens a terminal window where the user signs `agent` in. */
export function signInAgent(agent: string): Promise<unknown> {
  return call(
    `/api/agent-connect/agents/${encodeURIComponent(agent)}/sign-in`,
    {
      method: 'POST',
    }
  )
}

/** Opens a new conversation in the desktop app with the paste prompt for `attempt` typed
 * in, for the user to send. */
export function openInApp(
  app: string,
  attempt: string,
  target?: string
): Promise<unknown> {
  return call('/api/agent-connect/open-app', {
    method: 'POST',
    body: JSON.stringify({ app, attempt, target }),
  })
}

/** The prompt that has the user's own agent answer questions about `target`. */
export function fetchAskPrompt(
  target: string
): Promise<{ prompt: string; attempt: string }> {
  return call('/api/agent-connect/ask-prompt', {
    method: 'POST',
    body: JSON.stringify({ target }),
  })
}

/** Adds RDST to the agent's own MCP servers, so its conversations can use RDST. */
export function connectAgent(agent: string): Promise<unknown> {
  return call('/api/agent-connect/connect', {
    method: 'POST',
    body: JSON.stringify({ agent }),
  })
}

/** Opens the agent's app to ask about `target`: the conversation that set it up when
 * there was one, else a new one. */
export function askInApp(
  app: string,
  target: string
): Promise<{ opened: boolean; resumed: boolean }> {
  return call('/api/agent-connect/ask-open', {
    method: 'POST',
    body: JSON.stringify({ app, target }),
  })
}

/** The prompt that has the user's own agent get `target` ready to monitor, with the
 * attempt it opens. */
export function fetchFixPrompt(
  target: string
): Promise<{ prompt: string; attempt: string }> {
  return call('/api/agent-connect/fix-prompt', {
    method: 'POST',
    body: JSON.stringify({ target }),
  })
}

/** The kinds of evidence that found a database, for analytics: never the paths. */
export function evidenceKinds(found: FoundDatabase): string {
  const kinds = new Set<string>()
  for (const line of found.found_by) {
    if (/compose|service /i.test(line)) kinds.add('compose')
    else if (/docker container/i.test(line)) kinds.add('docker')
    else if (/answering on port/i.test(line)) kinds.add('port')
    else if (/^Used in a|shell history/i.test(line)) kinds.add('history')
    else if (/^Saved in|connection |pgpass|pg_service|my\.cnf/i.test(line))
      kinds.add('saved')
    else if (/database\.yml/i.test(line)) kinds.add('rails')
    else if (/^A database library|^Declared in/i.test(line)) kinds.add('code')
    else if (/ in \.env|\.env\b/i.test(line)) kinds.add('env')
    else kinds.add('config')
  }
  return [...kinds].sort().join(',')
}

/** Looks for databases on this computer, without an agent. */
export function startScan(): Promise<DiscoveryRun> {
  return call('/api/agent-connect/scan', { method: 'POST' })
}

/** Has an agent get databases already in RDST ready to monitor. */
export function startAgentFix(
  agent: string,
  targets: string[],
  /** What went wrong when RDST tried to connect. */
  problem?: string | null,
  /** The project folder it belongs to, when known: the agent works there. */
  folder?: string | null
): Promise<DiscoveryRun> {
  return call('/api/agent-connect/setup', {
    method: 'POST',
    body: JSON.stringify({
      agent,
      targets,
      problem: problem ?? undefined,
      folder: folder ?? undefined,
    }),
  })
}

/** Where the conversation about a database is: in the Claude or Codex app once the user
 * took it there. */
export function fetchConversation(target: string): Promise<{
  agent: string | null
  in_app: 'claude' | 'codex' | 'terminal' | null
}> {
  return call(`/api/agent-connect/conversation/${encodeURIComponent(target)}`)
}

/** Whether RDST can connect to a database it already has. */
export function checkTarget(
  target: string
): Promise<{ target: string; ok: boolean; message: string | null }> {
  return call('/api/agent-connect/check-target', {
    method: 'POST',
    body: JSON.stringify({ target }),
  })
}

/** Opens the run's agent session in its desktop app. */
export function continueInApp(run: string, app: string): Promise<unknown> {
  return call(
    `/api/agent-connect/discover/${encodeURIComponent(run)}/continue`,
    { method: 'POST', body: JSON.stringify({ app }) }
  )
}

/** Gives the agent the user's answer to its question: the same conversation carries on,
 * as a new run. */
export function replyToAgent(run: string, text: string): Promise<DiscoveryRun> {
  return call(`/api/agent-connect/discover/${encodeURIComponent(run)}/reply`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

/** Opens a terminal window that continues the run's agent conversation. */
export function continueInTerminal(run: string): Promise<unknown> {
  return call(
    `/api/agent-connect/discover/${encodeURIComponent(run)}/terminal`,
    { method: 'POST' }
  )
}

/** Has the agent look for databases; with `scan`, for the ones that scan missed, and what
 * it finds joins the scan's list. */
export function startDiscovery(
  agent: string,
  hint?: string,
  scan?: string
): Promise<DiscoveryRun> {
  return call('/api/agent-connect/discover', {
    method: 'POST',
    body: JSON.stringify({ agent, hint, scan }),
  })
}

/** Reads a project the scan only listed by name, now that the user picked it. */
export function lookInProject(
  run: string,
  project: string
): Promise<DiscoveryRun> {
  return call(`/api/agent-connect/discover/${encodeURIComponent(run)}/folder`, {
    method: 'POST',
    body: JSON.stringify({ project }),
  })
}

/** Has the agent find and set up a database the user described. */
export function describeDatabase(
  agent: string,
  text: string
): Promise<DiscoveryRun> {
  return call('/api/agent-connect/describe', {
    method: 'POST',
    body: JSON.stringify({ agent, text }),
  })
}

export function fetchDiscovery(id: string): Promise<DiscoveryRun> {
  return call(`/api/agent-connect/discover/${encodeURIComponent(id)}`)
}

export function cancelDiscovery(id: string): Promise<DiscoveryRun> {
  return call(`/api/agent-connect/discover/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export interface AddedDatabase {
  /** The name RDST saved it under; null when it couldn't connect, so it kept nothing. */
  target: string | null
  persisted?: boolean
  /** Whether RDST reached it once added. */
  ok: boolean
  message: string | null
  category?: string | null
  ready_to_monitor?: boolean | null
}

/** Adds one found database; RDST reads its URL or password where the scan saw it, then
 * checks it can reach it. */
export async function addFoundDatabase(
  run: string,
  candidate: string
): Promise<AddedDatabase> {
  return call(`/api/agent-connect/discover/${encodeURIComponent(run)}/add`, {
    method: 'POST',
    body: JSON.stringify({ candidate }),
  })
}

/** The one to pre-pick: the first, best found, that isn't production, and that RDST can
 * add without a sign-in. */
export function recommended(found: FoundDatabase[]): FoundDatabase | null {
  const pickable = found.filter(
    (item) =>
      !item.saved_as &&
      !item.deferred &&
      item.host &&
      (!signInProvider(item) || readsItsOwnSecret(item))
  )
  return (
    pickable.find((item) => item.environment !== 'production') ??
    pickable[0] ??
    null
  )
}

/** Whether RDST can add it without asking for the password. */
export const readsItsOwnSecret = (found: FoundDatabase) =>
  Boolean(
    (found.env_file && found.env_var) ||
      (found.password_file && found.password_var && found.host && found.user) ||
      (found.url_file && found.url_host)
  )

/** The providers RDST signs in to and imports from. */
export const SIGN_IN_PROVIDERS = [
  'supabase',
  'neon',
  'digitalocean',
  'aws',
] as const
export type SignInProvider = (typeof SIGN_IN_PROVIDERS)[number]

export const signInProvider = (found: FoundDatabase): SignInProvider | null =>
  (SIGN_IN_PROVIDERS as readonly string[]).includes(found.provider)
    ? (found.provider as SignInProvider)
    : null
