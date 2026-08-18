import type { Page } from '@playwright/test'
import { clearTargets, expect, test } from '../fixtures'

// The Postgres tier runs the unmodified app against a live database, so these
// suggestions come from real pg_stats / column reads, not fixtures.
const templatedQuery =
  'SELECT tconst, primarytitle FROM title_basics WHERE titletype = $1 LIMIT $2'

async function configurePostgresTarget(page: Page) {
  const host = process.env.RDST_E2E_DB_HOST ?? '127.0.0.1'
  const port = Number(process.env.RDST_E2E_DB_PORT ?? '15432')
  await clearTargets(page.request)
  const added = await page.request.post('/api/configure/targets', {
    data: {
      name: 'postgres-e2e',
      target: {
        engine: 'postgresql',
        host,
        port,
        database: 'rdst_test',
        user: 'rdst_test',
        password_env: 'RDST_E2E_DB_PASSWORD',
      },
    },
  })
  expect(added.ok()).toBe(true)
  const madeDefault = await page.request.put('/api/configure/default', {
    data: { name: 'postgres-e2e' },
  })
  expect(madeDefault.ok()).toBe(true)
  const initialized = await page.request.post('/api/init/complete')
  expect(initialized.ok()).toBe(true)
}

test('suggests real column values for a templated query from a live database', async ({
  page,
}) => {
  await configurePostgresTarget(page)

  const response = await page.request.post('/api/analyze/parameter-suggestions', {
    data: { query: templatedQuery, target: 'postgres-e2e' },
  })
  expect(response.ok()).toBe(true)
  const body = (await response.json()) as {
    placeholders: {
      placeholder: string
      column: string | null
      suggestions: { value: string; provenance: string }[]
    }[]
  }
  const byPlaceholder = Object.fromEntries(
    body.placeholders.map((entry) => [entry.placeholder, entry])
  )
  // $1 is compared to title_basics.titletype; the values are real rows
  // (pg_stats most common values once ANALYZE has run, a bounded DISTINCT
  // read before that), never invented.
  expect(byPlaceholder['$1'].column).toBe('title_basics.titletype')
  const titleTypes = byPlaceholder['$1'].suggestions.map((s) => s.value)
  expect(titleTypes.length).toBeGreaterThan(0)
  for (const value of titleTypes) {
    expect(['short', 'movie', 'tvSeries', 'tvEpisode', 'video', 'tvMovie']).toContain(value)
  }
  for (const suggestion of byPlaceholder['$1'].suggestions) {
    expect(suggestion.provenance).toMatch(/title_basics\.titletype/)
  }
  // LIMIT gets a shape hint rather than a column sample.
  expect(byPlaceholder['$2'].suggestions).toEqual([
    { value: '10', provenance: 'Query shape (LIMIT)' },
  ])

  // The same values show up in the parameter dialog before analysis runs.
  const search = new URLSearchParams({ query: templatedQuery, target: 'postgres-e2e' })
  await page.goto(`/results?${search.toString()}`)
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Enter parameter values')).toBeVisible()
  const chip = dialog.getByRole('button', { name: titleTypes[0], exact: true })
  await expect(chip).toBeVisible({ timeout: 15_000 })
  await expect(dialog.getByText('from title_basics.titletype')).toBeVisible()
  await chip.click()
  await expect(dialog.getByLabel('Value for $1')).toHaveValue(titleTypes[0])
  await dialog.getByRole('button', { name: '10', exact: true }).click()
  await expect(dialog.getByLabel('Value for $2')).toHaveValue('10')
})
