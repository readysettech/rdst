import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse } from '@/test-utils'
import {
  bulkAddFleetTargets,
  createFleetAwsProfile,
  FleetAwsLoginError,
  fetchFleetAwsLogin,
  fetchFleetAwsStatus,
  fetchFleetDiscoverPreview,
  fleetAwsLogout,
  startFleetAwsLogin,
  updateFleetTargetGroup,
} from './useFleet'

vi.mock('./client', () => ({
  api: { POST: vi.fn(), GET: vi.fn(), DELETE: vi.fn() },
}))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

/** Stub global fetch to answer with `responses` in order; the last repeats. */
function stubFetch(...responses: Response[]) {
  const fetchMock = vi.fn()
  for (const response of responses.slice(0, -1)) {
    fetchMock.mockResolvedValueOnce(response)
  }
  fetchMock.mockResolvedValue(responses[responses.length - 1])
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('fetchFleetAwsStatus', () => {
  it('returns the credential summary', async () => {
    const payload = {
      has_credentials: true,
      method: 'sso',
      identity_arn: 'arn:aws:sts::123:assumed-role/dev/mike',
      account: '123',
      active_profile: 'dev',
      available_profiles: ['dev', 'prod'],
      region: 'us-east-1',
    }
    const fetchMock = stubFetch(jsonResponse(payload))

    const result = await fetchFleetAwsStatus()

    expect(fetchMock).toHaveBeenCalledWith('/api/providers/aws-status')
    expect(result.has_credentials).toBe(true)
    expect(result.available_profiles).toEqual(['dev', 'prod'])
  })

  it('passes the selected signed-in profile through to the status endpoint', async () => {
    const fetchMock = stubFetch(
      jsonResponse({
        has_credentials: true,
        method: 'sso',
        identity_arn: 'arn:aws:sts::123:assumed-role/prod/mike',
        account: '123',
        active_profile: 'production sso',
        available_profiles: ['production sso'],
        region: 'us-west-2',
      })
    )

    await expect(fetchFleetAwsStatus('production sso')).resolves.toMatchObject({
      has_credentials: true,
      active_profile: 'production sso',
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/providers/aws-status?profile=production%20sso'
    )
  })

  it('throws with the response body on failure', async () => {
    stubFetch(new Response('boto missing', { status: 500 }))
    await expect(fetchFleetAwsStatus()).rejects.toThrow('boto missing')
  })
})

describe('AWS SSO and group-edit contracts', () => {
  it('signs out through the AWS logout endpoint', async () => {
    const fetchMock = stubFetch(jsonResponse({ signed_out: true }))

    await expect(fleetAwsLogout()).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith('/api/providers/aws-logout', {
      method: 'POST',
    })
  })

  it('starts and polls an AWS SSO login', async () => {
    const fetchMock = stubFetch(
      jsonResponse({ login_id: 'l1', state: 'started', detail: 'opened' }),
      jsonResponse({
        state: 'success',
        detail: 'signed in',
        verification_url: 'https://example.test',
      })
    )

    await expect(startFleetAwsLogin('dev')).resolves.toMatchObject({
      login_id: 'l1',
    })
    await expect(fetchFleetAwsLogin('l1')).resolves.toMatchObject({
      state: 'success',
    })
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/providers/aws-login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ profile: 'dev' }),
      })
    )
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/providers/aws-login/l1')
  })

  it('preserves the CLI-missing fallback contract', async () => {
    stubFetch(
      jsonResponse(
        {
          code: 'aws_cli_missing',
          detail: 'AWS CLI is missing',
          fallback_command: 'aws sso login --profile dev',
        },
        409
      )
    )
    const error = await startFleetAwsLogin('dev').catch((caught) => caught)
    expect(error).toBeInstanceOf(FleetAwsLoginError)
    expect(error).toMatchObject({
      code: 'aws_cli_missing',
      fallbackCommand: 'aws sso login --profile dev',
    })
  })

  it('creates a complete SSO profile', async () => {
    const input = {
      name: 'dev',
      sso_start_url: 'https://example.awsapps.com/start',
      sso_region: 'us-east-1',
      sso_account_id: '123',
      sso_role_name: 'Developer',
      region: 'us-west-2',
    }
    const fetchMock = stubFetch(jsonResponse({ created: true, profile: 'dev' }))
    await expect(createFleetAwsProfile(input)).resolves.toEqual({
      created: true,
      profile: 'dev',
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/providers/aws-profiles',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(input) })
    )
  })

  it('PATCHes the target group and supports removing it', async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }))
    await updateFleetTargetGroup('db one', null)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/fleet/targets/db%20one',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ group: null }),
      })
    )
  })
})

describe('fleet discovery preview contracts', () => {
  it('previews without importing and bulk-adds only the passed members', async () => {
    const preview = {
      members: [
        {
          name: 'orders',
          engine: 'postgresql',
          host: 'orders.test',
          port: 5432,
          database: 'orders',
          user: 'app',
          password_env: 'FLEET_PASS',
          group: 'orders-cluster',
          tags: [],
          instance_class: 'db.r6g.large',
          region: 'us-east-1',
          already_exists: false,
        },
        {
          name: 'existing',
          engine: 'postgresql',
          host: 'existing.test',
          port: 5432,
          database: 'app',
          user: 'app',
          password_env: 'FLEET_PASS',
          group: null,
          tags: [],
          instance_class: null,
          region: 'us-east-1',
          already_exists: true,
        },
      ],
      errors: [],
    }
    const fetchMock = stubFetch(
      jsonResponse(preview),
      jsonResponse({ imported: 1, skipped: 0, target_names: ['orders'] })
    )

    const discovered = await fetchFleetDiscoverPreview({
      regions: ['us-east-1'],
      profile: 'dev',
    })
    expect(discovered.members[1].already_exists).toBe(true)
    await expect(
      bulkAddFleetTargets([discovered.members[0]])
    ).resolves.toMatchObject({ target_names: ['orders'] })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/providers/discover-preview',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          regions: ['us-east-1'],
          profile: 'dev',
        }),
      })
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/providers/bulk-add',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ members: [preview.members[0]] }),
      })
    )
  })
})
