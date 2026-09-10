/**
 * Shared machinery for the UX regression suite.
 *
 * Consolidated from the six audit packs: the SSE frame builder and capture
 * mocks (health check), the comparison event builders and sandbox states
 * (benchmarks), the scrolling screenshot and clip/overflow probes (layout),
 * the per-route structural probe and focus walk (cross-cutting sweeps).
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type {
  APIRequestContext,
  Locator,
  Page,
  Request,
} from '@playwright/test'
import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  mockAiKeyReady,
  mockConnectivityOk,
} from '../fixtures'

// Evidence.

/**
 * The flows were recorded with screenshots. They are evidence, not
 * assertions, so the suite runs without them unless QA_SHOTS=1.
 */
export const SHOTS = process.env.QA_SHOTS === '1'

const ARTIFACTS = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'test-results',
  'qa-shots'
)

function artifactPath(rel: string) {
  const path = resolve(ARTIFACTS, rel)
  mkdirSync(dirname(path), { recursive: true })
  return path
}

export async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.screenshot({ path: artifactPath(`${name}.png`), fullPage: true })
}

export async function shotOf(locator: Locator, name: string) {
  if (!SHOTS) return
  await locator.screenshot({ path: artifactPath(`${name}.png`) })
}

/**
 * The app scrolls inside its own container, so `fullPage` only ever captures
 * the viewport. Walk the scroller and capture each screenful.
 */
export async function shotScroll(page: Page, name: string, max = 4) {
  if (!SHOTS) return
  const scroller = await page.evaluateHandle(() => {
    const main = document.querySelector('#main-content') as HTMLElement | null
    let node: HTMLElement | null = main
    while (node) {
      if (node.scrollHeight > node.clientHeight + 20) return node
      node = node.parentElement
    }
    return document.scrollingElement as HTMLElement
  })
  for (let i = 0; i < max; i += 1) {
    await page.screenshot({ path: artifactPath(`${name}-p${i + 1}.png`) })
    const more = await scroller.evaluate((el: HTMLElement, step: number) => {
      const before = el.scrollTop
      el.scrollTop = before + step
      return el.scrollTop > before + 4
    }, 700)
    if (!more) break
  }
  await scroller.evaluate((el: HTMLElement) => {
    el.scrollTop = 0
  })
}

/** Structural probe output, written next to the screenshots when enabled. */
export function writeProbe(rel: string, value: unknown) {
  if (!SHOTS) return
  writeFileSync(artifactPath(rel), JSON.stringify(value, null, 2))
}

// Viewports and settling.

export const DESKTOP = { width: 1280, height: 800 }
/** The product's stated minimum desktop size. */
export const NARROW = { width: 1024, height: 720 }
export const PHONE = { width: 390, height: 844 }

export async function settle(page: Page, ms = 500) {
  await page.waitForTimeout(ms)
}

/**
 * Focus styles animate in. Sampling the moment after Tab reads the start of
 * the transition rather than the ring the user sees, so give it 450ms.
 */
export const FOCUS_SETTLE_MS = 450

/** Time, in ms, until nothing under #main-content is still fading in. */
export async function settleTime(page: Page, budgetMs = 4000) {
  const start = Date.now()
  while (Date.now() - start < budgetMs) {
    const dim = await page.evaluate(
      () =>
        Array.from(document.querySelectorAll('#main-content *')).filter(
          (el) => Number(getComputedStyle(el).opacity) < 0.95
        ).length
    )
    if (dim === 0) return Date.now() - start
    await page.waitForTimeout(50)
  }
  return -1
}

// Routes.

export interface RouteSpec {
  id: string
  path: string
  /** Final URL path after any redirect. */
  lands?: string
}

