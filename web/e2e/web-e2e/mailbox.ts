/**
 * Reads the CI mailbox over HTTP.
 *
 * The trial and report flows are only meaningfully covered if the email really
 * leaves the keyservice and really arrives somewhere, so these tests wait on a
 * live inbox rather than asserting against the send call's return value.
 *
 * Nothing here drives a browser. The message is fetched over the API and the
 * link is pulled out of the HTML; the browser is only ever handed the resulting
 * URL. Automating a webmail UI would add a second flaky surface for no coverage.
 */

const API = 'https://api.resend.com'

/** Domain the CI addresses live on. Catch-all, so any local part resolves. */
export const MAILBOX_DOMAIN =
  process.env.RDST_CI_MAIL_DOMAIN ?? 'olkeewia.resend.app'

export type ReceivedEmail = {
  id: string
  to: string[]
  from: string
  subject: string
  created_at: string
}

export type ReceivedEmailBody = ReceivedEmail & {
  html: string
  text: string
}

function apiKey(): string {
  const key = process.env.RDST_CI_RESEND_API_KEY
  if (!key) {
    throw new Error(
      'RDST_CI_RESEND_API_KEY is required to read the CI mailbox. It must be a ' +
        'full-access Resend key — the send-only key the Worker uses returns 401 ' +
        'on every read endpoint.'
    )
  }
  return key
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${apiKey()}` },
  })
  if (!response.ok) {
    throw new Error(
      `Resend ${path} responded ${response.status}: ${await response.text()}`
    )
  }
  return (await response.json()) as T
}

/**
 * Address for this build. Unique per CL so concurrent builds never contend for
 * the same account, and so each run is a genuine first-time signup.
 */
export function ciAddress(label: string): string {
  const change = process.env.GERRIT_CHANGE_ID ?? 'local'
  const build = process.env.BUILDKITE_BUILD_NUMBER ?? `${Date.now()}`
  return `${label}-${change}-${build}@${MAILBOX_DOMAIN}`.toLowerCase()
}

/**
 * Wait for a message addressed to `recipient`, optionally also requiring
 * `contains` somewhere in the body.
 *
 * The list endpoint has no recipient filter, so this pages the recent window and
 * matches client-side. That is fine at CI volume and keeps concurrent builds
 * independent, since each one waits on an address only it uses.
 */
export async function waitForEmail(
  recipient: string,
  options: {
    contains?: string
    /** Ignore anything received before this. */
    newerThan?: Date
    timeoutMs?: number
    intervalMs?: number
  } = {}
): Promise<ReceivedEmailBody> {
  const {
    contains,
    newerThan,
    timeoutMs = 120_000,
    intervalMs = 3_000,
  } = options
  const target = recipient.toLowerCase()
  const floor = newerThan ? newerThan.getTime() : 0
  const deadline = Date.now() + timeoutMs
  let lastSeen = 0

  while (Date.now() < deadline) {
    const list = await get<{ data: ReceivedEmail[] }>(
      '/emails/receiving?limit=50'
    )
    lastSeen = list.data.length

    // Newest first. A retried run reuses its address, and the earlier
    // attempt's verification token was invalidated when the account was
    // swept — following the stale link lands on "invalid or expired".
    const candidates = list.data
      .filter((m) => m.to?.some((address) => address.toLowerCase() === target))
      .filter((m) => new Date(m.created_at).getTime() >= floor)
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )

    for (const summary of candidates) {
      const full = await get<ReceivedEmailBody>(
        `/emails/receiving/${summary.id}`
      )
      if (!contains || (full.html ?? '').includes(contains)) return full
    }

    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }

  throw new Error(
    `No email for ${recipient}${contains ? ` containing "${contains}"` : ''} ` +
      `${newerThan ? `after ${newerThan.toISOString()} ` : ''}` +
      `within ${timeoutMs}ms (${lastSeen} messages in the recent window). ` +
      `Either the keyservice did not send it, or delivery to ${MAILBOX_DOMAIN} ` +
      `is broken.`
  )
}

/**
 * The one-time password the report email displays beside the link.
 *
 * The template renders a "Report Password" caption immediately followed by the
 * value, so the caption is what anchors the match - the password itself is
 * eight arbitrary alphanumerics and would otherwise look like any other token
 * in the markup.
 */
export function reportPasswordFrom(email: ReceivedEmailBody): string {
  const match = (email.html ?? '').match(
    /Report Password<\/div>\s*<div[^>]*>\s*([A-Za-z0-9]{8})\s*<\/div>/
  )
  if (!match) {
    throw new Error(
      `No report password in "${email.subject}". The email template may have ` +
        `changed, or the report was sent without password protection.`
    )
  }
  return match[1]
}

/**
 * First link in the message whose href starts with `prefix`.
 *
 * Callers pass the preview Worker's own origin as the prefix, which both finds
 * the right link and proves the mail came from this build's Worker rather than
 * a neighbouring one.
 */
export function linkFrom(email: ReceivedEmailBody, prefix: string): string {
  const hrefs = [...(email.html ?? '').matchAll(/href="([^"]+)"/g)].map(
    (match) => match[1].replace(/&amp;/g, '&')
  )
  const match = hrefs.find((href) => href.startsWith(prefix))
  if (!match) {
    throw new Error(
      `No link starting with ${prefix} in "${email.subject}". ` +
        `Found: ${hrefs.join(', ') || '(none)'}`
    )
  }
  return match
}
