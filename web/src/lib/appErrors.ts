/**
 * `app_error` from the UI: uncaught errors, unhandled promise rejections and the root
 * error boundary. Messages are scrubbed of SQL, hosts, addresses, paths, emails and
 * quoted values before they leave, and the stream is rate limited: a few events a
 * minute, each distinct error once an hour.
 */

export const APP_ERRORS_PER_MINUTE = 5
export const APP_ERROR_DEDUPE_MS = 60 * 60 * 1000
const MESSAGE_LIMIT = 200

const SCRUBS: [RegExp, string][] = [
  [
    /\b(SELECT|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|WITH\s+\S+\s+AS|CREATE|ALTER|DROP|TRUNCATE)\b[\s\S]*/g,
    '[sql]',
  ],
  [
    /\b(select\s[\s\S]*?\sfrom\s|insert\s+into\s|delete\s+from\s|update\s+\S+\s+set\s)[\s\S]*/gi,
    '[sql]',
  ],
  [/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '[email]'],
  [/\b[a-z][a-z0-9+.-]*:\/\/\S+/gi, '[url]'],
  [
    /\b(host|hostname|hostaddr|server|port|dbname|database|db|user|username|password|passwd|sslrootcert|target|schema|table|relation)\s*[=:]\s*\S+/gi,
    '$1=[redacted]',
  ],
  [/'[^']*'|"[^"]*"|`[^`]*`/g, '[value]'],
  [/\[?\b[0-9a-f]{0,4}(:[0-9a-f]{0,4}){2,7}\b\]?/gi, '[ip]'],
  [/\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b/g, '[ip]'],
  [/\b[A-Za-z]:\\\S*/g, '[path]'],
  [/(~|\.{1,2})?(\/[^\s/:,;()]+){2,}\/?/g, '[path]'],
  [
    /\b[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.(com|net|org|io|dev|cloud|internal|local|localdomain|lan|aws|azure|amazonaws|rds|gcp|app|co|us|eu|uk|de|in|ai|db|svc|cluster)\b(:\d+)?/gi,
    '[host]',
  ],
  [/\blocalhost(:\d+)?\b/gi, '[host]'],
]

/** A short message with SQL, hosts, addresses, paths, emails and quoted values removed. */
export function scrubMessage(text: unknown, limit = MESSAGE_LIMIT): string {
  let value = String(text ?? '')
  for (const [pattern, replacement] of SCRUBS)
    value = value.replace(pattern, replacement)
  value = value.split(/\s+/).filter(Boolean).join(' ')
  return value.length <= limit ? value : `${value.slice(0, limit - 3)}...`
}

const PRIVATE_EVENT_KEYS = new Set([
  'sql',
  'query',
  'query_text',
  'normalized_sql',
  'host',
  'hostname',
  'server',
  'database',
  'database_name',
  'dbname',
  'target',
  'target_name',
  'email',
  'username',
  'password',
  'path',
  '$pathname',
  '$title',
  'file',
  'url',
  'title',
  'body',
  'detail',
])
const IDENTITY_EVENT_KEYS = new Set([
  'distinct_id',
  '$device_id',
  '$anon_distinct_id',
  '$session_id',
  '$window_id',
  'device_id',
  'installation_id',
  'account_user_id',
  'analytics_account_id',
])

/** Scrub named events and automatic captures, including nested exception payloads. */
export function scrubEventProperties(
  properties: Record<string, unknown>
): Record<string, unknown> {
  const clean = (value: unknown, depth: number): unknown => {
    if (depth > 5) return '[redacted]'
    if (typeof value === 'string') return scrubMessage(value)
    if (Array.isArray(value))
      return value.slice(0, 100).map((item) => clean(item, depth + 1))
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value)
          .slice(0, 100)
          .filter(([key]) => !PRIVATE_EVENT_KEYS.has(key.toLowerCase()))
          .map(([key, item]) => [key, clean(item, depth + 1)])
      )
    }
    return value
  }
  const safe: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(properties).slice(0, 100)) {
    if (PRIVATE_EVENT_KEYS.has(key.toLowerCase())) continue
    if (key === '$set' || key === '$set_once') {
      // These are PostHog person-profile operations, removed during event ingestion.
      const profile = value as Record<string, unknown> | null
      safe[key] = {
        ...(typeof profile?.email === 'string' ? { email: profile.email } : {}),
        ...(typeof profile?.account_kind === 'string'
          ? { account_kind: profile.account_kind }
          : {}),
      }
    } else if (
      IDENTITY_EVENT_KEYS.has(key) ||
      (key.endsWith('version') &&
        typeof value === 'string' &&
        /^[A-Za-z0-9_.+-]{1,60}$/.test(value))
    ) {
      safe[key] = value
    } else {
      safe[key] = clean(value, 0)
    }
  }
  return safe
}

