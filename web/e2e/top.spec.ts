import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  test,
} from './fixtures'

test('redirects the retired slow-query route into the library, slowest first', async ({
  page,
}) => {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })

  await page.goto('/top')

  // The old URL asked for the slowest queries, so the library is ordered that
  // way and says where the view went. [C-80]
  await expect(page).toHaveURL(/\/queries\?.*sort=slowest-average/)
  await expect(
    page.getByRole('heading', { name: 'Queries', exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Top queries now live in the Query Library', { exact: true })
  ).toBeVisible()
})
