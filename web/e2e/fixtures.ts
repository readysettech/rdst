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

// The runner seeds a primary email so the EmailGate stays closed, but the
// gate still overlays the app while its settings check is in flight. Wait it
// out before clicking buttons whose names collide with the gate's.
export async function awaitEmailGateClosed(page: Page) {
  await expect(
    page.getByRole('dialog', { name: 'Tell us where to reach you' })
  ).toHaveCount(0)
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

export async function fillCodeMirror(editor: Locator, sql: string) {
  const content = editor.locator('.cm-content[contenteditable="true"]')
  await content.fill(sql)
}
