/**
 * Target management on the Settings page — the surface the standalone /fleet
 * screen was merged into. The row set comes from /api/configure/targets; groups
 * and tags are joined from /api/fleet/targets, and reachability streams from
 * /api/fleet/status.
 */

import type { Page, Route } from '@playwright/test'
import { configureTestTarget, expect, sseBody, test } from './fixtures'

type FleetMember = {
  name: string
  engine: string
  host: string
  port: number
  database: string
  user?: string
  password_env?: string
  has_password?: boolean
  group: string | null
  tags?: string[]
}

type FleetFixture = {
  members: FleetMember[]
  groups: string[]
  count: number
}

const inventory: FleetFixture = {
  members: [
    {
      name: 'aurora-writer',
      engine: 'postgresql',
      host: 'aurora-1.internal',
      port: 5432,
      database: 'app',
      group: 'production',
      tags: ['role:writer'],
    },
    {
      name: 'aurora-reader',
      engine: 'postgresql',
      host: 'aurora-2.internal',
      port: 5432,
      database: 'app',
      group: 'production',
      tags: ['role:reader'],
    },
    {
      name: 'old-dead',
      engine: 'mysql',
      host: 'decommissioned.internal',
      port: 3306,
      database: 'legacy',
      group: null,
    },
  ],
  groups: ['production'],
  count: 3,
}

const connectivity = [
  {
    type: 'connectivity',
    target_name: 'aurora-writer',
    status: 'ok',
    latency_ms: 11,
    server_version: 'PostgreSQL 16.3',
  },
  {
    type: 'connectivity',
    target_name: 'aurora-reader',
    status: 'ok',
    latency_ms: 14,
    server_version: 'PostgreSQL 16.3',
  },
  {
    type: 'connectivity',
    target_name: 'old-dead',
    status: 'failed',
    error: 'Connection timed out',
  },
]

/** The same members, in the shape the configure target list returns. */
const asConfigureTargets = (members: FleetMember[]) => ({
  targets: members.map((member) => ({
    name: member.name,
    engine: member.engine,
    host: member.host,
    port: member.port,
    database: member.database,
    has_password: member.has_password ?? true,
    is_default: false,
  })),
  default_target: null,
})

const awsSignedIn = {
  has_credentials: true,
  method: 'sso',
  identity_arn: 'arn:aws:sts::123456789012:assumed-role/dev-role/mike',
  account: '123456789012',
  active_profile: 'dev',
  available_profiles: ['dev'],
  region: 'us-east-1',
}

type RouteHandler = (route: Route) => unknown

/**
 * Stub the four endpoints the Settings row set is assembled from. A test that
 * needs one of them to behave differently passes its own handler rather than
 * re-stating the other three.
 */
async function mockTargets(
  page: Page,
  fleet: FleetFixture = inventory,
  overrides: {
    configureTargets?: RouteHandler
    fleetTargets?: RouteHandler
    fleetTargetsPattern?: string
    fleetStatus?: RouteHandler
    awsStatus?: RouteHandler
  } = {}
) {
  await page.route(
    '**/api/configure/targets',
    overrides.configureTargets ??
      ((route) => route.fulfill({ json: asConfigureTargets(fleet.members) }))
  )
  await page.route(
    overrides.fleetTargetsPattern ?? '**/api/fleet/targets*',
    overrides.fleetTargets ?? ((route) => route.fulfill({ json: fleet }))
  )
  await page.route(
    '**/api/fleet/status*',
    overrides.fleetStatus ??
      ((route) =>
        route.fulfill({
          headers: { 'content-type': 'text/event-stream' },
          body: sseBody(connectivity),
        }))
  )
  await page.route(
    '**/api/fleet/aws-status*',
    overrides.awsStatus ?? ((route) => route.fulfill({ json: awsSignedIn }))
  )
}

async function prepareSettingsPage(
  page: Page,
  fleet: FleetFixture = inventory
) {
  await configureTestTarget(page, { hasPassword: true })
  await mockTargets(page, fleet)
  await page.goto('/configure')
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
}

