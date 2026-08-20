import type { ErrorClass } from '@rs/ui-new/error-state'

/**
 * Shared error envelope (B7/T24). The backend emits this over HTTP
 * (`shared/api/app.py`) and on SSE `error` events; the client normalizes any
 * failure into this one shape so the UI never parses an ad-hoc body or leaks a
 * raw `str(e)`.
 */
export interface ApiErrorEnvelope {
  code: string
  message: string
  detail?: string
  category?: string
  target?: string
}

export type { ErrorClass }

export const TRIAL_EXHAUSTED_MESSAGE =
  'Your free trial credit is used up — add your own Anthropic API key or a new trial token.'

export function isTrialExhaustedError(
  error: Pick<ApiErrorEnvelope, 'code' | 'message'> | string | undefined
): boolean {
  const code =
    typeof error === 'string' || error == null ? '' : (error.code ?? '')
  const message =
    typeof error === 'string'
      ? error
      : error == null
        ? ''
        : (error.message ?? '')
  const value = `${code} ${message}`.toLowerCase()
  return (
    /trial[_\s-]*exhausted/.test(value) ||
    /free[-\s]*trial.*(?:used up|run out|exhausted|no (?:credit|tokens?) left)/.test(
      value
    ) ||
    /trial (?:credit|tokens?).*(?:used up|run out|exhausted)/.test(value)
  )
}

const MODEL_CONTEXT_LIMIT_CODES = new Set([
  'anthropic_context_window_exceeded',
  'anthropic_request_too_large',
])

export function isModelContextLimitError(
  error: Pick<ApiErrorEnvelope, 'code' | 'message'>
): boolean {
  if (MODEL_CONTEXT_LIMIT_CODES.has((error.code ?? '').toLowerCase())) {
    return true
  }
  const message = (error.message ?? '').toLowerCase()
  return (
    message.includes('complete database schema could not fit within') &&
    (message.includes('model context window') ||
      message.includes('messages api request-body limit'))
  )
}

/** One routed recovery action: a human label + an app route to send them to. */
export interface RecoveryTarget {
  label: string
  to: string
}

// Route each failure class to the closest existing recovery destination. The
// single settings hub is `/configure` today (T17/C-04 later splits it into
// Target Secrets vs AI Settings + footer Settings); Readyset/Docker setup lives
// on `/cache`. `valid-negative` is a real result, not a failure — no recovery.
const RECOVERY: Record<ErrorClass, RecoveryTarget | undefined> = {
  'user-config': { label: 'Open Settings', to: '/configure' },
  database: { label: 'Check connection', to: '/configure' },
  'local-dependency': { label: 'Set up caching', to: '/cache' },
  'rdst-service': { label: 'Fix API key', to: '/configure' },
  provider: { label: 'Fix API key', to: '/configure' },
  'valid-negative': undefined,
}

export function recoveryFor(
  errorClass: ErrorClass
): RecoveryTarget | undefined {
  return RECOVERY[errorClass]
}

/** A retry can plausibly help only for transient/service/provider failures. */
export function retryHelps(errorClass: ErrorClass): boolean {
  return (
    errorClass === 'local-dependency' ||
    errorClass === 'rdst-service' ||
    errorClass === 'provider'
  )
}

const CONTAINS = (haystack: string, needles: string[]): boolean =>
  needles.some((n) => haystack.includes(n))

/**
 * Best-effort classification of an envelope into one of the six contract
 * classes, from the code first and the message text as a fallback. Presentation
 * only — it drives the glyph/accent and the default recovery route.
 */
