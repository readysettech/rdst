/**
 * The analyze drawer: reaching its controls, the values it collects before a
 * run, the consent it asks for, the tabs that wait for an analysis, the job
 * list that travels with it, and stopping a run in flight.
 */
import type { Page } from '@playwright/test'
import {
  acceptExplainAnalyzeConsent,
  clearQueryRegistry,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  addQuery,
  completeAnalysis,
  DESKTOP,
  prepareQueries,
  recordPosts,
  shot,
  slowAnalyzeEvents,
  successEvents,
} from './_helpers'

test.use({ viewport: DESKTOP })

const ORDERS = 'SELECT id, total FROM orders ORDER BY created_at DESC'
// A concrete literal: the registry normalizes it to `email = :p1`, so every
// typed value has to survive a round trip through the parameter form.
const EMAIL_SQL = "SELECT id FROM users WHERE email = 'seed@example.com'"

const YESTERDAY = new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString()
const LAST_WEEK = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString()

interface StoredSummary {
  analysis_id: string
  analyzed_at: string
  created_at: string
  target: string
  overall_rating: string
  efficiency_score: number
}

function storedSummary(
  id: string,
  createdAt: string,
  rating: string,
  score: number
): StoredSummary {
  return {
    analysis_id: id,
    analyzed_at: createdAt,
    created_at: createdAt,
    target: 'e2e-guard',
    overall_rating: rating,
    efficiency_score: score,
  }
}

/** Report stored analyses for one query, newest first, with bodies to read. */
async function mockStoredAnalyses(
  page: Page,
  hash: string,
  summaries: StoredSummary[]
) {
  await page.route(`**/api/query-registry/${hash}/analysis/latest`, (route) =>
    route.fulfill({
      json: { found: true, analysis: summaries[0], error: null },
    })
  )
  await page.route(`**/api/query-registry/${hash}/analyses`, (route) =>
    route.fulfill({ json: { hash, analyses: summaries } })
  )
  await page.route(
    `**/api/query-registry/${hash}/analysis/stored-*`,
    (route) => {
      const id = route.request().url().split('/').pop() ?? ''
      const record = summaries.find((s) => s.analysis_id === id) ?? summaries[0]
      return route.fulfill({
        json: {
          hash,
          ...record,
          analysis: {
            display_payload: {
              analysis_id: record.analysis_id,
              query_hash: hash,
              explain_results: completeAnalysis.explain_results,
              llm_analysis: completeAnalysis.llm_analysis,
              rewrite_testing: completeAnalysis.rewrite_testing,
              readyset_cacheability: completeAnalysis.readyset_cacheability,
            },
          },
        },
      })
    }
  )
}

/** Clean registry, one configured target, and the given queries registered. */
async function prepare(
  page: Page,
  queries: string[] = [],
  { consent = true }: { consent?: boolean } = {}
) {
  if (consent) await acceptExplainAnalyzeConsent(page)
  const hashes = await prepareQueries(page, queries)
  await mockConnectivityOk(page)
  return hashes
}

/** Open the drawer on a query and return its inline parameter form. */
async function openParameterForm(page: Page, sql = EMAIL_SQL) {
  const hash = await addQuery(page, sql)
  await page.goto(`/queries?analyze=${hash}`)
  const form = page.getByTestId('parameter-form-inline')
  await expect(form).toBeVisible({ timeout: 15_000 })
  return form
}

/** How Tab reports the node it landed on, matching the focusable inventory. */
function describeActive(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null
    if (!el) return 'none'
    return `${el.tagName.toLowerCase()}[${el.getAttribute('role') ?? ''}]:${
      el.getAttribute('aria-label') ??
      (el.textContent || '').trim().slice(0, 28)
    }`
  })
}

/**
 * B-07 - every control inside the drawer is reachable with Tab, not only the
 * two the focus trap used to allow.
 */
