import { spawn } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const appDir = resolve(__dirname, '..')
const repoRoot = resolve(appDir, '..', '..', '..')
const rdstDir = resolve(repoRoot, 'rdst')

const backendArgs = [
  'run',
  '--directory',
  rdstDir,
  'rdst',
  'web',
  '--ui',
  'none',
  '--reload',
]

const backend = spawn('uv', backendArgs, {
  cwd: appDir,
  stdio: 'inherit',
})

const frontend = spawn('pnpm', ['run', 'dev:vite'], {
  cwd: appDir,
  stdio: 'inherit',
})

let shuttingDown = false

function terminateChild(child) {
  if (!child || child.exitCode !== null) {
    return
  }

  child.kill('SIGTERM')
  setTimeout(() => {
    if (child.exitCode === null) {
      child.kill('SIGKILL')
    }
  }, 3000)
}

function shutdown(code = 0) {
  if (shuttingDown) {
    return
  }
  shuttingDown = true

  terminateChild(backend)
  terminateChild(frontend)

  setTimeout(() => process.exit(code), 0)
}

backend.on('error', (err) => {
  console.error(`Failed to start backend process: ${err.message}`)
  shutdown(1)
})

frontend.on('error', (err) => {
  console.error(`Failed to start frontend process: ${err.message}`)
  shutdown(1)
})

backend.on('exit', (code, signal) => {
  if (shuttingDown) {
    return
  }

  if (code === 0 || signal === 'SIGTERM' || signal === 'SIGINT') {
    shutdown(0)
    return
  }

  console.error(`Backend exited unexpectedly (code=${code}, signal=${signal})`)
  shutdown(code ?? 1)
})

frontend.on('exit', (code, signal) => {
  if (shuttingDown) {
    return
  }

  if (code === 0 || signal === 'SIGTERM' || signal === 'SIGINT') {
    shutdown(0)
    return
  }

  console.error(`Frontend exited unexpectedly (code=${code}, signal=${signal})`)
  shutdown(code ?? 1)
})

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))
