import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BenchmarkRequest } from './api'
import type { BackgroundRunState } from './backgroundRuns'

const mocks = vi.hoisted(() => ({
  runs: [] as BackgroundRunState[],
  startLoadTestRun: vi.fn(),
  cancelBackgroundRun: vi.fn(),
}))

vi.mock('./backgroundRuns', () => ({
  useBackgroundRuns: () => mocks.runs,
  startLoadTestRun: mocks.startLoadTestRun,
  cancelBackgroundRun: mocks.cancelBackgroundRun,
}))

vi.mock('./targetSwitchLock', () => ({
  useTargetSwitchLock: vi.fn(),
}))

vi.mock('./analytics', () => ({
  trackEvent: vi.fn(),
}))

import { trackEvent } from './analytics'
import { useBenchmark } from './sse'

function run(
  runId: string,
  target: string,
  status: BackgroundRunState['status']
): BackgroundRunState {
  return {
    runId,
    kind: 'load_test',
    target,
    stage: status,
    status,
    message: runId,
    lastSeq: 1,
    current: null,
    total: null,
    hasWarnings: false,
  }
}

describe('useBenchmark run selection', () => {
  beforeEach(() => {
    mocks.runs = []
    mocks.startLoadTestRun.mockReset()
    mocks.cancelBackgroundRun.mockReset()
  })

  it('falls back only to an active run for the selected target', () => {
    mocks.runs = [
      run('done-a', 'target-a', 'done'),
      run('running-b', 'target-b', 'running'),
    ]

    const { result } = renderHook(() => useBenchmark(undefined, 'target-a'))

    expect(result.current.state).toBe('idle')
    expect(result.current.status).toBeUndefined()
  })

  it('stays idle after reset instead of selecting an older terminal run', () => {
    mocks.runs = [
      run('older-failed', 'target-a', 'failed'),
      run('selected-done', 'target-a', 'done'),
    ]
    const { result } = renderHook(() =>
      useBenchmark('selected-done', 'target-a')
    )
    expect(result.current.state).toBe('complete')
    expect(result.current.status).toBe('done')

    act(() => result.current.reset())

    expect(result.current.state).toBe('idle')
  })

  it('opens the explicitly selected historical run', () => {
    mocks.runs = [
      run('older-failed', 'target-a', 'failed'),
      run('newer-done', 'target-a', 'done'),
    ]

    const { result } = renderHook(() =>
      useBenchmark('older-failed', 'target-a')
    )

    expect(result.current.state).toBe('error')
    expect(result.current.status).toBe('failed')
    expect(result.current.error).toBe('older-failed')
  })

  it('preserves cancelled and partial terminal statuses for the result UI', () => {
    mocks.runs = [
      run('cancelled-run', 'target-a', 'cancelled'),
      run('partial-run', 'target-b', 'partial'),
    ]

    const cancelled = renderHook(() =>
      useBenchmark('cancelled-run', 'target-a')
    )
    const partial = renderHook(() => useBenchmark('partial-run', 'target-b'))

    expect(cancelled.result.current.state).toBe('complete')
    expect(cancelled.result.current.status).toBe('cancelled')
    expect(partial.result.current.state).toBe('complete')
    expect(partial.result.current.status).toBe('partial')
  })
})

describe('load_test_run analytics (E1)', () => {
  const request: BenchmarkRequest = {
    queries: ['Q1'],
    target: 'demo',
    mode: 'interval',
    interval_ms: 100,
    concurrency: 1,
    duration_seconds: 30,
  }

  beforeEach(() => {
    mocks.runs = []
    mocks.startLoadTestRun.mockReset()
    vi.mocked(trackEvent).mockClear()
  })

  it('tracks load_test_run once the run actually starts', async () => {
    mocks.startLoadTestRun.mockResolvedValue('new-run-id')
    const { result } = renderHook(() => useBenchmark())

    await act(async () => {
      await result.current.start(request)
    })

    expect(trackEvent).toHaveBeenCalledWith('load_test_run')
  })

  it('does not track when the run fails to start', async () => {
    mocks.startLoadTestRun.mockResolvedValue(null)
    const { result } = renderHook(() => useBenchmark())

    await act(async () => {
      await result.current.start(request)
    })

    expect(trackEvent).not.toHaveBeenCalled()
  })
})
