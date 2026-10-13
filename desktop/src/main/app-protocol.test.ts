import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { createServer, type RequestListener, type Server } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  APP_ORIGIN,
  createAppRequestHandler,
  isAppUrl,
  isRendererUrl,
  resolveStaticPath,
  rewriteAccountReturnUrl,
  UNREACHABLE_HEADER,
} from './app-protocol.js'

let upstream: Server | undefined
let root: string | undefined

afterEach(async () => {
  upstream?.closeAllConnections()
  await new Promise<void>((resolve) =>
    upstream ? upstream.close(() => resolve()) : resolve()
  )
  upstream = undefined
  if (root) rmSync(root, { recursive: true, force: true })
})

function bundle(): string {
  root = mkdtempSync(path.join(os.tmpdir(), 'rdst-protocol-test-'))
  writeFileSync(path.join(root, 'index.html'), 'RDST')
  mkdirSync(path.join(root, 'assets'))
  writeFileSync(path.join(root, 'assets', 'app.js'), 'run()')
  return root
}

async function backend(handler: RequestListener): Promise<string> {
  upstream = createServer(handler)
  await new Promise<void>((resolve) =>
    upstream!.listen(0, '127.0.0.1', resolve)
  )
  const address = upstream.address()
  if (!address || typeof address === 'string')
    throw new Error('Missing upstream address')
  return `http://127.0.0.1:${address.port}`
}

async function handlerFor(handler: RequestListener, smokeAuth = false) {
  const base = await backend(handler)
  return {
    base,
    handle: createAppRequestHandler({
      rootDir: bundle(),
      apiBaseUrl: () => base,
      smokeAuth,
    }),
  }
}

const app = (route: string, init?: RequestInit) =>
  new Request(`${APP_ORIGIN}${route}`, init)

describe('app origin', () => {
  it('recognizes only its own pages and the dev server', () => {
    expect(isAppUrl('rdst-app://ui/inbox?item=3')).toBe(true)
    expect(isAppUrl('rdst-app://other/')).toBe(false)
    expect(isAppUrl('http://127.0.0.1:4000/')).toBe(false)
    expect(isRendererUrl('rdst-app://ui/')).toBe(true)
    expect(
      isRendererUrl('http://localhost:3001/home', 'http://localhost:3001')
    ).toBe(true)
    expect(
      isRendererUrl('http://localhost:9999/', 'http://localhost:3001')
    ).toBe(false)
    expect(isRendererUrl('https://readyset.io/')).toBe(false)
  })
})

describe('static files', () => {
  it('serves bundle files and falls back to index.html for routes', async () => {
    const handle = createAppRequestHandler({
      rootDir: bundle(),
      apiBaseUrl: () => null,
    })
    const asset = await handle(app('/assets/app.js'))
    expect(asset.headers.get('content-type')).toBe(
      'text/javascript; charset=utf-8'
    )
    expect(await asset.text()).toBe('run()')
    expect(await (await handle(app('/inbox?item=2'))).text()).toBe('RDST')
    expect(await (await handle(app('/'))).text()).toBe('RDST')
  })

  it('never serves a file outside the bundle', () => {
    const dir = bundle()
    expect(resolveStaticPath(dir, '/%2e%2e/%2e%2e/etc/passwd')).toBeNull()
    expect(resolveStaticPath(dir, '/..%2fsecret')).toBeNull()
    expect(resolveStaticPath(dir, '/%00')).toBeNull()
    expect(resolveStaticPath(dir, '/%E0%A4%A')).toBeNull()
    expect(resolveStaticPath(dir, '/assets/app.js')).toBe(
      path.join(dir, 'assets', 'app.js')
    )
    expect(resolveStaticPath(dir, '/assets')).toBe(path.join(dir, 'index.html'))
  })

  it('refuses another host on the scheme', async () => {
    const handle = createAppRequestHandler({
      rootDir: bundle(),
      apiBaseUrl: () => null,
    })
    expect((await handle(new Request('rdst-app://elsewhere/'))).status).toBe(
      404
    )
  })
})

