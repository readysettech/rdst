import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

// Keep the desktop scrubber aligned with the web app through telemetry.test.ts.
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

/** Desktop events use the backend; startup failures also have a direct capture path. */

export interface DirectTelemetryConfig {
  key: string
  host: string
  internalProbeUrl: string
  deviceId: string
  appVersion: string
  os: string
  disabled: boolean
  userId?: string
  email?: string
  linkedAccountId?: string
}

declare const __RDST_TELEMETRY_CONFIG__: Pick<
  DirectTelemetryConfig,
  'key' | 'host' | 'internalProbeUrl'
>

/** Read the opt-out and install identity from the same data directory as the sidecar. */
export function loadDirectTelemetryConfig(options: {
  env: NodeJS.ProcessEnv
  packaged: boolean
  appVersion: string
  dataDir: string
  defaults?: Pick<DirectTelemetryConfig, 'key' | 'host' | 'internalProbeUrl'>
}): DirectTelemetryConfig {
  const defaults =
    options.defaults ??
    (typeof __RDST_TELEMETRY_CONFIG__ === 'undefined'
      ? { key: '', host: '', internalProbeUrl: '' }
      : __RDST_TELEMETRY_CONFIG__)
  const { dataDir } = options
  let disabled = telemetryDisabled(options.env, options.packaged)
  let account = ''
  try {
    const config = fs.readFileSync(path.join(dataDir, 'config.toml'), 'utf8')
    disabled ||= /telemetry_enabled\s*=\s*false/i.test(config)
    account =
      config.match(/^\[account\]\s*\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m)?.[1] ?? ''
  } catch {
    // A new installation can start before config.toml exists.
  }
  const key = options.env.RDST_POSTHOG_KEY ?? defaults.key
  disabled ||= !key
  let deviceId = ''
  if (!disabled) {
    const file = path.join(dataDir, 'device_id')
    try {
      deviceId = fs.readFileSync(file, 'utf8').trim()
    } catch {
      /* First launch. */
    }
    if (!deviceId) {
      deviceId = randomUUID()
      try {
        fs.mkdirSync(dataDir, { recursive: true })
        fs.writeFileSync(file, deviceId, { encoding: 'utf8', flag: 'wx' })
      } catch {
        try {
          deviceId = fs.readFileSync(file, 'utf8').trim()
        } catch {
          disabled = true
        }
      }
    }
  }
  const accountValue = (name: string): string => {
    const value = account.match(
      new RegExp(`^${name}\\s*=\\s*("[^"\\n]*"|'[^'\\n]*')`, 'm')
    )?.[1]
    try {
      return value?.startsWith('"')
        ? (JSON.parse(value) as string)
        : (value?.slice(1, -1) ?? '')
    } catch {
      return ''
    }
  }
  const userId =
    accountValue('status') === 'active' ? accountValue('user_id') : ''
  let linkedAccountId = ''
  try {
    const state = JSON.parse(
      fs.readFileSync(path.join(dataDir, 'telemetry_state.json'), 'utf8')
    ) as Record<string, unknown>
    linkedAccountId = String(state[`identity_account:${deviceId}`] ?? '')
  } catch {
    /* An install can begin with no telemetry state. */
  }
  disabled ||= !deviceId
  return {
    ...defaults,
    key,
    deviceId,
    disabled,
    userId,
    email: userId ? accountValue('email') : '',
    linkedAccountId,
    internalProbeUrl:
      options.env.RDST_INTERNAL_PROBE_URL ?? defaults.internalProbeUrl,
    appVersion: options.appVersion,
    os: process.platform,
  }
}

function safeProperties(properties: EventProperties): EventProperties {
  const safe: EventProperties = {}
  for (const [key, value] of Object.entries(properties)) {
    if (
      /^(sql|query|query_text|text|host|hostname|database|target|name|email|path|url|password)$/i.test(
        key
      )
    )
      continue
    safe[key] =
      typeof value === 'string' &&
      !(key.endsWith('version') && /^[A-Za-z0-9_.+-]{1,60}$/.test(value))
        ? scrubMessage(value)
        : value
  }
  return safe
}

export type EventValue = string | number | boolean | null
export type EventProperties = Record<string, EventValue>

export interface QueuedEvent {
  event: string
  properties: EventProperties
}

export type EventPoster = (url: string, body: unknown) => Promise<boolean>

const QUEUE_LIMIT = 100
const BATCH_LIMIT = 50
const TELEMETRY_OFF = new Set([
  '0',
  'false',
  'no',
  'off',
  'disable',
  'disabled',
])

