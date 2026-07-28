import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  jsonResponse,
  runSurvivesRemountAndCancel,
  sseFrames as frames,
  sseResponse,
} from '@/test-utils'
import {
  __resetAuditSessionForTests,
  getActiveAuditSession,
} from './auditSession'
import { __resetBackgroundRunsForTests } from './backgroundRuns'
import { api } from './client'
import {
  bulkAddFleetTargets,
  createFleetAwsProfile,
  fetchFleetDiscoverPreview,
  FleetAwsLoginError,
  fetchFleetAwsLogin,
  fetchFleetAwsStatus,
  fleetAwsLogout,
  startFleetAwsLogin,
  updateFleetTargetGroup,
  useFleetAudit,
} from './useFleet'

vi.mock('./client', () => ({
  api: { POST: vi.fn(), GET: vi.fn(), DELETE: vi.fn() },
}))

const FLEET_RUN_ID = 'fleet_audit_fleet_20260727'

beforeEach(() => {
  __resetBackgroundRunsForTests()
  vi.mocked(api.POST).mockResolvedValue({
    data: { run_id: FLEET_RUN_ID, reused: false },
    error: undefined,
  } as never)
  vi.mocked(api.DELETE).mockResolvedValue({
    data: { run_id: FLEET_RUN_ID, cancelled: true },
    error: undefined,
  } as never)
})

