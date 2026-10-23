import { McpClient } from '../firstRun'
import {
  acceptBrowserError,
  clearTargets,
  expect,
  mockSignedIn,
  test,
} from '../fixtures'

// A database with no report yet answers report/latest with 404, which the browser logs.
const NO_REPORT_404 =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

const TARGET = 'agent-shop'

test('a coding agent connects and adds a database, then the user turns monitoring on in the dialog', async ({
  page,
  request,
  browserErrors,
}) => {
  test.setTimeout(180_000)
  acceptBrowserError(browserErrors, NO_REPORT_404)
  await clearTargets(page.request)
  // Turning on monitoring needs a Readyset account (monitoring-signin-gate.spec.ts).
  await mockSignedIn(page)

  await page.goto('/?add=agent')
  const dialog = page.getByRole('dialog', { name: 'Find your database' })
  await expect(dialog).toBeVisible()
  await dialog.getByTestId('agent-prompt-reveal').click()
  const agentStatus = dialog.getByTestId('agent-status')
  await expect(agentStatus).toHaveAttribute('data-state', 'waiting')
  await expect(dialog.getByText('Waiting for your agent')).toBeVisible()
  await expect(
    dialog.getByText('Prompt for your agent', { exact: true })
  ).toBeVisible()

  // What the user pastes: RDST's local MCP URL, with no secret and nothing that expires.
  const prompt = await dialog.getByTestId('agent-prompt').innerText()
  const baseUrl = new URL(page.url()).origin
  expect(prompt).toContain(`${baseUrl}/mcp?a=`)
  expect(prompt).toContain('keeps the connection here')
  expect(prompt).not.toMatch(/pair|expire|token|Authorization|Bearer/i)
  await expect(dialog.getByTestId('agent-prompt-caption')).toHaveCount(0)

  // /mcp refuses a web page and a DNS-rebound host name.
  const fromPage = await request.post(`${baseUrl}/mcp`, {
    headers: { Origin: 'https://evil.example' },
    data: { jsonrpc: '2.0', id: 1, method: 'tools/list' },
  })
  expect(fromPage.status()).toBe(403)
  const rebound = await request.post(`${baseUrl}/mcp`, {
    headers: { Host: 'evil.example' },
    data: { jsonrpc: '2.0', id: 1, method: 'tools/list' },
  })
  expect(rebound.status()).toBe(403)
  await expect(agentStatus).toHaveAttribute('data-state', 'waiting')

  // The agent's first request to the prompt's URL ticks "connected".
  const agent = McpClient.fromPrompt(request, prompt)
  await expect(agentStatus).toHaveAttribute('data-state', 'waiting')
  const init = await agent.rpc('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'rdst-e2e-agent', version: '1.0.0' },
  })
  expect(init).toMatchObject({
    protocolVersion: '2025-06-18',
    serverInfo: { name: 'rdst' },
  })
  await expect(agentStatus).toHaveAttribute('data-state', 'connected')
  await expect(dialog.getByText('Your agent connected.')).toBeVisible()
  await agent.notify('notifications/initialized')
  const listed = (await agent.rpc('tools/list')) as {
    tools: { name: string; description: string }[]
  }
  const names = listed.tools.map((tool) => tool.name)
  expect(names).toEqual(
    expect.arrayContaining([
      'rdst_status',
      'rdst_add_database',
      'rdst_test_database',
      'rdst_enable_monitoring',
    ])
  )
  for (const tool of listed.tools)
    expect(tool.description, tool.name).toContain('OS keyring')

  // Each call ticks its step in the dialog as it lands.
  const host = process.env.RDST_E2E_DB_HOST ?? '127.0.0.1'
  const port = process.env.RDST_E2E_DB_PORT ?? '15432'
  const password = process.env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password'
  const added = await agent.callTool('rdst_add_database', {
    name: TARGET,
    url: `postgresql://rdst_test:${password}@${host}:${port}/rdst_test`,
  })
  expect(added).toMatchObject({ target: TARGET, added: true })
  // The suite runs without an OS keyring, and the reply says so.
  expect(added.persisted).toBe(false)
  expect(String(added.warning)).toContain("keyring isn't available")
  expect(JSON.stringify(added)).not.toContain(password)
  await expect(agentStatus).toHaveAttribute('data-state', 'added')
  await expect(agentStatus).toContainText(`Added ${TARGET}`)

  // Added: the database's page opens on the same turn-on dialog the connection form
  // leads to, with its prerequisite checks.
  await expect(page).toHaveURL(/\/database/, { timeout: 15_000 })
  await expect(dialog).toHaveCount(0)
  const turnOn = page.getByTestId('enable-monitoring')
  await expect(
    turnOn.getByRole('heading', { name: `Monitor ${TARGET}` })
  ).toBeVisible()
  await expect(turnOn.getByTestId('prereq-checker')).toBeVisible()

  const tested = await agent.callTool('rdst_test_database', { name: TARGET })
  expect(tested).toMatchObject({ target: TARGET, ok: true })
  await expect(
    turnOn.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeEnabled({ timeout: 15_000 })

  // The agent asks to turn monitoring on. The first time is the user's call: the dialog
  // says so and stays open until they click.
  const waiting = await agent.callTool('rdst_enable_monitoring', {
    name: TARGET,
  })
  expect(waiting).toMatchObject({
    target: TARGET,
    enabled: false,
    waiting_for_user: true,
  })
  expect(String(waiting.message)).toContain(
    'RDST is showing the Turn on monitoring screen.'
  )
  await expect(turnOn.getByTestId('agent-awaiting-user')).toContainText(
    'Your turn: read and click Turn on monitoring'
  )
  await expect(turnOn.getByTestId('explain-analyze-warning')).toBeVisible()
  await page.waitForTimeout(1_500)
  await expect(turnOn).toBeVisible()
  const before = await agent.callTool('rdst_status')
  expect(before.targets).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ name: TARGET, monitoring: false }),
    ])
  )

  // The user turns it on; the dialog closes on the monitored page.
  await turnOn.getByRole('button', { name: 'Turn on monitoring' }).click()
  await expect(turnOn).toHaveCount(0, { timeout: 15_000 })
  await expect(
    page.getByRole('heading', { level: 1, name: TARGET })
  ).toBeVisible()

  const status = await agent.callTool('rdst_status')
  expect(status.targets).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ name: TARGET, monitoring: true }),
    ])
  )
  const settings = await page.request.get(
    `/api/monitor/targets/${TARGET}/settings`
  )
  await expect(settings.json()).resolves.toMatchObject({ enabled: true })

  // Once the user has turned it on, the agent may switch monitoring off and on again.
  const off = await agent.callTool('rdst_disable_monitoring', { name: TARGET })
  expect(off).toMatchObject({ target: TARGET, enabled: false })
  const on = await agent.callTool('rdst_enable_monitoring', { name: TARGET })
  expect(on).toMatchObject({ target: TARGET, enabled: true })
  expect(on.waiting_for_user).toBeUndefined()

  await clearTargets(page.request)
})