/** Every user-facing destination. `id` doubles as the artifact filename stem. */
export const ROUTES: RouteSpec[] = [
  { id: 'index', path: '/' },
  { id: 'queries', path: '/queries' },
  {
    id: 'results',
    path: '/results?query=SELECT%20id%20FROM%20users%20WHERE%20id%20%3D%201&target=e2e-guard',
  },
  { id: 'ask', path: '/ask' },
  { id: 'audit', path: '/audit' },
  { id: 'cache', path: '/cache' },
  { id: 'schema', path: '/schema' },
  { id: 'configure', path: '/configure' },
  { id: 'configure-connections', path: '/configure#connections' },
  { id: 'demo', path: '/demo' },
  { id: 'onboarding', path: '/onboarding' },
  { id: 'account-login', path: '/account-login' },
  { id: 'guards', path: '/guards' },
  { id: 'scan', path: '/scan' },
]

/** Legacy URLs that keep working by redirecting. */
export const REDIRECTS: RouteSpec[] = [
  { id: 'agents', path: '/agents', lands: '/ask' },
  { id: 'analyze', path: '/analyze', lands: '/queries' },
  { id: 'fleet', path: '/fleet', lands: '/configure' },
  { id: 'dev-settings', path: '/dev-settings', lands: '/configure' },
  { id: 'benchmark', path: '/benchmark', lands: '/cache' },
  { id: 'top', path: '/top', lands: '/queries' },
]

// Query registry.

export async function addQuery(page: Page, sql: string, target = 'e2e-guard') {
  const response = await page.request.post('/api/query-registry', {
    data: { sql, target },
  })
  if (!response.ok()) throw new Error(`add query failed: ${response.status()}`)
  return ((await response.json()) as { hash: string }).hash
}

export async function addQueryVia(
  request: APIRequestContext,
  sql: string,
  extra: Record<string, unknown> = {}
) {
  const response = await request.post('/api/query-registry', {
    data: { sql, target: 'e2e-guard', ...extra },
  })
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as { success: boolean; hash: string }
  expect(body.success).toBe(true)
  return body.hash
}

/** Clean registry, one configured target, and the given queries registered. */
export async function prepareQueries(page: Page, queries: string[]) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  const hashes: string[] = []
  for (const query of queries)
    hashes.push(await addQueryVia(page.request, query))
  return hashes
}

// Analyze fixtures.

export const completeAnalysis = {
  success: true,
  analysis_id: 'analysis-qa-001',
  query_hash: 'query-qa-001',
  explain_results: {
    success: true,
    database_engine: 'postgresql',
    execution_time_ms: 128.4,
    rows_examined: 12000,
    rows_returned: 25,
    cost_estimate: 87.3,
  },
  llm_analysis: {
    success: true,
    performance_assessment: {
      overall_rating: 'poor',
      efficiency_score: 32,
      primary_concerns: ['Sequential scan reads too many rows'],
    },
    index_recommendations: [
      {
        sql: 'CREATE INDEX idx_orders_customer_id ON orders (customer_id)',
        table: 'orders',
        columns: ['customer_id'],
        index_type: 'btree',
        rationale: 'Avoids the sequential scan for customer lookups.',
        estimated_impact: 'high',
        caveats: [],
      },
    ],
    optimization_opportunities: [
      {
        priority: 'medium',
        description: 'Return only columns needed by the caller.',
        type: 'projection',
      },
    ],
    rewrite_suggestions: [],
    token_usage: {
      input: 100,
      output: 50,
      total: 150,
      estimated_cost_usd: 0.01,
    },
  },
  rewrite_testing: {
    tested: false,
    skipped_reason: 'qa_fixture',
    message: 'Skipped in browser tests',
  },
  readyset_cacheability: {
    checked: true,
    cacheable: true,
    confidence: 'medium',
    method: 'static_analysis',
    explanation: 'Static SQL screening found no obvious blockers.',
    issues: [],
    warnings: [],
  },
}

export const successEvents = [
  {
    type: 'progress',
    stage: 'normalizing',
    percent: 10,
    message: 'Preparing query',
  },
  {
    type: 'progress',
    stage: 'executing_explain',
    percent: 45,
    message: 'Running EXPLAIN ANALYZE',
  },
  {
    type: 'progress',
    stage: 'analyzing_llm',
    percent: 75,
    message: 'Analyzing the execution plan',
  },
  { type: 'complete', ...completeAnalysis },
]

