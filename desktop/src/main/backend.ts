import type { ChildProcess } from 'node:child_process'
import { spawn } from 'node:child_process'
import { accessSync, constants, existsSync, statSync } from 'node:fs'
import path from 'node:path'

const BACKEND_NAME = 'rdst'
const DEFAULT_HOST = '127.0.0.1'
const DEFAULT_PORT = 8787
const READY_PATH = '/api/init/status'
const STARTUP_TIMEOUT_MS = 30_000
const READINESS_INTERVAL_MS = 250
const MAX_OUTPUT_LINES = 10

export interface BackendHandle {
  process: ChildProcess
  apiBaseUrl: string
  binaryPath: string
}

export interface StartBackendOptions {
  resourcesPath: string
  host?: string
  port?: number
}

function backendBinaryName(platform: NodeJS.Platform): string {
  return platform === 'win32' ? `${BACKEND_NAME}.exe` : BACKEND_NAME
}

function candidateBinaryPaths(
  baseDir: string,
  platform: NodeJS.Platform
): string[] {
  const name = backendBinaryName(platform)
  return [path.join(baseDir, BACKEND_NAME, name), path.join(baseDir, name)]
}

function candidatePlatformDirs(
  platform: NodeJS.Platform,
  arch: NodeJS.Architecture
): string[] {
  if (platform === 'darwin') {
    return [
      '',
      `mac-${arch}`,
      `${platform}-${arch}`,
      'darwin-universal',
      'darwin',
    ]
  }
  if (platform === 'win32') {
    return ['', `win-${arch}`, `${platform}-${arch}`, 'win32']
  }
  return ['', `linux-${arch}`, `${platform}-${arch}`, 'linux']
}

export function getBackendBinaryCandidates(
  resourcesPath: string,
  platform: NodeJS.Platform = process.platform,
  arch: NodeJS.Architecture = process.arch
): string[] {
  const sidecarRoots = [
    path.join(resourcesPath, 'sidecar'),
    path.join(path.dirname(resourcesPath), 'sidecar'),
  ]
  return sidecarRoots.flatMap((sidecarRoot) => {
    return candidatePlatformDirs(platform, arch).flatMap((dir) => {
      if (!dir) {
        return candidateBinaryPaths(sidecarRoot, platform)
      }
      return candidateBinaryPaths(path.join(sidecarRoot, dir), platform)
    })
  })
}

export function resolveBackendBinaryPath(resourcesPath: string): string {
  const candidates = getBackendBinaryCandidates(resourcesPath)
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return candidate
    }
  }

  throw new Error(
    `Bundled RDST backend binary not found. Searched:\n${candidates
      .map((candidate) => `  - ${candidate}`)
      .join('\n')}`
  )
}

function validateExecutable(binaryPath: string): void {
  if (process.platform === 'win32') return
  accessSync(binaryPath, constants.X_OK)
}

function appendOutput(lines: string[], chunk: Buffer): void {
  const text = chunk.toString().replace(/\r/g, '')
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    lines.push(line)
    if (lines.length > MAX_OUTPUT_LINES) {
      lines.shift()
    }
  }
}

function formatOutput(lines: string[]): string {
  return lines.length > 0 ? lines.join('\n') : '<no output>'
}

async function wait(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitForBackend(
  apiBaseUrl: string,
  getExitStatus: () => {
    code: number | null
    signal: NodeJS.Signals | null
  } | null,
  getStdout: () => string[],
  getStderr: () => string[]
): Promise<void> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS
  let lastError = 'not checked yet'

  while (Date.now() < deadline) {
    const exitStatus = getExitStatus()
    if (exitStatus) {
      throw new Error(
        `RDST backend exited before it became ready (code=${exitStatus.code}, signal=${exitStatus.signal}).\nSTDOUT:\n${formatOutput(
          getStdout()
        )}\nSTDERR:\n${formatOutput(getStderr())}`
      )
    }

    try {
      const response = await fetch(`${apiBaseUrl}${READY_PATH}`)
      if (response.ok) {
        return
      }
      lastError = `HTTP ${response.status}`
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error)
    }

    await wait(READINESS_INTERVAL_MS)
  }

  throw new Error(
    `Timed out waiting for RDST backend at ${apiBaseUrl}${READY_PATH}. Last error: ${lastError}`
  )
}

export async function startBackend(
  options: StartBackendOptions
): Promise<BackendHandle> {
  const host = options.host ?? DEFAULT_HOST
  const port = options.port ?? DEFAULT_PORT
  const apiBaseUrl = `http://${host}:${port}`
  const binaryPath = resolveBackendBinaryPath(options.resourcesPath)
  validateExecutable(binaryPath)

  const stdoutLines: string[] = []
  const stderrLines: string[] = []
  let exitStatus: {
    code: number | null
    signal: NodeJS.Signals | null
  } | null = null

  const child = spawn(
    binaryPath,
    ['web', '--ui', 'none', '--host', host, '--port', String(port)],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  )

  child.stdout.on('data', (chunk: Buffer) => {
    appendOutput(stdoutLines, chunk)
    process.stdout.write(`[rdst] ${chunk.toString()}`)
  })
  child.stderr.on('data', (chunk: Buffer) => {
    appendOutput(stderrLines, chunk)
    process.stderr.write(`[rdst] ${chunk.toString()}`)
  })
  child.once('exit', (code, signal) => {
    exitStatus = { code, signal }
  })

  await waitForBackend(
    apiBaseUrl,
    () => exitStatus,
    () => stdoutLines,
    () => stderrLines
  )

  return {
    process: child,
    apiBaseUrl,
    binaryPath,
  }
}

export function stopBackend(handle: BackendHandle | null): void {
  if (!handle || handle.process.exitCode !== null) return
  handle.process.kill('SIGTERM')
  const timeout = setTimeout(() => {
    if (handle.process.exitCode === null) {
      handle.process.kill('SIGKILL')
    }
  }, 3000)
  timeout.unref()
}
