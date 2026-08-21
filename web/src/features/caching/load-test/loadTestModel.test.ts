import { describe, expect, it } from 'vitest'
import {
  deriveLoadTestResultModel,
  readLanesRun,
  readQueryLanes,
  readRequestLanes,
} from './loadTestModel'

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

  it('reads no skipped queries from a payload that predates skip reporting', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 4,
        queries: [],
      },
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.skippedQueries).toEqual([])
  })

  it('reports comparative lane aggregates when every query carries origin and readyset stats', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 10,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 1,
        queries: [
          {
            query_hash: 'q1',
            query_name: 'Q1',
            executions: 8,
            successes: 8,
            failures: 0,
            avg_ms: 10,
            min_ms: 1,
            max_ms: 20,
            p50_ms: 9,
            p95_ms: 15,
            p99_ms: 20,
            lanes: {
              origin: {
                successes: 8,
                failures: 0,
                avg_ms: 10,
                p95_ms: 15,
                p99_ms: 20,
              },
              readyset: {
                successes: 8,
                failures: 0,
                avg_ms: 2,
                p95_ms: 3,
                p99_ms: 4,
              },
            },
          },
          {
            query_hash: 'q2',
            query_name: 'Q2',
            executions: 2,
            successes: 2,
            failures: 0,
            avg_ms: 20,
            min_ms: 15,
            max_ms: 30,
            p50_ms: 18,
            p95_ms: 25,
            p99_ms: 30,
            lanes: {
              origin: {
                successes: 2,
                failures: 0,
                avg_ms: 20,
                p95_ms: 25,
                p99_ms: 30,
              },
              readyset: {
                successes: 2,
                failures: 0,
                avg_ms: 4,
                p95_ms: 5,
                p99_ms: 6,
              },
            },
          },
        ],
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.comparative).toBe(true)
    expect(result.readysetSetup).toBeUndefined()
    expect(result.laneAggregates?.origin.successes).toBe(10)
    expect(result.laneAggregates?.origin.meanLatency).toBeCloseTo(12)
    expect(result.laneAggregates?.readyset.meanLatency).toBeCloseTo(2.4)
    expect(result.speedup).toBeCloseTo(5)
  })

  it('stays origin-only when no query carries lane data', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 10,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 1,
        queries: [
          {
            query_hash: 'q1',
            query_name: 'Q1',
            executions: 10,
            successes: 10,
            failures: 0,
            avg_ms: 10,
            min_ms: 1,
            max_ms: 20,
            p50_ms: 9,
            p95_ms: 15,
            p99_ms: 20,
          },
        ],
      },
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.comparative).toBe(false)
    expect(result.laneAggregates).toBeUndefined()
    expect(result.speedup).toBeNull()
  })

  it('reports Readyset cache preparation while the run is still silent', () => {
    const result = deriveLoadTestResultModel({
      state: 'running',
      status: 'running',
      stage: 'preparing',
      progress: {
        type: 'progress',
        elapsed_seconds: 0,
        total_executions: 0,
        total_successes: 0,
        total_failures: 0,
        qps: 0,
        queries: [],
        phase: 'preparing',
        prepared_count: 1,
        prepare_total: 3,
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.preparation).toEqual({ preparedCount: 1, prepareTotal: 3 })
    expect(result.statusLabel).toBe('Preparing')
    expect(result.title).toContain('1 of 3')
  })

  it('keeps a measuring tick out of the preparing state', () => {
    const result = deriveLoadTestResultModel({
      state: 'running',
      status: 'running',
      stage: 'running',
      progress: {
        type: 'progress',
        elapsed_seconds: 2,
        total_executions: 20,
        total_successes: 20,
        total_failures: 0,
        qps: 10,
        queries: [],
      },
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.preparation).toBeUndefined()
    expect(result.title).toBe('Load test in progress')
  })

  it('ignores a preparing tick whose counts are missing', () => {
    const result = deriveLoadTestResultModel({
      state: 'running',
      status: 'running',
      stage: 'preparing',
      progress: {
        type: 'progress',
        elapsed_seconds: 0,
        total_executions: 0,
        total_successes: 0,
        total_failures: 0,
        qps: 0,
        queries: [],
        phase: 'preparing',
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.preparation).toBeUndefined()
    expect(result.statusLabel).toBe('Running')
  })

  it('reads a neutral Readyset-unavailable note and keeps origin-only rendering', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 10,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 1,
        queries: [],
        readyset_setup: {
          status: 'unavailable',
          detail: 'Readyset could not be reached for this run.',
        },
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.comparative).toBe(false)
    expect(result.readysetSetup).toEqual({
      status: 'unavailable',
      detail: 'Readyset could not be reached for this run.',
    })
  })

  it('drops a malformed readyset_setup payload rather than misreporting status', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'done',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 10,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 1,
        queries: [],
        readyset_setup: { status: 'not-a-real-status' },
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.readysetSetup).toBeUndefined()
  })

  it('reads well-formed skipped queries and drops malformed entries', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'partial',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 4,
        queries: [],
        skipped_queries: [
          { query_hash: 'abc123', reason: 'No stored value for $1' },
          { reason: 'missing a query_hash' },
          'not an object',
        ],
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.skippedQueries).toEqual([
      { query_hash: 'abc123', reason: 'No stored value for $1' },
    ])
  })

  it('reads per-lane skip reasons when the backend distinguishes them', () => {
    const result = deriveLoadTestResultModel({
      state: 'complete',
      status: 'partial',
      stage: 'complete',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 4,
        queries: [],
        skipped_queries: [
          {
            query_hash: 'abc123',
            reason: 'No stored value for $1',
            lanes: {
              origin: 'No stored value for $1',
              readyset: 'Query is not cacheable',
            },
          },
        ],
      } as unknown as Parameters<
        typeof deriveLoadTestResultModel
      >[0]['progress'],
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    expect(result.skippedQueries).toEqual([
      {
        query_hash: 'abc123',
        reason: 'No stored value for $1',
        laneReasons: {
          origin: 'No stored value for $1',
          readyset: 'Query is not cacheable',
        },
      },
    ])
  })
})

