import { readFileSync } from 'node:fs'

/**
 * A real inbox the agent can sign up with and read: a catch-all Resend
 * domain, as the web-e2e suite uses. Account flows are enabled only against
 * a non-production keyservice, so runs never create production accounts.
 */
const PRODUCTION_KEYSERVICE_HOSTS = new Set([
  'keyservice.readyset.io',
  'rdst-keyservice.readysetio.workers.dev',
])
const API = 'https://api.resend.com'
const MAX_TEXT_CHARS = 4_000

export interface MailConfig {
  apiKey: string
  domain: string
  keyserviceUrl: string
  /** Signs the app's keyservice requests; non-production keyservices use their own. */
  attestationSecret?: string
}

function fromFileOrEnv(environment: NodeJS.ProcessEnv, name: string) {
  const file = environment[`${name}_FILE`]
  return (file ? readFileSync(file, 'utf8') : (environment[name] ?? '')).trim()
}

/** True for production's hosts, and for anything unparseable, which is refused too. */
export function isProductionKeyservice(url: string): boolean {
  try {
    return PRODUCTION_KEYSERVICE_HOSTS.has(new URL(url).hostname.toLowerCase())
  } catch {
    return true
  }
}

/** Mail config from the environment, or why account flows are off. */
export function mailConfig(
  environment: NodeJS.ProcessEnv = process.env
): MailConfig | { unavailable: string } {
  const apiKey = fromFileOrEnv(environment, 'RDST_RELENTLESS_RESEND_API_KEY')
  const keyserviceUrl = environment.RDST_RELENTLESS_KEYSERVICE_URL?.trim()
  if (!apiKey) return { unavailable: 'no Resend key for the test inbox' }
  if (!keyserviceUrl) return { unavailable: 'no test keyservice configured' }
  if (isProductionKeyservice(keyserviceUrl)) {
    return { unavailable: 'the configured keyservice is production' }
  }
  const attestationSecret = fromFileOrEnv(
    environment,
    'RDST_RELENTLESS_ATTESTATION_SECRET'
  )
  return {
    apiKey,
    keyserviceUrl,
    domain: environment.RDST_RELENTLESS_MAIL_DOMAIN ?? 'olkeewia.resend.app',
    ...(attestationSecret ? { attestationSecret } : {}),
  }
}

interface Received {
  id: string
  to: string[]
  from: string
  subject: string
  created_at: string
}

async function get<T>(config: MailConfig, path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${config.apiKey}` },
    signal: AbortSignal.timeout(15_000),
  })
  if (!response.ok) {
    throw new Error(`Resend ${path} responded ${response.status}`)
  }
  return (await response.json()) as T
}

/** Newest message to `to` received after `since`, waiting up to `timeoutMs`. */
export async function waitForMail(
  config: MailConfig,
  to: string,
  {
    since,
    contains,
    timeoutMs,
  }: { since: number; contains?: string; timeoutMs: number }
) {
  const target = to.toLowerCase()
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const list = await get<{ data: Received[] }>(
      config,
      '/emails/receiving?limit=50'
    )
    const candidates = list.data
      .filter((m) => m.to?.some((address) => address.toLowerCase() === target))
      .filter((m) => new Date(m.created_at).getTime() >= since)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
    for (const summary of candidates) {
      const full = await get<Received & { html?: string; text?: string }>(
        config,
        `/emails/receiving/${summary.id}`
      )
      const html = full.html ?? ''
      if (
        contains &&
        !html.includes(contains) &&
        !(full.text ?? '').includes(contains)
      ) {
        continue
      }
      const links = [...html.matchAll(/href="([^"]+)"/g)].map((m) =>
        m[1].replace(/&amp;/g, '&')
      )
      return {
        subject: full.subject,
        from: full.from,
        received_at: full.created_at,
        text: (full.text ?? html.replace(/<[^>]+>/g, ' ')).slice(
          0,
          MAX_TEXT_CHARS
        ),
        links,
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 3_000))
  }
  throw new Error(`no email to ${to} within ${timeoutMs}ms`)
}
