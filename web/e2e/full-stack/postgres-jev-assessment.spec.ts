import { clearQueryRegistry, clearTargets, expect, test } from '../fixtures'

test('discovers a Postgres shape and assesses literal variants once', async ({
  page,
}) => {
  const host = process.env.RDST_E2E_DB_HOST ?? '127.0.0.1'
  const port = Number(process.env.RDST_E2E_DB_PORT ?? '15432')
  const target = 'postgres-jev-e2e'
  await clearTargets(page.request)
  await clearQueryRegistry(page.request)
  const added = await page.request.post('/api/configure/targets', {
    data: {
      name: target,
      target: {
        engine: 'postgresql',
        host,
        port,
        database: 'rdst_test',
        user: 'rdst_test',
        password: process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password',
      },
    },
  })
  expect(added.ok()).toBe(true)
  await page.request.put('/api/configure/default', { data: { name: target } })
  await page.addInitScript((selected) => {
    window.localStorage.setItem('rdst_selected_target', selected)
  }, target)

  const firstSql =
    'SELECT tconst FROM title_basics WHERE startyear = 2001 ORDER BY primarytitle'
  expect(
    (
      await page.request.post(`/__test/execute/${target}`, {
        data: { sql: firstSql, repeat: 3 },
      })
    ).ok()
  ).toBe(true)
  await page.goto('/queries')
  expect((await page.request.post(`/__test/discovery/${target}`)).ok()).toBe(
    true
  )

  await expect(page.getByText('Jev · High priority')).toBeVisible({
    timeout: 20_000,
  })

  const secondSql =
    'SELECT tconst FROM title_basics WHERE startyear = 2002 ORDER BY primarytitle'
  await page.request.post(`/__test/execute/${target}`, {
    data: { sql: secondSql, repeat: 2 },
  })
  await page.request.post(`/__test/discovery/${target}`)

  await expect(async () => {
    const response = await page.request.get(
      `/api/query-registry?target=${target}&view=all&source=all&params=all&activity=all&impact=all&sort=jev-priority&limit=500`
    )
    expect(response.ok()).toBe(true)
    const body = (await response.json()) as {
      queries: {
        sql: string
        frequency: number
        jev_assessment?: { status: string; attempt_count: number }
      }[]
    }
    // Both literal variants normalize to this one shape, so a second
    // observation must raise its frequency without adding a second identity
    // or a second assessment attempt.
    const matches = body.queries.filter((query) =>
      query.sql.toLowerCase().includes('order by primarytitle')
    )
    expect(matches).toHaveLength(1)
    expect(matches[0].frequency).toBeGreaterThanOrEqual(5)
    expect(matches[0].jev_assessment).toMatchObject({
      status: 'complete',
      attempt_count: 1,
    })
  }).toPass({ timeout: 20_000 })
})
