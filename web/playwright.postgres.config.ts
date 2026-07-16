import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

const appDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(appDir, '..', '..', '..')
const rdstDir = resolve(repoRoot, 'rdst')
const distDir = resolve(appDir, 'dist')
const baseURL = process.env.RDST_E2E_BASE_URL ?? 'http://127.0.0.1:8788'
const isCI = Boolean(process.env.CI)

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
  webServer: {
    command: `pnpm run build && uv run --directory "${rdstDir}" uvicorn tests.web_e2e.full_stack_server:app --host 0.0.0.0 --port 8788`,
    cwd: appDir,
    env: {
      ...process.env,
      RDST_WEB_DIST_DIR: distDir,
      RDST_TESTING: '1',
      RDST_TELEMETRY: 'off',
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
      name: 'chromium-postgres',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