/** Progress events spaced far enough apart to observe a running analysis. */
export function slowAnalyzeEvents(holdMs: number) {
  return [
    {
      type: 'progress',
      stage: 'normalizing',
      percent: 10,
      message: 'Preparing query',
    },
    {
      type: 'progress',
      stage: 'executing_explain',
      percent: 45,
      message: 'Running EXPLAIN ANALYZE',
      _delay_ms: holdMs,
    },
    {
      type: 'progress',
      stage: 'analyzing_llm',
      percent: 75,
      message: 'Analyzing the execution plan',
      _delay_ms: holdMs,
    },
    { type: 'complete', ...completeAnalysis, _delay_ms: holdMs },
  ]
}

const YESTERDAY = new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString()

/** Report a query as previously analyzed, with a stored record to read back. */
export async function mockAnalyzedQuery(page: Page, hash: string) {
  const summary = {
    analysis_id: 'stored-yesterday',
    analyzed_at: YESTERDAY,
    created_at: YESTERDAY,
    target: 'e2e-guard',
    overall_rating: 'poor',
    efficiency_score: 32,
  }
  await page.route('**/api/query-registry?*', async (route) => {
    const response = await route.fetch()
    const body = (await response.json()) as {
      queries: Record<string, unknown>[]
    }
    for (const query of body.queries) {
      if (query.hash === hash) {
        query.last_analyzed_at = YESTERDAY
        query.analysis_count = 1
      }
    }
    await route.fulfill({ json: body })
  })
  await page.route(`**/api/query-registry/${hash}/analysis/latest`, (route) =>
    route.fulfill({ json: { found: true, analysis: summary, error: null } })
  )
  await page.route(`**/api/query-registry/${hash}/analyses`, (route) =>
    route.fulfill({ json: { hash, analyses: [summary] } })
  )
}

/** Labels of the filled (solid) buttons inside a query card's action row. */
export async function solidActionLabels(card: Locator) {
  return card
    .locator('[data-testid="query-card-footer-content"] button')
    .evaluateAll((nodes) =>
      nodes
        .filter((node) =>
          (node as HTMLElement).className
            .split(/\s+/)
            .includes('bg-surface-primary-solid')
        )
        .map((node) => ((node as HTMLElement).textContent || '').trim())
    )
}

/** Bodies of every POST to `pathname` observed after this call. */
export function recordPosts(page: Page, pathname: string) {
  const bodies: (string | null)[] = []
  page.on('request', (request: Request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === pathname
    ) {
      bodies.push(request.postData())
    }
  })
  return bodies
}

// Benchmarks: sandbox state and comparison events.

export const readySandbox = {
  phase: 'ready',
  current_target: 'e2e-guard',
  generation: 1,
  lease_owner: null,
  lease_purpose: null,
  queued_requests: 0,
  dirty_reason: null,
  failed_target: null,
  last_error: null,
  last_released_at: '2026-07-23T12:00:00+00:00',
  expires_at: '2026-07-24T12:00:00+00:00',
  container_name: 'rdst-readyset-sandbox',
  healthy: true,
}

export const pendingSandbox = {
  ...readySandbox,
  phase: 'snapshotting',
  healthy: false,
  last_released_at: null,
}

export const upstreamLane = {
  scheduled: 120,
  completed: 120,
  errors: 0,
  dropped: 0,
  in_flight: 0,
  throughput_rps: 4,
  error_rate: 0,
  mean_ms: 12,
  p50_ms: 11,
  p95_ms: 17,
  p99_ms: 18,
}

export const readysetLane = {
  scheduled: 480,
  completed: 480,
  errors: 0,
  dropped: 0,
  in_flight: 0,
  throughput_rps: 16,
  error_rate: 0,
  mean_ms: 3,
  p50_ms: 2.8,
  p95_ms: 4,
  p99_ms: 5,
}

