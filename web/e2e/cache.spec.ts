import {
  clearQueryRegistry,
  configureTestTarget,
  expect,
  fillCodeMirror,
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

const benchmarkResult = {
  success: true,
  query,
  iterations: 15,
  origin_stats: {
    mean: 34,
    median: 31,
    min: 24,
    max: 48,
    p50: 31,
    p95: 45,
    p99: 48,
  },
  cache_stats: {
    mean: 4,
    median: 3.5,
    min: 0.7,
    max: 6,
    p50: 3.5,
    p95: 5.5,
    p99: 6,
  },
  speedup_mean: 8.5,
  speedup_median: 8.9,
  improvement_pct: 88.2,
  winner: 'readyset',
}

async function prepareCachePage(
  page: Parameters<typeof configureTestTarget>[0]
) {
  await clearQueryRegistry(page.request)
  await configureTestTarget(page, { hasPassword: true })
}

test('deploys remotely, creates, benchmarks, restarts, and deletes a cache', async ({
  page,
}) => {
  setBackendFixtures({
    cache_add: [
      {
        events: [
          {
            type: 'cache_add',
            success: true,
            supported: true,
            query,
            query_hash: 'orders-summary-hash',
            detail: 'Query can be cached by ReadySet.',
          },
        ],
      },
      {
        events: [
          {
            type: 'cache_add',
            success: true,
            supported: true,
            query,
            query_hash: 'orders-summary-hash',
            detail: 'Cache created.',
          },
        ],
      },
    ],
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
    cache_list: [
      {
        events: [{ type: 'cache_list', success: true, count: 0, caches: [] }],
      },
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
      },
    ],
    cache_run: [
      {
        events: [
          {
            type: 'progress',
            stage: 'benchmark',
            percent: 50,
            message: 'Comparing origin and ReadySet...',
          },
          { type: 'cache_run_complete', ...benchmarkResult },
        ],
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

  const addRequests: unknown[] = []
  let deployRequest: unknown
  let benchmarkRequest: unknown
  let lifecycleRequest: unknown
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/cache/deploy') {
      deployRequest = request.postDataJSON()
    }
    if (url.pathname === '/api/cache/add') {
      addRequests.push(request.postDataJSON())
    }
    if (url.pathname === '/api/cache/run') {
      benchmarkRequest = request.postDataJSON()
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
  await expect(page.getByText('No queries cached yet')).toBeVisible()

  // Caching#1: Add Cache moved off the bottom of the page into a modal opened
  // from the list header / empty-state CTA. The only editable cm-content is the
  // one inside the open dialog, so fillCodeMirror still resolves uniquely.
  await page.getByRole('button', { name: 'Add cache' }).first().click()
  const addDialog = page.getByRole('dialog')
  await expect(
    addDialog.getByRole('heading', { name: 'Add cache' })
  ).toBeVisible()
  await fillCodeMirror(page.locator('.cm-editor'), query)
  await addDialog.getByRole('button', { name: 'Check & Cache' }).click()

  const cacheRow = page.getByTestId('cache-query-row')
  await expect(cacheRow).toHaveCount(1)
  await expect(cacheRow).toHaveAttribute('data-cache-id', cachedQuery.cache_id)
  await expect(cacheRow).toContainText(cachedQuery.cache_name)
  // A real (non-optimistic) success closes the modal and toasts. The shared
  // @rs/ui-new/modal renders `Dialog.Portal` with `forceMount`, so a closed
  // dialog stays in the DOM as a ghost (role="dialog" data-state="closed",
  // opacity 0 — not display:none or inert). Asserting zero dialog nodes would
  // match that ghost and flake; assert the dialog is in its closed state. [FIX-6]
  await expect(page.getByRole('dialog')).toHaveAttribute('data-state', 'closed')
  // The toast prints its description both in a visible node and in an aria-live
  // status region, so a substring match resolves to 2 elements. Scope to the
  // exact description node to keep the assertion unambiguous. [FIX-6]
  await expect(
    page.getByText('Your query is now served from ReadySet.', { exact: true })
  ).toBeVisible()
  expect(addRequests).toEqual([
    { query, target: 'e2e-guard', dry_run: true },
    { query, target: 'e2e-guard', dry_run: false },
  ])

  await cacheRow.getByRole('button', { name: 'Bench' }).click()
  await expect(page.getByText('8.5x faster', { exact: true })).toBeVisible()
  await expect(
    page.getByText('ReadySet cache outperforms origin', { exact: true })
  ).toBeVisible()
  expect(benchmarkRequest).toEqual({
    query,
    target: 'e2e-guard',
    iterations: 15,
    warmup: 5,
  })

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

test('shows cacheability and benchmark failures and retries successfully', async ({
  page,
}) => {
  setBackendFixtures({
    cache_add: [
      {
        events: [
          {
            type: 'error',
            message: 'Cache control plane is temporarily unavailable',
          },
        ],
      },
      {
        events: [
          {
            type: 'cache_add',
            success: true,
            supported: false,
            query,
            query_hash: null,
            detail: 'Correlated subqueries are not supported by ReadySet.',
          },
        ],
      },
    ],
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
    ],
    cache_run: [
      {
        events: [
          {
            type: 'error',
            message: 'ReadySet endpoint did not respond',
          },
        ],
      },
      {
        events: [{ type: 'cache_run_complete', ...benchmarkResult }],
      },
    ],
    cache_status: [
      {
        events: [
          {
            type: 'cache_status',
            deployed: true,
            running: true,
            endpoint: 'postgresql://127.0.0.1:5433/app',
            cache_target: 'e2e-guard-cache',
            container_name: 'rdst-readyset-e2e-guard',
          },
        ],
      },
    ],
  })
  await prepareCachePage(page)

  let addCalls = 0
  let benchmarkCalls = 0
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/cache/add') {
      addCalls += 1
    }
    if (url.pathname === '/api/cache/run') {
      benchmarkCalls += 1
    }
  })

  await page.goto('/cache')
  const cacheRow = page.getByTestId('cache-query-row')
  await expect(cacheRow).toBeVisible()

  await cacheRow.getByRole('button', { name: 'Bench' }).click()
  await expect(
    page.getByText('ReadySet endpoint did not respond', { exact: true })
  ).toBeVisible()
  await cacheRow.getByRole('button', { name: 'Bench' }).click()
  await expect(page.getByText('8.5x faster', { exact: true })).toBeVisible()
  expect(benchmarkCalls).toBe(2)

  // Open the Add Cache modal; the check-error and "not cacheable" paths keep it
  // open (only a real cache success closes it). [Caching#1]
  await page.getByRole('button', { name: 'Add cache' }).first().click()
  const addDialog = page.getByRole('dialog')
  await fillCodeMirror(page.locator('.cm-editor'), query)
  await addDialog.getByRole('button', { name: 'Check & Cache' }).click()
  await expect(
    page.getByText('Cache control plane is temporarily unavailable', {
      exact: true,
    })
  ).toBeVisible()

  await addDialog.getByRole('button', { name: 'Check & Cache' }).click()
  await expect(
    page.getByText('Query cannot be cached', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText('Correlated subqueries are not supported by ReadySet.', {
      exact: true,
    })
  ).toBeVisible()
  expect(addCalls).toBe(2)
})
