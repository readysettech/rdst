import {
  acceptExplainAnalyzeConsent,
  clearQueryRegistry,
  configureTestTarget,
  expect,
  mockConnectivityOk,
  fillCodeMirror,
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
      },
    ],
  })
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
  // The Analyze handoff lands on /results, which preflights target
  // reachability before POST /api/analyze.
  await mockConnectivityOk(page)
  await acceptExplainAnalyzeConsent(page)

  const initialSql = 'SELECT id FROM users'
  const updatedSql = 'SELECT id, email FROM users ORDER BY id'

  await page.goto('/query-registry')
  await expect(
    page.getByRole('heading', { name: 'Queries', exact: true })
  ).toBeVisible()
  await expect(page.getByText('No queries match these filters')).toBeVisible()

  await page.getByRole('button', { name: 'Add query' }).click()
  await fillCodeMirror(page.locator('.cm-editor').first(), initialSql)
  await page.getByRole('button', { name: 'Save query' }).click()

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
  await expect(queryRow.locator('[title]')).toHaveAttribute('title', updatedSql)

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
  await Promise.all([
    page.waitForURL((url) => url.pathname === '/results'),
    analyzeButton.click(),
  ])
  expect(new URL(page.url()).searchParams.get('query')).toBe(updatedSql)
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

  await expect(page.getByText('No queries match these filters')).toBeVisible()
  const registryAfterDelete = await page.request.get(
    '/api/query-registry?limit=150'
  )
  expect(registryAfterDelete.ok()).toBe(true)
  await expect(registryAfterDelete.json()).resolves.toMatchObject({
    total: 0,
    queries: [],
  })
})
