import { createReadStream, existsSync, statSync } from 'node:fs'
import path from 'node:path'
import { Readable } from 'node:stream'
import type { CustomScheme } from 'electron'

// The renderer is served from this origin. It stays the same across launches, so the
// renderer's localStorage and per-origin zoom last; each flavor has its own Electron
// profile, so flavors keep separate storage under the same origin.
export const APP_SCHEME = 'rdst-app'
const APP_HOST = 'ui'
export const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`

/** Registered before the app is ready: a standard, secure origin that fetch and SSE work on. */
export const APP_SCHEME_PRIVILEGES: CustomScheme = {
  scheme: APP_SCHEME,
  privileges: {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    corsEnabled: true,
    stream: true,
  },
}

/** Marks the proxy's own answers when the backend can't be reached. */
export const UNREACHABLE_HEADER = 'x-rdst-backend'

const MIME_TYPES: Record<string, string> = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ttf': 'font/ttf',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

// Hop-by-hop and per-connection headers, and the browser's source headers: requests that
// reach the protocol come only from RDST's own pages, and the backend treats a headerless
// loopback call as local.
const DROPPED_REQUEST_HEADERS = [
  'connection',
  'content-length',
  'host',
  'origin',
  'referer',
  'transfer-encoding',
]
// fetch decodes the body, so its encoding and length no longer describe what is sent on.
const DROPPED_RESPONSE_HEADERS = [
  'connection',
  'content-encoding',
  'content-length',
  'keep-alive',
  'transfer-encoding',
]
const NULL_BODY_STATUSES = new Set([101, 204, 205, 304])

function parseUrl(url: string): URL | null {
  try {
    return new URL(url)
  } catch {
    return null
  }
}

/** True for a page of RDST's own renderer origin. */
export function isAppUrl(url: string): boolean {
  const parsed = parseUrl(url)
  return parsed?.protocol === `${APP_SCHEME}:` && parsed.host === APP_HOST
}

/** True for a page the window shows in place: the app origin, or the dev server's. */
export function isRendererUrl(url: string, devRendererUrl?: string): boolean {
  if (isAppUrl(url)) return true
  if (!devRendererUrl) return false
  const parsed = parseUrl(url)
  const dev = parseUrl(devRendererUrl)
  return Boolean(
    parsed && dev && parsed.origin !== 'null' && parsed.origin === dev.origin
  )
}

function text(
  status: number,
  body: string,
  headers: Record<string, string> = {}
): Response {
  return new Response(body, {
    status,
    headers: { 'content-type': 'text/plain; charset=utf-8', ...headers },
  })
}

function unreachable(status: number, body: string): Response {
  return text(status, body, { [UNREACHABLE_HEADER]: 'unreachable' })
}

/** The bundle file a renderer path names; null when it escapes the bundle. */
export function resolveStaticPath(
  rootDir: string,
  pathname: string
): string | null {
  let decoded: string
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  if (decoded.includes('\0')) return null
  const resolved = path.resolve(rootDir, `.${path.sep}${decoded}`)
  if (resolved !== rootDir && !resolved.startsWith(rootDir + path.sep))
    return null
  if (
    resolved !== rootDir &&
    existsSync(resolved) &&
    statSync(resolved).isFile()
  )
    return resolved
  // Routes are the single-page app's own; every other path gets index.html.
  return path.join(rootDir, 'index.html')
}

function serveStatic(rootDir: string, pathname: string): Response {
  const filePath = resolveStaticPath(rootDir, pathname)
  if (!filePath) return text(404, 'Not found')
  const stream = Readable.toWeb(
    createReadStream(filePath)
  ) as unknown as ReadableStream
  return new Response(stream, {
    status: 200,
    headers: {
      'content-type':
        MIME_TYPES[path.extname(filePath)] ?? 'application/octet-stream',
      'x-content-type-options': 'nosniff',
    },
  })
}

/**
 * A desktop sign-in returns through the external browser, which can't open the app's own
 * origin, so the callback goes to the backend's loopback address instead.
 */
export function rewriteAccountReturnUrl(
  body: string,
  apiBaseUrl: string
): string {
  try {
    const parsed = JSON.parse(body) as unknown
    if (!parsed || typeof parsed !== 'object') return body
    const request = parsed as { return_url?: unknown }
    if (typeof request.return_url !== 'string' || !isAppUrl(request.return_url))
      return body
    const original = new URL(request.return_url)
    request.return_url = new URL(
      original.pathname + original.search,
      apiBaseUrl
    ).toString()
    return JSON.stringify(request)
  } catch {
    return body
  }
}

function smokeRequirements(): Response {
  return new Response(
    JSON.stringify({
      requirements: [
        {
          kind: 'anthropic_api_key',
          name: 'ANTHROPIC_API_KEY',
          satisfied: true,
          source: 'readyset_account',
        },
      ],
      missing: [],
      all_satisfied: true,
      keyring_available: false,
    }),
    { status: 200, headers: { 'content-type': 'application/json' } }
  )
}

/** A backend redirect stays on the app origin; one that leaves the backend is refused. */
function redirectResponse(
  response: Response,
  target: URL,
  apiBaseUrl: string
): Response {
  const location = response.headers.get('location')
  const resolved = location
    ? parseUrl(new URL(location, target).toString())
    : null
  if (!resolved || resolved.origin !== new URL(apiBaseUrl).origin) {
    return text(502, 'The RDST backend redirected outside RDST')
  }
  return new Response(null, {
    status: response.status,
    headers: {
      location: `${APP_ORIGIN}${resolved.pathname}${resolved.search}${resolved.hash}`,
    },
  })
}

async function proxyApi(
  request: Request,
  url: URL,
  options: AppRequestHandlerOptions
): Promise<Response> {
  const apiBaseUrl = options.apiBaseUrl()
  if (!apiBaseUrl) return unreachable(503, 'RDST is starting')
  const target = new URL(url.pathname + url.search, apiBaseUrl)
  const headers = new Headers(request.headers)
  for (const name of DROPPED_REQUEST_HEADERS) headers.delete(name)
  const method = request.method.toUpperCase()
  let body: string | ArrayBuffer | undefined
  if (method !== 'GET' && method !== 'HEAD') {
    body = await request.arrayBuffer()
    if (method === 'POST' && url.pathname === '/api/account/login') {
      body = rewriteAccountReturnUrl(new TextDecoder().decode(body), apiBaseUrl)
    }
  }

  let response: Response
  try {
    response = await (options.fetch ?? fetch)(target, {
      method,
      headers,
      body,
      redirect: 'manual',
      signal: request.signal,
    })
  } catch (error) {
    return unreachable(
      502,
      error instanceof Error ? error.message : 'RDST API proxy failed'
    )
  }

  if (
    response.status >= 300 &&
    response.status < 400 &&
    response.headers.has('location')
  ) {
    await response.body?.cancel()
    return redirectResponse(response, target, apiBaseUrl)
  }
  const responseHeaders = new Headers(response.headers)
  for (const name of DROPPED_RESPONSE_HEADERS) responseHeaders.delete(name)
  const passBody = !NULL_BODY_STATUSES.has(response.status) && method !== 'HEAD'
  if (!passBody) await response.body?.cancel()
  return new Response(passBody ? response.body : null, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  })
}

export interface AppRequestHandlerOptions {
  /** The renderer bundle; it must hold index.html. */
  rootDir: string
  /** The backend's address; null while it is starting or restarting. */
  apiBaseUrl: () => string | null
  /** Package CI supplies UI sign-in discovery without production account credentials. */
  smokeAuth?: boolean
  fetch?: typeof fetch
}

/**
 * The app scheme's handler: the renderer bundle with a single-page-app fallback, and /api
 * proxied to the backend with streaming bodies (SSE) passed through.
 */
export function createAppRequestHandler(
  options: AppRequestHandlerOptions
): (request: Request) => Promise<Response> {
  const rootDir = path.resolve(options.rootDir)
  if (!existsSync(path.join(rootDir, 'index.html'))) {
    throw new Error(`RDST web bundle is missing index.html at ${rootDir}`)
  }
  return async (request) => {
    const url = parseUrl(request.url)
    if (!url || !isAppUrl(request.url)) return text(404, 'Not found')
    if (url.pathname === '/api' || url.pathname.startsWith('/api/')) {
      if (options.smokeAuth && url.pathname === '/api/env/requirements') {
        return smokeRequirements()
      }
      return proxyApi(request, url, options)
    }
    return serveStatic(rootDir, url.pathname)
  }
}
