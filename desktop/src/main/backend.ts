import type { ChildProcess } from 'node:child_process'
import { spawn } from 'node:child_process'
import { accessSync, constants, existsSync, statSync } from 'node:fs'
import { createServer } from 'node:net'
import os from 'node:os'
import path from 'node:path'

const BACKEND_NAME = 'rdst'
const DEFAULT_HOST = '127.0.0.1'
const READY_PATH = '/api/init/status'
const STARTUP_TIMEOUT_MS = 30_000
const READINESS_INTERVAL_MS = 250
const READINESS_REQUEST_TIMEOUT_MS = 1_000
const SHUTDOWN_TIMEOUT_MS = 3_000
const FORCE_KILL_TIMEOUT_MS = 1_000
const MAX_OUTPUT_LINES = 10

const POSIX_SYSTEM_PATHS = ['/usr/local/bin', '/usr/bin', '/bin']

function addUniquePathEntry(entries: string[], entry: string): void {
  if (entry && !entries.includes(entry)) entries.push(entry)
}

/**
 * GUI applications do not inherit the interactive shell's PATH on macOS.
 * Keep the launch environment intact, but add the conventional locations for
 * Docker Desktop and package-manager-installed command line tools so the
 * Python sidecar can resolve `docker` when RDST is opened from Finder.
 */
export function backendExecutablePath(
  currentPath: string | undefined = process.env.PATH,
  platform: NodeJS.Platform = process.platform,
  homeDir: string = os.homedir()
): string | undefined {
  if (platform === 'win32') return currentPath

  const entries = (currentPath ?? '')
    .split(path.delimiter)
    .filter((entry) => entry.length > 0)

  for (const entry of POSIX_SYSTEM_PATHS) addUniquePathEntry(entries, entry)
  addUniquePathEntry(entries, path.join(homeDir, '.docker', 'bin'))

  if (platform === 'darwin') {
    addUniquePathEntry(entries, '/opt/homebrew/bin')
    addUniquePathEntry(entries, '/opt/local/bin')
    addUniquePathEntry(
      entries,
      '/Applications/Docker.app/Contents/Resources/bin'
    )
    addUniquePathEntry(
      entries,
      path.join(
        homeDir,
        'Applications',
        'Docker.app',
        'Contents',
        'Resources',
        'bin'
      )
    )
  }

  return entries.join(path.delimiter)
}

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

export function backendEnvironment(
  base: NodeJS.ProcessEnv = process.env
): NodeJS.ProcessEnv {
  return {
    ...base,
    PATH: backendExecutablePath(base.PATH),
    RDST_DESKTOP: '1',
  }
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

export async function findAvailablePort(host: string): Promise<number> {
  const server = createServer()
  server.unref()

  return await new Promise<number>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, host, () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error(`Unable to allocate an RDST backend port on ${host}`))
        return
      }

      const { port } = address
      server.close((error) => {
        if (error) {
          reject(error)
          return
        }
        resolve(port)
      })
    })
  })
}

async function waitForBackend(
  apiBaseUrl: string,
  getExitStatus: () => {
    code: number | null
    signal: NodeJS.Signals | null
  } | null,
  getSpawnError: () => Error | null,
  getStdout: () => string[],
  getStderr: () => string[]
): Promise<void> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS
  let lastError = 'not checked yet'

  while (Date.now() < deadline) {
    const spawnError = getSpawnError()
    if (spawnError) {
      throw new Error(`Unable to start RDST backend: ${spawnError.message}`)
    }

    const exitStatus = getExitStatus()
    if (exitStatus) {
      throw new Error(
        `RDST backend exited before it became ready (code=${exitStatus.code}, signal=${exitStatus.signal}).\nSTDOUT:\n${formatOutput(
          getStdout()
        )}\nSTDERR:\n${formatOutput(getStderr())}`
      )
    }

    try {
      const response = await fetch(`${apiBaseUrl}${READY_PATH}`, {
        signal: AbortSignal.timeout(READINESS_REQUEST_TIMEOUT_MS),
      })
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
  const port = options.port ?? (await findAvailablePort(host))
  const apiBaseUrl = `http://${host}:${port}`
  const binaryPath = resolveBackendBinaryPath(options.resourcesPath)
  validateExecutable(binaryPath)

  const stdoutLines: string[] = []
  const stderrLines: string[] = []
  let exitStatus: {
    code: number | null
    signal: NodeJS.Signals | null
  } | null = null
  let spawnError: Error | null = null

  const child = spawn(
    binaryPath,
    ['web', '--ui', 'none', '--host', host, '--port', String(port)],
    {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: backendEnvironment(),
    }
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
  child.once('error', (error) => {
    spawnError = error
  })

  try {
    await waitForBackend(
      apiBaseUrl,
      () => exitStatus,
      () => spawnError,
      () => stdoutLines,
      () => stderrLines
    )
  } catch (error) {
    if (!spawnError) {
      await terminateProcess(child)
    }
    throw error
  }

  return {
    process: child,
    apiBaseUrl,
    binaryPath,
  }
}

function processHasExited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null
}

async function waitForExit(
  child: ChildProcess,
  timeoutMs: number
): Promise<boolean> {
  if (processHasExited(child)) return true

  return await new Promise<boolean>((resolve) => {
    let settled = false
    const finish = (exited: boolean) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      child.off('exit', onExit)
      child.off('close', onExit)
      resolve(exited)
    }
    const onExit = () => finish(true)
    const timeout = setTimeout(() => finish(false), timeoutMs)
    child.once('exit', onExit)
    child.once('close', onExit)
  })
}

async function terminateProcess(child: ChildProcess): Promise<void> {
  if (processHasExited(child)) return

  try {
    child.kill('SIGTERM')
  } catch (error) {
    console.warn('[rdst-desktop] failed to terminate RDST backend:', error)
  }
  if (await waitForExit(child, SHUTDOWN_TIMEOUT_MS)) return

  try {
    child.kill('SIGKILL')
  } catch (error) {
    console.warn('[rdst-desktop] failed to kill RDST backend:', error)
  }
  if (!(await waitForExit(child, FORCE_KILL_TIMEOUT_MS))) {
    console.warn('[rdst-desktop] RDST backend did not exit after SIGKILL')
  }
}

export async function stopBackend(handle: BackendHandle | null): Promise<void> {
  if (!handle) return
  await terminateProcess(handle.process)
}