export interface AppErrorEvent {
  source: 'web'
  component: string
  error_class: string
  message: string
  handled: boolean
  suppressed_repeats: number
}

export type AppErrorSink = (event: AppErrorEvent) => void

/** Admits a few errors a minute, and each distinct one once an hour. */
export class AppErrorGate {
  private sent: number[] = []
  private last = new Map<string, number>()
  private suppressed = new Map<string, number>()

  constructor(
    private readonly perMinute = APP_ERRORS_PER_MINUTE,
    private readonly dedupeMs = APP_ERROR_DEDUPE_MS
  ) {}

  /** Suppressed repeats to report with this error, or null to drop it. */
  admit(key: string, now: number): number | null {
    this.sent = this.sent.filter((ts) => now - ts < 60_000)
    const last = this.last.get(key)
    if (
      (last !== undefined && now - last < this.dedupeMs) ||
      this.sent.length >= this.perMinute
    ) {
      this.suppressed.set(key, (this.suppressed.get(key) ?? 0) + 1)
      return null
    }
    if (this.last.size > 500) {
      this.last.clear()
      this.suppressed.clear()
    }
    this.last.set(key, now)
    this.sent.push(now)
    const repeats = this.suppressed.get(key) ?? 0
    this.suppressed.delete(key)
    return repeats
  }
}

let sink: AppErrorSink | null = null
let gate = new AppErrorGate()

function errorClass(error: unknown): string {
  if (error instanceof Error)
    return error.name || error.constructor?.name || 'Error'
  if (error === null || error === undefined) return 'Unknown'
  return typeof error === 'object' ? 'NonErrorObject' : `Non${typeof error}`
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return ''
}

/** Report one UI error. A no-op until `installAppErrorCapture` provides a sink. */
export function reportAppError(
  component: string,
  error: unknown,
  { handled = true, now = Date.now() }: { handled?: boolean; now?: number } = {}
): void {
  if (!sink) return
  try {
    const event: Omit<AppErrorEvent, 'suppressed_repeats'> = {
      source: 'web',
      component: scrubMessage(component, 60) || 'unknown',
      error_class: scrubMessage(errorClass(error), 80),
      message: scrubMessage(errorText(error)),
      handled,
    }
    const repeats = gate.admit(
      `${event.component}|${event.error_class}|${event.message.slice(0, 80)}`,
      now
    )
    if (repeats === null) return
    sink({ ...event, suppressed_repeats: repeats })
  } catch {
    // Reporting an error must never raise one.
  }
}

/** Send uncaught errors and unhandled rejections in this window to `report`. */
export function installAppErrorCapture(
  report: AppErrorSink,
  target: Window = window
): () => void {
  sink = report
  gate = new AppErrorGate()
  const onError = (event: ErrorEvent) =>
    reportAppError('window', event.error ?? event.message, { handled: false })
  const onRejection = (event: PromiseRejectionEvent) =>
    reportAppError('promise', event.reason, { handled: false })
  target.addEventListener('error', onError)
  target.addEventListener('unhandledrejection', onRejection)
  return () => {
    target.removeEventListener('error', onError)
    target.removeEventListener('unhandledrejection', onRejection)
    sink = null
  }
}