test('Tab reaches every control inside the drawer', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [ORDERS])

  await page.goto(`/queries?analyze=${hash}`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()
  await expect(
    page.getByText('Performance score', { exact: true })
  ).toBeVisible({ timeout: 20_000 })

  const focusable = await drawer.evaluate((el) =>
    Array.from(
      el.querySelectorAll<HTMLElement>(
        'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])'
      )
    )
      .filter((node) => !node.hasAttribute('disabled') && node.offsetParent)
      .map(
        (node) =>
          `${node.tagName.toLowerCase()}[${node.getAttribute('role') ?? ''}]:${
            node.getAttribute('aria-label') ??
            (node.textContent || '').trim().slice(0, 28)
          }`
      )
  )
  expect(focusable.length).toBeGreaterThan(2)

  await drawer.click({ position: { x: 400, y: 300 } })
  const reached: string[] = []
  for (let step = 0; step < focusable.length * 2 + 4; step += 1) {
    await page.keyboard.press('Tab')
    const label = await describeActive(page)
    if (!reached.includes(label)) reached.push(label)
  }
  await shot(page, 'analyze-drawer-focus')

  expect(focusable.filter((control) => !reached.includes(control))).toEqual([])
})

/**
 * B-01 - a value whose own text looks like a placeholder still reaches
 * POST /api/analyze instead of re-opening the prompt forever.
 */
test('an email-shaped parameter value runs the analysis', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  await prepare(page)
  const analyzeCalls = recordPosts(page, '/api/analyze')

  const form = await openParameterForm(page)
  await form
    .getByRole('textbox', { name: 'Value for :p1' })
    .fill('alice@example.com')
  await form.getByRole('button', { name: 'Analyze query' }).click()

  await expect(page.getByTestId('parameter-form-inline')).toHaveCount(0)
  await expect(page.getByText('Parameter values required')).toHaveCount(0)
  await expect
    .poll(() => analyzeCalls.length, { timeout: 15_000 })
    .toBeGreaterThan(0)
  expect(String(analyzeCalls[0])).toContain("'alice@example.com'")
})

/**
 * B-01, B-15, B-16 - the value shapes that used to dead-end all run, the
 * value's own text is sent as a literal, and only an empty value holds the
 * submit button.
 */
test('every parameter value shape reaches the backend as a literal', async ({
  page,
}) => {
  test.setTimeout(180_000)
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  await prepare(page)
  const posts = recordPosts(page, '/api/analyze')

  // The five shapes that used to dead-end, plus one that proves the quoting
  // and one that must hold the submit button.
  const shapes = [
    { name: 'email', value: 'alice@example.com' },
    { name: 'dollar-digit', value: '$100 refund' },
    { name: 'colon-word', value: 'note:urgent' },
    { name: 'at-word', value: '@handle' },
    { name: 'question-mark', value: 'why?' },
    { name: 'number-as-string', value: '00042' },
    { name: 'whitespace-only', value: '   ' },
  ]
  const report: Record<string, string> = {}

  for (const shape of shapes) {
    await clearQueryRegistry(page.request)
    const seen = posts.length
    const form = await openParameterForm(page)
    await form.getByRole('textbox', { name: 'Value for :p1' }).fill(shape.value)
    const submit = form.getByRole('button', { name: 'Analyze query' })
    if (await submit.isDisabled()) {
      report[shape.name] = 'BLOCKED: submit disabled'
      continue
    }
    await submit.click()
    await page.waitForTimeout(1200)
    const stillAsking = await page
      .getByTestId('parameter-form-inline')
      .isVisible()
      .catch(() => false)
    report[shape.name] = stillAsking
      ? 'LOOP: parameter form re-opened, no analyze request'
      : posts.length > seen
        ? `SENT: ${String(posts[posts.length - 1])}`
        : 'NO REQUEST but form gone'
  }

  // B-01: no shape a user can supply may re-open the prompt.
  expect(
    Object.entries(report).filter(([, outcome]) => outcome.startsWith('LOOP'))
  ).toEqual([])
  for (const [name, outcome] of Object.entries(report)) {
    if (name === 'whitespace-only') continue
    expect(outcome, `${name} must reach POST /api/analyze`).toContain('SENT')
  }
  // B-15: the value's own text is a literal, never SQL.
  expect(report['number-as-string']).toContain("'00042'")
  // B-16: an empty value is the only thing that holds the primary action.
  expect(report['whitespace-only']).toContain('BLOCKED')
})

