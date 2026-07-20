import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  setBackendFixtures,
  test,
} from './fixtures'

const query =
  'SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id ORDER BY COUNT(*) DESC'

const cachedQuery = {
  cache_id: 'q_orders_summary_001',
  cache_name: 'orders_summary',
  query,
  type: 'shallow',
  ttl: 'forever',
  registry_hash: 'orders-summary-hash',
}

async function prepareCachePage(
  page: Parameters<typeof configureTestTarget>[0]
) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
}

test('deploys remotely, then restarts and deletes a served cache', async ({
  page,
}) => {
  setBackendFixtures({
    cache_delete: [
      {
        events: [
          {
            type: 'cache_delete',
            success: true,
            cache_id: cachedQuery.cache_id,
          },
        ],
      },
    ],
    cache_deploy: [
      {
        events: [
          {
            type: 'progress',
            stage: 'deploy',
            percent: 60,
            message: 'Starting ReadySet on remote host...',
          },
          {
            type: 'deploy_complete',
            success: true,
            deployed: true,
            running: true,
            endpoint: 'postgresql://cache.example.test:5433/app',
            cache_target: 'e2e-guard-cache',
            container_name: null,
          },
        ],
      },
    ],
    cache_lifecycle: [
      {
        events: [
          {
            type: 'cache_lifecycle',
            success: true,
            operation: 'restart',
            state: 'running',
            detail: 'ReadySet restarted.',
          },
        ],
      },
    ],
    // The cache itself is created from the Queries workbench (Cache & test)
    // now; it reaches the deployment-only Cache page via the served-cache list.
    cache_list: [
      {
        events: [
          {
            type: 'cache_list',
            success: true,
            count: 1,
            caches: [cachedQuery],
          },
        ],
      },
      {
        events: [{ type: 'cache_list', success: true, count: 0, caches: [] }],
        repeat: true,
      },
    ],
    cache_status: [
      {
        events: [
          {
            type: 'cache_status',
            deployed: false,
            running: false,
            endpoint: null,
            cache_target: null,
            container_name: null,
          },
        ],
      },
      {
        events: [
          {
            type: 'cache_status',
            deployed: true,
            running: true,
            endpoint: 'postgresql://cache.example.test:5433/app',
            cache_target: 'e2e-guard-cache',
            container_name: null,
          },
        ],
        repeat: true,
      },
    ],
  })
  await prepareCachePage(page)

  let deployRequest: unknown
  let lifecycleRequest: unknown
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/cache/deploy') {
      deployRequest = request.postDataJSON()
    }
    if (url.pathname === '/api/cache/restart') {
      lifecycleRequest = request.postDataJSON()
    }
  })

  await page.goto('/cache')
  await expect(
    page.getByRole('heading', { name: 'ReadySet Cache' })
  ).toBeVisible()
  await expect(page.getByText('Deploy a cache for "e2e-guard"')).toBeVisible()

  // C-09 collapsed the non-Docker deploy modes behind the "Other deploy
  // options" disclosure; open it before selecting Remote Host.
  await page.getByRole('button', { name: /Other deploy options/ }).click()
  await page.getByRole('button', { name: /Remote Host/ }).click()
  await expect(
    page.getByRole('button', { name: 'Deploy to Remote' })
  ).toBeDisabled()
  await page.locator('[name="remote-dest"]').fill('ops@cache.example.test')
  await page.getByRole('button', { name: 'Systemd', exact: true }).click()
  await page.getByRole('button', { name: 'Deploy to Remote' }).click()

  await expect(page.getByText('Cache Running', { exact: true })).toBeVisible()
  await expect(
    page.getByText('postgresql://cache.example.test:5433/app', {
      exact: true,
    })
  ).toBeVisible()
  expect(deployRequest).toEqual({
    target: 'e2e-guard',
    mode: 'systemd',
    host: 'cache.example.test',
    ssh_user: 'ops',
  })

  // The served-cache list surfaces the cache created via Queries.
  const cacheRow = page.getByTestId('cache-query-row')
  await expect(cacheRow).toHaveCount(1)
  await expect(cacheRow).toHaveAttribute('data-cache-id', cachedQuery.cache_id)
  await expect(cacheRow).toContainText(cachedQuery.cache_name)

  // Lifecycle actions collapsed into the "Cache actions" overflow menu at
  // C-09; Restart is a menuitem there now.
  await page.getByRole('button', { name: 'Cache actions' }).click()
  await page.getByRole('menuitem', { name: 'Restart' }).click()
  await expect(page.getByText('Cache restarted', { exact: true })).toBeVisible()
  expect(lifecycleRequest).toEqual({ target: 'e2e-guard' })

  await cacheRow.getByRole('button', { name: 'Delete' }).click()
  await expect(cacheRow.getByText('Remove this cached query?')).toBeVisible()
  await cacheRow.getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByText('No queries cached yet')).toBeVisible()
})

test('registers an endpoint for a deployed cache and shows the empty state', async ({
  page,
}) => {
  const endpoint = 'postgresql://readyset.internal:15433/app'
  setBackendFixtures({
    cache_register: [
      {
        events: [
          {
            type: 'cache_status',
            deployed: true,
            running: true,
            endpoint,
            cache_target: 'e2e-guard-cache',
            container_name: null,
          },
        ],
      },
    ],
    cache_status: [
      {
        events: [
          {
            type: 'cache_status',
            deployed: true,
            running: false,
            endpoint: null,
            cache_target: 'e2e-guard-cache',
            container_name: null,
          },
        ],
      },
      {
        events: [
          {
            type: 'cache_status',
            deployed: true,
            running: true,
            endpoint,
            cache_target: 'e2e-guard-cache',
            container_name: null,
          },
        ],
      },
    ],
  })
  await prepareCachePage(page)

  let registerRequest: unknown
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/cache/register') {
      registerRequest = request.postDataJSON()
    }
  })

  await page.goto('/cache')
  await expect(
    page.getByText('Endpoint Required', { exact: true })
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Connect' })).toBeDisabled()
  await page.locator('[name="endpoint-host"]').fill('readyset.internal')
  await page.locator('[name="endpoint-port"]').fill('15433')
  await page.getByRole('button', { name: 'Connect' }).click()

  await expect(page.getByText('Connected', { exact: true })).toBeVisible()
  await expect(page.getByText('Cache Running', { exact: true })).toBeVisible()
  await expect(page.getByText(endpoint, { exact: true })).toBeVisible()
  await expect(page.getByText('No queries cached yet')).toBeVisible()
  expect(registerRequest).toEqual({
    target: 'e2e-guard',
    cache_host: 'readyset.internal',
    cache_port: 15433,
  })
})
