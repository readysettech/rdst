import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, openSync, readFileSync, rmSync } from 'node:fs'
import { request as httpRequest } from 'node:http'
import { join } from 'node:path'

import type { Mode } from './app.ts'
import { paths } from './paths.ts'
import type { CommandInput } from './session.ts'

export interface DriverResponse {
  ok: boolean
  data?: unknown
  error?: string
  kind?: string
}

export interface StartOptions {
  runDir: string
  mode: Mode
  desktopExecutable?: string
  headed?: boolean
  homeFrom?: string
  noFirstRunPopups?: boolean
}

const START_TIMEOUT_MS = 240_000
const CALL_TIMEOUT_MS = 450_000

interface DriverFile {
  ready: boolean
  port?: number
  token?: string
  pid?: number
  error?: string
}

function readDriverFile(runDir: string): DriverFile | null {
  const file = join(runDir, 'driver.json')
  if (!existsSync(file)) return null
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as DriverFile
  } catch {
    return null
  }
}

/** Start a driver daemon for runDir and wait until its app is ready. */
export async function startDriver(options: StartOptions): Promise<DriverFile> {
  mkdirSync(options.runDir, { recursive: true })
  rmSync(join(options.runDir, 'driver.json'), { force: true })
  const runId = `${Date.now()}-${randomBytes(4).toString('hex')}`
  const args = [
    paths.daemon,
    '--run-dir',
    options.runDir,
    '--run-id',
    runId,
    '--mode',
    options.mode,
  ]
  if (options.desktopExecutable) {
    args.push('--desktop-exec', options.desktopExecutable)
  }
  if (options.headed) args.push('--headed')
  if (options.homeFrom) args.push('--home-from', options.homeFrom)
  if (options.noFirstRunPopups) args.push('--no-first-run-popups')
  const log = openSync(join(options.runDir, 'daemon.log'), 'a')
  const child = spawn(process.execPath, args, {
    detached: true,
    stdio: ['ignore', log, log],
    env: process.env,
  })
  child.unref()

  const deadline = Date.now() + START_TIMEOUT_MS
  while (Date.now() < deadline) {
    const driver = readDriverFile(options.runDir)
    if (driver?.ready) return driver
    if (driver && !driver.ready) {
      throw new Error(`driver failed to start: ${driver.error}`)
    }
    if (child.exitCode !== null) {
      throw new Error(
        `driver exited with code ${child.exitCode}; see ${join(options.runDir, 'daemon.log')}`
      )
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`driver did not become ready in ${START_TIMEOUT_MS}ms`)
}

export async function call(
  runDir: string,
  name: string,
  input: CommandInput = { pos: [], flags: {} }
): Promise<DriverResponse> {
  const driver = readDriverFile(runDir)
  if (!driver?.ready) {
    return {
      ok: false,
      error: `no running driver for ${runDir}`,
      kind: 'infra',
    }
  }
  // node:http rather than fetch: fetch gives up waiting for response headers
  // after 300s, shorter than the daemon's own command timeout.
  return await new Promise<DriverResponse>((resolve) => {
    const request = httpRequest(
      {
        host: '127.0.0.1',
        port: driver.port,
        method: 'POST',
        headers: { authorization: `Bearer ${driver.token}` },
        timeout: CALL_TIMEOUT_MS,
      },
      (response) => {
        let body = ''
        response.on('data', (chunk) => {
          body += chunk
        })
        response.on('end', () => {
          try {
            resolve(JSON.parse(body) as DriverResponse)
          } catch {
            resolve({
              ok: false,
              error: `bad driver reply: ${body}`,
              kind: 'infra',
            })
          }
        })
      }
    )
    request.on('timeout', () => request.destroy(new Error('no reply')))
    request.on('error', (error) =>
      resolve({
        ok: false,
        error: `driver unreachable: ${error.message}`,
        kind: 'infra',
      })
    )
    request.end(JSON.stringify({ name, input }))
  })
}
