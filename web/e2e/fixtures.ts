import { randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  type APIRequestContext,
  test as base,
  expect,
  type Locator,
  type Page,
} from '@playwright/test'

type BrowserErrors = {
  browserErrors: string[]
}

type AiAccess = {
  aiAccess: boolean
}

const acceptedBrowserErrors = new WeakMap<string[], Set<string>>()

export const test = base.extend<BrowserErrors & AiAccess>({
  aiAccess: [
    async ({ page }, use, testInfo) => {
      // Product browser suites exercise their own workflows, not account
      // enrollment. Make the provider prerequisite explicit for those runs.
      // The dedicated live suites must see the real first-run gate so they can
      // prove Supabase sign-in and hosted inference end to end.
      if (!testInfo.project.name.startsWith('chromium-web-e2e')) {
        await mockAiKeyReady(page)
      }
      await use(true)
    },
    { auto: true },
  ],
  browserErrors: [
    async ({ page }, use) => {
      const errors: string[] = []
      const accepted = new Set<string>()
      acceptedBrowserErrors.set(errors, accepted)
      page.on('console', (message) => {
        if (message.type() === 'error' && !accepted.has(message.text())) {
          errors.push(message.text())
        }
      })
      page.on('pageerror', (error) => {
        if (!accepted.has(error.message)) errors.push(error.message)
      })

      await use(errors)

      expect(
        errors,
        `Unexpected browser errors:\n${errors.join('\n')}`
      ).toEqual([])
      acceptedBrowserErrors.delete(errors)
    },
    { auto: true },
  ],
})

export { expect }

type FixtureResponse = (
  | {
      events: Record<string, unknown>[]
      delay_ms?: number
      // Semantic layer definition persisted for real by the fake init.
      layer?: Record<string, unknown>
    }
  | { value: unknown; delay_ms?: number; match_sql?: string }
  | { error: { status: number; detail: string } }
) & { repeat?: boolean }

export type BackendFixtures = Record<string, FixtureResponse[]>

const commonBackendFixtures: BackendFixtures = {
  autocomplete_schema: [{ value: {}, repeat: true }],
}

export function setBackendFixtures(operations: BackendFixtures = {}) {
  const fixtureHome = process.env.RDST_E2E_HOME
  if (!fixtureHome) throw new Error('RDST_E2E_HOME is required')
  writeFileSync(
    join(fixtureHome, 'backend-fixtures.json'),
    JSON.stringify({
      revision: randomUUID(),
      operations: { ...commonBackendFixtures, ...operations },
    })
  )
}

/**
 * The page's own content, without the chrome around it. A run's error message
 * is reported both on the page and by the Jobs sidebar, so assertions about
 * what a page says need to name the page.
 */
export function mainContent(page: Page) {
  return page.locator('#main-content')
}

export function consumeBrowserError(browserErrors: string[], expected: string) {
  expect(browserErrors).toContain(expected)
  expect(browserErrors.filter((error) => error !== expected)).toEqual([])
  browserErrors.splice(0, browserErrors.length)
  acceptedBrowserErrors.get(browserErrors)?.add(expected)
}

export function acceptBrowserError(browserErrors: string[], expected: string) {
  acceptedBrowserErrors.get(browserErrors)?.add(expected)
}

export async function configureTestTarget(
  page: Page,
  { hasPassword = false }: { hasPassword?: boolean } = {}
) {
  await clearTargets(page.request)
  const added = await page.request.post('/api/configure/targets', {
    data: {
      name: 'e2e-guard',
      target: {
        engine: 'postgresql',
        host: 'database.external.test',
        port: 5432,
        database: 'application',
        user: 'rdst_e2e',
        password_env: hasPassword ? 'TEST_DB_PASSWORD' : 'MISSING_E2E_PASSWORD',
      },
    },
  })
  expect(added.ok()).toBe(true)
  const madeDefault = await page.request.put('/api/configure/default', {
    data: { name: 'e2e-guard' },
  })
  expect(madeDefault.ok()).toBe(true)
  const initialized = await page.request.post('/api/init/complete')
  expect(initialized.ok()).toBe(true)
}

/**
 * Record the product's persistent EXPLAIN ANALYZE consent for workflows whose
 * subject is downstream of the consent gate. The dedicated analyze consent
 * spec intentionally does not call this helper.
 */
export async function acceptExplainAnalyzeConsent(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('rdst.explain-analyze-consent', 'accepted')
  })
}

export async function clearTargets(request: APIRequestContext) {
  const response = await request.get('/api/configure/targets')
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as { targets: { name: string }[] }

  for (const target of body.targets) {
    const deleted = await request.delete(
      `/api/configure/targets/${encodeURIComponent(target.name)}`
    )
    expect(deleted.ok()).toBe(true)
    await expect(deleted.json()).resolves.toMatchObject({ success: true })
  }
}

