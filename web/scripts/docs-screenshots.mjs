// Captures the RDST web UI for the desktop documentation section.
//
// Playwright screenshots carry no browser chrome, so the output reads as the
// desktop app rather than a browser tab. Point RDST_URL at a running
// `rdst web` and OUT at the docs public asset directory:
//
//   RDST_URL=http://127.0.0.1:8788 \
//   OUT=../../apps/docs/public/rdst-desktop \
//   node scripts/docs-screenshots.mjs
//
// ONLY=home,top limits the run to named shots while iterating.
import { mkdirSync } from 'node:fs'
import { chromium } from '@playwright/test'

const BASE = process.env.RDST_URL ?? 'http://127.0.0.1:8788'
const OUT = process.env.OUT ?? './screenshots'
const TARGET = process.env.TARGET ?? 'shopdb'
// Health check screenshots come from a real RDS instance, so the report can
// reach a sizing verdict instead of reporting an unknown instance class.
const AUDIT_TARGET = process.env.AUDIT_TARGET ?? 'rdst-sshtest-pg'
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(',')) : null

// A 16:10 window at 2x reads as a desktop app screenshot on a retina display
// and stays sharp when the docs page scales it down to its 900px column.
const VIEWPORT = { width: 1440, height: 900 }

// The capture environment is a real account against real databases, so the
// rendered pages carry an account id, a work address, and host names that
// should not ship in public documentation. Rewrite them in the DOM just before
// the shutter, using addresses reserved for documentation (RFC 5737) and the
// AWS example account id.
// Each entry is a [pattern, replacement] pair applied as a global regex.
const REDACTIONS = [
  ['michael\\.v@readyset\\.io', 'you@yourcompany.com'],
  ['069491470376|701495964134', '123456789012'],
  ['cfp0qwefgklt', 'abcdefghijkl'],
  ['18\\.226\\.104\\.100', '203.0.113.10'],
  ['16\\.59\\.28\\.178', '203.0.113.20'],
  // The capture home is a throwaway directory; show the path a reader would have.
  ['/tmp/[^\\s]*?/docs-home', '/Users/you'],
  ['/home/mikev', '/Users/you'],
  ['(AWSReservedSSO_[A-Za-z]+)[_a-z0-9]*', '$1'],
  ['vpc-[0-9a-f]{8,}', 'vpc-0a1b2c3d4e5f6a7b8'],
]

/** Rewrites redacted strings in every text node and input value on the page. */
function applyRedactions(pairs) {
  const compiled = pairs.map(([from, to]) => [new RegExp(from, 'g'), to])
  const swap = (value) =>
    compiled.reduce((text, [from, to]) => text.replace(from, to), value)

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const next = swap(node.nodeValue)
    if (next !== node.nodeValue) node.nodeValue = next
  }
  for (const field of document.querySelectorAll('input, textarea')) {
    const next = swap(field.value)
    if (next !== field.value) field.value = next
  }
  for (const el of document.querySelectorAll('[title], [aria-label], [placeholder]')) {
    for (const attr of ['title', 'aria-label', 'placeholder']) {
      const current = el.getAttribute(attr)
      if (current === null) continue
      const next = swap(current)
      if (next !== current) el.setAttribute(attr, next)
    }
  }
}

const routes = [
  ['home', '/'],
  ['ask', '/ask'],
  ['slow-queries', '/top'],
  ['health-check', '/audit'],
  ['analyze-query', '/analyze'],
  ['comparisons', '/cache'],
  ['queries', '/query-registry'],
  ['benchmark', '/benchmark'],
  ['schema', '/schema'],
  ['code-scan', '/scan'],
  ['agents', '/agents'],
  ['guards', '/guards'],
  ['settings', '/configure'],
  ['onboarding', '/onboarding'],
]

mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const context = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 2,
  reducedMotion: 'reduce',
})
// Seed the target the app reads on boot so every page renders against the
// same database instead of whatever the first render happens to pick.
await context.addInitScript(
  (target) => localStorage.setItem('rdst_selected_target', target),
  TARGET
)
const page = await context.newPage()

/** Navigates, settles, redacts, and writes `<OUT>/<name>.png`. */
export async function shoot(name, path, { settle = 2_000 } = {}) {
  if (path) {
    try {
      await page.goto(`${BASE}${path}`, {
        waitUntil: 'networkidle',
        timeout: 20_000,
      })
    } catch {
      await page.goto(`${BASE}${path}`, {
        waitUntil: 'domcontentloaded',
        timeout: 20_000,
      })
    }
  }
  await page.waitForTimeout(settle)
  await page.evaluate(applyRedactions, REDACTIONS)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log(`captured ${name}`)
}

/** Clicks `name` if such a button exists, and reports whether it did. */
async function clickIfPresent(name, { timeout = 8_000 } = {}) {
  const button = page.getByRole('button', { name }).first()
  if (!(await button.count())) return false
  await button.click({ timeout })
  return true
}