/** A drained trailing sample: nothing scheduled in either lane. */
export const drainSample = {
  type: 'cache_compare_sample',
  elapsed_seconds: 31,
  concurrency: 4,
  origin: { ...upstreamLane, scheduled: 0, completed: 2, throughput_rps: 0.06 },
  readyset: {
    ...readysetLane,
    scheduled: 0,
    completed: 3,
    throughput_rps: 0.09,
  },
}

export function comparisonEvents(
  query: string,
  {
    speedup = 4,
    improvement = 75,
    sampleDelayMs = 0,
    startDelayMs = 0,
    withDrain = false,
  } = {}
) {
  const samples = [1, 5, 10, 20, 30].map((second, index) => ({
    type: 'cache_compare_sample',
    elapsed_seconds: second,
    concurrency: index < 2 ? 2 : 4,
    origin: {
      ...upstreamLane,
      throughput_rps: 4 + index * 0.4,
      p95_ms: 17 + index,
    },
    readyset: {
      ...readysetLane,
      throughput_rps: 16 + index * 4,
      p95_ms: 4 - index * 0.2,
    },
    ...(sampleDelayMs ? { _delay_ms: sampleDelayMs } : {}),
  }))
  return {
    delay_ms: startDelayMs,
    events: [
      ...samples,
      ...(withDrain ? [drainSample] : []),
      {
        type: 'cache_compare_complete',
        success: true,
        query,
        duration_seconds: 30,
        elapsed_seconds: 30,
        concurrency: 4,
        origin: upstreamLane,
        readyset: readysetLane,
        timeline: [...samples, ...(withDrain ? [drainSample] : [])],
        phases: [
          { concurrency: 2, elapsed_seconds: 10 },
          { concurrency: 4, elapsed_seconds: 30 },
        ],
        speedup_mean: speedup,
        improvement_pct: improvement,
        winner: speedup >= 1 ? 'readyset' : 'origin',
      },
    ],
  }
}

export function comparisonFailure(message: string) {
  return { events: [{ type: 'error', message, category: 'query' }] }
}

export function compareQueryList(page: Page) {
  return page.getByRole('region', { name: 'Queries available for comparison' })
}

export async function selectQueryByIndex(page: Page, index: number) {
  await compareQueryList(page)
    .getByRole('button', { name: /^Select / })
    .nth(index)
    .click()
}

export async function selectQueryByHash(page: Page, hash: string) {
  await compareQueryList(page)
    .locator(`[data-query-hash="${hash}"]`)
    .first()
    .click()
}

// Health check: background runs and fleet.

/** The registry promotes each event's `type` to the SSE `event:` frame name. */
export function runEvents(
  events: Array<Record<string, unknown>>,
  { end = 'done' }: { end?: string | null } = {}
): string {
  const frames = events.map((event) => {
    const { type, ...rest } = event as { type: string }
    return `event: ${type}\ndata: ${JSON.stringify(rest)}\n\n`
  })
  if (end)
    frames.push(`event: run_end\ndata: ${JSON.stringify({ status: end })}\n\n`)
  return frames.join('')
}

/**
 * Serve a background capture run: the POST hands back a run id, the events
 * endpoint replays `events`. `holdMs` keeps the stream open so running states
 * stay on screen long enough to observe.
 */
export async function mockCaptureRun(
  page: Page,
  events: Array<Record<string, unknown>>,
  { runId = 'run_capture_1', end = 'done' as string | null, holdMs = 0 } = {}
) {
  await page.route('**/api/audit/capture', (route) =>
    route.fulfill({ json: { run_id: runId, reused: false } })
  )
  await page.route(`**/api/runs/${runId}/events*`, async (route) => {
    if (holdMs) await new Promise((r) => setTimeout(r, holdMs))
    await route
      .fulfill({
        headers: { 'content-type': 'text/event-stream' },
        body: runEvents(events, { end }),
      })
      .catch(() => undefined)
  })
}

/**
 * A capture whose start request never resolves: the run session is open (the
 * store begins it before the POST) and the stream never closes, so the running
 * UI stays on screen until the returned `release` is called.
 */
