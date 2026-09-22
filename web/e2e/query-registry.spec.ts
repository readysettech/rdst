import {
  acceptExplainAnalyzeConsent,
  clearQueryRegistry,
  configureTestTarget,
  expect,
  fillCodeMirror,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from './fixtures'

function jevResponse(score: number, choice = 'possible_concern') {
  const probabilities = {
    no_evidence: choice === 'no_evidence' ? 1 : 0,
    possible_concern: choice === 'possible_concern' ? 1 : 0,
    strong_concern: choice === 'strong_concern' ? 1 : 0,
    insufficient_context: choice === 'insufficient_context' ? 1 : 0,
  }
  const answer = {
    type: 'choice',
    choice,
    probabilities,
    confidence: 0.82,
  }
  return {
    model: 'jev-1.13.0',
    rubric_version: 'query-quick-assessment-v2',
    answers: {
      access_expression_risk: answer,
      index_coverage: answer,
      join_growth: answer,
      repeated_work: answer,
      broad_work: answer,
      priority: {
        type: 'score',
        score,
        legend: {
          '0': 'low',
          '1': 'one',
          '2': 'two',
          '3': 'three',
          '4': 'high',
        },
        probabilities: { '0': 0, '1': 0, '2': 0, '3': 1, '4': 0 },
        confidence: 0.76,
      },
    },
    usage: { input_tokens: 850, output_tokens: 20 },
  }
}

test('shows persisted Jev assessments and applies server priority sorting', async ({
  page,
}) => {
  setBackendFixtures({
    jev_assessment: [
      { value: jevResponse(3.2), delay_ms: 300, match_sql: 'FROM orders' },
      { value: jevResponse(0.8, 'no_evidence'), match_sql: 'FROM customers' },
    ],
  })
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  const low = await page.request.post('/api/query-registry', {
    data: {
      sql: 'SELECT id FROM customers WHERE email = 1',
      target: 'e2e-guard',
    },
  })
  const high = await page.request.post('/api/query-registry', {
    data: {
      sql: 'SELECT * FROM orders ORDER BY created_at',
      target: 'e2e-guard',
    },
  })
  expect(low.ok()).toBe(true)
  expect(high.ok()).toBe(true)

  await page.goto('/queries')
  await expect(page.getByText('Jev · High priority')).toBeVisible({
    timeout: 10_000,
  })
  await expect(page.getByText('Jev · Low priority')).toBeVisible({
    timeout: 10_000,
  })

  const highRow = page
    .getByTestId('query-registry-row')
    .filter({ hasText: 'Jev · High priority' })
  await highRow.getByRole('button', { name: 'View Jev results' }).click()
  await expect(page.getByText(/Jev classified this query/)).toBeVisible()
  await expect(page.getByText(/Schema context:/)).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Deep analyze this query' })
  ).toBeVisible()

  await page.getByRole('button', { name: 'Order queries' }).click()
  await page.getByRole('menuitem', { name: 'Jev' }).click()
  await page.getByRole('menuitem', { name: 'Review priority' }).click()
  await expect(page).toHaveURL(/sort=jev-priority/)
  const rows = page.getByTestId('query-registry-row')
  await expect(rows.first().locator('code[title]')).toHaveAttribute(
    'title',
    'SELECT * FROM orders ORDER BY created_at'
  )
})

test('orders the registry by the Jev concern shown on the card', async ({
  page,
}) => {
  setBackendFixtures({
    jev_assessment: [
      { value: jevResponse(3.2, 'strong_concern'), match_sql: 'FROM orders' },
      { value: jevResponse(0.8, 'no_evidence'), match_sql: 'FROM customers' },
    ],
  })
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await page.request.post('/api/query-registry', {
    data: {
      sql: 'SELECT id FROM customers WHERE email = 1',
      target: 'e2e-guard',
    },
  })
  await page.request.post('/api/query-registry', {
    data: {
      sql: 'SELECT * FROM orders ORDER BY created_at',
      target: 'e2e-guard',
    },
  })

  await page.goto('/queries')
  await expect(page.getByText('Jev · High priority')).toBeVisible({
    timeout: 10_000,
  })
  await expect(page.getByText('Jev · Low priority')).toBeVisible({
    timeout: 10_000,
  })

  // The concern reads off the collapsed card, before any disclosure opens.
  const rows = page.getByTestId('query-registry-row')
  const flaggedRow = rows.filter({ hasText: 'Jev · High priority' })
  await expect(flaggedRow.getByText('Missing index')).toBeVisible()

  await page.getByRole('button', { name: 'Order queries' }).click()
  await page.getByRole('menuitem', { name: 'Jev' }).click()
  await page.getByRole('menuitem', { name: 'Missing index' }).click()
  await expect(page).toHaveURL(/sort=jev-index_coverage/)
  await expect(rows.first().locator('code[title]')).toHaveAttribute(
    'title',
    'SELECT * FROM orders ORDER BY created_at'
  )
})

