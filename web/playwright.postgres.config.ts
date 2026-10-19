import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'
import { webBuild } from './e2e/web-build'

const appDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(appDir, '..', '..', '..')
const rdstDir = resolve(repoRoot, 'rdst')
const { distDir, command: buildCommand } = webBuild(appDir)
const baseURL = process.env.RDST_E2E_BASE_URL ?? 'http://127.0.0.1:8788'
const isCI = Boolean(process.env.CI)
// Hosted AI without sign-in starts an anonymous Key Service session on first use. This tier
// answers it from a local stub so no run mints sessions or claims slots in production.
const keyservicePort = process.env.RDST_E2E_KEYSERVICE_PORT ?? '8786'
const keyserviceURL = `http://127.0.0.1:${keyservicePort}`

export default defineConfig({
  testDir: './e2e/full-stack',
  testMatch: '**/*.spec.ts',
  outputDir: './test-results/postgres-artifacts',
  fullyParallel: false,
  forbidOnly: isCI,
  failOnFlakyTests: isCI,
  retries: isCI ? 1 : 0,
  workers: 1,
  preserveOutput: 'failures-only',
  reporter: [
    ['list'],
    ['junit', { outputFile: './test-results/rdst-web-postgres-e2e.xml' }],
    ['html', { outputFolder: './playwright-report-postgres', open: 'never' }],
  ],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node ./e2e/keyservice-stub.mjs',
      cwd: appDir,
      env: { ...process.env, RDST_E2E_KEYSERVICE_PORT: keyservicePort },
      url: `${keyserviceURL}/health`,
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      timeout: 30_000,
    },
    {
      command: `${buildCommand} && uv run --directory "${rdstDir}" uvicorn tests.web_e2e.full_stack_server:app --host 0.0.0.0 --port 8788`,
      cwd: appDir,
      env: {
        ...process.env,
        RDST_WEB_DIST_DIR: distDir,
        RDST_TESTING: '1',
        RDST_TELEMETRY: 'off',
        RDST_E2E_CONTROLLED_JEV: '1',
        RDST_E2E_DB_PASSWORD:
          process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password',
        PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
        RDST_KEYSERVICE_URL: keyserviceURL,
        // Hosted AI runs on the anonymous session, never a developer's own key.
        ANTHROPIC_API_KEY: '',
      },
      url: `${baseURL}/health`,
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      timeout: 120_000,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 3_000 },
    },
  ],
  projects: [
    {
      name: 'chromium-postgres',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