export async function mockGatedCaptureStart(page: Page): Promise<() => void> {
  let release: () => void = () => undefined
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/audit/capture', async (route) => {
    await gate
    await route
      .fulfill({ json: { run_id: 'never', reused: false } })
      .catch(() => undefined)
  })
  return release
}

export async function mockFleetTargets(
  page: Page,
  members: Array<Record<string, unknown>>,
  groups: Array<Record<string, unknown>> = []
) {
  await page.route('**/api/fleet/targets*', (route) =>
    route.fulfill({ json: { members, groups, count: members.length } })
  )
}

export function member(name: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    engine: 'postgresql',
    host: `${name}.internal`,
    port: 5432,
    database: 'application',
    user: 'rdst_e2e',
    password_env: 'TEST_DB_PASSWORD',
    has_password: true,
    group: null,
    ...extra,
  }
}

/** Standard /audit setup: one healthy target, satisfied AI gate, ok preflight. */
export async function prepareAuditPage(
  page: Page,
  requirements: Record<string, unknown> = {}
) {
  await configureTestTarget(page, { hasPassword: true })
  await mockAiKeyReady(page)
  await mockConnectivityOk(page)
  await page.route('**/api/audit/requirements*', (route) =>
    route.fulfill({
      json: {
        target: 'e2e-guard',
        engine: 'postgresql',
        query_stats: 'ok',
        detail: 'Query statistics are available',
        remediation: null,
        docker_available: true,
        ...requirements,
      },
    })
  )
  await page.goto('/audit')
  await expect(
    page.getByRole('heading', { name: 'Health Check' })
  ).toBeVisible()
}

export async function selectTarget(page: Page, name = 'e2e-guard') {
  await page
    .getByRole('checkbox', { name: `Select ${name}` })
    .click({ force: true })
}

/** A saved capture run detail payload (workload shape) with a health analysis. */
export function savedCaptureRun(runId: string) {
  return {
    run_id: runId,
    target_name: 'e2e-guard',
    engine: 'postgresql',
    started_at: '2026-09-01T09:00:00Z',
    duration_seconds: 60,
    queries: [
      {
        query_hash: 'abc123',
        sql: 'SELECT * FROM orders WHERE customer_id = $1',
        calls: 1200,
        total_time_ms: 4800,
        mean_time_ms: 4,
      },
    ],
    summary: { unique_queries: 1, total_executions: 1200 },
    health_analysis: {
      health_score: 72,
      health_label: 'Fair',
      executive_summary: 'Connection headroom is fine; two queries dominate.',
      top_findings: [
        {
          severity: 'warn',
          title: 'One query is 62% of database time',
          body: 'The orders lookup dominates total execution time.',
        },
        {
          severity: 'ok',
          title: 'Connection headroom is healthy',
          body: 'The pool has room for bursts.',
        },
      ],
    },
  }
}

// Network shaping.

/** Endpoints the shell itself needs; failing them replaces the whole app. */
export const SHELL_API =
  /\/api\/(configure|init|env|system|version|telemetry|health|update|account|desktop)/

/**
 * Playwright matches the most recently registered handler first, so a broad
 * `**\/api/**` route shadows the AI-gate stubs installed by the auto fixture.
 * Re-apply them after installing one.
 */
export async function reapplyAiKeyReady(page: Page) {
  await mockAiKeyReady(page)
}

/** Fail every product endpoint while leaving the shell reachable. */
export async function failProductApi(
  page: Page,
  detail = 'Upstream service unavailable'
) {
  await page.route('**/api/**', (route) => {
    const { pathname } = new URL(route.request().url())
    if (SHELL_API.test(pathname)) return route.continue()
    return route.fulfill({ status: 500, json: { detail } })
  })
  await reapplyAiKeyReady(page)
}

/** Hold every product endpoint open so loading states stay on screen. */
export async function stallProductApi(page: Page, holdMs = 6000) {
  await page.route('**/api/**', async (route) => {
    await new Promise((r) => setTimeout(r, holdMs))
    await route.continue().catch(() => undefined)
  })
  await reapplyAiKeyReady(page)
}

// Structural probes.