test('keeps the sort trigger clear of the result count at every width', async ({
  page,
}) => {
  setBackendFixtures({ jev_assessment: [{ value: jevResponse(3.2) }] })
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await page.request.post('/api/query-registry', {
    data: {
      sql: 'SELECT * FROM orders ORDER BY created_at',
      target: 'e2e-guard',
    },
  })
  await page.goto('/queries')

  // The longest label the control can show.
  await page.getByRole('button', { name: 'Order queries' }).click()
  await page.getByRole('menuitem', { name: 'Jev' }).click()
  await page.getByRole('menuitem', { name: 'Missing index' }).click()
  await expect(page).toHaveURL(/sort=jev-index_coverage/)

  const trigger = page.getByTestId('query-library-sort-trigger')
  const count = page.getByTestId('query-library-result-count')
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.waitForTimeout(300)
    await expect(trigger).toBeVisible()
    await expect(count).toBeVisible()
    const a = await trigger.boundingBox()
    const b = await count.boundingBox()
    expect(a, `trigger box at ${width}`).not.toBeNull()
    expect(b, `count box at ${width}`).not.toBeNull()
    if (!a || !b) continue
    const overlaps =
      a.x < b.x + b.width &&
      b.x < a.x + a.width &&
      a.y < b.y + b.height &&
      b.y < a.y + a.height
    expect(overlaps, `sort trigger overlaps the count at ${width}`).toBe(false)
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth
      ),
      `horizontal overflow at ${width}`
    ).toBe(0)
  }
})

test('creates, renames, edits, searches, analyzes, and deletes a saved query', async ({
  page,
}) => {
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'complete',
            success: true,
            analysis_id: 'query-registry-e2e',
            query_hash: 'query-registry-e2e',
          },
        ],
        repeat: true,
      },
    ],
  })
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  // Analyze measures the query in the drawer over the library, and its full
  // view preflights target reachability before POST /api/analyze.
  await mockConnectivityOk(page)
  await acceptExplainAnalyzeConsent(page)

  const initialSql = 'SELECT id FROM users'
  const updatedSql = 'SELECT id, email FROM users ORDER BY id'

  await page.goto('/query-registry')
  await expect(
    page.getByRole('heading', { name: 'Queries', exact: true })
  ).toBeVisible()
  // An empty library with no filter applied says nothing has been seen yet;
  // the filtered-out copy belongs to a filter the user can clear.
  await expect(
    page.getByRole('heading', { name: 'No queries observed yet' })
  ).toBeVisible()

  // The empty state offers the same action as the header, so take the header
  // primary rather than either of the two matches.
  await page.getByRole('button', { name: 'Add query' }).first().click()
  await fillCodeMirror(page.locator('.cm-editor').first(), initialSql)
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Add query' })
    .click()

  let queryRow = page.getByTestId('query-registry-row')
  await expect(queryRow).toHaveCount(1)
  // Unnamed queries now show a derived readable name (verb · table), not the
  // literal "(unnamed)" [C-09 saved-queries migration].
  await expect(queryRow).toContainText('Select · users')

  // Row management actions live in the always-visible ⋯ overflow menu now
  // (Rename / Edit SQL / Delete), with Analyze the one visible button.
  await queryRow.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await queryRow.locator('[name^="edit-tag-"]').fill('active-users')
  await queryRow.getByRole('button', { name: 'Save' }).click()
  await expect(
    queryRow.getByText('active-users', { exact: true })
  ).toBeVisible()

  await queryRow.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Edit SQL' }).click()
  await fillCodeMirror(queryRow, updatedSql)
  await queryRow.getByRole('button', { name: 'Save' }).click()

  // Save is a mutation; read the registry only once the row it rewrote shows
  // the new SQL, or the request can land before the edit is stored.
  await expect(
    page.getByTestId('query-registry-row').locator('code[title]')
  ).toHaveAttribute('title', updatedSql)

  const registryAfterEdit = await page.request.get(
    '/api/query-registry?limit=150'
  )
  expect(registryAfterEdit.ok()).toBe(true)
  const registryBody = (await registryAfterEdit.json()) as {
    queries: { hash: string; tag: string; sql: string }[]
    total: number
  }
  expect(registryBody).toMatchObject({
    total: 1,
    queries: [{ tag: 'active-users', sql: updatedSql }],
  })

  queryRow = page.locator(
    `[data-testid="query-registry-row"][data-query-hash="${registryBody.queries[0].hash}"]`
  )
  await expect(queryRow).toBeVisible()
  await expect(
    queryRow.getByText('active-users', { exact: true })
  ).toBeVisible()
  // v3: the SQL renders as a highlighted <code title={sql}> (no expand button);
  // its title attribute carries the full query as a stable per-row hook.
  await expect(queryRow.locator('code[title]')).toHaveAttribute(
    'title',
    updatedSql
  )

  const search = page.getByPlaceholder('Search name, hash, or SQL...')
  await search.fill('active-users')
  await expect(queryRow).toHaveCount(1)
  await search.fill('missing-query')
  await expect(
    page.getByRole('heading', { name: 'No queries match this search' })
  ).toBeVisible()
  await search.fill('active-users')

  queryRow = page.getByTestId('query-registry-row')
  const analyzeButton = queryRow.getByRole('button', { name: 'Analyze' })
  await expect(analyzeButton).toBeEnabled()
  // Analyze opens the query's analyze drawer over the library and measures it
  // there; the full page is one click away and keeps the same analysis.
  await analyzeButton.click()
  await expect(page).toHaveURL(/[?&]analyze=[^&]+/)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()
  await Promise.all([
    page.waitForURL((url) => url.pathname === '/results'),
    drawer.getByRole('button', { name: 'Open full view' }).click(),
  ])
  expect(new URL(page.url()).searchParams.get('query')).toBe(updatedSql)
  // The full page replaces the library and its drawer. Wait for that handover
  // before asserting on the SQL, which both surfaces render.
  await expect(drawer).toHaveCount(0)
  await expect(page.getByText(updatedSql, { exact: true })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Run analysis again' })
  ).toBeVisible()

  await page.goto('/query-registry')
  queryRow = page.getByTestId('query-registry-row')
  await expect(queryRow).toHaveCount(1)
  await queryRow.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await expect(queryRow.getByText('Delete this query?')).toBeVisible()
  await queryRow.getByRole('button', { name: 'Delete' }).click()

  await expect(
    page.getByRole('heading', { name: 'No queries observed yet' })
  ).toBeVisible()
  const registryAfterDelete = await page.request.get(
    '/api/query-registry?limit=150'
  )
  expect(registryAfterDelete.ok()).toBe(true)
  await expect(registryAfterDelete.json()).resolves.toMatchObject({
    total: 0,
    queries: [],
  })
})

