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

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  testIgnore: '**/full-stack/**',
  outputDir: './test-results/artifacts',
  // The production test server intentionally uses one isolated RDST home.
  // Keep stateful target and registry flows serial in local runs as well as CI.
  fullyParallel: false,
  forbidOnly: isCI,
  failOnFlakyTests: isCI,
  retries: isCI ? 1 : 0,
  workers: 1,
  expect: { timeout: isCI ? 15_000 : 5_000 },
  // Keep only evidence from failed attempts. Buildkite uploads this directory
  // together with the HTML report and the complete runner/server log.
  preserveOutput: 'failures-only',
  reporter: [
    ['list'],
    [
      'junit',
      { outputFile: './test-results/rdst-web-browser-integration.xml' },
    ],
    ['html', { outputFolder: './playwright-report', open: 'never' }],
  ],
  use: {
    baseURL,
    // Animations never settle on GPU-less CI runners, so actionability
    // checks (e.g. checkbox stability) time out without this.
    contextOptions: { reducedMotion: 'reduce' },
    // Retain the original failure instead of only tracing its retry. This
    // makes local failures and CI flakes diagnosable from the same artifact.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    // `python -m uvicorn` resolves inside the project interpreter, so a venv
    // whose console-script shebangs have gone stale (e.g. after the repo
    // moved on disk) still serves.
    command: `pnpm run build && uv run --directory "${rdstDir}" python -m uvicorn tests.web_e2e.server:app --host 127.0.0.1 --port ${serverPort}`,
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
