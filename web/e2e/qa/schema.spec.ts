/**
 * Schema (/schema) - the semantic-layer explorer.
 *
 * Consolidated from the recorded audit flows: search and progressive reveal,
 * the tablist's ARIA contract, row density, the edit affordance, and the
 * loading and empty states the explorer falls back to.
 */
import {
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, shot } from './_helpers'

test.use({ viewport: DESKTOP })

const SEARCH_PLACEHOLDER = 'Search tables and columns'

const smallLayer = {
  tables: {
    customers: {
      description: 'Customer accounts and contact details',
      business_context: 'One row per customer account.',
      row_estimate: '1200',
      columns: {
        id: { type: 'bigint', description: 'Primary customer identifier' },
        email: {
          type: 'text',
          description: 'Primary contact email',
          is_pii: true,
        },
      },
      relationships: [
        {
          target_table: 'orders',
          relationship_type: 'one_to_many',
          join_pattern: 'customers.id = orders.customer_id',
        },
      ],
    },
    orders: {
      description: 'Order headers',
      columns: {
        id: { type: 'bigint', description: 'Order id' },
        total: { type: 'numeric', description: 'Order total' },
      },
      relationships: [],
    },
  },
  terminology: {
    'active customer': {
      definition: 'A customer with an order in the last 30 days',
      sql_pattern: "last_order_at > NOW() - INTERVAL '30 days'",
      synonyms: ['recent customer'],
    },
  },
  metrics: {
    'customer revenue': {
      definition: 'Total completed order revenue',
      sql: 'SUM(orders.total)',
    },
  },
}

const emptyLayer = { tables: {}, terminology: {}, metrics: {} }

/** A schema large enough to expose the explorer's scaling behaviour. */
function largeLayer(tableCount: number) {
  const tables: Record<string, unknown> = {}
  for (let index = 0; index < tableCount; index += 1) {
    const name = `analytics_fact_table_${String(index).padStart(3, '0')}`
    const columns: Record<string, unknown> = {}
    for (let column = 0; column < 12; column += 1) {
      columns[`column_${column}`] = {
        type: 'text',
        description: `Column ${column} of ${name}`,
      }
    }
    tables[name] = {
      description: `Generated fact table ${index}`,
      columns,
      relationships: [],
    }
  }
  return { tables, terminology: {}, metrics: {} }
}

function initFixture(layer: unknown) {
  const tables = (layer as { tables: Record<string, unknown> }).tables
  return {
    semantic_init: [
      {
        layer: layer as Record<string, unknown>,
        events: [
          {
            type: 'complete',
            operation: 'init',
            success: true,
            init_result: {
              success: true,
              target: 'e2e-guard',
              tables: Object.keys(tables).length,
              columns: 2,
              relationships: 1,
              enum_columns: [],
              path: '/tmp/e2e-guard-schema.yaml',
              error: null,
            },
          },
        ],
      },
    ],
  }
}

/** One configured target and no layer, so first use is reachable. */
async function prepareSchema(
  page: Parameters<typeof configureTestTarget>[0],
  layer: unknown
) {
  setBackendFixtures(initFixture(layer))
  await configureTestTarget(page, { hasPassword: true })
  await page.request.delete('/api/semantic-layer?target=e2e-guard')
}

/** Initializes the layer from the fixture and waits for the explorer. */
async function initialize(page: Parameters<typeof configureTestTarget>[0]) {
  await page.goto('/schema')
  await page.getByRole('button', { name: 'Initialize schema' }).click()
  await expect(page.getByPlaceholder(SEARCH_PLACEHOLDER)).toBeVisible({
    timeout: 15_000,
  })
}

/** The collapsed table rows the explorer currently paints. */
const TABLE_ROW = '#main-content [role="tabpanel"] .divide-y > div > button'

function tableRows(page: Parameters<typeof configureTestTarget>[0]) {
  return page.locator(TABLE_ROW)
}

/**
 * MG-07 (was C-64), C-66, C-60 - first use is one sentence and one action,
 * and the explorer it opens can be searched.
 */
test('first use offers one action and the explorer it opens can be searched', async ({
  page,
}) => {
  await prepareSchema(page, smallLayer)

  await page.goto('/schema')
  await expect(page.getByRole('heading', { name: 'Schema' })).toBeVisible()
  // The shared empty-state anatomy, written in sentence case.
  await expect(page.getByText('No semantic layer yet')).toBeVisible()
  await page.getByRole('button', { name: 'Initialize schema' }).click()

  const search = page.getByPlaceholder(SEARCH_PLACEHOLDER)
  await expect(search).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('2 tables', { exact: true })).toBeVisible()

  // A table-name query narrows the list and says how far it narrowed it.
  await search.fill('orders')
  await expect(page.getByText('1 of 2 tables', { exact: true })).toBeVisible()
  await expect(page.getByText('customers', { exact: true })).toHaveCount(0)

  // A column-level hit opens the table that matched, showing why.
  await search.fill('contact email')
  await expect(page.getByText('Primary contact email')).toBeVisible()

  // No match lands on the shared empty state, with a way back.
  await search.fill('zzz-nothing')
  await expect(page.getByText('No tables match "zzz-nothing"')).toBeVisible()
  await page.getByRole('button', { name: 'Show all tables' }).click()
  await expect(page.getByText('2 tables', { exact: true })).toBeVisible()
  await shot(page, 'schema-search')
})

