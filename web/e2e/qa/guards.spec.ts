/**
 * /guards: the workspace's own states (empty, loading, failure), the editor,
 * the rule tags, and deleting a guard, plus the findings left behind by the
 * retired Agents workspace, which /guards is the last surface to mention.
 */
import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, shot } from './_helpers'

const PII_GUARD = {
  name: 'pii-guard',
  description: 'Masks PII and blocks unbounded reads',
  intent: 'Protect customer data in analytical queries',
  derived: false,
  masking: { '*.email': 'email' },
  restrictions: {
    denied_columns: ['users.password'],
    allowed_tables: ['users', 'orders'],
    required_filters: { orders: ['tenant_id'] },
  },
  guards: {
    require_where: true,
    require_limit: false,
    no_select_star: true,
    max_tables: 2,
    cost_limit: null,
    max_estimated_rows: null,
  },
  limits: { max_rows: 1000, timeout_seconds: 30 },
  created_at: '2026-07-15T12:00:00Z',
}

async function clearGuards(page: Page) {
  const existing = (await (await page.request.get('/api/guards')).json()) as {
    guards: { name: string }[]
  }
  for (const guard of existing.guards) {
    await page.request.delete(`/api/guards/${guard.name}`)
  }
}

/** One configured target, an empty guard store, and optionally one guard. */
async function setup(page: Page, { seed = false } = {}) {
  setBackendFixtures()
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await clearGuards(page)
  if (seed) {
    expect(
      (await page.request.post('/api/guards', { data: PII_GUARD })).ok()
    ).toBe(true)
  }
  await page.setViewportSize(DESKTOP)
}

/** Take the console errors an injected failure causes, and only those. */
function acceptInducedErrors(browserErrors: string[]) {
  for (const message of [...new Set(browserErrors)]) {
    acceptBrowserError(browserErrors, message)
  }
  browserErrors.splice(0, browserErrors.length)
}

/**
 * F-05 - guards stays off the nav until it leaves experimental. The test is
 * held so the inbound link is written the day the hold lifts, and reads the
 * shape it will take then: one link to /guards from inside the product.
 */
test.fixme('F-05 - held: guards stays off the nav', async ({ page }) => {
  await setup(page, { seed: true })
  await page.goto('/')
  await expect(page.getByRole('link', { name: /guards/i })).toHaveCount(1)
})

/** F-17, F-18 - the empty state is the shared anatomy: heading, body, one CTA. */
test('guards: the empty state is a heading with one call to action', async ({
  page,
}) => {
  await setup(page)
  await page.goto('/guards')
  await expect(
    page.getByRole('heading', { name: 'Guards', level: 1 })
  ).toBeVisible()

  await expect(
    page.getByRole('heading', { name: 'No guards yet' })
  ).toBeVisible()
  const create = page.getByRole('button', { name: 'Create a guard' })
  await expect(create).toHaveCount(1)
  await expect(create).toBeVisible()
  // The hero primary stands down so the empty state owns the only "create".
  await expect(page.getByRole('button', { name: 'New guard' })).toHaveCount(0)
  // A verification panel with nothing to verify is withheld.
  await expect(page.getByText('Test SQL')).toHaveCount(0)
  await shot(page, 'guards/empty')
})

/**
 * F-06, F-07, F-23, F-27 - a failed list renders the shared error treatment:
 * no count asserted over an unresolved request, an alert, one retry verb, and
 * the payload behind the technical-details expander.
 */
test('guards: a failed list is the shared error surface', async ({
  page,
  browserErrors,
}) => {
  await setup(page)
  await page.route('**/api/guards', (route) =>
    route.fulfill({ status: 500, json: { detail: 'guard store unavailable' } })
  )
  await page.goto('/guards')

  await expect(page.getByText("Guards couldn't be loaded")).toBeVisible({
    timeout: 15_000,
  })
  // The header never claims a count it does not have.
  await expect(page.getByText('Guards (0)')).toHaveCount(0)
  await expect(page.getByText('Loading guards')).toHaveCount(0)
  await expect(page.getByText(/Failed to load guards/)).toHaveCount(0)

  await expect(page.locator('[role="alert"]').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  // Retry has one name everywhere.
  await expect(
    page.getByRole('button', { name: /^(Retry|Check again|Re-check)$/ })
  ).toHaveCount(0)

  await page.getByRole('button', { name: 'Technical details' }).click()
  await expect(page.getByText(/guard store unavailable/)).toBeVisible()

  acceptInducedErrors(browserErrors)
})

/** F-08, F-22 - a slow list fills its container with skeletons. */
test('guards: a slow list is skeletoned', async ({ page }) => {
  await setup(page, { seed: true })
  await page.route('**/api/guards', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 4000))
    await route.continue().catch(() => undefined)
  })
  await page.goto('/guards')

  await expect(page.locator('#skeleton').first()).toBeVisible()
  await expect(page.getByText('Loading guards')).toHaveCount(0)
  // Nothing claims the store is empty while the request is still open.
  await expect(page.getByText('No guards yet')).toHaveCount(0)
})

/**
 * F-11, F-12, F-13 - an expanded guard prints its description once and its
 * rule tags in one colour, and the editor explains why the name is locked.
 */
