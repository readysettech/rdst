import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'

const isolatedHome = mkdtempSync(join(tmpdir(), 'rdst-web-e2e-'))
const browserPath =
  process.env.PLAYWRIGHT_BROWSERS_PATH ?? join(homedir(), '.cache', 'ms-playwright')
const pnpm = process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm'
const child = spawn(pnpm, ['exec', 'playwright', 'test', ...process.argv.slice(2)], {
  env: {
    ...process.env,
    HOME: isolatedHome,
    PLAYWRIGHT_BROWSERS_PATH: browserPath,
    RDST_E2E_HOME: isolatedHome,
    RDST_TESTING: '1',
    RDST_TELEMETRY: 'off',
    TEST_DB_PASSWORD: 'testpassword',
    PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
  },
  stdio: 'inherit',
})

let cleanedUp = false
function cleanup() {
  if (cleanedUp) return
  cleanedUp = true
  rmSync(isolatedHome, { force: true, recursive: true })
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal))
}

child.on('error', (error) => {
  cleanup()
  console.error(`Unable to start Playwright: ${error.message}`)
  process.exit(1)
})

child.on('exit', (code, signal) => {
  cleanup()
  if (signal) {
    process.kill(process.pid, signal)
    return
  }
  process.exit(code ?? 1)
})