/**
 * B-05 - the drawer asks for EXPLAIN ANALYZE consent in the same warning tone
 * and the same words as the /results modal.
 */
test('the drawer consent is warning-toned', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  // Deliberately not accepting consent: the prompt is the subject here.
  const [hash] = await prepare(page, [ORDERS], { consent: false })

  const search = new URLSearchParams({
    query: 'SELECT id FROM orders WHERE total > 10',
    target: 'e2e-guard',
    fast: 'false',
  })
  await page.goto(`/results?${search.toString()}`)
  await expect(
    page.getByRole('dialog').getByRole('heading', {
      name: 'Run EXPLAIN ANALYZE?',
    })
  ).toBeVisible()

  await page.goto(`/queries?analyze=${hash}`)
  const inline = page.getByTestId('analyze-consent-inline')
  await expect(inline).toBeVisible({ timeout: 15_000 })
  await shot(page, 'analyze-consent-inline')

  const className = await inline.evaluate((el) => el.className)
  expect(className).toContain('surface-warning-soft')
  expect(className).toContain('border-border-warning-soft')
  expect(className).toContain('shadow-glow-warning')
  await expect(inline.getByText('Run EXPLAIN ANALYZE?')).toBeVisible()
  await expect(
    inline.getByText(
      'Analyze runs EXPLAIN ANALYZE, which executes your query once against the database to measure it. Cancel if this query should not be executed.'
    )
  ).toBeVisible()
  expect(await inline.locator('svg').count()).toBeGreaterThan(0)

  await inline.getByRole('button', { name: 'Run analyze' }).click()
  await expect(page.getByTestId('analyze-consent-inline')).toHaveCount(0)
})

/**
 * B-28 - the Follow-up tab waits for an analysis to discuss, and says so
 * instead of opening a conversation with no subject.
 */
test('Follow-up waits until there is an analysis', async ({ page }) => {
  setBackendFixtures({})
  const [hash] = await prepare(page, [ORDERS])

  await page.goto(`/queries?analyze=${hash}&tab=follow-up`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('Run an analysis first')).toBeVisible({
    timeout: 15_000,
  })
  await expect(
    drawer.getByRole('button', { name: 'Open Analyze' })
  ).toBeVisible()
  const tab = drawer.getByRole('tab', { name: 'Follow-up' })
  await expect(tab).toHaveAttribute('aria-disabled', 'true')
  // The tab carries its reason where a pointer can find it.
  await expect(tab).toHaveAttribute('title', 'Run an analysis first')
})

/**
 * B-08 - the job list travels with the drawer, so a run can be watched and
 * stopped without closing the thing that is reporting it.
 */
