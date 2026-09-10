import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

const appDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(appDir, '..', '..', '..')
const rdstDir = resolve(repoRoot, 'rdst')
const distDir = resolve(appDir, 'dist')
const baseURL = process.env.RDST_E2E_BASE_URL ?? 'http://127.0.0.1:8787'
const serverURL = new URL(baseURL)
const serverPort =
  serverURL.port || (serverURL.protocol === 'https:' ? '443' : '80')
const isCI = Boolean(process.env.CI)

// The UX regression suite. Same server and browser as the browser-integration
// run; a separate config so the flows recorded during the 2026-09 audit can be
// run on their own, and so a screenshot switch can be scoped to them.
export default defineConfig({
  testDir: './e2e/qa',
  testMatch: '**/*.spec.ts',
  outputDir: './test-results/qa-artifacts',
  // These flows share one RDST home, one query registry and one target.
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: isCI ? 15_000 : 7_000 },
  preserveOutput: 'failures-only',
  reporter: [
    ['list'],
    ['junit', { outputFile: './test-results/rdst-web-qa.xml' }],
    ['html', { outputFolder: './playwright-report-qa', open: 'never' }],
  ],
  use: {
    baseURL,
    // Animations never settle on GPU-less CI runners, so actionability
    // checks (e.g. checkbox stability) time out without this.
    contextOptions: { reducedMotion: 'reduce' },
    trace: 'retain-on-failure',
    // The flows were recorded with screenshots. QA_SHOTS=1 writes the evidence
    // set to test-results/qa-shots; the assertions never depend on it.
    screenshot: 'only-on-failure',
    video: 'off',
  },
  webServer: {
    // `python -m uvicorn` resolves inside the project interpreter, so a venv
    // whose console-script shebangs have gone stale (e.g. after the repo
    // moved on disk) still serves.
    command: `pnpm run build && uv run --directory "${rdstDir}" python -m uvicorn tests.web_e2e.server:app --host ${serverURL.hostname} --port ${serverPort}`,
    cwd: appDir,
    env: {
      ...process.env,
      RDST_WEB_DIST_DIR: distDir,
      RDST_TESTING: '1',
      RDST_TELEMETRY: 'off',
      TEST_DB_PASSWORD: 'testpassword',
      PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
    },
    url: `${baseURL}/health`,
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
    timeout: 120_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 3_000 },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
