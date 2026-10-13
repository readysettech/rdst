import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  test,
} from './fixtures'

test('redirects the retired slow-query route to the Database page', async ({
  page,
}) => {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/top')

  await expect(page).toHaveURL(/\/database/)
})