/** C-60 - "/" reaches the schema search from the page, and Escape clears it. */
test('the slash key focuses the schema search', async ({ page }) => {
  await prepareSchema(page, smallLayer)
  await initialize(page)

  await page.keyboard.press('/')
  const focused = await page.evaluate(() =>
    document.activeElement?.getAttribute('placeholder')
  )
  expect(focused).toBe(SEARCH_PLACEHOLDER)

  await page.keyboard.type('orders')
  await expect(page.getByText('1 of 2 tables', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByText('2 tables', { exact: true })).toBeVisible()
})

/**
 * C-60, C-61 - a 120-table layer reveals one page at a time, at a density
 * worth scanning.
 */
test('a 120-table layer reveals one page at a time and stays scannable', async ({
  page,
}) => {
  await prepareSchema(page, largeLayer(120))
  await initialize(page)
  await expect(
    page.getByText('analytics_fact_table_000', { exact: true })
  ).toBeVisible()

  // C-60: one page of rows, with search as the way to the rest.
  const named = await tableRows(page).evaluateAll(
    (rows) =>
      rows.filter((row) => /analytics_fact_table_/.test(row.textContent || ''))
        .length
  )
  expect(named).toBe(40)

  // C-61: a collapsed row is one line, so a real schema fits on a screen.
  const heights = await tableRows(page).evaluateAll((rows) =>
    rows
      .filter((row) => /analytics_fact_table_/.test(row.textContent || ''))
      .slice(0, 10)
      .map((row) => Math.round(row.getBoundingClientRect().height))
  )
  expect(heights).toHaveLength(10)
  expect(Math.max(...heights)).toBeLessThan(56)

  await page.getByPlaceholder(SEARCH_PLACEHOLDER).fill('table_117')
  await expect(page.getByText('1 of 120 tables', { exact: true })).toBeVisible()
  await expect(
    page.getByText('analytics_fact_table_117', { exact: true })
  ).toBeVisible()
  await shot(page, 'schema-large')
})

/** C-62 - the schema tabs implement the full ARIA tablist contract. */
test('the schema tabs carry the whole ARIA tablist contract', async ({
  page,
}) => {
  await prepareSchema(page, smallLayer)
  await initialize(page)

  const tabs = await page.evaluate(() => {
    const list = document.querySelector(
      '[role="tablist"][aria-label="Semantic layer sections"]'
    )
    if (!list) return null
    return {
      items: [...list.querySelectorAll('[role="tab"]')].map((tab) => ({
        text: (tab.textContent || '').trim(),
        ariaControls: tab.getAttribute('aria-controls'),
        tabIndex: (tab as HTMLElement).tabIndex,
        ariaSelected: tab.getAttribute('aria-selected'),
      })),
      panels: document.querySelectorAll('#main-content [role="tabpanel"]')
        .length,
    }
  })
  expect(tabs).not.toBeNull()
  expect(tabs!.items).toHaveLength(3)
  expect(tabs!.panels).toBe(1)
  expect(tabs!.items.every((tab) => !!tab.ariaControls)).toBe(true)
  // A roving tabindex: the tablist is one tab stop.
  expect(tabs!.items.filter((tab) => tab.tabIndex === 0)).toHaveLength(1)
  expect(tabs!.items.filter((tab) => tab.ariaSelected === 'true')).toHaveLength(
    1
  )

  // The arrow keys move between the tabs inside the list.
  await page.getByRole('tab', { name: /Tables/ }).focus()
  await page.keyboard.press('ArrowRight')
  const afterArrow = await page.evaluate(() =>
    (document.activeElement?.textContent || '').trim()
  )
  expect(afterArrow).toContain('Terminology')
  await expect(page.getByText('active customer')).toBeVisible()
})

/**
 * C-63 - the edit affordance on a table row exists without a pointer over
 * it.
 */
test('a table row shows it is editable before the pointer reaches it', async ({
  page,
}) => {
  await prepareSchema(page, smallLayer)
  await initialize(page)

  const affordance = await page.evaluate((selector) => {
    const row = document.querySelector(selector)
    const glyph = row?.querySelector('use[href$="#edit"]')?.parentElement
    if (!glyph) return null
    return {
      opacity: Number(getComputedStyle(glyph).opacity),
      width: Math.round(glyph.getBoundingClientRect().width),
    }
  }, TABLE_ROW)
  expect(affordance).not.toBeNull()
  expect(affordance!.width).toBeGreaterThan(0)
  expect(affordance!.opacity).toBeGreaterThanOrEqual(0.5)
})

/** C-71 - the schema loading state is a skeleton shaped like the table list. */
test('the schema loading state is a skeleton in the shape of the list', async ({
  page,
}) => {
  setBackendFixtures(initFixture(smallLayer))
  await configureTestTarget(page, { hasPassword: true })
  await page.route('**/api/semantic-layer/status*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 4000))
    await route.continue().catch(() => undefined)
  })

  await page.goto('/schema')
  const busy = page.locator('#main-content [aria-busy="true"]')
  await expect(busy).toBeVisible()
  await expect(busy.getByText('Loading semantic layer')).toBeAttached()
  // A search bar and several rows stand in for the list that is coming, so
  // the page does not jump when the layer lands.
  expect(await busy.locator('.animate-pulse').count()).toBeGreaterThanOrEqual(8)
})

/**
 * C-75 - a layer that holds no tables offers the way to re-run
 * introspection.
 */
test('a layer with no tables offers a way to refresh the structure', async ({
  page,
}) => {
  await prepareSchema(page, emptyLayer)

  await page.goto('/schema')
  await page.getByRole('button', { name: 'Initialize schema' }).click()
  await expect(page.getByText('No tables in the semantic layer')).toBeVisible({
    timeout: 15_000,
  })
  await expect(
    page.getByRole('button', { name: 'Refresh structure' })
  ).toBeVisible()
})