test('groups the connections and shows live reachability', async ({ page }) => {
  await prepareSettingsPage(page)

  await expect(
    page.getByText('3 targets · 1 group', { exact: true }).first()
  ).toBeVisible()
  await expect(page.getByText('production', { exact: true })).toBeVisible()
  await expect(page.getByText('Ungrouped', { exact: true })).toBeVisible()
  await expect(page.getByText('aurora-writer', { exact: true })).toBeVisible()
  await expect(page.getByText('11ms', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Check all' }).first()
  ).toBeVisible()
  // The unreachable row states the failure next to itself.
  await expect(page.getByText('Connection timed out')).toBeVisible()
})

test('does not render an Ungrouped group header when there are no real groups', async ({
  page,
}) => {
  const flat: FleetFixture = {
    members: inventory.members
      .slice(0, 2)
      .map((member) => ({ ...member, group: null })),
    groups: [],
    count: 2,
  }
  await prepareSettingsPage(page, flat)

  await expect(
    page.getByText('2 targets · 0 groups', { exact: true }).first()
  ).toBeVisible()
  await expect(page.getByText('Ungrouped', { exact: true })).toHaveCount(0)
  // With no groups there is nothing to switch between, so the view control hides.
  await expect(page.getByRole('button', { name: 'Groups' })).toHaveCount(0)
  await expect(page.getByText('aurora-writer', { exact: true })).toBeVisible()
})

test('moves a target to a new group from its overflow menu', async ({
  page,
}) => {
  let current: FleetFixture = inventory
  let patchBody: unknown
  await configureTestTarget(page, { hasPassword: true })
  await mockTargets(page, inventory, {
    fleetTargetsPattern: '**/api/fleet/targets**',
    fleetTargets: (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON()
        current = {
          ...inventory,
          members: inventory.members.map((member) =>
            member.name === 'old-dead'
              ? { ...member, group: 'archive' }
              : member
          ),
          groups: ['production', 'archive'],
        }
        return route.fulfill({ json: { updated: true } })
      }
      return route.fulfill({ json: current })
    },
  })
  await page.goto('/configure')

  await page.getByRole('button', { name: 'More actions for old-dead' }).click()
  await page.getByRole('menuitem', { name: 'Move to group…' }).click()
  await expect(
    page.getByText('Move old-dead to group', { exact: true })
  ).toBeVisible()
  await page.locator('input[name="move-target-new-group"]').fill('archive')
  await page.getByRole('button', { name: 'Move target' }).click()

  expect(patchBody).toEqual({ group: 'archive' })
  await expect(
    page.getByText('3 targets · 2 groups', { exact: true }).first()
  ).toBeVisible()
  await expect(page.getByText('archive', { exact: true })).toBeVisible()
})

test('/fleet redirects to the Database connections section', async ({
  page,
}) => {
  await prepareSettingsPage(page)

  await page.goto('/fleet')
  await page.waitForURL(/\/configure/)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'Database connections' })
  ).toBeVisible()
})

test('/fleet?add=aws lands with the discovery drawer open on the AWS tab', async ({
  page,
}) => {
  await configureTestTarget(page, { hasPassword: true })
  await mockTargets(page)
  await page.route('**/api/fleet/discover-preview', (route) =>
    route.fulfill({ json: { members: [], errors: [] } })
  )

  await page.goto('/fleet?add=aws')
  await page.waitForURL(/\/configure/)

  const drawer = page.getByRole('dialog', { name: 'Add Targets' })
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('Regions', { exact: true })).toBeVisible()
  await expect(
    drawer.getByRole('button', { name: 'Discover', exact: true })
  ).toBeVisible()
})

test('completes the signed-out AWS SSO browser flow and polls to success', async ({
  page,
}) => {
  await configureTestTarget(page, { hasPassword: true })
  let signedIn = false
  let polls = 0
  await mockTargets(page, inventory, {
    awsStatus: (route) =>
      route.fulfill({
        json: signedIn
          ? awsSignedIn
          : {
              has_credentials: false,
              method: null,
              identity_arn: null,
              account: null,
              active_profile: null,
              available_profiles: ['dev'],
              region: null,
            },
      }),
  })
  await page.route('**/api/fleet/aws-login', (route) =>
    route.fulfill({
      json: { login_id: 'login-1', state: 'started', detail: 'Browser opened' },
    })
  )
  await page.route('**/api/fleet/aws-login/login-1', (route) => {
    polls += 1
    if (polls >= 2) {
      signedIn = true
      return route.fulfill({
        json: {
          state: 'success',
          detail: 'Signed in',
          verification_url: 'https://device.sso.aws.amazon.com/',
        },
      })
    }
    return route.fulfill({
      json: {
        state: 'running',
        detail: 'Waiting for approval',
        verification_url: 'https://device.sso.aws.amazon.com/',
      },
    })
  })

  // Signed out, AWS sign-in lives inside the discovery drawer.
  await page.goto('/configure')
  await page.getByRole('button', { name: 'Discover & import' }).click()

  await expect(
    page.getByText(
      'Sign in with AWS SSO so we can read details about your imported databases.',
      { exact: true }
    )
  ).toBeVisible()
  await page.getByRole('button', { name: 'Sign in with AWS SSO' }).click()
  await expect(
    page.getByText('Complete the sign-in in the browser window we opened', {
      exact: true,
    })
  ).toBeVisible()
  await expect(
    page.getByRole('link', { name: 'Open the AWS verification page' })
  ).toHaveAttribute('href', 'https://device.sso.aws.amazon.com/')
  await expect(
    page.getByText('Signed into AWS as mike', { exact: true })
  ).toBeVisible({ timeout: 7_000 })
  expect(polls).toBeGreaterThanOrEqual(2)
})