/**
 * Everything a screenshot cannot show: roles, labels, disabled state,
 * computed tokens, heading outline, overflow.
 */
export async function probeRoute(page: Page) {
  return page.evaluate(() => {
    const text = (el: Element | null) =>
      (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    const main = document.querySelector('#main-content') ?? document.body

    const headings = [...main.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(
      (h) => {
        const cs = getComputedStyle(h)
        return {
          tag: h.tagName.toLowerCase(),
          text: text(h).slice(0, 120),
          fontSize: cs.fontSize,
          fontWeight: cs.fontWeight,
          color: cs.color,
        }
      }
    )

    const buttons = [...main.querySelectorAll('button,[role="button"],a[href]')]
      .filter((b) => (b as HTMLElement).offsetParent !== null)
      .map((b) => {
        const cs = getComputedStyle(b)
        return {
          tag: b.tagName.toLowerCase(),
          label: text(b).slice(0, 60),
          aria: b.getAttribute('aria-label'),
          disabled:
            (b as HTMLButtonElement).disabled ||
            b.getAttribute('aria-disabled') === 'true',
          iconOnly: text(b).length === 0,
          bg: cs.backgroundColor,
          color: cs.color,
          radius: cs.borderRadius,
          height: (b as HTMLElement).offsetHeight,
        }
      })

    const iconOnlyUnlabeled = [
      ...main.querySelectorAll('button,[role="button"]'),
    ]
      .filter((b) => {
        const el = b as HTMLElement
        if (el.offsetParent === null) return false
        if (text(b).length > 0) return false
        return !b.getAttribute('aria-label') && !b.getAttribute('title')
      })
      .map((b) => (b as HTMLElement).outerHTML.slice(0, 160))

    const de = document.documentElement
    return {
      url: location.pathname + location.search + location.hash,
      title: document.title,
      headings,
      h1Count: main.querySelectorAll('h1').length,
      buttons,
      iconOnlyUnlabeled,
      imagesWithoutAlt: main.querySelectorAll('img:not([alt])').length,
      overflowX: de.scrollWidth - de.clientWidth,
      widestOffenders: [...main.querySelectorAll('*')]
        .filter((el) => (el as HTMLElement).offsetWidth > de.clientWidth + 2)
        .slice(0, 8)
        .map((el) => ({
          tag: el.tagName.toLowerCase(),
          cls: (el.getAttribute('class') ?? '').slice(0, 120),
          width: (el as HTMLElement).offsetWidth,
        })),
      mainText: text(main).slice(0, 4000),
    }
  })
}

export interface FocusStop {
  tag: string
  label: string
  outline: string
  boxShadow: string
  ring: boolean
}

/**
 * Walk Tab through the page and record what receives focus and how it is
 * marked. Focus styling animates, so each stop settles before it is sampled.
 */
export async function focusWalk(page: Page, steps = 22): Promise<FocusStop[]> {
  const seen: FocusStop[] = []
  for (let i = 0; i < steps; i += 1) {
    await page.keyboard.press('Tab')
    await page.waitForTimeout(FOCUS_SETTLE_MS)
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      if (!el || el === document.body) return null
      const cs = getComputedStyle(el)
      const outline =
        cs.outlineStyle === 'none'
          ? ''
          : `${cs.outlineWidth} ${cs.outlineColor}`
      return {
        tag: el.tagName.toLowerCase(),
        label: (
          el.getAttribute('aria-label') ??
          (el.textContent ?? '').replace(/\s+/g, ' ').trim()
        ).slice(0, 50),
        outline,
        boxShadow: cs.boxShadow.slice(0, 100),
        ring:
          outline !== '' || (cs.boxShadow !== 'none' && cs.boxShadow !== ''),
      }
    })
    if (info) seen.push(info)
  }
  return seen
}

export interface ContrastRow {
  route: string
  color: string
  bg: string
  fontSize: string
  ratio: number
  required: number
  pass: boolean
  sample: string
}

/**
 * Every distinct colour-on-background pair carrying its own text under
 * `#main-content`, with its WCAG contrast ratio and the AA threshold for its
 * size.
 */
