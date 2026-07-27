import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  runSurvivesRemountAndCancel,
  sseFrames as frames,
  sseResponse,
} from '@/test-utils'
import {
  fetchAuditRequirements,
  isWorkloadRun,
  useAuditCapture,
  useAuditRun,
} from './useAudit'
import { __resetBackgroundRunsForTests } from './backgroundRuns'
import { api } from './client'
import { __resetTargetSwitchLockForTests } from './targetSwitchLock'
import {
  __resetAuditSessionForTests,
  getActiveAuditSession,
} from './auditSession'

vi.mock('./client', () => ({
  api: { POST: vi.fn(), GET: vi.fn(), DELETE: vi.fn() },
}))

beforeEach(() => {
  __resetBackgroundRunsForTests()
  __resetTargetSwitchLockForTests()
  vi.mocked(api.POST).mockImplementation(
    async (path: string) =>
      ({
        data: {
          run_id:
            path === '/api/audit/capture' ? 'audit_capture_prod' : 'audit_prod',
          reused: false,
        },
        error: undefined,
      }) as never
  )
  vi.mocked(api.DELETE).mockResolvedValue({
    data: { run_id: 'audit_prod', cancelled: true },
    error: undefined,
  } as never)
})

afterEach(() => {
  __resetAuditSessionForTests()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('useAuditRun', () => {
  it('survives hook remount and cancellation cancels the background run', async () => {
    const cancelled = await runSurvivesRemountAndCancel({
      renderRunHook: () => renderHook(() => useAuditRun()),
      start: (hook) => void hook.run('prod'),
      assertRunning: (hook) => {
        expect(hook.state).toBe('running')
        expect(getActiveAuditSession()?.targetLabel).toBe('prod')
        expect(getActiveAuditSession()?.runId).toBe('audit_prod')
      },
    })

    expect(cancelled.state).toBe('idle')
    expect(getActiveAuditSession()).toBeNull()
    expect(api.DELETE).toHaveBeenCalledWith('/api/runs/{run_id}', {
      params: { path: { run_id: 'audit_prod' } },
    })
  })

  it('collects status, report, snapshot id, and completes', async () => {
    const payload = frames(
      ['status', { phase: 'collect', message: 'Collecting database metrics...' }],
      ['snapshot_saved', { snapshot_id: 'audit_prod_20260706_000000' }],
      [
        'target_complete',
        {
          target_name: 'prod',
          result: { target_name: 'prod', engine: 'postgresql' },
          index: 0,
          total: 1,
        },
      ],
      ['complete', { success: true, snapshot_id: 'audit_prod_20260706_000000' }],
      ['run_end', { status: 'done' }]
    )
    const fetchMock = vi.fn(async () => sseResponse(payload))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAuditRun())

    await act(async () => {
      await result.current.run('prod')
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/audit_prod/events?after_seq=0',
      { signal: expect.any(AbortSignal) }
    )
    expect(result.current.state).toBe('complete')
    expect(result.current.report?.target_name).toBe('prod')
    expect(result.current.snapshotId).toBe('audit_prod_20260706_000000')
    expect(result.current.error).toBeUndefined()
  })

  it('surfaces target errors on failed runs', async () => {
    const payload = frames(
      [
        'target_error',
        { target_name: 'prod', error: 'connection refused', index: 0, total: 1 },
      ],
      ['complete', { success: false }],
      ['run_end', { status: 'failed' }]
    )
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(payload)))

    const { result } = renderHook(() => useAuditRun())

    await act(async () => {
      await result.current.run('prod')
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toBe('connection refused')
    expect(result.current.report).toBeUndefined()
  })

  it('reports a run that ends without completing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(frames(['run_end', { status: 'failed' }])))
    )

    const { result } = renderHook(() => useAuditRun())

    await act(async () => {
      await result.current.run('prod')
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toBe('Audit ended before completing')
    expect(getActiveAuditSession()).toBeNull()
  })
})

describe('fetchAuditRequirements', () => {
  it('returns the requirements payload for a target', async () => {
    const payload = {
      target: 'prod',
      engine: 'postgresql',
      query_stats: 'missing',
      detail: 'pg_stat_statements is not in shared_preload_libraries',
      remediation: 'CREATE EXTENSION pg_stat_statements;',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchAuditRequirements('prod')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/audit/requirements?target=prod'
    )
    expect(result.query_stats).toBe('missing')
    expect(result.remediation).toBe('CREATE EXTENSION pg_stat_statements;')
  })

  it('encodes the target name and throws on HTTP failure', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('target not found', { status: 404 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchAuditRequirements('my db')).rejects.toThrow(
      'target not found'
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/audit/requirements?target=my%20db'
    )
  })
})

describe('isWorkloadRun', () => {
  it('detects capture payloads by queries + run_id, including merged audit fields', () => {
    expect(isWorkloadRun({ run_id: 'r1', queries: [] })).toBe(true)
    expect(isWorkloadRun({ run_id: 'r1', queries: [], metrics: {} })).toBe(true)
    expect(isWorkloadRun({ target_name: 'prod', metrics: {} })).toBe(false)
    expect(isWorkloadRun(null)).toBe(false)
    expect(isWorkloadRun('nope')).toBe(false)
  })
})

describe('useAuditCapture', () => {
  it('tracks progress, completes with summary + analysis', async () => {
    const payload = frames(
      ['status', { phase: 'config', message: 'Configuring capture...' }],
      [
        'connected',
        { target_name: 'prod', db_engine: 'postgresql', source: 'pg_stat' },
      ],
      [
        'capture_progress',
        {
          elapsed_seconds: 2,
          total_seconds: 30,
          unique_queries: 3,
          total_executions: 12,
          cache_hit_ratio: 98.5,
          active_connections: 2,
          tps: 6,
        },
      ],
      [
        'capture_complete',
        {
          unique_queries: 5,
          total_executions: 40,
          total_query_time_ms: 123.4,
          duration_seconds: 30,
        },
      ],
      ['queries_saved', { count: 5, hashes: ['a', 'b'] }],
      [
        'complete',
        {
          success: true,
          run_id: 'cap_prod_1',
          summary: {
            unique_queries: 5,
            total_executions: 40,
            total_query_time_ms: 123.4,
            duration_seconds: 30,
            queries: [{ query_hash: 'a', query_text: 'SELECT 1', calls: 40 }],
          },
          analysis: { health_score: 80, workload_characterization: 'read-heavy' },
        },
      ],
      ['run_end', { status: 'done' }]
    )
    const fetchMock = vi.fn(async () => sseResponse(payload))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('complete')
    expect(result.current.result?.runId).toBe('cap_prod_1')
    expect(result.current.result?.summary?.unique_queries).toBe(5)
    expect(result.current.result?.analysis?.health_score).toBe(80)
    expect(result.current.error).toBeUndefined()
    expect(api.POST).toHaveBeenCalledWith('/api/audit/capture', {
      body: { target: 'prod', duration: 30, analysis: true, readyset: true },
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/audit_capture_prod/events?after_seq=0',
      { signal: expect.any(AbortSignal) }
    )
  })

  it('surfaces a graceful analysis-failure warning without erroring', async () => {
    const payload = frames(
      ['status', { phase: 'analysis', message: 'Analysis failed: invalid API key' }],
      [
        'complete',
        {
          success: true,
          run_id: 'cap_prod_2',
          summary: { unique_queries: 1, queries: [] },
          analysis: null,
        },
      ],
      ['run_end', { status: 'done' }]
    )
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(payload)))

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('complete')
    expect(result.current.analysisWarning).toMatch(/Analysis failed/)
    expect(result.current.result?.analysis).toBeUndefined()
    expect(result.current.error).toBeUndefined()
  })

  it('attaches to the capture the server reports as already running', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      data: { run_id: 'audit_capture_prod', reused: true },
      error: undefined,
    } as never)
    const payload = frames(
      ['capture_progress', { elapsed_seconds: 12, total_seconds: 30 }],
      ['complete', { success: true, run_id: 'cap_prod_3', summary: null }],
      ['run_end', { status: 'done' }]
    )
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(payload)))

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('complete')
    expect(result.current.progress?.elapsedSeconds).toBe(12)
    expect(result.current.result?.runId).toBe('cap_prod_3')
  })

  it('reports a capture that could not be started', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      data: undefined,
      error: { message: 'target locked' },
    } as never)

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toBe('Capture could not start')
    expect(getActiveAuditSession()).toBeNull()
  })
})
