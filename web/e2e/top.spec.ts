import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  test,
} from './fixtures'

test('redirects the retired slow-query route to the unified high-impact library', async ({
  page,
}) => {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/top')

  await expect(page).toHaveURL(/\/queries\?.*view=high-impact/)
  await expect(
    page.getByRole('heading', { name: 'Queries', exact: true })
  ).toBeVisible()
  await expect(page.getByText('No queries match these filters')).toBeVisible()
})