test('the job list is reachable while the drawer is open', async ({ page }) => {
  setBackendFixtures({
    analyze: [{ events: slowAnalyzeEvents(9000), repeat: true }],
  })
  const [hash] = await prepare(page, [ORDERS])

  await page.goto(`/queries?analyze=${hash}`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()
  await expect(
    page.getByRole('status', { name: 'Analysis in progress' }).first()
  ).toBeVisible({ timeout: 15_000 })

  // The drawer stops at the sidebar rather than cutting the jobs chip in half.
  const triggerBox = await page.getByTestId('jobs-trigger').boundingBox()
  const drawerBox = await drawer.boundingBox()
  expect(triggerBox).not.toBeNull()
  expect(drawerBox).not.toBeNull()
  expect(drawerBox?.x ?? 0).toBeGreaterThanOrEqual(
    (triggerBox?.x ?? 0) + (triggerBox?.width ?? 0) - 1
  )

  const jobs = drawer.getByRole('button', { name: /Jobs/ })
  await expect(jobs).toBeVisible()
  await jobs.click()
  await expect(drawer.getByText(/^Analyzing /)).toBeVisible()
  await expect(drawer.getByRole('button', { name: 'Stop' })).toBeVisible()
  await shot(page, 'analyze-drawer-jobs')
})

/**
 * B-09, B-10, B-27 - the jobs popover clears the steps it reports on, names
 * the run by its query, and its stop control asks before it cancels.
 */
test('stopping a run asks first and reaches a cancelled state', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [{ events: slowAnalyzeEvents(9000), repeat: true }],
  })
  await prepare(page, [ORDERS])

  const search = new URLSearchParams({
    query: ORDERS,
    target: 'e2e-guard',
    fast: 'false',
  })
  await page.goto(`/results?${search}`)
  const steps = page.getByRole('status', { name: 'Analysis in progress' })
  await expect(steps.first()).toBeVisible({ timeout: 15_000 })

  await page.getByTestId('jobs-trigger').click()
  const popover = page.locator('[aria-label="Background jobs"]')
  await expect(popover).toBeVisible()
  await shot(page, 'analyze-jobs-popover')

  // B-27: the job is named by its query wherever the run was started.
  await expect(popover.getByText('Analyzing Select · orders')).toBeVisible()

  // B-09: the list opens clear of the steps it is reporting on.
  const popoverBox = await popover.boundingBox()
  const stepsBox = await steps.first().boundingBox()
  expect(popoverBox).not.toBeNull()
  expect(stepsBox).not.toBeNull()
  if (popoverBox && stepsBox) {
    const overlaps =
      popoverBox.x < stepsBox.x + stepsBox.width &&
      popoverBox.x + popoverBox.width > stepsBox.x &&
      popoverBox.y < stepsBox.y + stepsBox.height &&
      popoverBox.y + popoverBox.height > stepsBox.y
    expect(overlaps).toBe(false)
  }

  // B-10: the control is labelled for what it does, and it asks first.
  const stop = popover.getByRole('button', { name: 'Stop' })
  await expect(stop).toBeVisible()
  expect(await stop.getAttribute('title')).toContain('Stop Analyzing')
  await stop.click()

  const confirm = page.getByRole('dialog').filter({ hasText: 'Stop this job?' })
  await expect(confirm).toBeVisible()
  await confirm.getByRole('button', { name: 'Keep running' }).click()
  await expect(steps.first()).toBeVisible()

  await page.getByTestId('jobs-trigger').click()
  await popover.getByRole('button', { name: 'Stop' }).click()
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Stop job' })
    .click()

  await expect(page.getByText('Analysis cancelled').first()).toBeVisible({
    timeout: 10_000,
  })
  await expect(steps).toHaveCount(0)
  await expect(
    page.getByRole('button', { name: 'Run analysis again' })
  ).toBeVisible()
})

/**
 * B-12 - a stored analysis in the Overview history reads as something that
 * opens, and opening one shows the analysis it names.
 */
test('the analyses history opens a stored analysis', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [ORDERS])
  await mockStoredAnalyses(page, hash, [
    storedSummary('stored-yesterday', YESTERDAY, 'poor', 32),
  ])

  await page.goto(`/queries?analyze=${hash}&tab=overview`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()

  const row = drawer.getByRole('button', { name: /Open the analysis from/ })
  await expect(row).toHaveCount(1)
  expect(await row.evaluate((el) => getComputedStyle(el).cursor)).toBe(
    'pointer'
  )
  // The row carries an affordance of its own, not only a pointer cursor.
  expect(await row.locator('svg').count()).toBeGreaterThan(0)
  await shot(page, 'analyze-overview-history')

  await row.click()
  await expect(drawer.getByTestId('stored-analysis-header')).toBeVisible({
    timeout: 15_000,
  })
  await expect(drawer.getByText(/^Viewing analysis from /)).toBeVisible()
})

/**
 * B-13 - the analyses list ages every entry on one relative scale, so the
 * rows are read by comparing the same kind of number.
 */
test('B-13 - the analyses list uses one date format', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [ORDERS])
  await mockStoredAnalyses(page, hash, [
    storedSummary('stored-yesterday', YESTERDAY, 'poor', 32),
    storedSummary('stored-last-week', LAST_WEEK, 'fair', 61),
  ])

  await page.goto(`/queries?analyze=${hash}&tab=overview`)
  const rows = page
    .getByTestId('analyze-drawer')
    .getByRole('button', { name: /Open the analysis from/ })
  await expect(rows).toHaveCount(2)

  const labels = await rows.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute('aria-label') ?? '')
  )
  for (const label of labels) {
    expect(label).toMatch(/(ago|just now)$/)
  }
})
