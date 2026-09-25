import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { createServer, get, type RequestListener, type Server } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { afterEach, expect, it } from 'vitest'
import { type StaticServerHandle, startStaticServer } from './static-server.js'

let upstream: Server | undefined
let proxy: StaticServerHandle | undefined
let root: string | undefined

afterEach(async () => {
  await proxy?.close()
  upstream?.closeAllConnections()
  await new Promise<void>((resolve) =>
    upstream ? upstream.close(() => resolve()) : resolve()
  )
  if (root) rmSync(root, { recursive: true, force: true })
})

async function start(handler: RequestListener) {
  root = mkdtempSync(path.join(os.tmpdir(), 'rdst-proxy-test-'))
  writeFileSync(path.join(root, 'index.html'), 'RDST')
  upstream = createServer(handler)
  await new Promise<void>((resolve) =>
    upstream!.listen(0, '127.0.0.1', resolve)
  )
  const address = upstream.address()
  if (!address || typeof address === 'string')
    throw new Error('Missing upstream address')
  proxy = await startStaticServer({
    rootDir: root,
    apiBaseUrl: `http://127.0.0.1:${address.port}`,
  })
  return proxy.url
}

it('ends a broken upstream stream without sending a second set of headers', async () => {
  const url = await start((_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write('data: first\n\n')
    setTimeout(() => res.destroy(), 50)
  })
  const response = await fetch(`${url}/api/stream`)
  expect(response.status).toBe(200)
  await expect(response.text()).rejects.toThrow()
  expect(await (await fetch(url)).text()).toBe('RDST')
})

it('cancels the upstream stream when the downstream client disconnects', async () => {
  let closed!: () => void
  const upstreamClosed = new Promise<void>((resolve) => {
    closed = resolve
  })
  const url = await start((_req, res) => {
    res.once('close', closed)
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write('data: first\n\n')
  })
  await new Promise<void>((resolve, reject) => {
    const client = get(`${url}/api/stream`, (response) => {
      response.once('data', () => {
        client.destroy()
        resolve()
      })
    })
    client.once('error', reject)
  })
  await upstreamClosed
  expect(await (await fetch(url)).text()).toBe('RDST')
})

it('returns a gateway error when the upstream fails before headers', async () => {
  const url = await start((_req, res) => res.destroy())
  expect((await fetch(`${url}/api/status`)).status).toBe(502)
})
