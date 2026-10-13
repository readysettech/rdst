import type {
  APIRequestContext,
  APIResponse,
  Page,
  TestInfo,
} from '@playwright/test'
import { expect } from '../../fixtures'

/** The engine this run monitors; every scenario runs the same on both. */
export const engine = (process.env.RDST_E2E_ENGINE ?? 'postgresql') as
  | 'postgresql'
  | 'mysql'
export const target = `ci-shop-${engine === 'mysql' ? 'mysql' : 'pg'}`
export const base = `/api/monitor/targets/${encodeURIComponent(target)}`
const hooks = `/__test/ci-shop/${engine}`

export type Shape = {
  name: string
  role: string
  sql: string
  key: string
  observed: boolean
  computed_key: string
  calls: number
  regression: boolean
  noise: boolean
}

export type JudgePass = {
  id: number
  outcome: string
  gate: { smaller: string[]; minor: string[]; intermittent: string[] } | null
  shortlist: string[]
  queries: { query_key: string; current_5m: { temp_blks: number } }[]
  event_scope: string | null
  verdict: { query_key: string; kind: string; attention: string }[]
}

export type InboxItem = {
  id: number
  type: string
  target: string
  subject: string
  state: string
  title: string
  delivery: string
  severity_band: string
  body_json: string
}

export async function ok<T>(response: Promise<APIResponse>): Promise<T> {
  const settled = await response
  if (!settled.ok()) {
    throw new Error(`${settled.url()} failed: ${await settled.text()}`)
  }
  return (await settled.json()) as T
}

export const hook = {
  locks: (request: APIRequestContext, waiters: number) =>
    ok(request.post(`${hooks}/locks/${target}`, { data: { waiters } })),
  releaseLocks: (request: APIRequestContext) =>
    ok(request.delete(`${hooks}/locks/${target}`)),
  spill: (request: APIRequestContext, seconds: number) =>
    ok<{ sql: string }>(
      request.post(`${hooks}/spill/${target}`, { data: { seconds } })
    ),
  bloat: (request: APIRequestContext) =>
    ok(request.post(`${hooks}/bloat/${target}`)),
  nightly: (request: APIRequestContext) =>
    ok<{ id: number; state: string }>(
      request.post(`${hooks}/report/nightly/${target}`, { timeout: 300_000 })
    ),
  setup: (request: APIRequestContext) =>
    ok<{
      host: string
      port: number
      database: string
      user: string
      password_env: string
    }>(request.post(`${hooks}/setup`, { timeout: 120_000 })),
  phase: (request: APIRequestContext, name: string, on: boolean) =>
    ok<{ plan?: string[] }>(
      request.post(`${hooks}/phase/${name}`, { data: { on }, timeout: 60_000 })
    ),
  proxy: (request: APIRequestContext, state: 'cut' | 'open') =>
    ok<{ open: boolean; at: number }>(request.post(`${hooks}/proxy/${state}`)),
  history: (request: APIRequestContext) =>
    ok<{
      minute_rows: number
      bursts: number[]
      replays: { outcome: string | null; event_scope?: string }[]
      nightly: {
        report_id: number | null
        state: string
        report_jobs: number
        analyses_step_done: boolean
      }
      burst_items: { id: number; state: string; occurrences: number }[]
      elapsed_s: number
    }>(
      request.post(`${hooks}/history/${target}`, {
        data: { analysis_cap: 10 },
        timeout: 300_000,
      })
    ),
  shapes: async (request: APIRequestContext) =>
    (
      await ok<{ shapes: Shape[] }>(
        request.post(`${hooks}/catalogue/${target}`)
      )
    ).shapes,
  judge: (request: APIRequestContext) =>
    ok<JudgePass>(
      request.post(`${hooks}/judge/${target}`, { timeout: 150_000 })
    ),
  events: async (request: APIRequestContext, id: number) =>
    (
      await ok<{ events: { ts: number; kind: string }[] }>(
        request.post(`${hooks}/item-events/${id}`)
      )
    ).events,
  stats: (request: APIRequestContext) =>
    ok<Record<string, unknown>>(request.post(`${hooks}/stats`)),
}

export async function shapeByName(
  request: APIRequestContext
): Promise<Record<string, Shape>> {
  return Object.fromEntries(
    (await hook.shapes(request)).map((shape) => [shape.name, shape])
  )
}

/** Every item for this target, digest ones included. */
export async function items(
  request: APIRequestContext,
  params = 'include_digest=true'
): Promise<InboxItem[]> {
  const body = await ok<{ items: InboxItem[] }>(
    request.get(`/api/monitor/inbox?${params}`)
  )
  return body.items.filter((item) => item.target === target)
}

export function body(item: InboxItem): Record<string, unknown> {
  return JSON.parse(item.body_json) as Record<string, unknown>
}