test('Tab reaches every control in the analyze drawer', async ({ page }) => {
  setBackendFixtures({
    analyze: [
      {
        events: [
          {
            type: 'complete',
            success: true,
            analysis_id: 'drawer-keyboard-e2e',
            query_hash: 'drawer-keyboard-e2e',
          },
        ],
        repeat: true,
      },
    ],
  })
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
  await acceptExplainAnalyzeConsent(page)

  const created = await page.request.post('/api/query-registry', {
    data: { sql: 'SELECT id FROM orders', target: 'e2e-guard' },
  })
  expect(created.ok()).toBe(true)
  const { hash } = (await created.json()) as { hash: string }

  await page.goto(`/queries?analyze=${hash}`)
  const drawer = page.getByTestId('analyze-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('button', { name: 'Close' })).toBeVisible()

  // The sidebar sits behind this drawer's scrim, so the job list comes with
  // it and takes its own place in the tab order.
  await expect(drawer.getByText('Jobs', { exact: true })).toBeVisible()

  // The star is the first control in the header, so one pass of Tab from it
  // walks the rest. All of these were mouse-only while the drawer's focus
  // scope reclaimed the Tab that closed the star's tooltip.
  await drawer.getByTestId('query-star-toggle').focus()
  const reached: string[] = []
  for (let index = 0; index < 6; index++) {
    await page.keyboard.press('Tab')
    reached.push(
      await page.evaluate(() => {
        const active = document.activeElement as HTMLElement | null
        const content = document.querySelector('[data-testid="analyze-drawer"]')
        if (!active || !content?.contains(active)) return 'outside the drawer'
        return (
          active.getAttribute('aria-label') ?? (active.textContent ?? '').trim()
        )
      })
    )
  }
  expect(reached).toEqual([
    'Open full view',
    'Close',
    expect.stringContaining('Jobs'),
    'Overview',
    'Analyze',
    'Follow-up',
  ])
})
