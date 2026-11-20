// Monitoring prerequisites for one saved target: GET
// /api/configure/targets/{name}/prerequisites. The backend reads the real server
// state and runs a tagged probe statement on a second session, so `ready` means
// RDST will see new queries. Safe to poll every few seconds.

export type PrereqStatus = 'ok' | 'missing' | 'warn' | 'unknown' | 'waiting'

export type ProviderId =
  | 'self_hosted'
  | 'rds'
  | 'aurora'
  | 'cloudsql'
  | 'azure'
  | 'supabase'
  | 'neon'
  | 'digitalocean'
  | 'cnpg'
  | 'zalando'

export interface PrereqFix {
  summary: string
  /** One statement per entry, each copyable. */
  sql: string[]
  /** Console or dashboard steps, in order. */
  steps: string[]
  /** Shell commands or config lines, each copyable. */
  commands: string[]
  /** Steps that follow the SQL and commands (for example a restart). */
  after: string[]
  restart: boolean
  note: string | null
  docs_url: string | null
}

export interface PrereqCheck {
  id: string
  label: string
  status: PrereqStatus
  detail: string
  /** False when monitoring can start without it. */
  blocking: boolean
  /** Improves what RDST captures; listed under Recommended. */
  recommended?: boolean
  /** What's wrong right now, for a check that isn't ok; `label` names the goal. */
  problem?: string | null
  fix: PrereqFix | null
  remediation: string | null
}

export interface PrereqResult {
  target: string
  engine: 'postgresql' | 'mysql' | string
  database: string | null
  checked_at: number
  provider: ProviderId | string | null
  provider_name: string | null
  /** Every blocking check passed: monitoring can start. */
  ready: boolean
  blocking: number
  /** Non-blocking checks that failed (for example other users' SQL hidden). */
  attention: number
  warnings: number
  checks: PrereqCheck[]
  connection_error: string | null
  /** Why the connection failed, when RDST can tell: a provider's network restrictions. */
  connection_category?: string | null
}

export class PrereqRequestError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = 'PrereqRequestError'
    this.code = code
  }
}

export const prereqKey = (target: string) =>
  ['configure', 'prerequisites', target] as const

export async function fetchPrerequisites(
  target: string,
  init?: RequestInit
): Promise<PrereqResult> {
  const response = await fetch(
    `/api/configure/targets/${encodeURIComponent(target)}/prerequisites`,
    init
  )
  if (!response.ok) {
    let detail: Record<string, unknown> | undefined
    try {
      const body = (await response.json()) as Record<string, unknown>
      detail =
        body.detail && typeof body.detail === 'object'
          ? (body.detail as Record<string, unknown>)
          : body
    } catch {
      detail = undefined
    }
    const code =
      typeof detail?.code === 'string' ? detail.code : `HTTP_${response.status}`
    const message =
      code === 'TARGET_PASSWORD_REQUIRED'
        ? `Enter the password for ${target} to check it.`
        : typeof detail?.message === 'string'
          ? detail.message
          : typeof detail?.detail === 'string'
            ? detail.detail
            : `Couldn't check ${target} (${response.status}).`
    throw new PrereqRequestError(code, message)
  }
  return (await response.json()) as PrereqResult
}

/** The in-database setup RDST can run itself, on Neon: creating an extension. */
export const SETUP_STATEMENT = /^\s*create\s+extension\b/i

export interface SetupRunResult {
  target: string
  ran: string
  ready_to_monitor: boolean
}

/**
 * Runs one statement the check lists for a saved target over RDST's own
 * connection: POST /api/configure/targets/{name}/prerequisites/run. A refusal
 * (from RDST or the database) throws with the reason to show.
 */
export async function runSetupStatement(
  target: string,
  statement: string
): Promise<SetupRunResult> {
  const response = await fetch(
    `/api/configure/targets/${encodeURIComponent(target)}/prerequisites/run`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ statement }),
    }
  )
  if (!response.ok) {
    let message = `RDST couldn't run it (${response.status}).`
    try {
      const body = (await response.json()) as { detail?: unknown }
      const detail = body.detail
      if (typeof detail === 'string') message = detail
      else if (detail && typeof detail === 'object') {
        const text = (detail as Record<string, unknown>).message
        if (typeof text === 'string') message = text
      }
    } catch {
      // The status line is all there is.
    }
    throw new Error(message)
  }
  return (await response.json()) as SetupRunResult
}