afterEach(() => {
  __resetAuditSessionForTests()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

/**
 * A per-target `status` frame. `message` is derived unless a test asserts on
 * it, in which case it comes through `extra`.
 */
function status(
  target: string | null,
  phase: string,
  step?: string,
  extra: Record<string, unknown> = {}
): [string, Record<string, unknown>] {
  return [
    'status',
    {
      phase,
      message: target ? `${phase} ${target}` : phase,
      ...(target ? { target_name: target } : {}),
      ...(step ? { step } : {}),
      ...extra,
    },
  ]
}

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

describe('useFleetAudit', () => {
  it('keeps a fleet run across remount and aborts it through cancel', async () => {
    const cancelled = await runSurvivesRemountAndCancel({
      renderRunHook: () => renderHook(() => useFleetAudit()),
      start: (hook) =>
        void hook.runAudit({ targets: ['a', 'b'], duration: 30 }),
      assertRunning: (hook) => {
        expect(hook.running).toBe(true)
        expect(getActiveAuditSession()?.targetNames).toEqual(['a', 'b'])
        expect(getActiveAuditSession()?.targetLabel).toBe('2 targets')
        expect(getActiveAuditSession()?.runId).toBe(FLEET_RUN_ID)
      },
    })

    expect(cancelled.state).toBe('idle')
    expect(getActiveAuditSession()).toBeNull()
    expect(api.DELETE).toHaveBeenCalledWith('/api/runs/{run_id}', {
      params: { path: { run_id: FLEET_RUN_ID } },
    })
  })

  it('starts one background run and tracks per-target progress', async () => {
    const payload = frames(
      ['target_start', { target_name: 'a', index: 0, total: 2 }],
      [
        'target_complete',
        {
          target_name: 'a',
          result: {
            sizing: { verdict: 'oversized' },
            cache_opportunity: { score: 70 },
          },
          index: 0,
          total: 2,
        },
      ],
      [
        'target_error',
        {
          target_name: 'b',
          error: 'connection refused',
          index: 1,
          total: 2,
        },
      ],
      ['complete', { success: true, summary: { successes: 1, failures: 1 } }],
      ['run_end', { status: 'done' }]
    )
    const fetchMock = vi.fn(async () => sseResponse(payload))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useFleetAudit())

    await act(async () => {
      await result.current.runAudit({ targets: ['a', 'b'], duration: 30 })
    })

    expect(api.POST).toHaveBeenCalledWith('/api/fleet/audit', {
      body: { targets: ['a', 'b'], duration: 30 },
    })
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/runs/${FLEET_RUN_ID}/events?after_seq=0`,
      { signal: expect.any(AbortSignal) }
    )
    expect(result.current.state).toBe('complete')
    expect(result.current.targets.a).toEqual({
      status: 'done',
      verdict: 'oversized',
      cacheScore: 70,
    })
    expect(result.current.targets.b).toEqual({
      status: 'error',
      error: 'connection refused',
    })
    expect(result.current.summary?.successes).toBe(1)
  })

  it('follows the run already in flight when the server reuses one', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      data: { run_id: 'fleet_audit_prod_earlier', reused: true },
      error: undefined,
    } as never)
    const fetchMock = vi.fn(async () =>
      sseResponse(
        frames(
          ['complete', { success: true, snapshot_id: 'fleet_earlier' }],
          ['run_end', { status: 'done' }]
        )
      )
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useFleetAudit())
    await act(async () => {
      await result.current.runAudit({ targets: ['a'] })
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/fleet_audit_prod_earlier/events?after_seq=0',
      { signal: expect.any(AbortSignal) }
    )
    expect(result.current.state).toBe('complete')
    expect(result.current.snapshotId).toBe('fleet_earlier')
  })

  it('reports a run that ends without completing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(frames(['run_end', { status: 'failed' }])))
    )

    const { result } = renderHook(() => useFleetAudit())
    await act(async () => {
      await result.current.runAudit({ targets: ['a'] })
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toBe(
      'Fleet health check ended before completing'
    )
    expect(getActiveAuditSession()).toBeNull()
  })

  it('captures the machine-readable code of the target-cap error', async () => {
    const payload = frames(
      [
        'error',
        {
          message:
            'Audit up to 8 targets at once — filter by group or tag (12 targets selected)',
          code: 'too_many_targets',
          stage: 'start',
        },
      ],
      ['run_end', { status: 'failed' }]
    )
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(payload))
    )

    const { result } = renderHook(() => useFleetAudit())

    await act(async () => {
      await result.current.runAudit({})
    })

    expect(result.current.state).toBe('error')
    expect(result.current.errorCode).toBe('too_many_targets')
    expect(result.current.error).toMatch(/filter by group/)
  })

  it('keeps the latest target phase and surfaces fleet insights as a phase', async () => {
    const payload = frames(
      ['target_start', { target_name: 'a', index: 0, total: 1 }],
      status('a', 'capture', 'capturing', {
        elapsed_seconds: 10,
        total_seconds: 30,
      }),
      status('a', 'analysis', 'analyzing'),
      status('a', 'readyset', 'deploying', { message: 'Starting Readyset' }),
      status(null, 'insights')
    )
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(payload))
    )

    const { result } = renderHook(() => useFleetAudit())
    await act(async () => {
      await result.current.runAudit({ targets: ['a'], duration: 30 })
    })

    expect(result.current.phase).toBe('insights')
    expect(result.current.targets.a.phase).toBe('readyset')
    expect(result.current.targets.a.statusMessage).toBe('Starting Readyset')
    expect(result.current.targets.a.benchmarkStep).toBe('deploying')
  })

  it('tracks simultaneous captures before sequential Readyset benchmarks', async () => {
    // The Jobs chip and this hook each hold their own subscription to the run,
    // so feed every reader that attaches.
    const readers: ReadableStreamDefaultController<Uint8Array>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                readers.push(controller)
              },
            }),
            { status: 200 }
          )
      )
    )
    const emit = (...items: Array<[string, Record<string, unknown>]>) => {
      const chunk = new TextEncoder().encode(frames(...items))
      for (const reader of readers) reader.enqueue(chunk)
    }

    const { result } = renderHook(() => useFleetAudit())
    let run: Promise<void> | undefined
    await act(async () => {
      run = result.current.runAudit({
        targets: ['orders', 'users'],
        duration: 30,
      })
    })

    await act(async () => {
      emit(
        ['target_start', { target_name: 'orders', index: 0, total: 2 }],
        ['target_start', { target_name: 'users', index: 1, total: 2 }],
        status('orders', 'capture', 'capturing', {
          elapsed_seconds: 1,
          total_seconds: 30,
        }),
        status('users', 'capture', 'capturing', {
          elapsed_seconds: 1,
          total_seconds: 30,
        })
      )
    })
    expect(result.current.targets.orders.phase).toBe('capture')
    expect(result.current.targets.users.phase).toBe('capture')
    expect(
      Object.values(result.current.targets).filter(
        (target) => target.status === 'running'
      )
    ).toHaveLength(2)

    await act(async () => {
      emit(
        status('orders', 'capture_complete', 'queued'),
        status('users', 'capture_complete', 'queued'),
        status('orders', 'readyset', 'deploying')
      )
    })
    expect(result.current.targets.orders.phase).toBe('readyset')
    expect(result.current.targets.orders.benchmarkStep).toBe('deploying')
    expect(result.current.targets.users.phase).toBe('capture_complete')

    await act(async () => {
      emit(
        status('orders', 'readyset', 'done'),
        [
          'target_complete',
          { target_name: 'orders', result: {}, index: 0, total: 2 },
        ],
        status('users', 'readyset', 'deploying'),
        status(null, 'insights')
      )
    })
    expect(result.current.targets.orders.status).toBe('done')
    expect(result.current.targets.users.phase).toBe('readyset')
    expect(result.current.targets.users.benchmarkStep).toBe('deploying')
    expect(result.current.phase).toBe('insights')

    await act(async () => {
      for (const reader of readers) reader.close()
      await run
    })
  })
})

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
