import { appendFileSync } from 'node:fs'
import type { BrowserContext, Response } from '@playwright/test'

export type EvidenceKind =
  | 'console'
  | 'page_error'
  | 'request_failed'
  | 'api'
  | 'stream'
  | 'lifecycle'

export interface EvidenceEvent {
  seq: number
  ts: string
  kind: EvidenceKind
  [key: string]: unknown
}

const MAX_EVENTS_IN_MEMORY = 5_000
const MAX_BODY_CHARS = 2_000
const MAX_STREAM_EVENTS = 40
const MAX_TAPPED_CHARS = 2_000_000
const STREAM_BINDING = '__rdstRelentlessStream'

interface TappedStream {
  method: string
  path: string
  status: number
  ended: 'complete' | 'aborted'
  text: string
}

const STREAM_TAP = `(() => {
  const report = window.${STREAM_BINDING}
  const originalFetch = window.fetch
  window.fetch = async (...args) => {
    const response = await originalFetch(...args)
    const type = response.headers.get('content-type') || ''
    if (!type.includes('text/event-stream') || !response.body) return response
    const url = new URL(response.url, location.href)
    const input = args[0]
    const method = (args[1] && args[1].method) || (input instanceof Request ? input.method : 'GET')
    const [forPage, forDriver] = response.body.tee()
    const reader = forDriver.getReader()
    const decoder = new TextDecoder()
    let text = ''
    const finish = (ended) => report({ method: method.toUpperCase(), path: url.pathname + url.search, status: response.status, ended, text })
    ;(async () => {
      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          if (text.length < ${MAX_TAPPED_CHARS}) text += decoder.decode(value, { stream: true })
        }
        finish('complete')
      } catch {
        finish('aborted')
      }
    })()
    return new Response(forPage, { status: response.status, statusText: response.statusText, headers: response.headers })
  }
})()`

function truncate(text: string, limit = MAX_BODY_CHARS) {
  return text.length > limit ? `${text.slice(0, limit)}...[truncated]` : text
}

function apiPath(url: string) {
  const parsed = new URL(url)
  return `${parsed.pathname}${parsed.search}`
}

/**
 * Credentials the app is issued while it runs, such as an account's access
 * token, which no list of known secrets can name ahead of time: JWTs, fields
 * named like a token, and bearer headers. Only token characters are
 * replaced, so JSON they sit in, escaped or not, stays valid.
 */