export function classifyError(envelope: ApiErrorEnvelope): ErrorClass {
  const code = (envelope.code ?? '').toLowerCase()
  const msg = (envelope.message ?? '').toLowerCase()
  const hay = `${code} ${msg}`

  // Stable connectivity categories beat message heuristics. In particular,
  // `ssh_auth_failed` contains "auth", but it is database connectivity — not
  // an Anthropic/API-key failure.
  if (isConnectionFailure(envelope)) return 'database'

  if (
    CONTAINS(hay, [
      'missing secret',
      'credential',
      'api key',
      'aws profile',
      'region',
      'not configured',
      'no target',
    ])
  ) {
    return 'user-config'
  }
  // Only distinctly-local tokens classify as local-dependency. Generic
  // connectivity words (connection, timeout, unreachable, refused) belong to
  // the database class below: a target-DB connection timeout must route to
  // "check connection", never to local Docker/Readyset setup.
  if (CONTAINS(hay, ['docker', 'readyset', 'container', 'port collision'])) {
    return 'local-dependency'
  }
  if (
    CONTAINS(hay, [
      'trial',
      'keyservice',
      'verification',
      'verify your email',
      'rate_limited',
      'program_full',
      'token',
    ])
  ) {
    return 'rdst-service'
  }
  // Database credential failures must beat the provider auth tokens below: a
  // Postgres/MySQL "password authentication failed for user …" (or a pg_hba
  // rule rejection) is a connection problem that routes to "Check connection",
  // never to "Fix API key".
  if (
    CONTAINS(hay, [
      'password authentication',
      'authentication failed for user',
      'access denied for user',
      'pg_hba',
    ])
  ) {
    return 'database'
  }
  // Provider = the AI credential/LLM layer. Auth-failure tokens live here (not
  // in the DB class below) so an AI authentication error routes recovery to
  // "Fix API key", never to "check connection". User-config / rdst-service are
  // checked first, so an AWS-profile or trial message never lands here. The
  // auth words are word-bounded and 401 is matched only in its HTTP-status
  // forms, so "unauthorized_logs" or "1401 rows" never classify as provider.
  if (
    CONTAINS(hay, [
      'anthropic',
      'claude',
      'provider',
      'llm',
      'auth_invalid',
      'invalid api key',
      'ai service',
    ]) ||
    /\bauthentication\b/.test(hay) ||
    /\bunauthorized\b/.test(hay) ||
    /\b(?:http|status|code)[\s:_-]*401\b/.test(hay)
  ) {
    return 'provider'
  }
  if (
    CONTAINS(hay, [
      'syntax',
      'relation',
      'permission',
      'connection',
      'timeout',
      'timed out',
      'unreachable',
      'refused',
      'invalid_request',
      'query',
      'sql',
      'database',
      'column',
      'table',
    ])
  ) {
    return 'database'
  }
  return 'database'
}

/**
 * Turn a raw EXPLAIN/driver error into a friendly, safe message. Keeps the
 * database's own syntax text (the useful part) but frames it for a human.
 */
export function friendlySqlError(raw: string | undefined): string {
  const text = (raw ?? '').trim()
  if (!text) {
    return 'The query could not be analyzed. Check the SQL and try again.'
  }
  if (
    /connection (?:to server .* )?failed|connection refused|could not connect|server closed the connection|network is unreachable|no route to host|connection timed out/i.test(
      text
    )
  ) {
    return 'Could not connect to the database. Check that it is running and reachable, then try again.'
  }
  if (
    /password authentication failed|authentication failed for user|access denied for user|no pg_hba\.conf entry/i.test(
      text
    )
  ) {
    return 'The database rejected the connection. Check the target credentials and access settings.'
  }
  if (/permission denied|must be owner|insufficient privilege/i.test(text)) {
    return "The database denied permission to analyze this query. Check the target user's permissions."
  }
  if (/syntax error/i.test(text)) {
    return `SQL syntax error: ${text.replace(/^.*?syntax error/i, 'syntax error')}`
  }
  if (
    /does not exist|unknown (column|table)|no such (column|table)|relation .* does not exist/i.test(
      text
    )
  ) {
    return `The query references something the database can't find: ${text}`
  }
  return `The query could not be analyzed: ${text}`
}

/**
 * Classify an EXPLAIN failure by its actual cause. A failed EXPLAIN does not
 * necessarily mean invalid SQL: connection, authentication, and permission
 * failures need database recovery, while only query-shape failures should send
 * the user back to the editor.
 */
export function normalizeExplainError(
  raw: string | undefined
): ApiErrorEnvelope {
  const text = (raw ?? '').trim()
  const connection =
    /connection (?:to server .* )?failed|connection refused|could not connect|server closed the connection|network is unreachable|no route to host|connection timed out/i.test(
      text
    )
  const authentication =
    /password authentication failed|authentication failed for user|access denied for user|no pg_hba\.conf entry/i.test(
      text
    )
  const permission =
    /permission denied|must be owner|insufficient privilege/i.test(text)

  return {
    code: connection
      ? 'database_connection'
      : authentication
        ? 'database_authentication'
        : permission
          ? 'database_permission'
          : 'invalid_sql',
    message: friendlySqlError(text),
    detail: text || undefined,
  }
}

/**
 * Reframe a backend/CLI error string for the web UI: strip CLI-only remediation
 * (`Run 'rdst init'`, `rdst configure …`) that has no meaning in a browser with
 * no terminal, keeping the substantive cause (P47).
 */
