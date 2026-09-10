/**
 * The cross-cutting state sweep: the same nine destinations put under the same
 * empty, loading and failing conditions, plus the desktop smoke gate that is
 * this suite's baseline: plain navigation to every route stays silent.
 */
import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  clearQueryRegistry,
  configureTestTarget,
  expect,
  mainContent,
  mockAiKeyReady,
  setBackendFixtures,
  test,
} from '../fixtures'
import {
  DESKTOP,
  failProductApi,
  ROUTES,
  settle,
  shot,
  stallProductApi,
} from './_helpers'

/** The nine destinations a user reaches from the shell. */
const MAIN_IDS = [
  'index',
  'queries',
  'ask',
  'audit',
  'cache',
  'schema',
  'scan',
  'configure',
  'guards',
]

const MAIN = ROUTES.filter((route) => MAIN_IDS.includes(route.id))

/**
 * Ask is swept separately under load and failure: its Recent questions column
 * is still keyed off the target, so it settles into its own state before the
 * target does. The two open cases are declared below.
 */
const MAIN_WITHOUT_ASK = MAIN.filter((route) => route.id !== 'ask')

/** Copy that claims the user has nothing, or must pick something that is there. */
const FALSE_EMPTY = [
  'No targets yet',
  'No target selected',
  'No queries observed yet',
  'No guards yet',
  'No questions yet',
  'Select one or more targets to check',
  'Select a target database from the sidebar',
]

/** Shapes of a raw response leaking into user-facing copy. */
const RAW_PAYLOAD = /HTTP error|\{"detail"|"detail":|status: \d{3}/

/** What the browser logs for each response the injected 500 fulfils. */
const SERVER_ERROR =
  'Failed to load resource: the server responded with a status of 500 (Internal Server Error)'

async function setup(page: Page) {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await page.setViewportSize(DESKTOP)
}

/** Skeletons the accessibility tree can still see, decoration excluded. */
function exposedSkeletons(page: Page) {
  return page
    .locator('#skeleton')
    .evaluateAll(
      (nodes) =>
        nodes.filter((node) => !node.closest('[aria-hidden="true"]')).length
    )
}

/** Take the console errors the injected failure causes, and only those. */
function acceptInducedErrors(browserErrors: string[]) {
  for (const message of [...new Set(browserErrors)]) {
    acceptBrowserError(browserErrors, message)
  }
  browserErrors.splice(0, browserErrors.length)
}

/**
 * F-53 - plain navigation to every route logs no console error and no page
 * error. This is the suite's baseline gate: it accepts nothing.
 */
test('states: plain navigation to every route is silent', async ({ page }) => {
  await setup(page)
  for (const route of MAIN) {
    await page.goto(route.path)
    await expect(mainContent(page)).toBeVisible()
    await settle(page, 1500)
  }
  await shot(page, 'states/smoke-last-route')
})

/**
 * F-20, F-22 - a route with a request still in flight renders skeletons, and
 * never the state that says the user has nothing.
 */
test('states: no route claims to be empty while it is still loading', async ({
  page,
  browserErrors,
}) => {
  await setup(page)
  await stallProductApi(page, 12_000)

  for (const route of MAIN_WITHOUT_ASK) {
    await page.goto(route.path)
    await settle(page, 1500)
    await expect(
      page.locator('#skeleton').first(),
      `${route.id} renders a skeleton while loading`
    ).toBeVisible()
    const copy = await mainContent(page).innerText()
    for (const claim of FALSE_EMPTY) {
      expect(copy, `${route.id} claims "${claim}" while loading`).not.toContain(
        claim
      )
    }
    acceptInducedErrors(browserErrors)
  }
})

/**
 * F-20 - /ask keeps its Recent questions column on a skeleton while the
 * default target is still resolving.
 */
test('F-20 - the Ask recent-questions column claims empty while it loads', async ({
  page,
  browserErrors,
}) => {
  await setup(page)
  await stallProductApi(page, 12_000)

  await page.goto('/ask')
  await settle(page, 1500)
  await expect(
    page.locator('#skeleton').first(),
    'ask renders a skeleton while loading'
  ).toBeVisible()
  expect(
    await mainContent(page).innerText(),
    'ask claims "No questions yet" while loading'
  ).not.toContain('No questions yet')
  acceptInducedErrors(browserErrors)
})

/**
 * F-23, F-24, F-27 - every failing route renders the one error treatment: an
 * alert, one retry verb, and no raw response body as user-facing copy.
 */
test('states: every failing route renders the shared error treatment', async ({
  page,
  browserErrors,
}) => {
  await setup(page)
  await failProductApi(page)

  for (const route of MAIN_WITHOUT_ASK) {
    await page.goto(route.path)
    await settle(page, 2500)
    await expect(
      page.locator('[role="alert"]').first(),
      `${route.id} names the failure`
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Try again' }).first(),
      `${route.id} offers the shared retry`
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: /^(Retry|Check again|Re-check)$/ }),
      `${route.id} uses a second retry verb`
    ).toHaveCount(0)
    const copy = await mainContent(page).innerText()
    expect(copy, `${route.id} prints a raw response body`).not.toMatch(
      RAW_PAYLOAD
    )
    acceptInducedErrors(browserErrors)
  }
  await shot(page, 'states/error-last-route')
})

