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
