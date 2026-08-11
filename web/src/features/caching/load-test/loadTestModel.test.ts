import { describe, expect, it } from 'vitest'
import { deriveLoadTestResultModel } from './loadTestModel'

const request = {
  queries: ['Q1'],
  target: 'demo',
  mode: 'interval' as const,
  interval_ms: 100,
  concurrency: 1,
  duration_seconds: 30,
}

describe('deriveLoadTestResultModel', () => {
  it('does not misreport cancellation as failure when there are no samples', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'cancelled',
      stage: 'cancelled',
      progress: undefined,
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.outcome).toBe('cancelled')
    expect(result.statusLabel).toBe('Cancelled')
    expect(result.title).toBe('Load test cancelled')
  })

  it('distinguishes a partial run from an all-failed run', () => {
    const base = {
      type: 'complete' as const,
      elapsed_seconds: 30,
      qps: 10,
      queries: [],
    }
    const partial = deriveLoadTestResultModel({
      state: 'complete',
      status: 'partial',
      stage: 'complete',
      progress: {
        ...base,
        total_executions: 10,
        total_successes: 8,
        total_failures: 2,
      },
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })
    const failed = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        ...base,
        total_executions: 10,
        total_successes: 0,
        total_failures: 10,
      },
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(partial.outcome).toBe('partial')
    expect(failed.outcome).toBe('failed')
  })

  it('labels a successful paced run as observed rather than capacity', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 120,
        total_successes: 120,
        total_failures: 0,
        qps: 4,
        queries: [],
      },
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.outcome).toBe('complete')
    expect(result.title).toContain('observed QPS')
  })
})
