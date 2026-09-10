/**
 * The Queries library: what a card says while it is idle, running and already
 * analyzed, which of its actions is the primary one, and the toolbar that
 * finds a query again.
 */
import type { Page } from '@playwright/test'
import {
  acceptExplainAnalyzeConsent,
  expect,
  fillCodeMirror,
  mainContent,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  DESKTOP,
  mockAnalyzedQuery,
  prepareQueries,
  readySandbox,
  shot,
  slowAnalyzeEvents,
  solidActionLabels,
  successEvents,
} from './_helpers'

test.use({ viewport: DESKTOP })

// Literal-free, so the registry keeps the SQL as written and no card sits
// behind the parameter form.
const ORDERS = 'SELECT id, total FROM orders ORDER BY created_at DESC'
const USERS = 'SELECT email FROM users ORDER BY id'

/** Clean registry, one configured target, consent given, queries registered. */
async function prepare(page: Page, queries: string[] = []) {
  await acceptExplainAnalyzeConsent(page)
  const hashes = await prepareQueries(page, queries)
  await mockConnectivityOk(page)
  return hashes
}

function cardFor(page: Page, hash: string) {
  return page.locator(
    `[data-testid="query-registry-row"][data-query-hash="${hash}"]`
  )
}

function backgroundOf(page: Page, hash: string) {
  return cardFor(page, hash).evaluate(
    (el) => getComputedStyle(el).backgroundColor
  )
}

/**
 * MG-08, MG-16 - Analyze is the card's one solid action, and the measurement
 * beside it carries the single name the product settled on.
 */
test('the card action row leads with Analyze', async ({ page }) => {
  setBackendFixtures({
    analyze: [{ events: successEvents, repeat: true }],
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
  })
  await prepare(page, [ORDERS, USERS])

  await page.goto('/queries')
  const cards = page.getByTestId('query-registry-row')
  await expect(cards).toHaveCount(2)
  await expect(
    cards.first().getByRole('button', { name: 'Compare against Readyset' })
  ).toBeVisible()
  await shot(page, 'queries-card-hierarchy')

  for (let index = 0; index < 2; index += 1) {
    expect(await solidActionLabels(cards.nth(index))).toEqual(['Analyze'])
  }

  const main = await mainContent(page).innerText()
  expect(main).toContain('Compare against Readyset')
  expect(main).not.toContain('Compare & test')
  expect(main).not.toContain('Run benchmark')
})

/**
 * B-11 - an analyzed card reads as analyzed while scanning: the outcome and
 * its age are text on the card, and its own action names what comes next.
 */
test('an analyzed card is distinct from a fresh one', async ({ page }) => {
  setBackendFixtures({
    analyze: [{ events: successEvents, repeat: true }],
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
  })
  const [analyzedHash, freshHash] = await prepare(page, [ORDERS, USERS])
  await mockAnalyzedQuery(page, analyzedHash)

  await page.goto('/queries')
  await expect(page.getByTestId('query-registry-row')).toHaveCount(2)
  const analyzed = cardFor(page, analyzedHash)
  const fresh = cardFor(page, freshHash)

  // The age is on the card, so it survives both scanning and touch.
  await expect(analyzed.getByText(/^Analyzed /)).toBeVisible()
  await expect(analyzed.getByText('Poor · 32/100')).toBeVisible()
  await shot(page, 'queries-analyzed-vs-fresh')

  const [analyzedEdge, freshEdge] = await Promise.all(
    [analyzed, fresh].map((card) =>
      card.evaluate((el) => {
        const style = getComputedStyle(el)
        return `${style.borderLeftColor}/${style.borderLeftWidth}`
      })
    )
  )
  expect(analyzedEdge).not.toBe(freshEdge)

  expect(await solidActionLabels(analyzed)).toEqual(['View analysis'])
  expect(await solidActionLabels(fresh)).toEqual(['Analyze'])
})

/**
 * B-06 - a run in flight is a state of the whole card: the action that would
 * measure it again is shut, the tone changes, and the card is the way back.
 */