export async function contrastRows(
  page: Page,
  routeId: string
): Promise<ContrastRow[]> {
  return page.evaluate((rid) => {
    const lum = (c: string) => {
      const m = c.match(/\d+(\.\d+)?/g)
      if (!m) return 0
      const [r, g, b] = m
        .slice(0, 3)
        .map(Number)
        .map((v) => {
          const s = v / 255
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
        })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const bgOf = (el: Element): string => {
      let cur: Element | null = el
      while (cur) {
        const bg = getComputedStyle(cur).backgroundColor
        if (bg && !/rgba\(0, 0, 0, 0\)|transparent/.test(bg)) return bg
        cur = cur.parentElement
      }
      return 'rgb(0, 0, 0)'
    }
    const out: ContrastRow[] = []
    const seen = new Set<string>()
    const main = document.querySelector('#main-content') ?? document.body
    for (const el of [...main.querySelectorAll('*')]) {
      if ((el as HTMLElement).offsetParent === null) continue
      const own = [...el.childNodes].some(
        (n) => n.nodeType === 3 && (n.textContent ?? '').trim().length > 2
      )
      if (!own) continue
      const cs = getComputedStyle(el)
      const key = `${cs.color}|${bgOf(el)}|${cs.fontSize}`
      if (seen.has(key)) continue
      seen.add(key)
      const l1 = lum(cs.color)
      const l2 = lum(bgOf(el))
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
      const px = Number.parseFloat(cs.fontSize)
      const large = px >= 24 || (px >= 18.66 && Number(cs.fontWeight) >= 700)
      out.push({
        route: rid,
        color: cs.color,
        bg: bgOf(el),
        fontSize: cs.fontSize,
        ratio: Math.round(ratio * 100) / 100,
        required: large ? 3 : 4.5,
        pass: ratio >= (large ? 3 : 4.5),
        sample: (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 50),
      })
    }
    return out
  }, routeId)
}

// Layout probes.

/** True when the element's painted box escapes its nearest clipping ancestor. */
export async function clipped(locator: Locator) {
  return locator.evaluate((el) => {
    const box = el.getBoundingClientRect()
    let parent = el.parentElement
    while (parent) {
      const cs = getComputedStyle(parent)
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const p = parent.getBoundingClientRect()
        return box.right > p.right + 1 || box.left < p.left - 1
      }
      parent = parent.parentElement
    }
    return box.right > window.innerWidth + 1 || box.left < -1
  })
}

/** Elements inside `root` whose painted width exceeds the viewport. */
export async function widestOffenders(page: Page, root = '#main-content') {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) ?? document.body
    const limit = document.documentElement.clientWidth
    return [...main.querySelectorAll('*')]
      .filter((el) => (el as HTMLElement).offsetWidth > limit + 2)
      .slice(0, 8)
      .map((el) => ({
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute('class') ?? '').slice(0, 140),
        width: (el as HTMLElement).offsetWidth,
      }))
  }, root)
}

export interface Box {
  text: string
  x: number
  y: number
  w: number
  h: number
}

/** Boxes of every element matching `selector`, in viewport coordinates. */
export async function boxesOf(page: Page, selector: string): Promise<Box[]> {
  return page.evaluate(
    (sel) =>
      [...document.querySelectorAll(sel)].map((el) => {
        const r = el.getBoundingClientRect()
        return {
          text: (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 60),
          x: Math.round(r.x),
          y: Math.round(r.y),
          w: Math.round(r.width),
          h: Math.round(r.height),
        }
      }),
    selector
  )
}

export function overlaps(a: Box, b: Box) {
  return (
    a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
  )
}

export interface LayoutProblem {
  kind: 'occluded' | 'clipped' | 'overflow' | 'offscreen'
  text: string
  selector: string
  detail: string
}

/**
 * Leaf content that another element paints over, that its clipping ancestor
 * cuts off, or that scrolls horizontally inside a container with no scrollbar.
 */