describe('api proxy', () => {
  it('forwards method, path, query, headers and body without browser source headers', async () => {
    const { handle } = await handlerFor((req, res) => {
      let body = ''
      req.on('data', (chunk) => {
        body += chunk
      })
      req.on('end', () => {
        res.writeHead(201, {
          'content-type': 'application/json',
          'x-seen': 'yes',
        })
        res.end(
          JSON.stringify({
            url: req.url,
            method: req.method,
            body,
            kind: req.headers['x-kind'],
            origin: req.headers.origin ?? null,
          })
        )
      })
    })
    const response = await handle(
      app('/api/monitor/targets?x=1', {
        method: 'PUT',
        headers: {
          'content-type': 'application/json',
          'x-kind': 'test',
          origin: APP_ORIGIN,
        },
        body: '{"a":1}',
      })
    )
    expect(response.status).toBe(201)
    expect(response.headers.get('x-seen')).toBe('yes')
    expect(await response.json()).toEqual({
      url: '/api/monitor/targets?x=1',
      method: 'PUT',
      body: '{"a":1}',
      kind: 'test',
      origin: null,
    })
  })

  it('streams server-sent events and closes the upstream when the reader stops', async () => {
    let closed!: () => void
    const upstreamClosed = new Promise<void>((resolve) => {
      closed = resolve
    })
    const { handle } = await handlerFor((_req, res) => {
      res.once('close', closed)
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write('data: first\n\n')
    })
    const response = await handle(app('/api/stream'))
    expect(response.headers.get('content-type')).toBe('text/event-stream')
    const reader = response.body!.getReader()
    expect(new TextDecoder().decode((await reader.read()).value)).toBe(
      'data: first\n\n'
    )
    await reader.cancel()
    await upstreamClosed
  })

  it('keeps backend redirects on the app origin and refuses ones that leave it', async () => {
    const { handle } = await handlerFor((req, res) => {
      const inside = req.url === '/api/slash/'
      res.writeHead(307, {
        location: inside
          ? `http://${req.headers.host}/api/slash`
          : 'https://example.com/',
      })
      res.end()
    })
    const inside = await handle(app('/api/slash/'))
    expect(inside.status).toBe(307)
    expect(inside.headers.get('location')).toBe(`${APP_ORIGIN}/api/slash`)
    expect((await handle(app('/api/away'))).status).toBe(502)
  })

  it('marks its own errors when the backend is down or not started', async () => {
    const { handle } = await handlerFor((_req, res) => res.destroy())
    const failed = await handle(app('/api/status'))
    expect(failed.status).toBe(502)
    expect(failed.headers.get(UNREACHABLE_HEADER)).toBe('unreachable')

    const starting = createAppRequestHandler({
      rootDir: bundle(),
      apiBaseUrl: () => null,
    })
    const pending = await starting(app('/api/status'))
    expect(pending.status).toBe(503)
    expect(pending.headers.get(UNREACHABLE_HEADER)).toBe('unreachable')
  })

  it('follows a backend that moved to a new address', async () => {
    const base = await backend((_req, res) => res.end('moved'))
    let current: string | null = 'http://127.0.0.1:1'
    const handle = createAppRequestHandler({
      rootDir: bundle(),
      apiBaseUrl: () => current,
    })
    expect((await handle(app('/api/status'))).status).toBe(502)
    current = base
    expect(await (await handle(app('/api/status'))).text()).toBe('moved')
  })

  it('answers sign-in discovery itself in package smoke runs', async () => {
    const { handle } = await handlerFor((_req, res) => res.end('backend'), true)
    expect(
      (await (await handle(app('/api/env/requirements'))).json()).all_satisfied
    ).toBe(true)
    expect(await (await handle(app('/api/other'))).text()).toBe('backend')
  })

  it('sends a desktop sign-in back to the backend loopback address', async () => {
    const { base, handle } = await handlerFor((req, res) => {
      let body = ''
      req.on('data', (chunk) => {
        body += chunk
      })
      req.on('end', () => res.end(body))
    })
    const response = await handle(
      app('/api/account/login', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          return_url: `${APP_ORIGIN}/api/account/oauth/callback`,
        }),
      })
    )
    expect(await response.json()).toEqual({
      return_url: `${base}/api/account/oauth/callback`,
    })
    expect(
      rewriteAccountReturnUrl('{"return_url":"http://127.0.0.1:5/x"}', base)
    ).toBe('{"return_url":"http://127.0.0.1:5/x"}')
    expect(rewriteAccountReturnUrl('not json', base)).toBe('not json')
  })
})