/** Poll for the value, attaching `diagnose()` to the report if it never comes. */
export async function eventually<T>(
  testInfo: TestInfo,
  label: string,
  read: () => Promise<T>,
  accept: (value: T) => boolean,
  options: {
    timeout: number
    interval?: number
    diagnose?: () => Promise<unknown>
  }
): Promise<T> {
  const deadline = Date.now() + options.timeout
  let last: T | undefined
  let error: unknown
  while (Date.now() < deadline) {
    try {
      last = await read()
      if (accept(last)) return last
    } catch (caught) {
      error = caught
    }
    await new Promise((settle) => setTimeout(settle, options.interval ?? 2_000))
  }
  const diagnostics = options.diagnose
    ? await options.diagnose().catch(String)
    : null
  const report = JSON.stringify(
    diagnostics !== null
      ? { diagnostics, error: error ? String(error) : null, last }
      : { error: error ? String(error) : null, last },
    null,
    2
  )
  await testInfo.attach(`${label}.json`, {
    body: report,
    contentType: 'application/json',
  })
  console.log(`${label} diagnostics: ${report.slice(0, 6000)}`)
  throw new Error(`Timed out waiting for ${label}`)
}

/** Run AI checks until one files what `accept` wants, up to `passes` checks. */
export async function judgeUntil(
  request: APIRequestContext,
  testInfo: TestInfo,
  label: string,
  accept: (all: InboxItem[]) => boolean,
  passes = 3,
  spacingS = 0
): Promise<{ items: InboxItem[]; runs: JudgePass[] }> {
  const runs: JudgePass[] = []
  for (let attempt = 0; attempt < passes; attempt += 1) {
    if (attempt && spacingS) await hold(spacingS)
    runs.push(await hook.judge(request))
    const all = await items(request)
    if (accept(all)) return { items: all, runs }
  }
  const report = JSON.stringify({ runs, items: await items(request) }, null, 2)
  await testInfo.attach(`${label}.json`, {
    body: report,
    contentType: 'application/json',
  })
  console.log(`${label} diagnostics: ${report.slice(0, 6000)}`)
  throw new Error(`${label}: no matching item after ${passes} AI checks`)
}

export async function selectTarget(page: Page) {
  await page.addInitScript((selected) => {
    window.localStorage.setItem('rdst_selected_target', selected)
  }, target)
}

/** The Database page on the test database: Live unless `params` asks for the Report. */
export async function openDatabase(page: Page, params = '') {
  await selectTarget(page)
  await page.goto(`/database?target=${encodeURIComponent(target)}${params}`)
  // A database that isn't monitored yet has no views, only the row that turns monitoring on.
  const views = page.getByRole('tablist', { name: 'Database views' })
  await expect(
    views.or(page.getByRole('button', { name: 'Turn on monitoring' })).first()
  ).toBeVisible()
  // Live and Report; the changes to make live on the global Actions page.
  if (await views.isVisible())
    await expect(views.getByRole('tab')).toHaveCount(2)
}

/** The global Actions page narrowed to the test database. */
export async function openActions(page: Page, params = '') {
  await page.goto(`/actions?db=${encodeURIComponent(target)}${params}`)
  await expect(
    page.getByRole('heading', { level: 1, name: 'Actions' })
  ).toBeVisible()
}

/**
 * The /database search value for a query key, encoded as the app's router writes it. A key that
 * reads as a number (`0e3074175539`) is quoted, so the router keeps it a string.
 */
export function queryParam(key: string) {
  return `query=${encodeURIComponent(JSON.stringify(key))}`
}

/** A search parameter as the app's router reads it back, quoted or not. */
export function searchParam(page: Page, name: string): string | null {
  const raw = new URL(page.url()).searchParams.get(name)
  if (raw === null) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'string' ? parsed : raw
  } catch {
    return raw
  }
}

export function inboxRow(page: Page, id: number) {
  return page.locator(`[data-inbox-item="${id}"]`)
}

export function findItem(
  all: InboxItem[],
  type: string | string[],
  subject?: string
) {
  return all.find(
    (item) =>
      (Array.isArray(type) ? type.includes(item.type) : item.type === type) &&
      (subject === undefined || item.subject === subject)
  )
}

export async function hold(seconds: number) {
  await new Promise((settle) => setTimeout(settle, seconds * 1000))
}

export async function openInbox(page: Page) {
  await selectTarget(page)
  await page.goto('/inbox')
  await expect(
    page.getByRole('heading', { level: 1, name: 'Inbox' })
  ).toBeVisible()
}

/** An item's queue row. */
export async function revealRow(page: Page, id: number) {
  const row = inboxRow(page, id)
  await expect(row).toBeVisible({ timeout: 30_000 })
  return row
}

/** The detail panel of the selected item. */
export function inboxDetail(page: Page, id: number) {
  return page.locator(`[data-inbox-detail="${id}"]`)
}

/**
 * The sidebar's Inbox badge: the open items that need action now. Absent when
 * none do.
 */
export function inboxBadge(page: Page) {
  return page
    .locator('#app-sidebar')
    .getByRole('link', { name: /^Inbox/ })
    .locator('[title$="need you"]')
}
