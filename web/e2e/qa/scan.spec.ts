/**
 * Code scan (/scan): the surface that is reachable by URL but deliberately kept
 * off the sidebar. Covers its navigation identity, directory entry, the failure
 * state, the run context bar, and how long paths are rendered.
 */
import type { Page } from '@playwright/test'
import {
  configureTestTarget,
  expect,
  mainContent,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, shot } from './_helpers'

test.use({ viewport: DESKTOP })

const directory = process.env.RDST_E2E_HOME ?? '/tmp/rdst-web-e2e'
const shortFile = `${directory}/src/orders.ts`
const longFile = `${directory}/services/orders/infrastructure/persistence/repositories/generated/orders-repository-implementation.ts`

function sqlQuery(file: string) {
  return {
    file,
    function: 'listOrders',
    class: 'OrderRepository',
    orm_code: 'prisma.order.findMany({ where: { status } })',
    snippet_hash: 'scan-orders-001',
    terminal_method: 'findMany',
    start_line: 42,
    end_line: 48,
    orm_type: 'prisma',
    sql: "SELECT id, customer_id, status FROM orders WHERE status = 'pending'",
    status: 'sql',
    issues: [],
    hash: 'orders-query-hash',
  }
}

const summary = {
  files_count: 1,
  queries_total: 1,
  queries_sql: 1,
  queries_skipped: 0,
  cache_hits: 0,
  cache_misses: 1,
  registry_new: 0,
  registry_updated: 0,
  registry_total: 0,
  registry_skipped: true,
}

function scanEvents(file: string) {
  return [
    { type: 'status', phase: 'discovery', message: 'Discovering ORM files...' },
    {
      type: 'files_found',
      files: [{ file, orms: ['prisma'], lines: 120 }],
      total: 1,
    },
    {
      type: 'progress',
      phase: 'extraction',
      current: 1,
      total: 1,
      message: 'Extracting queries',
    },
    { type: 'query_result', query: sqlQuery(file) },
    { type: 'complete', success: true, summary },
  ]
}

async function openScanPage(page: Page) {
  await configureTestTarget(page, { hasPassword: true })
  await page.goto('/scan')
  await expect(page.getByRole('heading', { name: 'Code scan' })).toBeVisible()
}

async function runScan(page: Page) {
  await page.getByLabel('Directory path').fill(directory)
  await page.getByRole('button', { name: 'Start scan' }).click()
}

/**
 * E-15 - a page kept out of the sidebar still has a place in the product: a
 * breadcrumb naming its parent, and copy saying why it is not in the nav.
 */
test('the page carries a navigation identity of its own', async ({ page }) => {
  setBackendFixtures()
  await openScanPage(page)

  const header = page.locator('header')
  await expect(header.getByRole('link', { name: 'Queries' })).toBeVisible()
  await expect(header.getByText('Code scan')).toBeVisible()
  await expect(
    page.getByText(/is experimental .* so it is kept out of the sidebar/)
  ).toBeVisible()
  await expect(
    page.locator('#app-sidebar').getByRole('link', { name: /scan/i })
  ).toHaveCount(0)
  await shot(page, 'scan-navigation-identity')
})

/**
 * E-16 - the directory is typed or pasted, and browsing is the second way in
 * rather than a one-click hand-over of the whole home directory.
 */
test('the directory path is typed into a labelled field', async ({ page }) => {
  setBackendFixtures()
  await openScanPage(page)

  await expect(page.getByRole('button', { name: 'Start scan' })).toBeDisabled()
  const field = page.getByLabel('Directory path')
  await expect(field).toHaveAttribute('placeholder', '/path/to/your/project')
  await expect(field).toBeEmpty()

  await field.fill(directory)
  await expect(field).toHaveValue(directory)
  await expect(page.getByRole('button', { name: 'Start scan' })).toBeEnabled()
  // Browse is offered beside the field, not in front of it.
  await expect(page.getByRole('button', { name: 'Browse' })).toBeVisible()
})

/**
 * E-43 - a failed scan is reported through the shared error state: a title, the
 * reason, and a retry, rather than a bare red status line.
 */
test('a failed scan explains itself and offers a retry', async ({ page }) => {
  setBackendFixtures({
    scan: [
      {
        events: [
          {
            type: 'error',
            message: 'Scanner crashed on orders.ts',
            phase: 'extraction',
          },
        ],
      },
    ],
  })
  await openScanPage(page)
  await runScan(page)

  await expect(
    page.getByText('The scan stopped', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Scanner crashed on orders.ts').first()
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  await expect(
    page.getByText(
      'Nothing in your code was changed, and the queries found before the failure are listed below.'
    )
  ).toBeVisible()
  // No hand-rolled "Error: <status>: <detail>" line.
  await expect(mainContent(page).getByText(/^Error: \d+:/)).toHaveCount(0)
  await shot(page, 'scan-failure')
})

/** E-42 - a long file path keeps its full text, filename included. */
test('a long file path keeps its filename', async ({ page }) => {
  setBackendFixtures({ scan: [{ events: scanEvents(longFile) }] })
  await openScanPage(page)
  await runScan(page)

  const group = page.getByTestId('scan-file-group-toggle')
  await expect(group).toBeVisible()
  await expect(group).toHaveAttribute('data-file', longFile)
  await expect(group).toContainText('orders-repository-implementation.ts')
  // The path is rendered whole; nothing replaces its middle with an ellipsis.
  const rendered = (await group.textContent()) ?? ''
  expect(rendered).toContain(longFile)
})

/**
 * E-44 - the context bar tag repeats the switch that turned the option on, so
 * the filters and the bar name the same thing.
 */
test('E-44 - the context bar names the analysis option like its neighbours', async ({
  page,
}) => {
  setBackendFixtures({ scan: [{ events: scanEvents(shortFile) }] })
  await openScanPage(page)
  await runScan(page)

  await expect(page.getByText(directory, { exact: true })).toBeVisible()
  await expect(
    mainContent(page).getByText('analyze', { exact: true })
  ).toHaveCount(0)
})