export function telemetryDisabled(
  env: NodeJS.ProcessEnv,
  packaged: boolean
): boolean {
  // Development builds and CI smokes never report; only an installed release does.
  if (!packaged || env.RDST_DESKTOP_SMOKE === '1') return true
  if (
    ['true', '1', 'yes'].includes(String(env.RDST_TESTING ?? '').toLowerCase())
  )
    return true
  return TELEMETRY_OFF.has(
    String(env.RDST_TELEMETRY ?? '')
      .trim()
      .toLowerCase()
  )
}

const postJson: EventPoster = async (url, body) => {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 5_000)
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    return response.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

export class DesktopTelemetry {
  private queue: QueuedEvent[] = []
  private apiBaseUrl: string | null = null
  private flushing: Promise<void> | null = null
  private internalUser: Promise<boolean> | null = null
  private identified = false

  constructor(
    private readonly file: string,
    private readonly disabled: boolean,
    private readonly post: EventPoster = postJson,
    private readonly direct?: DirectTelemetryConfig,
    private readonly probe: (url: string) => Promise<boolean> = async (url) => {
      try {
        return (
          (
            await fetch(url, {
              method: 'HEAD',
              redirect: 'manual',
              signal: AbortSignal.timeout(2_000),
            })
          ).status === 200
        )
      } catch {
        return false
      }
    }
  ) {
    if (disabled || direct?.disabled) return
    try {
      const saved = JSON.parse(fs.readFileSync(file, 'utf8')) as unknown
      if (Array.isArray(saved)) {
        this.queue = saved
          .filter(
            (item): item is QueuedEvent =>
              !!item &&
              typeof item === 'object' &&
              typeof (item as QueuedEvent).event === 'string'
          )
          .map((item) => ({
            event: item.event,
            properties: {
              ...safeProperties(item.properties ?? {}),
              delayed: true,
            },
          }))
          .slice(-QUEUE_LIMIT)
      }
    } catch {
      this.queue = []
    }
  }

  get pending(): readonly QueuedEvent[] {
    return this.queue
  }

  capture(event: string, properties: EventProperties = {}): void {
    if (this.disabled || this.direct?.disabled) return
    const context: EventProperties =
      event === 'desktop_startup_failed' && this.direct
        ? {
            os: this.direct.os,
            app_version: this.direct.appVersion,
            desktop_version: this.direct.appVersion,
          }
        : {}
    this.queue.push({
      event,
      properties: { ...safeProperties(properties), ...context },
    })
    if (this.queue.length > QUEUE_LIMIT)
      this.queue.splice(0, this.queue.length - QUEUE_LIMIT)
    this.save()
    if (this.apiBaseUrl || (event === 'desktop_startup_failed' && this.direct))
      void this.flush()
  }

  /** The backend is up (a URL) or gone (null); queued events go out once it's up. */
  setBackend(apiBaseUrl: string | null): void {
    this.apiBaseUrl = apiBaseUrl
    if (apiBaseUrl && this.queue.length) void this.flush()
  }

  flush(): Promise<void> {
    if (this.disabled || this.direct?.disabled) return Promise.resolve()
    this.flushing ??= this.send().finally(() => {
      this.flushing = null
    })
    return this.flushing
  }

  private async send(): Promise<void> {
    if (this.direct && !this.direct.disabled) {
      const config = this.direct
      for (const item of this.queue.filter(
        (event) => event.event === 'desktop_startup_failed'
      )) {
        this.internalUser ??= config.internalProbeUrl
          ? this.probe(config.internalProbeUrl).catch(() => false)
          : Promise.resolve(false)
        const internalUser = await this.internalUser
        let ok = false
        try {
          const url = `${config.host.replace(/\/$/, '')}/capture/`
          if (config.userId && !this.identified) {
            const identified = await this.post(url, {
              api_key: config.key,
              distinct_id: config.userId,
              event: '$identify',
              properties: {
                internal_user: internalUser,
                ...(!config.linkedAccountId ||
                config.linkedAccountId === config.userId
                  ? { $anon_distinct_id: config.deviceId }
                  : {}),
                $set: {
                  account_kind: 'signed_in',
                  ...(config.email ? { email: config.email } : {}),
                },
              },
            })
            if (!identified) break
            this.identified = true
          }
          ok = await this.post(`${config.host.replace(/\/$/, '')}/capture/`, {
            api_key: config.key,
            distinct_id: config.userId || config.deviceId,
            event: item.event,
            properties: {
              ...safeProperties(item.properties),
              source: 'desktop',
              surface: 'desktop',
              is_desktop: true,
              device_id: config.deviceId,
              installation_id: config.deviceId,
              os: item.properties.os ?? config.os,
              app_version: item.properties.app_version ?? config.appVersion,
              desktop_version:
                item.properties.desktop_version ?? config.appVersion,
              internal_user: internalUser,
              account_kind: config.userId ? 'signed_in' : 'device_only',
              ...(!config.userId
                ? { $set_once: { account_kind: 'device_only' } }
                : {}),
            },
          })
        } catch {
          // Keep the saved event for a later launch.
        }
        if (!ok) break
        this.remove(item)
        this.save()
      }
    }
    while (this.apiBaseUrl && this.queue.length) {
      const batch = this.queue
        .filter(
          (item) => !this.direct || item.event !== 'desktop_startup_failed'
        )
        .slice(0, BATCH_LIMIT)
      if (!batch.length) break
      let ok = false
      try {
        ok = await this.post(`${this.apiBaseUrl}/api/telemetry/events`, {
          source: 'desktop',
          events: batch,
        })
      } catch {
        // Keep the saved events for a later launch.
      }
      if (!ok) break
      for (const item of batch) this.remove(item)
    }
    this.save()
  }

