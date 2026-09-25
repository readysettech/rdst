import { createReadStream, existsSync, statSync } from 'node:fs'
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from 'node:http'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import type { ReadableStream } from 'node:stream/web'

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

export interface StaticServerHandle {
  url: string
  close: () => Promise<void>
}

export interface StartStaticServerOptions {
  rootDir: string
  apiBaseUrl: string
  host?: string
  /** Package CI supplies UI sign-in discovery without production account credentials. */
  smokeAuth?: boolean
}

function sendText(res: ServerResponse, statusCode: number, text: string): void {
  if (res.destroyed || res.writableEnded) return
  if (res.headersSent) {
    res.destroy()
    return
  }
  res.writeHead(statusCode, { 'content-type': 'text/plain; charset=utf-8' })
  res.end(text)
}

function resolveStaticPath(rootDir: string, requestUrl: string): string {
  const url = new URL(requestUrl, 'http://127.0.0.1')
  const decodedPathname = decodeURIComponent(url.pathname)
  const normalizedPath = path
    .normalize(decodedPathname)
    .replace(/^(\.\.[/\\])+/, '')
  const candidate =
    normalizedPath === path.sep
      ? path.join(rootDir, 'index.html')
      : path.join(rootDir, normalizedPath)

  if (!candidate.startsWith(rootDir)) {
    return path.join(rootDir, 'index.html')
  }

  if (existsSync(candidate) && statSync(candidate).isFile()) {
    return candidate
  }

  return path.join(rootDir, 'index.html')
}

async function proxyApi(
  req: IncomingMessage,
  res: ServerResponse,
  apiBaseUrl: string
): Promise<void> {
  if (!req.url) {
    sendText(res, 400, 'Missing request URL')
    return
  }

  const controller = new AbortController()
  const abort = () => controller.abort()
  req.once('aborted', abort)
  res.once('close', abort)
  try {
    const target = new URL(req.url, apiBaseUrl)
    const response = await fetch(target, {
      signal: controller.signal,
      method: req.method,
      headers: req.headers as HeadersInit,
      body: req.method === 'GET' || req.method === 'HEAD' ? undefined : req,
      duplex: 'half',
    } as RequestInit)

    if (controller.signal.aborted || res.destroyed) {
      await response.body?.cancel()
      return
    }
    res.writeHead(response.status, Object.fromEntries(response.headers))
    if (response.body) {
      await pipeline(Readable.fromWeb(response.body as ReadableStream), res)
    } else {
      res.end()
    }
  } catch (error) {
    if (controller.signal.aborted) return
    sendText(
      res,
      502,
      error instanceof Error ? error.message : 'RDST API proxy failed'
    )
  } finally {
    req.off('aborted', abort)
    res.off('close', abort)
  }
}

function serveStatic(
  req: IncomingMessage,
  res: ServerResponse,
  rootDir: string
): void {
  if (!req.url) {
    sendText(res, 400, 'Missing request URL')
    return
  }

  const filePath = resolveStaticPath(rootDir, req.url)
  const ext = path.extname(filePath)
  res.writeHead(200, {
    'content-type': MIME_TYPES[ext] ?? 'application/octet-stream',
  })
  void pipeline(createReadStream(filePath), res).catch(() => res.destroy())
}

export async function startStaticServer(
  options: StartStaticServerOptions
): Promise<StaticServerHandle> {
  const host = options.host ?? '127.0.0.1'
  const rootDir = path.resolve(options.rootDir)
  if (!existsSync(path.join(rootDir, 'index.html'))) {
    throw new Error(`RDST web bundle is missing index.html at ${rootDir}`)
  }

  const server = createServer((req, res) => {
    if (
      options.smokeAuth &&
      req.url?.split('?')[0] === '/api/env/requirements'
    ) {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(
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
        })
      )
      return
    }
    if (req.url?.startsWith('/api')) {
      void proxyApi(req, res, options.apiBaseUrl)
      return
    }
    serveStatic(req, res, rootDir)
  })

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, host, () => {
      server.off('error', reject)
      resolve()
    })
  })

  const address = server.address()
  if (!address || typeof address === 'string') {
    await closeServer(server)
    throw new Error('Unable to determine RDST desktop web server address')
  }

  return {
    url: `http://${host}:${address.port}`,
    close: () => closeServer(server),
  }
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) {
        reject(error)
        return
      }
      resolve()
    })
    server.closeAllConnections()
  })
}