// Screens that only exist part-way through an interaction. Each entry drives the
// app to that state and shoots it. Keep them independent: a flow must not assume
// another one ran first.
const flows = {
  // Settings -> Discover & import, showing what AWS discovery returned. The
  // region chips come pre-selected, so leave them alone.
  'discover-aws': async () => {
    await page.goto(`${BASE}/configure`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_500)
    await clickIfPresent(/Discover . import/i)
    await page.waitForTimeout(3_000)
    await shoot('discover-aws', null)
    await clickIfPresent(/^Discover$/)
    await page.waitForTimeout(10_000)
    await shoot('discover-results', null)
  },

  // Settings -> Add Target, with the SSH jump-host section opened.
  'add-target': async () => {
    await page.goto(`${BASE}/configure`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_500)
    await clickIfPresent(/^Add Target$/)
    await page.waitForTimeout(1_500)
    await shoot('add-target', null)
    await page.getByText(/Connect via SSH jump host/i).first().click()
    await page.waitForTimeout(1_500)
    await shoot('add-target-ssh', null)
  },

  // Slow Queries, after the scan has returned ranked results.
  'slow-queries-results': async () => {
    await page.goto(`${BASE}/top`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_000)
    await clickIfPresent(/Find slow queries/i)
    await page.waitForTimeout(12_000)
    await shoot('slow-queries-results', null)
  },

  // Analyze Query, driven from Slow Queries so the query under analysis is a
  // real one off this database rather than something pasted in.
  'analyze-report': async () => {
    await page.goto(`${BASE}/top`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_000)
    await clickIfPresent(/Find slow queries/i)
    await page.waitForTimeout(12_000)
    await page.getByRole('button', { name: /^Analyze$/ }).first().click()
    await page.waitForTimeout(4_000)

    // Historical statistics normalize literals away, so a query pulled from
    // Slow Queries arrives parameterized and RDST asks for the values back.
    const values = page.getByPlaceholder(/Enter value/i)
    if (await values.count()) {
      await shoot('analyze-parameters', null)
      for (let i = 0; i < (await values.count()); i += 1) {
        await values.nth(i).fill('20')
      }
      await page.getByRole('button', { name: /Analyze Query/i }).last().click()
    }

    // EXPLAIN ANALYZE executes the query, so RDST confirms before running it.
    await page.waitForTimeout(3_000)
    const confirm = page.getByRole('button', { name: /Run analyze/i }).first()
    if (await confirm.count()) {
      await shoot('analyze-confirm', null)
      await confirm.click()
    }

    await page.waitForTimeout(30_000)
    await shoot('analyze-running', null)
    await page.waitForTimeout(110_000)
    await shoot('analyze-report', null)
  },

  // Health Check over a single target, then the report it produces.
  'health-check-run': async () => {
    await page.goto(`${BASE}/audit`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_500)

    // The sidebar target switcher also renders every target name, so scope the
    // click to the row inside the Targets list rather than the first match.
    const row = page
      .locator('label, li, tr, div')
      .filter({ hasText: new RegExp(`^${AUDIT_TARGET}\\s*(postgresql|mysql)$`) })
      .last()
    if (await row.count()) {
      await row.click()
    } else {
      await page.getByRole('checkbox').last().check()
    }
    await page.waitForTimeout(1_500)
    await shoot('health-check-selected', null)
    await clickIfPresent(/Run health check/i)
    await page.waitForTimeout(30_000)
    await shoot('health-check-running', null)
    await page.waitForTimeout(120_000)
    await shoot('health-check-report', null)
  },

  // Ask, carried through to a generated query rather than the empty prompt.
  'ask-result': async () => {
    await page.goto(`${BASE}/ask`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_500)
    const box = page.locator('textarea, input[type="text"]').first()
    await box.click()
    await box.fill('Which product categories earned the most revenue?')
    await page.waitForTimeout(500)
    await shoot('ask-question', null)
    await page.keyboard.press('Enter')
    await page.waitForTimeout(35_000)
    await shoot('ask-result', null)
  },

  // The tabs of a saved report. Run after health-check-run has produced one.
  'health-check-tabs': async () => {
    await page.goto(`${BASE}/audit`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2_000)
    await page.getByText('Reports', { exact: true }).first().click()
    await page.waitForTimeout(2_500)
    await shoot('health-check-reports-list', null)

    await page
      .getByText(AUDIT_TARGET, { exact: true })
      .first()
      .click()
    await page.waitForTimeout(4_000)
    await shoot('health-check-report', null)

    for (const tab of ['Queries', 'Sizing', 'Next Steps']) {
      const target = page.getByRole('tab', { name: tab }).first()
      if (!(await target.count())) continue
      await target.click()
      await page.waitForTimeout(3_000)
      await shoot(`health-check-${tab.toLowerCase().replace(' ', '-')}`, null)
    }
  },
}

for (const [name, path] of routes) {
  if (ONLY && !ONLY.has(name)) continue
  await shoot(name, path)
}

for (const [name, run] of Object.entries(flows)) {
  if (ONLY && !ONLY.has(name)) continue
  await run()
}

await browser.close()
