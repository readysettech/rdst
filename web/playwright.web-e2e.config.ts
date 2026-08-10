import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

/**
 * Browser coverage of the trial lifecycle against a live keyservice Worker.
 *
 * Unlike the other two projects, nothing here is faked and nothing is local:
 * the app talks to the CL's own preview Worker, which talks to Anthropic, and
 * the verification email travels through Resend to a real inbox. This is the
 * only suite that proves attestation, token validation, usage accounting and
 * email delivery actually work together.
 */

const appDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(appDir, '..', '..', '..')
const rdstDir = resolve(repoRoot, 'rdst')
const distDir = resolve(appDir, 'dist')
const baseURL = process.env.RDST_E2E_BASE_URL ?? 'http://127.0.0.1:8789'
const isCI = Boolean(process.env.CI)

const keyserviceUrl = process.env.RDST_KEYSERVICE_URL
if (!keyserviceUrl) {
  throw new Error(
    'RDST_KEYSERVICE_URL must point at this build\'s preview Worker. Without ' +
      'it the suite would sign up against production.'
  )
}

export default defineConfig({
  testDir: './e2e/web-e2e',
  testMatch: '**/*.spec.ts',
  outputDir: './test-results/web-e2e-artifacts',
  fullyParallel: false,
  forbidOnly: isCI,
  failOnFlakyTests: isCI,
  // Waiting on real mail delivery, so one retry costs a couple of minutes.
  // Kept anyway: a retry distinguishes a slow relay from a broken flow.
  retries: isCI ? 1 : 0,
  workers: 1,
  // Mail delivery dominates; the default 5s expect timeout is unrelated to it
  // but UI settles slowly on GPU-less runners.
  expect: { timeout: isCI ? 15_000 : 5_000 },
  timeout: 5 * 60_000,
  preserveOutput: 'failures-only',
  reporter: [
    ['list'],
    ['junit', { outputFile: './test-results/rdst-web-e2e.xml' }],
    ['html', { outputFolder: './playwright-report-web-e2e', open: 'never' }],
  ],
  use: {
    baseURL,
    contextOptions: { reducedMotion: 'reduce' },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: `pnpm run build && uv run --directory "${rdstDir}" python -m uvicorn tests.web_e2e.full_stack_server:app --host 0.0.0.0 --port 8789`,
    cwd: appDir,
    env: {
      ...process.env,
      RDST_WEB_DIST_DIR: distDir,
      RDST_TESTING: '1',
      RDST_TELEMETRY: 'off',
      // Points every keyservice call in the app at this build's Worker.
      RDST_KEYSERVICE_URL: keyserviceUrl,
      RDST_E2E_DB_PASSWORD:
        process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password',
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
      name: 'chromium-web-e2e',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