  private remove(item: QueuedEvent): void {
    const index = this.queue.indexOf(item)
    if (index >= 0) this.queue.splice(index, 1)
  }

  private save(): void {
    try {
      if (!this.queue.length) {
        fs.rmSync(this.file, { force: true })
        return
      }
      fs.mkdirSync(path.dirname(this.file), { recursive: true })
      const temporary = `${this.file}.tmp`
      fs.writeFileSync(temporary, JSON.stringify(this.queue), 'utf8')
      fs.renameSync(temporary, this.file)
    } catch {
      // Telemetry never interferes with the app.
    }
  }
}

interface InstallMarker {
  version: string
  installedAt: number
}

export type InstallChange =
  | { kind: 'installed'; installKind: 'fresh' | 'existing_rdst_data' }
  | { kind: 'upgraded'; fromVersion: string; toVersion: string }
  | { kind: 'same' }
  | { kind: 'adopted' }

/**
 * Compare this launch's version with the one recorded in userData. The first launch of
 * a new install records it; an install from before this marker existed (its settings
 * file is already there) is adopted silently, so it never counts as new.
 */
export function detectInstallChange(options: {
  markerFile: string
  version: string
  settingsExisted: boolean
  rdstDataExists: boolean
  now?: number
}): InstallChange {
  let marker: InstallMarker | null = null
  try {
    const saved = JSON.parse(
      fs.readFileSync(options.markerFile, 'utf8')
    ) as Partial<InstallMarker>
    if (typeof saved.version === 'string') {
      marker = {
        version: saved.version,
        installedAt: Number(saved.installedAt) || 0,
      }
    }
  } catch {
    marker = null
  }
  const write = (installedAt: number) => {
    try {
      fs.mkdirSync(path.dirname(options.markerFile), { recursive: true })
      fs.writeFileSync(
        options.markerFile,
        JSON.stringify({ version: options.version, installedAt }),
        'utf8'
      )
    } catch {
      // A marker that can't be written only means the next launch asks again.
    }
  }
  const now = options.now ?? Date.now()
  if (!marker) {
    write(now)
    if (options.settingsExisted) return { kind: 'adopted' }
    return {
      kind: 'installed',
      installKind: options.rdstDataExists ? 'existing_rdst_data' : 'fresh',
    }
  }
  if (marker.version === options.version) return { kind: 'same' }
  write(marker.installedAt)
  return {
    kind: 'upgraded',
    fromVersion: marker.version,
    toVersion: options.version,
  }
}

/** The error's class name: `TypeError`, `Error`, or a short label for a non-error. */
export function errorClass(error: unknown): string {
  if (error instanceof Error) return error.name || 'Error'
  return error === null || error === undefined
    ? 'Unknown'
    : `Non${typeof error}`
}

/** A short label for a backend start failure, from its message. */
export function startFailureClass(message: string): string {
  const text = message.toLowerCase()
  if (
    text.includes('timed out') ||
    text.includes('timeout') ||
    text.includes('no progress')
  )
    return 'timeout'
  if (text.includes('exited') || text.includes('exit code')) return 'exited'
  if (
    text.includes('unable to start') ||
    text.includes('enoent') ||
    text.includes('spawn')
  )
    return 'spawn'
  if (text.includes('address') && text.includes('use')) return 'port_in_use'
  return 'other'
}
