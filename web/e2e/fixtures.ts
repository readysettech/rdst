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

export const test = base.extend<BrowserErrors>({
  browserErrors: [
    async ({ page }, use) => {
      const errors: string[] = []
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(message.text())
      })
      page.on('pageerror', (error) => errors.push(error.message))

      await use(errors)

      expect(
        errors,
        `Unexpected browser errors:\n${errors.join('\n')}`
      ).toEqual([])
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
  | { value: unknown }
  | { error: { status: number; detail: string } }
) & { repeat?: boolean }

export type BackendFixtures = Record<string, FixtureResponse[]>

const commonBackendFixtures: BackendFixtures = {
  autocomplete_schema: [{ value: {}, repeat: true }],
  cache_list: [
    {
      events: [{ type: 'cache_list', success: true, count: 0, caches: [] }],
      repeat: true,
    },
  ],
  cache_status: [
    {
      events: [
        {
          type: 'cache_status',
          deployed: false,
          running: false,
          endpoint: null,
          cache_target: null,
          container_name: null,
        },
      ],
      repeat: true,
    },
  ],
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

export function consumeBrowserError(browserErrors: string[], expected: string) {
  expect(browserErrors).toEqual([expected])
  browserErrors.splice(0, 1)
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

export async function clearTargets(request: APIRequestContext) {
  const response = await request.get('/api/configure/targets')
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as { targets: { name: string }[] }

  for (const target of body.targets) {
    const deleted = await request.delete(
      `/api/configure/targets/${encodeURIComponent(target.name)}`
    )
    expect(deleted.ok()).toBe(true)
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
