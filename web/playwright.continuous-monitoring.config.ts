import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'
import { webBuild } from './e2e/web-build'

/**
 * Continuous Monitoring against a live database, one engine per run
 * (RDST_E2E_ENGINE=postgresql or mysql, the same scenarios on both).
 *
 * The server seeds the ci-shop database, drives its load and connects RDST
 * through a proxy the suite can cut. Every AI call (Jev classification, the
 * attention judge, Deep Analyze and the report narrative) goes through the
 * keyservice Worker after a real mailbox sign-in, as in the live web E2E.
 */

const appDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(appDir, '..', '..', '..')
const rdstDir = resolve(repoRoot, 'rdst')
const { distDir, command: buildCommand } = webBuild(appDir)
const port = process.env.RDST_E2E_PORT ?? '8791'
const baseURL = process.env.RDST_E2E_BASE_URL ?? `http://127.0.0.1:${port}`
const isCI = Boolean(process.env.CI)
const engine = process.env.RDST_E2E_ENGINE ?? 'postgresql'
if (engine !== 'postgresql' && engine !== 'mysql') {
  throw new Error(`RDST_E2E_ENGINE must be postgresql or mysql, got ${engine}`)
}
const mysql = engine === 'mysql'

const keyserviceUrl = process.env.RDST_KEYSERVICE_URL
if (!keyserviceUrl) {
  throw new Error(
    "RDST_KEYSERVICE_URL must point at this build's preview Worker. Without " +
      'it the suite would sign up against production.'
  )
}

export default defineConfig({
  testDir: './e2e/web-e2e/continuous-monitoring',
  outputDir: `./test-results/continuous-monitoring-${engine}-artifacts`,
  fullyParallel: false,
  forbidOnly: isCI,
  // The scenarios share one live database and one monitor, in order; a
  // retry would replay them against state the first attempt left behind.
  retries: 0,
  workers: 1,
  expect: { timeout: 15_000 },
  timeout: 5 * 60_000,
  preserveOutput: 'failures-only',
  reporter: [
    ['list'],
    [
      'junit',
      {
        outputFile: `./test-results/rdst-continuous-monitoring-${engine}.xml`,
      },
    ],
    [
      'html',
      {
        outputFolder: `./playwright-report-continuous-monitoring-${engine}`,
        open: 'never',
      },
    ],
  ],
  use: {
    baseURL,
    contextOptions: { reducedMotion: 'reduce' },
    viewport: { width: 1440, height: 900 },
    actionTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: `${buildCommand} && uv run --directory "${rdstDir}" python -m uvicorn tests.web_e2e.full_stack_server:app --host 0.0.0.0 --port ${port}`,
    cwd: appDir,
    env: {
      ...process.env,
      RDST_WEB_DIST_DIR: distDir,
      RDST_TESTING: '1',
      RDST_TELEMETRY: 'off',
      RDST_KEYSERVICE_URL: keyserviceUrl,
      RDST_CIM_ENGINE: engine,
      RDST_CIM_DB_HOST: process.env.RDST_CIM_DB_HOST ?? '127.0.0.1',
      RDST_CIM_DB_PORT:
        process.env.RDST_CIM_DB_PORT ?? (mysql ? '13306' : '15432'),
      RDST_CIM_ADMIN_USER:
        process.env.RDST_CIM_ADMIN_USER ?? (mysql ? 'root' : 'rdst_test'),
      RDST_CIM_ADMIN_PASSWORD:
        process.env.RDST_CIM_ADMIN_PASSWORD ?? 'rdst_e2e_password',
      RDST_CIM_DB_PASSWORD:
        process.env.RDST_CIM_DB_PASSWORD ?? 'rdst_e2e_password',
      // Collection every 3 s, the analyze queue every 2 s, failure backoff in
      // seconds.
      RDST_E2E_FAST_MONITOR: '1',
      RDST_E2E_CONTROLLED_JEV: '0',
      RDST_MONITOR_JUDGE_INTERVAL_S: '2',
      RDST_MONITOR_JUDGE_DRY_RUN: '0',
      // No attention calls or analyses before the history exists; the history
      // hook opens both.
      RDST_MONITOR_JEV_DAILY_CAP: '0',
      RDST_MONITOR_ANALYZE_DAILY_CAP: '0',
      RDST_MONITOR_LEARNING_S: '0',
      RDST_MONITOR_LOCK_WAIT_OPEN_MINUTES: '2',
      RDST_MONITOR_LOCK_WAIT_RECOVERY_MINUTES: '1',
      // Every AI call must go through the account and the keyservice.
      ANTHROPIC_API_KEY: '',
      PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
    },
    url: `${baseURL}/health`,
    // A developer can keep one server up between runs to inspect its state.
    reuseExistingServer: process.env.RDST_E2E_REUSE_SERVER === '1',
    stdout: 'pipe',
    stderr: 'pipe',
    timeout: 180_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
  },
  projects: [
    {
      name: 'chromium-web-e2e-cim-setup',
      testMatch: 'setup.ts',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'chromium-web-e2e-cim',
      testMatch: '*.spec.ts',
      dependencies: ['chromium-web-e2e-cim-setup'],
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
})
