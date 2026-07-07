import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { isWorkloadRun, useAuditCapture, useAuditRun } from './useAudit'
import { __resetTargetSwitchLockForTests } from './targetSwitchLock'

function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

describe('useAuditRun', () => {
  it('collects status, report, snapshot id, and completes', async () => {
    __resetTargetSwitchLockForTests()
    const payload = [
      'event: status\ndata: {"type":"status","phase":"collect","message":"Collecting database metrics..."}\n\n',
      'event: snapshot_saved\ndata: {"type":"snapshot_saved","snapshot_id":"audit_prod_20260706_000000"}\n\n',
      'event: target_complete\ndata: {"type":"target_complete","target_name":"prod","result":{"target_name":"prod","engine":"postgresql"},"index":0,"total":1}\n\n',
      'event: complete\ndata: {"type":"complete","success":true,"snapshot_id":"audit_prod_20260706_000000"}\n\n',
    ].join('')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(payload)))

    const { result } = renderHook(() => useAuditRun())

    await act(async () => {
      await result.current.run('prod')
    })

    expect(result.current.state).toBe('complete')
    expect(result.current.report?.target_name).toBe('prod')
    expect(result.current.snapshotId).toBe('audit_prod_20260706_000000')
    expect(result.current.error).toBeUndefined()
  })

  it('surfaces target errors on failed runs', async () => {
    __resetTargetSwitchLockForTests()
    const payload = [
      'event: target_error\ndata: {"type":"target_error","target_name":"prod","error":"connection refused","index":0,"total":1}\n\n',
      'event: complete\ndata: {"type":"complete","success":false}\n\n',
    ].join('')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(payload)))

    const { result } = renderHook(() => useAuditRun())

    await act(async () => {
      await result.current.run('prod')
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toBe('connection refused')
    expect(result.current.report).toBeUndefined()
  })
})

describe('isWorkloadRun', () => {
  it('detects capture payloads by queries + run_id and absence of metrics', () => {
    expect(isWorkloadRun({ run_id: 'r1', queries: [] })).toBe(true)
    expect(isWorkloadRun({ run_id: 'r1', queries: [], metrics: {} })).toBe(false)
    expect(isWorkloadRun({ target_name: 'prod', metrics: {} })).toBe(false)
    expect(isWorkloadRun(null)).toBe(false)
    expect(isWorkloadRun('nope')).toBe(false)
  })
})

describe('useAuditCapture', () => {
  it('tracks progress, completes with summary + analysis', async () => {
    __resetTargetSwitchLockForTests()
    const payload = [
      'data: {"type":"status","phase":"config","message":"Configuring capture..."}\n\n',
      'data: {"type":"connected","target_name":"prod","db_engine":"postgresql","source":"pg_stat"}\n\n',
      'data: {"type":"capture_progress","elapsed_seconds":2,"total_seconds":30,"unique_queries":3,"total_executions":12,"cache_hit_ratio":98.5,"active_connections":2,"tps":6}\n\n',
      'data: {"type":"capture_complete","unique_queries":5,"total_executions":40,"total_query_time_ms":123.4,"duration_seconds":30}\n\n',
      'data: {"type":"queries_saved","count":5,"hashes":["a","b"]}\n\n',
      'data: {"type":"complete","success":true,"run_id":"cap_prod_1","summary":{"unique_queries":5,"total_executions":40,"total_query_time_ms":123.4,"duration_seconds":30,"queries":[{"query_hash":"a","query_text":"SELECT 1","calls":40}]},"analysis":{"health_score":80,"workload_characterization":"read-heavy"}}\n\n',
    ].join('')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(payload)))

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('complete')
    expect(result.current.result?.runId).toBe('cap_prod_1')
    expect(result.current.result?.summary?.unique_queries).toBe(5)
    expect(result.current.result?.analysis?.health_score).toBe(80)
    expect(result.current.error).toBeUndefined()
  })

  it('surfaces a graceful analysis-failure warning without erroring', async () => {
    __resetTargetSwitchLockForTests()
    const payload = [
      'data: {"type":"status","phase":"analysis","message":"Analysis failed: invalid API key"}\n\n',
      'data: {"type":"complete","success":true,"run_id":"cap_prod_2","summary":{"unique_queries":1,"queries":[]},"analysis":null}\n\n',
    ].join('')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(payload)))

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('complete')
    expect(result.current.analysisWarning).toMatch(/Analysis failed/)
    expect(result.current.result?.analysis).toBeUndefined()
    expect(result.current.error).toBeUndefined()
  })

  it('surfaces the concurrency-guard error', async () => {
    __resetTargetSwitchLockForTests()
    const payload =
      'data: {"type":"error","message":"A capture is already running for \'prod\'","phase":"config"}\n\n'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(payload)))

    const { result } = renderHook(() => useAuditCapture())

    await act(async () => {
      await result.current.run('prod', { duration: 30 })
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toMatch(/already running/)
  })
})
