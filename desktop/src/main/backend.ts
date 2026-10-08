import type { ChildProcess } from 'node:child_process'
import { spawn } from 'node:child_process'
import { accessSync, constants, existsSync, statSync } from 'node:fs'
import { createServer } from 'node:net'
import os from 'node:os'
import path from 'node:path'

import { type BackendLog, LineSplitter } from './backend-log.js'

const BACKEND_NAME = 'rdst'
const DEFAULT_HOST = '127.0.0.1'
const READY_PATH = '/api/init/status'
// How long the backend may go without answering or reporting progress before it counts as
// hung. A cold start of the bundled backend with an integrity check of a large cache.db can
// take most of a minute on a Windows machine with Defender scanning the data folder.
const STARTUP_TIMEOUT_MS = 120_000
// On quit, how long to let an interrupted data migration finish before stopping it anyway.
const MIGRATION_STOP_GRACE_MS = 10 * 60_000
const STARTUP_MARKER = 'RDST_STARTUP '
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
    .split(path.posix.delimiter)
    .filter((entry) => entry.length > 0)

  for (const entry of POSIX_SYSTEM_PATHS) addUniquePathEntry(entries, entry)
  addUniquePathEntry(entries, path.posix.join(homeDir, '.docker', 'bin'))

  if (platform === 'darwin') {
    addUniquePathEntry(entries, '/opt/homebrew/bin')
    addUniquePathEntry(entries, '/opt/local/bin')
    addUniquePathEntry(
      entries,
      '/Applications/Docker.app/Contents/Resources/bin'
    )
    addUniquePathEntry(
      entries,
      path.posix.join(
        homeDir,
        'Applications',
        'Docker.app',
        'Contents',
        'Resources',
        'bin'
      )
    )
  }

  return entries.join(path.posix.delimiter)
}

export interface BackendHandle {
  process: ChildProcess
  apiBaseUrl: string
  binaryPath: string
  /** True while the backend reports a data migration in progress. */
  migrating: () => boolean
}

export type StartupPhase = 'starting' | 'migrating'

export interface StartupMarker {
  state: string
  store?: string
  pending?: number
}

export interface StartBackendOptions {
  resourcesPath: string
  host?: string
  port?: number
  sourceDir?: string
  /** Runs this instead of the bundled backend; tests use it for a stand-in process. */
  command?: { file: string; args: string[] }
  log?: BackendLog
  /** The keyservice a change or staging build was packaged for; RDST_KEYSERVICE_URL wins. */
  keyservice?: string
  onPhase?: (phase: StartupPhase, store?: string) => void
  /** Called once the process exists, so a quit during startup can stop it. */
  onSpawn?: (handle: BackendHandle) => void
  startupTimeoutMs?: number
}

/** Reads a `RDST_STARTUP {...}` line from the backend's stdout. */
export function parseStartupMarker(line: string): StartupMarker | null {
  if (!line.startsWith(STARTUP_MARKER)) return null
  try {
    const value = JSON.parse(line.slice(STARTUP_MARKER.length)) as unknown
    if (
      value &&
      typeof value === 'object' &&
      typeof (value as StartupMarker).state === 'string'
    ) {
      return value as StartupMarker
    }
  } catch {
    // Not a marker after all.
  }
  return null
}

/**
 * What the startup markers say so far. A start that is migrating never times out; otherwise
 * the timeout counts from the last sign of progress (spawn or a marker).
 */
export class StartupWatch {
  private active = false
  private lastProgress: number

  constructor(
    private readonly timeoutMs: number = STARTUP_TIMEOUT_MS,
    private readonly now: () => number = Date.now
  ) {
    this.lastProgress = now()
  }

  get migrating(): boolean {
    return this.active
  }

  /** Apply one marker; returns the phase it moves to, if it changes. */
  apply(marker: StartupMarker): StartupPhase | null {
    this.lastProgress = this.now()
    if (marker.state === 'migrating' && !this.active) {
      this.active = true
      return 'migrating'
    }
    if (
      marker.state === 'migrated' &&
      this.active &&
      (marker.pending ?? 0) === 0
    ) {
      this.active = false
      return 'starting'
    }
    return null
  }

  timedOut(): boolean {
    return !this.active && this.now() - this.lastProgress > this.timeoutMs
  }
}