export async function clearQueryRegistry(request: APIRequestContext) {
  const response = await request.get('/api/query-registry?limit=150')
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as { queries: { hash: string }[] }

  for (const query of body.queries) {
    const deleted = await request.delete(
      `/api/query-registry/${encodeURIComponent(query.hash)}`
    )
    expect(deleted.ok()).toBe(true)
  }
}

/** Serialize events into an SSE response body. */
export function sseBody(events: Record<string, unknown>[]): string {
  return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
}

/** Report a satisfied, valid Anthropic key so the AI gate never blocks a run. */
export async function mockAiKeyReady(page: Page) {
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: false,
        requirements: [
          {
            kind: 'anthropic_api_key',
            satisfied: true,
            source: 'process_env',
            target: null,
            accepted_names: ['ANTHROPIC_API_KEY'],
          },
        ],
      },
    })
  )
  await page.route('**/api/env/anthropic/validate', (route) =>
    route.fulfill({
      json: { valid: true, reason: 'ok', model: 'claude-haiku' },
    })
  )
}

/**
 * Pin the connectivity probe to a settled `ok` result. Left unstubbed, the
 * page probes the target for real and the reachability verdict lands at an
 * arbitrary moment, relocating rows between the available and unavailable
 * sections while a test is mid-click.
 */
export async function mockConnectivityOk(page: Page, target = 'e2e-guard') {
  await page.route('**/api/fleet/status*', (route) =>
    route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: sseBody([
        { type: 'connectivity', target_name: target, status: 'ok' },
      ]),
    })
  )
}

export async function fillCodeMirror(editor: Locator, sql: string) {
  const content = editor.locator('.cm-content[contenteditable="true"]')
  await content.fill(sql)
}

/** A monitoring prerequisites answer for `target` (PostgreSQL, self-hosted). */
export function prerequisitesResult(
  target: string,
  { ready = true }: { ready?: boolean } = {}
) {
  const check = (
    id: string,
    label: string,
    ok: boolean,
    fix: Record<string, unknown> | null = null
  ) => ({
    id,
    label,
    status: ok ? 'ok' : 'missing',
    detail: ok ? '' : `${label} is missing.`,
    blocking: true,
    recommended: false,
    fix: ok ? null : fix,
    remediation: null,
  })
  return {
    target,
    engine: 'postgresql',
    database: 'app',
    checked_at: Math.floor(Date.now() / 1000),
    provider: 'self_hosted',
    provider_name: 'Self-hosted PostgreSQL',
    ready,
    blocking: ready ? 0 : 1,
    attention: 0,
    warnings: 0,
    connection_error: null,
    checks: [
      check(
        'pg_stat_statements_preload',
        'pg_stat_statements is loaded at server start',
        true
      ),
      check(
        'pg_stat_statements',
        'pg_stat_statements is created in app',
        ready,
        {
          summary: 'Create pg_stat_statements in app.',
          sql: ['CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'],
          steps: ['Connect to app as a superuser and run the SQL below.'],
          commands: [],
          after: [],
          restart: false,
          note: null,
          docs_url:
            'https://www.postgresql.org/docs/current/pgstatstatements.html',
        }
      ),
      check(
        'read_all_stats',
        "Permission to read every user's statements and activity",
        true
      ),
      check('visibility_probe', 'RDST sees new statements as they run', ready),
    ],
  }
}

/** Serve prerequisites for `target`; `answer(next)` changes what later checks see. */
export async function mockPrerequisites(
  page: Page,
  target: string,
  first: ReturnType<typeof prerequisitesResult> = prerequisitesResult(target)
) {
  let current = first
  let calls = 0
  await page.route(
    `**/api/configure/targets/${target}/prerequisites`,
    (route) => {
      calls += 1
      return route.fulfill({ json: current })
    }
  )
  return {
    calls: () => calls,
    answer: (next: ReturnType<typeof prerequisitesResult>) => {
      current = next
    },
  }
}

/**
 * Finish onboarding once a database connects: the next step is the "Turn on
 * monitoring" dialog on the new database's page, which checks the monitoring
 * prerequisites itself (the e2e Postgres preloads and creates
 * pg_stat_statements, and connects as a superuser).
 *
 * `monitor` leaves the dialog open to turn monitoring on; otherwise "Not now"
 * closes it and the test goes Home.
 */
export async function continueWhenMonitoringReady(
  page: Page,
  target: string,
  { monitor = false }: { monitor?: boolean } = {}
) {
  await expect(page).toHaveURL(/\/database\?panel=enable/, {
    timeout: 60_000,
  })
  const dialog = page.getByRole('dialog', {
    name: `Turn on monitoring for ${target}`,
  })
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  await expect(dialog.getByTestId('prereq-checker')).toHaveAttribute(
    'data-phase',
    'ready',
    { timeout: 60_000 }
  )
  if (monitor) return
  await dialog.getByRole('button', { name: 'Not now' }).click()
  await page.goto('/')
}