export async function layoutProblems(page: Page): Promise<LayoutProblem[]> {
  return page.evaluate(() => {
    const describe = (el: Element) => {
      const tag = el.tagName.toLowerCase()
      const id = el.id ? `#${el.id}` : ''
      const cls = (el.getAttribute('class') || '')
        .split(/\s+/)
        .slice(0, 3)
        .join('.')
      return `${tag}${id}${cls ? `.${cls}` : ''}`
    }
    const problems: {
      kind: 'occluded' | 'clipped' | 'overflow' | 'offscreen'
      text: string
      selector: string
      detail: string
    }[] = []

    for (const el of Array.from(document.querySelectorAll('*'))) {
      const style = getComputedStyle(el)
      if (style.visibility === 'hidden' || style.display === 'none') continue
      const rect = el.getBoundingClientRect()
      if (rect.width < 4 || rect.height < 4) continue
      // Off-canvas chrome (the closed mobile drawer) is not a layout problem.
      if (rect.right < 0 || rect.left > innerWidth) continue
      // Decorative backdrops carry no content to lose.
      if (el.closest('[aria-hidden="true"]')) continue

      const bleeder = Array.from(el.children).some(
        (child) => Number.parseFloat(getComputedStyle(child).marginLeft) < 0
      )
      if (
        !bleeder &&
        el.scrollWidth - el.clientWidth > 2 &&
        style.overflowX !== 'auto' &&
        style.overflowX !== 'scroll' &&
        style.textOverflow !== 'ellipsis' &&
        // A negative inline margin puts the box in the ancestor's own
        // padding, which is deliberate full-bleed rather than lost content.
        Number.parseFloat(style.marginLeft) >= 0 &&
        el.clientWidth > 0
      ) {
        problems.push({
          kind: 'overflow',
          text: (el.textContent || '').trim().slice(0, 60),
          selector: describe(el),
          detail: `scrollWidth ${el.scrollWidth} > clientWidth ${el.clientWidth}`,
        })
      }

      let clipper: Element | null = el.parentElement
      let isClipped = false
      while (clipper) {
        const cs = getComputedStyle(clipper)
        if (cs.overflow === 'hidden' || cs.overflowX === 'hidden') {
          if (clipper.getBoundingClientRect().right <= innerWidth + 2)
            isClipped = true
          break
        }
        clipper = clipper.parentElement
      }
      if (
        !isClipped &&
        rect.right > innerWidth + 2 &&
        style.position !== 'fixed'
      ) {
        problems.push({
          kind: 'offscreen',
          text: (el.textContent || '').trim().slice(0, 60),
          selector: describe(el),
          detail: `right ${Math.round(rect.right)} > viewport ${innerWidth}`,
        })
      }

      const hasOwnText = Array.from(el.childNodes).some(
        (n) => n.nodeType === 3 && (n.textContent || '').trim().length > 1
      )
      if (!hasOwnText) continue
      const text = (el.textContent || '').trim().slice(0, 60)

      const cx = rect.left + Math.min(rect.width / 2, 40)
      const cy = rect.top + rect.height / 2
      if (cx >= 0 && cy >= 0 && cx < innerWidth && cy < innerHeight) {
        const hit = document.elementFromPoint(cx, cy)
        if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) {
          problems.push({
            kind: 'occluded',
            text,
            selector: describe(el),
            detail: `covered by ${describe(hit)} "${(hit.textContent || '').trim().slice(0, 40)}"`,
          })
        }
      }

      let parent = el.parentElement
      while (parent) {
        const ps = getComputedStyle(parent)
        if (ps.overflow === 'hidden' || ps.overflowX === 'hidden') {
          const pr = parent.getBoundingClientRect()
          if (rect.right - pr.right > 2 || pr.left - rect.left > 2) {
            problems.push({
              kind: 'clipped',
              text,
              selector: describe(el),
              detail: `right ${Math.round(rect.right)} vs clip ${Math.round(pr.right)} on ${describe(parent)}`,
            })
            break
          }
        }
        parent = parent.parentElement
      }
    }
    return problems
  })
}
