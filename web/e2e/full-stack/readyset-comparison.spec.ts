import { execFileSync } from 'node:child_process'
import type { APIRequestContext, Page } from '@playwright/test'
import { clearQueryRegistry, clearTargets, expect, test } from '../fixtures'

const targetName = 'readyset-e2e'
const query = 'SELECT COUNT(*) AS movie_count FROM title_basics'

type DockerSandbox = {
  Config: {
    Env: string[]
    Image: string
    Labels: Record<string, string>
  }
  State: { Running: boolean }
}

function inspectSandbox(): DockerSandbox {
  return JSON.parse(
    execFileSync('docker', ['inspect', 'rdst-readyset-sandbox'], {
      encoding: 'utf8',
    })
  )[0] as DockerSandbox
}

function sandboxExists(): boolean {
  try {
    execFileSync('docker', ['inspect', 'rdst-readyset-sandbox'], {
      stdio: 'ignore',
    })
    return true
  } catch {
    return false
  }
}

async function configureTarget(request: APIRequestContext): Promise<string> {
  await clearTargets(request)
  await clearQueryRegistry(request)

  const target = await request.post('/api/configure/targets', {
    data: {
      name: targetName,
      target: {
        engine: 'postgresql',
        host: process.env.RDST_E2E_DB_HOST ?? '127.0.0.1',
        port: Number(process.env.RDST_E2E_DB_PORT ?? '15432'),
        database: 'rdst_test',
        user: 'rdst_test',
        password_env: 'RDST_E2E_DB_PASSWORD',
      },
    },
  })
  expect(target.ok(), await target.text()).toBe(true)

  const madeDefault = await request.put('/api/configure/default', {
    data: { name: targetName },
  })
  expect(madeDefault.ok(), await madeDefault.text()).toBe(true)

  const initialized = await request.post('/api/init/complete')
  expect(initialized.ok(), await initialized.text()).toBe(true)

  const registered = await request.post('/api/query-registry', {
    data: { sql: query, target: targetName },
  })
  expect(registered.ok(), await registered.text()).toBe(true)
  const registration = (await registered.json()) as {
    success: boolean
    hash?: string
  }
  expect(registration).toMatchObject({ success: true })
  expect(registration.hash).toEqual(expect.any(String))
  return registration.hash as string
}

async function selectQueryAndStart(page: Page, queryHash: string) {
  await page.goto('/cache')
  const queries = page.getByRole('region', {
    name: 'Queries available for comparison',
  })
  const queryCard = queries.locator(`[data-query-hash="${queryHash}"]`)
  await expect(queryCard).toContainText(query)
  await expect(queryCard).toHaveRole('button')
  await queryCard.click()
  await page.getByRole('button', { name: 'Run comparison' }).click()
  await expect(
    page.getByRole('heading', { name: 'Start this comparison?' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Start comparison' }).click()
}

test('pulls and uses the managed Readyset container from the web comparison', async ({
  page,
}) => {
  test.setTimeout(8 * 60_000)

  try {
    const queryHash = await configureTarget(page.request)
    await selectQueryAndStart(page, queryHash)

    await expect(page.getByText('Complete', { exact: true })).toBeVisible({
      timeout: 180_000,
    })
    // The verdict band carries both lanes and the combined request count; the
    // per-query card below it carries that query's measured window and errors.
    await expect(
      page.getByText(/^Upstream [\d,]+ QPS · [\d.]+ ms p95$/)
    ).toBeVisible()
    await expect(
      page.getByText(/^Readyset [\d,]+ QPS · [\d.]+ ms p95$/)
    ).toBeVisible()
    await expect(page.getByText(/^[\d,]+ requests$/)).toBeVisible()
    await expect(
      page.getByText(/^\d+s measured · 0 errors · hash [0-9a-f]+$/)
    ).toBeVisible()

    const statusResponse = await page.request.get('/api/cache/sandbox')
    expect(statusResponse.ok(), await statusResponse.text()).toBe(true)
    await expect(statusResponse.json()).resolves.toMatchObject({
      phase: 'ready',
      current_target: targetName,
      healthy: true,
      docker_installed: true,
      docker_running: true,
    })

    const sandbox = inspectSandbox()
    expect(sandbox.State.Running).toBe(true)
    expect(sandbox.Config.Image).toBe('docker.io/readysettech/readyset:latest')
    expect(sandbox.Config.Labels['io.readyset.rdst.sandbox']).toBe('true')
    expect(sandbox.Config.Labels['io.readyset.rdst.target']).toBe(targetName)
    expect(sandbox.Config.Env).toEqual(
      expect.arrayContaining([
        'PROMETHEUS_METRICS=false',
        'SHALLOW_MEMORY_PERCENT=80',
      ])
    )
  } finally {
    await clearTargets(page.request)
    await clearQueryRegistry(page.request)
  }

  expect(sandboxExists()).toBe(false)
})