describe('lane reader helpers', () => {
  it('reads a request lanes field defensively, dropping unknown lane names', () => {
    expect(
      readRequestLanes({
        ...request,
        lanes: ['origin', 'readyset', 'bogus'],
      } as unknown as Parameters<typeof readRequestLanes>[0])
    ).toEqual(['origin', 'readyset'])
  })

  it('returns undefined for a request that predates the lanes field', () => {
    expect(readRequestLanes(request)).toBeUndefined()
  })

  it('reads lanes_run from a complete payload, filtering unknown entries', () => {
    expect(
      readLanesRun({
        type: 'complete',
        elapsed_seconds: 1,
        total_executions: 1,
        total_successes: 1,
        total_failures: 0,
        qps: 1,
        queries: [],
        lanes_run: ['origin', 'readyset', 'bogus'],
      } as unknown as Parameters<typeof readLanesRun>[0])
    ).toEqual(['origin', 'readyset'])
  })

  it('returns undefined lanes_run for a payload that predates it', () => {
    expect(
      readLanesRun({
        type: 'complete',
        elapsed_seconds: 1,
        total_executions: 1,
        total_successes: 1,
        total_failures: 0,
        qps: 1,
        queries: [],
      })
    ).toBeUndefined()
  })

  it('reads full origin/readyset lane stats from a query entry', () => {
    const query = {
      query_hash: 'q1',
      query_name: 'Q1',
      executions: 1,
      successes: 1,
      failures: 0,
      avg_ms: 1,
      min_ms: 1,
      max_ms: 1,
      p50_ms: 1,
      p95_ms: 1,
      p99_ms: 1,
      lanes: {
        origin: {
          successes: 1,
          failures: 0,
          avg_ms: 1,
          p95_ms: 1,
          p99_ms: 1,
        },
        readyset: {
          successes: 1,
          failures: 0,
          avg_ms: 0.2,
          p95_ms: 0.3,
          p99_ms: 0.4,
        },
      },
    } as unknown as Parameters<typeof readQueryLanes>[0]

    expect(readQueryLanes(query)?.readyset.avg_ms).toBe(0.2)
  })

  it('rejects a lanes object missing the readyset side', () => {
    const query = {
      query_hash: 'q1',
      query_name: 'Q1',
      executions: 1,
      successes: 1,
      failures: 0,
      avg_ms: 1,
      min_ms: 1,
      max_ms: 1,
      p50_ms: 1,
      p95_ms: 1,
      p99_ms: 1,
      lanes: {
        origin: {
          successes: 1,
          failures: 0,
          avg_ms: 1,
          p95_ms: 1,
          p99_ms: 1,
        },
      },
    } as unknown as Parameters<typeof readQueryLanes>[0]

    expect(readQueryLanes(query)).toBeUndefined()
  })
})