export function sanitizeWebError(
  raw: string | undefined,
  fallback = 'This query could not be analyzed.'
): string {
  let text = (raw ?? '').trim()
  if (!text) return fallback
  if (
    /environment variable|password_env|export\s+\w+/i.test(text) &&
    /password|locked|not set|export/i.test(text)
  ) {
    const target = text.match(/Target\s+['"]([^'"]+)['"]/i)?.[1]
    return target
      ? `Enter the password for '${target}' again.`
      : 'Enter the database password again.'
  }
  // "Run 'rdst init'." / "Run `rdst configure add`" and bare `rdst <cmd> …`.
  text = text.replace(/\bRun\s+['"`]?rdst\b[^'"`.\n]*['"`]?\.?/gi, '').trim()
  text = text
    .replace(/\brdst\s+(init|configure|scan|analyze)\b[^.\n]*/gi, '')
    .trim()
  text = text
    .replace(/\s{2,}/g, ' ')
    .replace(/\s+([.,;])/g, '$1')
    .trim()
  return text || fallback
}

/**
 * Normalize an SSE `error` event payload into the shared envelope. Accepts both
 * the new {code, message, detail} shape and the legacy {message} shape.
 */
export function normalizeSseError(data: unknown): ApiErrorEnvelope {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    const message =
      typeof d.message === 'string' && d.message.trim()
        ? d.message.trim()
        : 'The operation could not be completed.'
    const code = typeof d.code === 'string' && d.code.trim() ? d.code : 'error'
    const detail =
      typeof d.detail === 'string'
        ? d.detail
        : d.detail != null
          ? JSON.stringify(d.detail)
          : undefined
    const category =
      typeof d.category === 'string' && d.category.trim()
        ? d.category
        : undefined
    const target =
      typeof d.target === 'string' && d.target.trim()
        ? d.target
        : typeof d.target_name === 'string' && d.target_name.trim()
          ? d.target_name
          : undefined
    return { code, message, detail, category, target }
  }
  return {
    code: 'error',
    message:
      typeof data === 'string' && data.trim()
        ? data
        : 'The operation could not be completed.',
  }
}

/**
 * Normalize a failed `fetch`/HTTP response body into the shared envelope. The
 * body may already be the envelope, a FastAPI `{detail}` shape, or opaque text.
 */
export function normalizeHttpError(
  status: number,
  body: unknown
): ApiErrorEnvelope {
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>
    // A direct envelope can legitimately carry object-valued diagnostic detail;
    // preserve its top-level message/code instead of mistaking that detail for
    // FastAPI's wrapper.
    if (typeof b.message === 'string' && b.message.trim()) {
      return {
        code: typeof b.code === 'string' && b.code ? b.code : `http_${status}`,
        message: b.message.trim(),
        category:
          typeof b.category === 'string' && b.category.trim()
            ? b.category
            : undefined,
        target:
          typeof b.target === 'string' && b.target.trim()
            ? b.target
            : typeof b.target_name === 'string' && b.target_name.trim()
              ? b.target_name
              : undefined,
        detail:
          typeof b.detail === 'string'
            ? b.detail
            : b.detail != null
              ? JSON.stringify(b.detail)
              : undefined,
      }
    }
    // FastAPI wraps HTTPException payloads in `{detail: ...}`. TargetGuard's
    // connectivity failures intentionally use an object-valued detail so the
    // category survives; unwrap it after ruling out a direct envelope.
    if (b.detail && typeof b.detail === 'object') {
      const nested = normalizeSseError(b.detail)
      return {
        ...nested,
        code: nested.code === 'error' ? `http_${status}` : nested.code,
      }
    }
    if (typeof b.detail === 'string' && b.detail.trim()) {
      return { code: `http_${status}`, message: b.detail.trim() }
    }
  }
  return {
    code: `http_${status}`,
    message: `The server returned an error (${status}).`,
  }
}

const CONNECTION_CODES = new Set(['database_connection_failed'])

/** True only for stable connection/tunnel/provider-network categories. */
export function isConnectionFailure(
  envelope: Pick<ApiErrorEnvelope, 'code' | 'category'>
): boolean {
  const value = (envelope.category || envelope.code || '').toLowerCase()
  return (
    value.startsWith('ssh_') ||
    value.startsWith('provider_ip_blocked') ||
    CONNECTION_CODES.has(value)
  )
}

/** Preserve structured fields attached to thrown API errors. */
export function normalizeUnknownError(
  error: unknown,
  fallback = 'The operation could not be completed.'
): ApiErrorEnvelope {
  if (error && typeof error === 'object') {
    const value = error as Record<string, unknown>
    return normalizeSseError({
      code: value.code,
      category: value.category,
      target: value.target,
      message:
        typeof value.message === 'string' && value.message.trim()
          ? value.message
          : fallback,
      detail: value.detail,
    })
  }
  return normalizeSseError(
    typeof error === 'string' && error.trim() ? error : fallback
  )
}
