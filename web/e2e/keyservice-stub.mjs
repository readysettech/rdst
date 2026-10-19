// A local stand-in for the Key Service, for browser suites that run the production backend
// without a deployed Worker. It mints anonymous sessions, answers the hosted-AI status and
// holds monitoring slots in memory, and refuses everything else (inference, Jev, sign-in)
// with a 503 so nothing reaches production. `/__stub/requests` lists what RDST asked for.
//
//   node e2e/keyservice-stub.mjs            (port RDST_E2E_KEYSERVICE_PORT, default 8786)
import { randomUUID } from 'node:crypto'
import { createServer } from 'node:http'

const port = Number(process.env.RDST_E2E_KEYSERVICE_PORT ?? '8786')
const host = process.env.RDST_E2E_KEYSERVICE_HOST ?? '127.0.0.1'
const ANONYMOUS_LIMIT_MICROUSD = 1_000_000
const TRIAL_DAYS = 30
const MONITOR_LIMIT = 3

/** Everything the stub has seen and handed out since start (or the last reset). */
function freshState() {
  return {
    requests: [],
    sessions: new Map(),
    refreshTokens: new Map(),
    slots: new Map(),
    machines: new Map(),
  }
}
let state = freshState()

function b64url(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

/** A JWT-shaped access token. RDST reads its claims unverified; nothing checks a signature. */
function accessToken(userId, anonymous, expiresAt) {
  return [
    b64url({ alg: 'none', typ: 'JWT' }),
    b64url({ sub: userId, is_anonymous: anonymous, exp: expiresAt, role: 'authenticated' }),
    'stub',
  ].join('.')
}

function issueTokens(session) {
  const now = Math.floor(Date.now() / 1000)
  const expiresAt = now + 3600
  const token = accessToken(session.userId, session.anonymous, expiresAt)
  const refresh = `refresh-${randomUUID()}`
  state.sessions.set(token, session)
  state.refreshTokens.set(refresh, session)
  return {
    access_token: token,
    refresh_token: refresh,
    expires_at: expiresAt,
    expires_in: 3600,
    token_type: 'bearer',
  }
}

function send(response, status, body) {
  response.writeHead(status, { 'content-type': 'application/json' })
  response.end(JSON.stringify(body))
}

function readBody(request) {
  return new Promise((resolve) => {
    let raw = ''
    request.on('data', (chunk) => {
      raw += chunk
    })
    request.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
}

function sessionOf(request) {
  const header = request.headers.authorization ?? ''
  const token = header.startsWith('Bearer ') ? header.slice(7).trim() : ''
  return state.sessions.get(token) ?? null
}

function slotSummary(userId) {
  const databases = [...state.slots.values()].filter((slot) => slot.userId === userId)
  return {
    limit: MONITOR_LIMIT,
    own_key_limit: 10,
    active: databases.length,
    hosted_active: databases.length,
    window_days: 7,
    databases: databases.map(({ fingerprint, firstSeenAt, label }) => ({
      fingerprint,
      tier: 'hosted',
      first_seen_at: firstSeenAt,
      last_seen_at: Math.floor(Date.now() / 1000),
      label,
    })),
  }
}

const UNAUTHORIZED = { code: 'UNAUTHORIZED', detail: 'Bearer token required' }

async function handle(request, response) {
  const url = new URL(request.url ?? '/', `http://${host}:${port}`)
  const path = url.pathname
  if (path === '/health') return send(response, 200, { ok: true })
  if (path === '/__stub/requests')
    return send(response, 200, { requests: state.requests })
  if (path === '/__stub/reset' && request.method === 'POST') {
    state = freshState()
    return send(response, 200, { ok: true })
  }

  const body = request.method === 'GET' ? {} : await readBody(request)
  state.requests.push({ method: request.method, path, at: Date.now() })

  if (path === '/account-auth/anonymous' && request.method === 'POST') {
    const now = Math.floor(Date.now() / 1000)
    const machine = String(body.machine_hash ?? '')
    if (!machine) return send(response, 400, { reason: 'machine_hash_required' })
    const firstSeen = state.machines.get(machine) ?? now
    state.machines.set(machine, firstSeen)
    const session = { userId: randomUUID(), anonymous: true, machine, firstSeen }
    return send(response, 200, {
      ...issueTokens(session),
      user_id: session.userId,
      is_anonymous: true,
      machine_first_seen_at: firstSeen,
      trial_days: TRIAL_DAYS,
    })
  }

  if (path === '/account-auth/refresh' && request.method === 'POST') {
    const session = state.refreshTokens.get(String(body.refresh_token ?? ''))
    if (!session) return send(response, 401, { detail: 'Invalid refresh token' })
    state.refreshTokens.delete(String(body.refresh_token))
    return send(response, 200, issueTokens(session))
  }

  if (path === '/v1/inference/status' && request.method === 'GET') {
    const session = sessionOf(request)
    if (!session) return send(response, 401, UNAUTHORIZED)
    const quota = {
      limit_microusd: ANONYMOUS_LIMIT_MICROUSD,
      committed_microusd: 0,
      remaining_microusd: ANONYMOUS_LIMIT_MICROUSD,
    }
    return send(response, 200, {
      model: 'stub',
      quota,
      is_anonymous: session.anonymous,
      limit_microusd: ANONYMOUS_LIMIT_MICROUSD,
      used_microusd: 0,
      percent_used: 0,
      machine_first_seen_at: session.firstSeen,
      trial_days: TRIAL_DAYS,
      days_remaining: TRIAL_DAYS,
      signin_prompt: 'none',
    })
  }

  if (path === '/v1/monitor/databases' && request.method === 'GET') {
    const session = sessionOf(request)
    if (!session) return send(response, 401, UNAUTHORIZED)
    return send(response, 200, slotSummary(session.userId))
  }

  if (path === '/v1/monitor/databases/claim' && request.method === 'POST') {
    const session = sessionOf(request)
    if (!session) return send(response, 401, UNAUTHORIZED)
    const fingerprint = String(body.fingerprint ?? '')
    const key = `${session.userId}:${fingerprint}`
    if (!state.slots.has(key))
      state.slots.set(key, {
        userId: session.userId,
        fingerprint,
        label: body.label ?? null,
        firstSeenAt: Math.floor(Date.now() / 1000),
      })
    return send(response, 200, slotSummary(session.userId))
  }

  if (path === '/v1/monitor/databases/release' && request.method === 'POST') {
    const session = sessionOf(request)
    if (!session) return send(response, 401, UNAUTHORIZED)
    state.slots.delete(`${session.userId}:${String(body.fingerprint ?? '')}`)
    return send(response, 200, slotSummary(session.userId))
  }

  // Inference, Jev, sign-in and anything new: unavailable, never forwarded.
  return send(response, 503, {
    code: 'STUB_UNAVAILABLE',
    detail: `The e2e Key Service stub doesn't serve ${request.method} ${path}`,
  })
}

const server = createServer((request, response) => {
  handle(request, response).catch((error) => {
    send(response, 500, { detail: String(error) })
  })
})
server.listen(port, host, () => {
  console.log(`Key Service stub on http://${host}:${port}`)
})
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)))
}