test('discovery previews grouped targets and bulk-adds only checked new targets', async ({
  page,
}) => {
  await configureTestTarget(page, { hasPassword: true })
  let added = false
  const discoveredTargets = [
    {
      name: 'orders-writer',
      engine: 'postgresql',
      host: 'orders.cluster.test',
      port: 5432,
      database: 'orders',
      user: 'master_orders',
      password_env: 'RDST_ORDERS_PASSWORD',
      has_password: false,
      group: 'orders-cluster',
      tags: ['role:writer'],
      instance_class: 'db.r6g.large',
      region: 'us-east-1',
      already_exists: false,
    },
    {
      name: 'orders-reader',
      engine: 'postgresql',
      host: 'orders-reader.cluster.test',
      port: 5432,
      database: 'orders',
      user: 'master_orders',
      password_env: 'RDST_ORDERS_PASSWORD',
      has_password: false,
      group: 'orders-cluster',
      tags: ['role:reader'],
      instance_class: 'db.r6g.large',
      region: 'us-east-1',
      already_exists: true,
    },
    {
      name: 'users-reader',
      engine: 'postgresql',
      host: 'users.cluster.test',
      port: 5432,
      database: 'users',
      user: 'master_users',
      password_env: 'RDST_USERS_PASSWORD',
      has_password: false,
      group: null,
      tags: [],
      instance_class: 'db.r6g.large',
      region: 'us-east-1',
      already_exists: false,
    },
  ]
  const liveMembers = () =>
    added ? [discoveredTargets[0] as FleetMember] : inventory.members
  // The credentials step re-checks exactly the target it just secured.
  let credentialChecks = 0
  await mockTargets(page, inventory, {
    configureTargets: (route) =>
      route.fulfill({ json: asConfigureTargets(liveMembers()) }),
    fleetTargets: (route) =>
      route.fulfill({
        json: {
          members: liveMembers(),
          groups: added ? ['orders-cluster'] : inventory.groups,
          count: added ? 1 : inventory.count,
        },
      }),
    fleetStatus: (route) => {
      const selected = new URL(route.request().url()).searchParams.getAll(
        'targets'
      )
      if (selected.length === 1 && selected[0] === 'orders-writer') {
        credentialChecks += 1
        return route.fulfill({
          headers: { 'content-type': 'text/event-stream' },
          body: sseBody([
            {
              type: 'connectivity',
              target_name: 'orders-writer',
              status: 'ok',
              latency_ms: 8,
            },
          ]),
        })
      }
      return route.fulfill({
        headers: { 'content-type': 'text/event-stream' },
        body: sseBody(connectivity),
      })
    },
  })
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: true,
        requirements: [
          {
            kind: 'anthropic_api_key',
            satisfied: true,
            source: 'process_env',
            target: null,
            accepted_names: ['ANTHROPIC_API_KEY'],
          },
        ],
      },
    })
  )
  const secretPosts: Array<Record<string, unknown>> = []
  await page.route('**/api/env/set', (route) => {
    secretPosts.push(route.request().postDataJSON())
    return route.fulfill({
      json: {
        success: true,
        name: secretPosts.at(-1)?.name,
        persisted: true,
        session_only: false,
      },
    })
  })
  let previewBody: Record<string, unknown> | undefined
  await page.route('**/api/fleet/discover-preview', (route) => {
    previewBody = route.request().postDataJSON()
    return route.fulfill({ json: { members: discoveredTargets, errors: [] } })
  })
  let bulkBody: { members: typeof discoveredTargets } | undefined
  await page.route('**/api/fleet/targets/bulk-add', (route) => {
    bulkBody = route.request().postDataJSON()
    added = true
    return route.fulfill({
      json: { imported: 1, skipped: 0, target_names: ['orders-writer'] },
    })
  })

  await page.goto('/configure')
  await page.getByRole('button', { name: 'Discover & import' }).click()
  await page.getByRole('button', { name: 'Discover AWS' }).click()
  await page.getByRole('button', { name: 'Discover', exact: true }).click()

  await expect(
    page.getByText('Choose which databases to add to your fleet.')
  ).toBeVisible()
  expect(previewBody?.profile).toBe('dev')
  expect(previewBody?.regions).toEqual(expect.arrayContaining(['us-east-1']))
  const addTargetsDialog = page.getByRole('dialog', { name: 'Add Targets' })
  const groupedPreviewLabel = addTargetsDialog.getByText('orders-cluster', {
    exact: true,
  })
  const ungroupedPreviewLabel = addTargetsDialog.getByText('Ungrouped', {
    exact: true,
  })
  await expect(groupedPreviewLabel).toBeVisible()
  await expect(ungroupedPreviewLabel).toBeVisible()
  const groupBeforeUngrouped = await groupedPreviewLabel.evaluate(
    (group, ungrouped) =>
      Boolean(
        group.compareDocumentPosition(ungrouped as Node) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ),
    await ungroupedPreviewLabel.elementHandle()
  )
  expect(groupBeforeUngrouped).toBe(true)
  await expect(
    page.getByText('already imported', { exact: true })
  ).toBeVisible()
  await expect(
    page.getByRole('checkbox', { name: 'Select orders-reader' })
  ).toHaveCount(0)
  await page.getByRole('checkbox', { name: 'Select users-reader' }).uncheck()
  await expect(
    page.getByRole('button', { name: 'Add 1 selected' })
  ).toBeVisible()
  await page.getByRole('button', { name: 'Add 1 selected' }).click()

  expect(bulkBody?.members.map((member) => member.name)).toEqual([
    'orders-writer',
  ])
  await expect(page.getByTestId('credentials-step')).toBeVisible()
  await expect(
    page
      .getByTestId('credentials-step')
      .getByText('orders-writer', { exact: true })
  ).toBeVisible()
  await expect(
    page.locator('input[name="credentials-user-orders-writer"]')
  ).toHaveValue('master_orders')
  await page
    .locator('input[name="credentials-password-orders-writer"]')
    .fill('orders-secret')
  await page.getByRole('button', { name: 'Save and check' }).click()

  await expect(
    page.getByTestId('credentials-step').getByText('Connected', { exact: true })
  ).toHaveCount(1)
  expect(secretPosts).toEqual([
    { name: 'RDST_ORDERS_PASSWORD', value: 'orders-secret', persist: true },
  ])
  expect(credentialChecks).toBeGreaterThanOrEqual(1)
  await expect(page.getByRole('button', { name: 'Done' })).toBeVisible()
})

test('CSV picker posts browser file content instead of a server path', async ({
  page,
}) => {
  await configureTestTarget(page, { hasPassword: true })
  await mockTargets(page)
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({ json: { keyring_available: true, requirements: [] } })
  )
  let importBody: Record<string, unknown> | undefined
  await page.route('**/api/fleet/import', (route) => {
    importBody = route.request().postDataJSON()
    return route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: sseBody([
        {
          type: 'import_complete',
          success: true,
          imported: 1,
          skipped: 0,
          errors: 0,
          target_names: ['browser-csv'],
        },
      ]),
    })
  })

  const csv =
    'name,host,engine,port,database,user\n' +
    'browser-csv,browser.test,postgresql,5432,app,app_user\n'
  await page.goto('/configure')
  await page.getByRole('button', { name: 'Discover & import' }).click()
  await page.getByRole('button', { name: 'Import CSV' }).click()
  await page.locator('input[type="file"]').setInputFiles({
    name: 'fleet.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(csv),
  })
  await page.getByRole('button', { name: 'Import', exact: true }).click()

  expect(importBody).toEqual({ csv_content: csv })
  expect(importBody).not.toHaveProperty('csv_file')
})