export function backendEnvironment(
  base: NodeJS.ProcessEnv = process.env,
  keyservice?: string
): NodeJS.ProcessEnv {
  return {
    ...base,
    PATH: backendExecutablePath(base.PATH),
    RDST_DESKTOP: '1',
    ...(keyservice && !base.RDST_KEYSERVICE_URL
      ? { RDST_KEYSERVICE_URL: keyservice }
      : {}),
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

function appendOutput(lines: string[], newLines: string[]): void {
  for (const line of newLines) {
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
  watch: StartupWatch,
  getExitStatus: () => {
    code: number | null
    signal: NodeJS.Signals | null
  } | null,
  getSpawnError: () => Error | null,
  getStdout: () => string[],
  getStderr: () => string[]
): Promise<void> {
  let lastError = 'not checked yet'

  while (!watch.timedOut()) {
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
  const binaryPath =
    options.command?.file ??
    (options.sourceDir ? 'uv' : resolveBackendBinaryPath(options.resourcesPath))
  if (!options.sourceDir && !options.command) validateExecutable(binaryPath)
  const log = options.log
  const watch = new StartupWatch(options.startupTimeoutMs)

  const stdoutLines: string[] = []
  const stderrLines: string[] = []
  let exitStatus: {
    code: number | null
    signal: NodeJS.Signals | null
  } | null = null
  let spawnError: Error | null = null

  const child = spawn(
    binaryPath,
    [
      ...(options.command?.args ?? []),
      ...(options.sourceDir && !options.command
        ? [
            'run',
            '--offline',
            '--no-sync',
            '--directory',
            options.sourceDir,
            'rdst',
          ]
        : []),
      'web',
      '--no-browser',
      '--ui',
      'none',
      '--host',
      host,
      '--port',
      String(port),
    ],
    {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      env: backendEnvironment(process.env, options.keyservice),
    }
  )

  log?.write('shell', [
    `starting backend: ${binaryPath} (pid ${child.pid ?? 'none'}, ${apiBaseUrl})`,
  ])
  const stdoutSplitter = new LineSplitter()
  const stderrSplitter = new LineSplitter()
  const onStdoutLines = (lines: string[]) => {
    appendOutput(stdoutLines, lines)
    log?.write('stdout', lines)
    for (const line of lines) {
      const marker = parseStartupMarker(line)
      if (!marker) continue
      const phase = watch.apply(marker)
      if (phase) options.onPhase?.(phase, marker.store)
    }
  }
  const onStderrLines = (lines: string[]) => {
    appendOutput(stderrLines, lines)
    log?.write('stderr', lines)
  }
  child.stdout.on('data', (chunk: Buffer) => {
    onStdoutLines(stdoutSplitter.push(chunk.toString()))
    process.stdout.write(`[rdst] ${chunk.toString()}`)
  })
  child.stderr.on('data', (chunk: Buffer) => {
    onStderrLines(stderrSplitter.push(chunk.toString()))
    process.stderr.write(`[rdst] ${chunk.toString()}`)
  })
  child.stdout.once('end', () => onStdoutLines(stdoutSplitter.flush()))
  child.stderr.once('end', () => onStderrLines(stderrSplitter.flush()))
  child.once('exit', (code, signal) => {
    exitStatus = { code, signal }
    log?.write('shell', [`backend exited (code=${code}, signal=${signal})`])
  })
  child.once('error', (error) => {
    spawnError = error
    log?.write('shell', [`backend could not start: ${error.message}`])
  })

  const handle: BackendHandle = {
    process: child,
    apiBaseUrl,
    binaryPath,
    migrating: () => watch.migrating,
  }
  options.onSpawn?.(handle)

  try {
    await waitForBackend(
      apiBaseUrl,
      watch,
      () => exitStatus,
      () => spawnError,
      () => stdoutLines,
      () => stderrLines
    )
  } catch (error) {
    log?.write('shell', [errorText(error)])
    // Only a start that stopped making progress gets here while the process runs, and
    // never mid-migration: StartupWatch doesn't time out while one is reported.
    if (!spawnError) {
      await terminateProcess(child)
    }
    throw error
  }

  log?.write('shell', ['backend is ready'])
  return handle
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
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

export async function terminateWindowsProcessTree(
  pid: number,
  spawnProcess: typeof spawn = spawn
): Promise<void> {
  const taskkill = spawnProcess(
    'taskkill.exe',
    ['/PID', String(pid), '/T', '/F'],
    { stdio: 'ignore', windowsHide: true }
  )
  await new Promise<void>((resolve, reject) => {
    taskkill.once('error', reject)
    taskkill.once('exit', (code) => {
      if (code === 0) {
        resolve()
      } else {
        reject(new Error(`taskkill exited with code ${code}`))
      }
    })
  })
}

async function terminateProcess(child: ChildProcess): Promise<void> {
  if (processHasExited(child)) return

  if (process.platform === 'win32' && child.pid !== undefined) {
    try {
      await terminateWindowsProcessTree(child.pid)
      if (await waitForExit(child, FORCE_KILL_TIMEOUT_MS)) return
    } catch (error) {
      console.warn(
        '[rdst-desktop] failed to terminate RDST backend process tree:',
        error
      )
    }
  }

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

/** Wait for a reported migration to finish (or the process to exit), up to `timeoutMs`. */
export async function waitForMigration(
  handle: BackendHandle,
  timeoutMs: number = MIGRATION_STOP_GRACE_MS,
  intervalMs = 250
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (
    handle.migrating() &&
    !processHasExited(handle.process) &&
    Date.now() < deadline
  ) {
    await wait(intervalMs)
  }
}

export async function stopBackend(
  handle: BackendHandle | null,
  migrationGraceMs: number = MIGRATION_STOP_GRACE_MS
): Promise<void> {
  if (!handle) return
  // A forced stop mid-migration leaves each store at its last committed step, which is safe
  // but means a second, partial upgrade on the next launch. Let a running one finish.
  if (handle.migrating()) await waitForMigration(handle, migrationGraceMs)
  await terminateProcess(handle.process)
}