const ISSUED_TOKENS: [RegExp, string][] = [
  [/eyJ[\w-]{8,}(?:\.[\w-]+){0,2}/g, '[REDACTED]'],
  [/(\\*"[a-z_]*token\\*"\s*:\s*\\*")[\w.~+/=-]+/gi, '$1[REDACTED]'],
  [/(Bearer\s+)[\w.~+/=-]+/gi, '$1[REDACTED]'],
]

export function redactIssuedTokens(text: string) {
  return ISSUED_TOKENS.reduce(
    (redacted, [pattern, replacement]) =>
      redacted.replace(pattern, replacement),
    text
  )
}

/**
 * Everything the application reported, in order. The agent reads it by
 * cursor so each check sees only what its own actions caused.
 */
export class EvidenceLog {
  private events: EvidenceEvent[] = []
  private seq = 0
  private readonly file: string
  private readonly redact: (text: string) => string
  /** The mission flow and phase in progress, stamped on every event. */
  context: { flow?: string; phase?: string } = {}

  constructor(file: string, redact: (text: string) => string) {
    this.file = file
    this.redact = redact
  }

  get cursor() {
    return this.seq
  }

  record(kind: EvidenceKind, fields: Record<string, unknown>) {
    const line = this.redact(
      JSON.stringify({
        seq: ++this.seq,
        ts: new Date().toISOString(),
        kind,
        ...this.context,
        ...fields,
      })
    )
    const event = JSON.parse(line) as EvidenceEvent
    this.events.push(event)
    if (this.events.length > MAX_EVENTS_IN_MEMORY) this.events.shift()
    appendFileSync(this.file, `${line}\n`)
    return event
  }

  since(cursor: number, kinds?: EvidenceKind[]) {
    return this.events.filter(
      (event) => event.seq > cursor && (!kinds || kinds.includes(event.kind))
    )
  }

  counts(cursor = 0) {
    const counts: Record<string, number> = {}
    for (const event of this.since(cursor)) {
      counts[event.kind] = (counts[event.kind] ?? 0) + 1
    }
    return counts
  }

  async attach(context: BrowserContext) {
    context.on('console', (message) => {
      if (message.type() !== 'error' && message.type() !== 'warning') return
      this.record('console', {
        level: message.type(),
        text: truncate(message.text()),
        url: message.page()?.url(),
      })
    })
    context.on('weberror', (error) => {
      this.record('page_error', {
        text: truncate(error.error().stack ?? error.error().message),
        url: error.page()?.url(),
      })
    })
    context.on('requestfailed', (request) => {
      this.record('request_failed', {
        method: request.method(),
        path: apiPath(request.url()),
        failure: request.failure()?.errorText,
      })
    })
    context.on('response', (response) => {
      if (!new URL(response.url()).pathname.startsWith('/api')) return
      void this.recordApiResponse(response)
    })
    // The browser discards a stream's body once the page stops reading it,
    // so SSE is captured in the page: fetch hands the page one branch of a
    // tee and reports the other to the driver when the stream ends.
    await context.exposeBinding(
      STREAM_BINDING,
      (_source, tapped: TappedStream) => {
        this.record('stream', {
          method: tapped.method,
          path: tapped.path,
          status: tapped.status,
          ...summarizeStream(tapped.text, tapped.ended),
        })
      }
    )
    await context.addInitScript(STREAM_TAP)
  }

  private async recordApiResponse(response: Response) {
    const request = response.request()
    let page: string | undefined
    try {
      page = new URL(response.frame().url()).pathname
    } catch {}
    const base = {
      method: request.method(),
      path: apiPath(response.url()),
      status: response.status(),
      // Which code made the request, for the selector's evidence index.
      source: 'renderer',
      resource: request.resourceType(),
      page,
      request_body: request.postData()
        ? truncate(request.postData() ?? '', 500)
        : undefined,
    }
    const contentType = response.headers()['content-type'] ?? ''
    if (contentType.includes('text/event-stream')) {
      this.record('api', { ...base, stream: 'opened' })
      return
    }
    if (response.status() < 400) {
      this.record('api', base)
      return
    }
    let body = ''
    try {
      body = truncate((await response.body()).toString('utf8'))
    } catch {}
    this.record('api', { ...base, body })
  }
}

/** Condense an SSE body to its event types and terminal event. */
export function summarizeStream(
  body: string,
  ended: 'complete' | 'aborted' = 'complete'
) {
  // An SSE event is a blank-line-separated block; its name is the `event:`
  // field, or the payload's `type` when the stream carries it in the data.
  const events: { type: string; data: unknown }[] = []
  for (const block of body.replace(/\r/g, '').split('\n\n')) {
    let name: string | undefined
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trim())
    }
    if (!name && data.length === 0) continue
    let payload: unknown = data.join('\n')
    try {
      payload = JSON.parse(payload as string)
    } catch {}
    const typed =
      payload && typeof payload === 'object' && 'type' in payload
        ? String((payload as { type: unknown }).type)
        : undefined
    events.push({ type: name ?? typed ?? 'message', data: payload })
  }
  const last = events.at(-1)
  return {
    ended,
    event_count: events.length,
    event_types: events.slice(0, MAX_STREAM_EVENTS).map(({ type }) => type),
    last_event: last
      ? truncate(JSON.stringify({ type: last.type, data: last.data }), 1_000)
      : null,
  }
}