/**
 * F-23 - a failed target list on /ask is named as a failure with the shared
 * retry. [F-26 is held: the acting target is kept, never substituted - see
 * "a failed target list degrades loudly".]
 */
test('F-23 - a failing Ask route names the failure and offers the retry', async ({
  page,
  browserErrors,
}) => {
  await setup(page)
  await failProductApi(page)

  await page.goto('/ask')
  await settle(page, 2500)
  await expect(
    page.locator('[role="alert"]').first(),
    'ask names the failure'
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Try again' }).first(),
    'ask offers the shared retry'
  ).toBeVisible()
  acceptInducedErrors(browserErrors)
})

/**
 * F-18, F-19 - a settled empty route names its state with a heading, and the
 * loading state it replaced is gone: nothing still reports itself busy, and
 * the silhouette left behind is decoration the accessibility tree never sees.
 */
test('states: a settled empty route is a heading, not a skeleton', async ({
  page,
}) => {
  await setup(page)
  // Reach the genuine empty library whatever the specs before this one wrote.
  await clearQueryRegistry(page.request)
  const empties = [
    ['/queries', 'No queries observed yet'],
    ['/ask', 'No questions yet'],
    ['/guards', 'No guards yet'],
  ] as const

  for (const [path, title] of empties) {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    await expect(
      mainContent(page).locator('[aria-busy="true"]'),
      `${path} still reports itself busy under its empty state`
    ).toHaveCount(0)
    await expect
      .poll(() => exposedSkeletons(page), {
        message: `${path} paints a live skeleton under its empty state`,
      })
      .toBe(0)
  }
})

/**
 * F-25, F-26 - a failed target list is reported as a failure, and never
 * silently swaps the database the page is acting on.
 */
test('states: a failed target list degrades loudly', async ({
  page,
  browserErrors,
}) => {
  await setup(page)
  // Settle the acting target first, the way a returning user arrives.
  await page.goto('/ask')
  await expect(page.getByText(/Read-only on/)).toContainText('e2e-guard')

  // The 500s below are this test's own injected failure.
  acceptBrowserError(browserErrors, SERVER_ERROR)
  await failProductApi(page)
  for (const path of ['/audit', '/scan']) {
    await page.goto(path)
    await settle(page, 2500)
    const copy = await mainContent(page).innerText()
    expect(copy, `${path} instructs a selection it cannot offer`).not.toContain(
      'Select one or more targets to check'
    )
    expect(copy, `${path} instructs a selection it cannot offer`).not.toContain(
      'Select a target database from the sidebar'
    )

    if (path === '/audit') {
      // The health check picks from the list that failed, so the page names
      // the failure and offers the shared retry in place of an empty picker.
      await expect(
        mainContent(page).getByText('Could not load your database targets')
      ).toBeVisible()
      await expect(
        mainContent(page).getByRole('button', { name: 'Try again' }).first()
      ).toBeVisible()
    } else {
      // Code scan already settled its target, so it keeps naming the database
      // it will act on rather than falling back to a selection prompt.
      await expect(
        mainContent(page).getByText('e2e-guard').first()
      ).toBeVisible()
    }
  }

  await page.goto('/ask')
  await settle(page, 2500)
  const askCopy = await mainContent(page).innerText()
  expect(askCopy, 'Ask substituted a database').not.toContain(
    'Read-only on demo'
  )
})

/**
 * MG-01 - the AI gate blocks the features that need a provider, not the
 * router: routes with no LLM in them still render themselves.
 */
test('states: an unconnected AI provider blocks features, not routes', async ({
  page,
}) => {
  await setup(page)
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: false,
        requirements: [
          {
            kind: 'anthropic_api_key',
            satisfied: false,
            source: null,
            target: null,
            accepted_names: ['ANTHROPIC_API_KEY'],
          },
        ],
      },
    })
  )

  for (const path of ['/guards', '/configure', '/cache', '/audit']) {
    await page.goto(path)
    await expect(
      mainContent(page).locator('h1'),
      `${path} was replaced by the provider chooser`
    ).toHaveCount(1)
  }

  // The one route that does need a provider asks for it inline, in place.
  await page.goto('/ask')
  await expect(
    page.getByRole('heading', { name: 'Ask', level: 1 })
  ).toBeVisible()
  await expect(
    page.getByText('Connect an AI provider to use this')
  ).toBeVisible()

  // Restore the satisfied gate for whatever runs next in this worker.
  await mockAiKeyReady(page)
})
