import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mockPrerequisites,
  test,
} from './fixtures'

test('invites existing installs once and retains the existing consent dialog', async ({
  page,
  browserErrors,
}) => {
  // The Database page reads a checkup that doesn't exist yet; the API answers 404.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 404 (Not Found)'
  )
  await configureTestTarget(page, { hasPassword: true })
  const fixtureHome = process.env.RDST_E2E_HOME
  if (!fixtureHome) throw new Error('RDST_E2E_HOME is required')
  const configPath = join(fixtureHome, '.rdst', 'config.toml')
  const freshConfig = readFileSync(configPath, 'utf8')
  expect(freshConfig).toContain('monitoring_upgrade_targets = []')
  // One database: Home is its Database page.
  await page.goto('/')
  await expect(page).toHaveURL(/\/database/)
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()
  await expect(
    page.getByRole('region', { name: 'Monitoring is new' })
  ).toHaveCount(0)

  // Restore the pre-monitoring init schema in the isolated fixture install.
  writeFileSync(
    configPath,
    freshConfig.replace(/^monitoring_upgrade_targets = .*\r?\n/m, '')
  )
  await mockPrerequisites(page, 'e2e-guard')
  await page.route('**/api/monitor/limit*', (route) =>
    route.fulfill({
      json: {
        signed_in: false,
        own_key: false,
        in_use: 0,
        limit: null,
        known: false,
        full: false,
        target_fits: null,
        targets: [],
      },
    })
  )
  await page.reload()
  const prompt = page.getByRole('region', { name: 'Monitoring is new' })
  await expect(prompt).toBeVisible()
  await expect(prompt.getByText('e2e-guard uses PostgreSQL.')).toBeVisible()
  await prompt.getByRole('button', { name: 'Turn on', exact: true }).click()
  const dialog = page.getByRole('dialog', {
    name: 'Turn on monitoring for e2e-guard',
  })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByTestId('explain-analyze-warning')).toBeVisible()
  await expect(dialog.getByRole('button', { name: /Sign in/ })).toBeVisible()
  await dialog.getByRole('button', { name: 'Not now' }).click()
  await prompt.getByRole('button', { name: 'Not now' }).click()
  await expect(prompt).toHaveCount(0)
  await page.reload()
  await expect(
    page.getByRole('heading', { level: 1, name: 'e2e-guard' })
  ).toBeVisible()
  await expect(prompt).toHaveCount(0)
  // A new browser's API read sees the persisted dismissal too.
  const state = await page.request.get('/api/init/monitoring-upgrade')
  expect(await state.json()).toEqual({ targets: [] })
})