test('a card carries its analysis while the run is in flight', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [{ events: slowAnalyzeEvents(6000), repeat: true }],
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
  })
  const [runningHash, idleHash] = await prepare(page, [ORDERS, USERS])

  await page.goto(`/queries?analyze=${runningHash}`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('Analyzing', { exact: true })).toBeVisible({
    timeout: 15_000,
  })

  // The run outlives the drawer it was started from.
  await drawer.getByRole('button', { name: 'Close' }).click()
  await expect(drawer).toHaveCount(0)

  const running = cardFor(page, runningHash)
  const analyzing = running.getByRole('button', { name: 'Analyzing' })
  await expect(analyzing).toBeVisible({ timeout: 10_000 })
  expect(await analyzing.isDisabled()).toBe(true)
  await expect(running.getByLabel('Analysis in progress')).toBeVisible()
  await expect(
    running.getByRole('button', { name: 'View progress' })
  ).toBeVisible()
  await shot(page, 'queries-running-card')

  // A second measurement of the same query is not one click away.
  expect(
    await running
      .getByRole('button', { name: 'Compare against Readyset' })
      .isDisabled()
  ).toBe(true)

  const [runningBg, idleBg] = await Promise.all([
    backgroundOf(page, runningHash),
    backgroundOf(page, idleHash),
  ])
  expect(runningBg).not.toBe(idleBg)
})

/**
 * B-03 - a query typed into Add query keeps the SQL the user wrote, and the
 * meta line agrees with the block above it.
 */
test('a hand-typed query keeps its own SQL', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  await prepare(page)

  await page.goto('/queries')
  await page.getByRole('button', { name: 'Add query' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await fillCodeMirror(dialog, 'SELECT 1 FROM revealed_table')
  await dialog.getByRole('button', { name: 'Add query' }).click()

  const card = page.getByTestId('query-registry-row').first()
  await expect(card).toBeVisible()
  const text = (await card.innerText()).replace(/\s+/g, ' ')
  expect(text).toContain('SELECT 1 FROM revealed_table')
  expect(text).not.toContain(':p1')
  expect(text).toContain('no parameters')
})

/**
 * B-26 - the toolbar's sort, search and star filter each carry a name, and a
 * search that matches nothing offers its own way back.
 */
test('sort, search and the star filter are named and recoverable', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [{ events: successEvents, repeat: true }],
    sandbox_diagnostics: [{ value: readySandbox, repeat: true }],
  })
  const [ordersHash, usersHash] = await prepare(page, [ORDERS, USERS])

  await page.goto('/queries')
  const cards = page.getByTestId('query-registry-row')
  await expect(cards).toHaveCount(2)

  const sort = page.getByRole('combobox', { name: 'Order queries' })
  await expect(sort).toBeVisible()
  await sort.click()
  await expect(
    page.getByRole('option', { name: 'Highest impact' })
  ).toBeVisible()
  await expect(
    page.getByRole('option', { name: 'Recently analyzed' })
  ).toBeVisible()
  await page.keyboard.press('Escape')

  const search = page.getByLabel('Search by name, hash, or SQL')
  await search.fill('zzz-no-such-query')
  await expect(
    page.getByRole('heading', { name: 'No queries match this search' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Clear search' }).first().click()
  await expect(cards).toHaveCount(2)

  // Every added query arrives starred, so the shortlist only narrows once one
  // of them is unmarked.
  const star = cardFor(page, ordersHash).getByTestId('query-star-toggle')
  await star.click()
  await expect(star).toHaveAttribute('aria-pressed', 'false')

  await page.getByRole('button', { name: 'Filter' }).click()
  await page.getByRole('button', { name: 'Starred only' }).click()
  await page.keyboard.press('Escape')

  await expect(cards).toHaveCount(1)
  await expect(cardFor(page, usersHash)).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Remove Starred filter' })
  ).toBeVisible()
})

/**
 * B-20 - the star toggle keeps one accessible name whichever way it is set,
 * and carries the state in aria-pressed.
 */
test('B-20 - the star toggle keeps one name', async ({ page }) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [ORDERS])

  await page.goto('/queries')
  const star = cardFor(page, hash).getByTestId('query-star-toggle')
  await expect(star).toHaveAttribute('aria-pressed', 'true')
  await expect(star).toHaveAccessibleName('Star this query')

  await star.click()
  await expect(star).toHaveAttribute('aria-pressed', 'false')
  await expect(star).toHaveAccessibleName('Star this query')
})

/**
 * B-18 - a query the captured workload has never run says it has no
 * measurement, rather than quoting a zero as one.
 */
test('B-18 - an un-observed card states that it has no measurement', async ({
  page,
}) => {
  setBackendFixtures({ analyze: [{ events: successEvents, repeat: true }] })
  const [hash] = await prepare(page, [ORDERS])

  await page.goto('/queries')
  const card = cardFor(page, hash)
  await expect(card).toBeVisible()
  await expect(card.getByText('Not yet observed')).toBeVisible()
  expect(await card.innerText()).not.toContain('0ms')
})
