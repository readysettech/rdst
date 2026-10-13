import {
  acceptBrowserError,
  addDatabaseThroughDialog,
  clearTargets,
  expect,
  test,
} from '../fixtures'

// A database with no report yet answers report/latest with 404, which the browser logs.
const NO_REPORT_404 =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

test('configures Postgres and bootstraps its schema through the UI', async ({
  page,
  browserErrors,
}) => {
  acceptBrowserError(browserErrors, NO_REPORT_404)
  const host = process.env.RDST_E2E_DB_HOST ?? '127.0.0.1'
  const port = Number(process.env.RDST_E2E_DB_PORT ?? '15432')

  await clearTargets(page.request)

  await addDatabaseThroughDialog(page, {
    host,
    port: String(port),
    database: 'rdst_test',
    user: 'rdst_test',
    password: process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password',
  })
  await page.goto('/')

  await expect(page).toHaveURL('/')

  // Connecting starts schema discovery in the background. Poll the API for
  // the discovered tables instead of racing the bootstrap request fired by
  // the add-database dialog.
  await expect(async () => {
    const schemaResponse = await page.request.get(
      '/api/schema?target=rdst_test'
    )
    expect(schemaResponse.ok()).toBe(true)
    await expect(schemaResponse.json()).resolves.toMatchObject({
      dialect: 'postgresql',
      tables: {
        title_basics: expect.arrayContaining(['tconst', 'primarytitle']),
        title_ratings: expect.arrayContaining(['tconst', 'averagerating']),
      },
    })
  }).toPass({ timeout: 30_000 })

  // Connecting kicked off the background bootstrap, which initializes the
  // semantic layer on its own; wait for the layer server-side rather than
  // racing the run from the UI.
  await expect(async () => {
    const status = await page.request.get(
      '/api/semantic-layer/status?target=rdst_test'
    )
    expect(status.ok()).toBe(true)
    expect((await status.json()).exists).toBe(true)
  }).toPass({ timeout: 30_000 })

  await page.goto('/schema')
  await expect(page.getByRole('heading', { name: 'Schema' })).toBeVisible()

  await expect(
    page.getByRole('button').filter({ hasText: 'title_basics' })
  ).toBeVisible({ timeout: 30_000 })
  await expect(
    page.getByRole('button').filter({ hasText: 'title_ratings' })
  ).toBeVisible()
})