test('guards: expanded detail and the editor explain themselves', async ({
  page,
}) => {
  await setup(page, { seed: true })
  await page.goto('/guards')

  const row = page.getByRole('button').filter({ hasText: 'pii-guard' })
  await expect(row).toHaveCount(1)
  await row.click()
  await expect(
    page.getByText('Protect customer data in analytical queries')
  ).toBeVisible()
  // The row already carries the description; the detail does not repeat it.
  await expect(
    page.getByText('Masks PII and blocks unbounded reads', { exact: true })
  ).toHaveCount(1)

  // Rule tags and the masking tag read as one key, so a protection is never
  // coloured as a warning.
  const ruleColor = await page
    .getByText('WHERE required', { exact: true })
    .first()
    .evaluate((el) => getComputedStyle(el).color)
  const maskColor = await page
    .getByText(/^\d+ masks?$/)
    .first()
    .evaluate((el) => getComputedStyle(el).color)
  expect(maskColor).toBe(ruleColor)

  await page.getByRole('button', { name: 'Edit' }).click()
  const name = page.locator('[name="guard-name"]')
  await expect(name).toBeDisabled()
  await expect(page.getByLabel('Name', { exact: true })).toBeDisabled()
  await expect(
    page.getByText(
      "A guard's name is its identity, so it can't be changed after it is created."
    )
  ).toBeVisible()
})

/**
 * F-04, MG-14 - deleting a guard names the boundary in the product's current
 * vocabulary, asks for the guard's name to be typed back, and cancels on
 * Escape.
 */
test('guards: deleting a guard is confirmed and typed', async ({
  page,
  browserErrors,
}) => {
  // The removed row's detail request can still be in flight; F-16 covers it.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await setup(page, { seed: true })
  await page.goto('/guards')
  await page.getByRole('button').filter({ hasText: 'pii-guard' }).click()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(
    dialog.getByText('Bound agents lose this protection')
  ).toBeVisible()
  // The consequence names where agents actually run now.
  await expect(
    dialog.getByText(/the MCP server or the Slack bot/)
  ).toBeVisible()
  await shot(page, 'guards/delete-dialog')

  // Escape cancels; it never deletes.
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(
    (
      (await (await page.request.get('/api/guards')).json()) as {
        count: number
      }
    ).count
  ).toBe(1)

  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  const confirm = page.getByRole('button', { name: 'Delete guard' })
  // A security boundary is not removed by one click.
  await expect(confirm).toBeDisabled()

  await page.locator('[name="confirm-typed"]').fill('pii-guard')
  await expect(confirm).toBeEnabled()
  await confirm.click()
  await expect(page.getByText('Guard deleted', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'No guards yet' })
  ).toBeVisible()
})

/**
 * F-16 - deleting the expanded guard logs no console error: the row stops
 * asking for its detail as the delete starts, and the delete drops that detail
 * from the cache rather than inviting a refetch of a name that is gone.
 */
test('F-16 - deleting the expanded guard is quiet', async ({
  page,
  browserErrors,
}) => {
  await setup(page, { seed: true })
  await page.goto('/guards')
  await page.getByRole('button').filter({ hasText: 'pii-guard' }).click()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await page.locator('[name="confirm-typed"]').fill('pii-guard')
  await page.getByRole('button', { name: 'Delete guard' }).click()
  await expect(
    page.getByRole('heading', { name: 'No guards yet' })
  ).toBeVisible()
  expect(browserErrors).toEqual([])
})

/**
 * F-01, F-02, F-03 - the retired Agents workspace: the URL still lands
 * somewhere useful and says why, nothing links to or ships the workspace, and
 * the API it used to write is gone.
 */
test('guards: the retired Agents workspace leaves nothing behind', async ({
  page,
}) => {
  await setup(page)

  await page.goto('/agents')
  const landed = new URL(page.url())
  expect(landed.pathname).toBe('/ask')
  expect(landed.search).toBe('?from=agents')
  await expect(
    page.getByRole('heading', { name: 'Ask', level: 1 })
  ).toBeVisible()
  // The arrival is explained once, and can be dismissed.
  await expect(page.getByText('Agents was retired')).toBeVisible()
  await page.getByRole('button', { name: 'Got it' }).click()
  await expect(page.getByText('Agents was retired')).toHaveCount(0)

  // Nothing in the shipped app navigates to the workspace.
  await page.goto('/')
  await expect(page.getByRole('link', { name: /agents?/i })).toHaveCount(0)
  // Its strings are not in the production bundle.
  const bundled = await page.evaluate(async () => {
    const html = await (await fetch('/')).text()
    const scripts = [...html.matchAll(/src="([^"]+\.js)"/g)].map((m) => m[1])
    for (const src of scripts) {
      const body = await (await fetch(src)).text()
      if (body.includes('No agents yet')) return true
    }
    return false
  })
  expect(bundled).toBe(false)

  // The writable surface with no UI is retired too.
  expect((await page.request.get('/api/agents')).status()).toBe(404)
  expect(
    (await page.request.post('/api/agents', { data: { name: 'orphan' } })).ok()
  ).toBe(false)
})
