import { createReadStream, existsSync, statSync } from 'node:fs'
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from 'node:http'
import path from 'node:path'

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
}

function sendText(res: ServerResponse, statusCode: number, text: string): void {
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

  const target = new URL(req.url, apiBaseUrl)
  try {
    const response = await fetch(target, {
      method: req.method,
      headers: req.headers as HeadersInit,
      body: req.method === 'GET' || req.method === 'HEAD' ? undefined : req,
      duplex: 'half',
    } as RequestInit)

    res.writeHead(response.status, Object.fromEntries(response.headers))
    if (response.body) {
      const reader = response.body.getReader()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        res.write(Buffer.from(value))
      }
    }
    res.end()
  } catch (error) {
    sendText(
      res,
      502,
      error instanceof Error ? error.message : 'RDST API proxy failed'
    )
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
  createReadStream(filePath).pipe(res)
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
