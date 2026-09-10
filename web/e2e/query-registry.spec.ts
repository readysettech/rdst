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
