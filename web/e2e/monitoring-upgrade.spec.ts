import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  acceptBrowserError,
  configureTestTarget,
  expect,
  mainContent,
  mockPrerequisites,
  test,
} from './fixtures'

test('records existing installs for the upgrade and invites them to monitor', async ({
  page,
  browserErrors,
}) => {
  // A checkup that doesn't exist yet reads as a 404 from the API.
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
  const upgrade = async () =>
    (await page.request.get('/api/init/monitoring-upgrade')).json()
  // A fresh install has nothing to upgrade.
  await expect(upgrade()).resolves.toEqual({ targets: [] })

  // Restore the pre-monitoring init schema in the isolated fixture install.
  writeFileSync(
    configPath,
    freshConfig.replace(/^monitoring_upgrade_targets = .*\r?\n/m, '')
  )
  try {
    await expect(upgrade()).resolves.toEqual({
      targets: [{ name: 'e2e-guard', engine: 'postgresql' }],
    })

    // Home names the unmonitored database; its page offers to turn monitoring
    // on through the enable dialog.
    await mockPrerequisites(page, 'e2e-guard')
    await page.goto('/')
    await expect(
      mainContent(page).getByText('e2e-guard: not monitored.')
    ).toBeVisible()
    await page.goto('/database?target=e2e-guard')
    await page.getByRole('button', { name: 'Turn on monitoring' }).click()
    const dialog = page.getByRole('dialog')
    await expect(
      dialog.getByRole('heading', { name: 'Monitor e2e-guard' })
    ).toBeVisible()
    await expect(dialog).toContainText('Analyze automatically turns it off.')
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
  } finally {
    writeFileSync(configPath, freshConfig)
  }
})
