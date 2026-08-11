import { beforeEach, describe, expect, it } from 'vitest'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import type { CacheCompareRunResult } from '../../../types/cache'
import {
  type CompareBatch,
  clearActiveCompareBatch,
  compareBatchSnapshot,
  forgetCompareBatch,
  latestCompareBatch,
  listCompareBatches,
  selectCompareBatch,
  settleCompareBatch,
} from './compareRuns'

const RESULT: CacheCompareRunResult = {
  success: true,
  query: 'SELECT 1',
  duration_seconds: 30,
  elapsed_seconds: 30,
  concurrency: 2,
  origin: {
    scheduled: 150,
    completed: 150,
    errors: 0,
    dropped: 0,
    throughput_rps: 5,
    error_rate: 0,
    mean_ms: 10,
    p50_ms: 9,
    p95_ms: 12,
    p99_ms: 12,
  },
  readyset: {
    scheduled: 150,
    completed: 150,
    errors: 0,
    dropped: 0,
    throughput_rps: 5,
    error_rate: 0,
    mean_ms: 1,
    p50_ms: 1,
    p95_ms: 1.2,
    p99_ms: 1.2,
  },
  timeline: [],
  phases: [{ elapsed_seconds: 0, concurrency: 2 }],
  speedup_mean: 10,
  improvement_pct: 900,
  winner: 'readyset',
}

const BATCH: CompareBatch = {
  id: 'compare-1',
  target: 'demo',
  createdAt: '2026-07-23T10:00:00.000Z',
  concurrency: 4,
  durationSeconds: 30,
  queries: [
    { cacheId: 'one', label: 'One', runId: 'run-1' },
    { cacheId: 'two', label: 'Two', runId: 'run-2' },
  ],
}

function run(
  runId: string,
  status: BackgroundRunState['status'],
  options: Partial<BackgroundRunState> = {}
): BackgroundRunState {
  return {
    runId,
    kind: 'cache_compare',
    target: 'demo',
    stage: 'cache',
    status,
    message: '',
    lastSeq: 1,
    current: 50,
    total: 100,
    hasWarnings: false,
    ...options,
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe('compareBatchSnapshot', () => {
  it('reports aggregate progress without claiming completion', () => {
    const snapshot = compareBatchSnapshot(BATCH, [
      run('run-1', 'done', { compareResult: RESULT, current: 100 }),
      run('run-2', 'running', { current: 40 }),
    ])

    expect(snapshot).toMatchObject({
      status: 'running',
      completed: 1,
      succeeded: 1,
      failed: 0,
      percent: 70,
    })
  })

  it('distinguishes partial completion from total failure', () => {
    const partial = compareBatchSnapshot(BATCH, [
      run('run-1', 'done', { compareResult: RESULT, current: 100 }),
      run('run-2', 'failed', {
        current: null,
        message: 'Unsupported aggregate function',
        errorCode: 'unsupported_query',
      }),
    ])
    expect(partial.status).toBe('partial')
    expect(partial.queryOutcomes[1]).toMatchObject({
      cacheId: 'two',
      status: 'failed',
      message: 'Unsupported aggregate function',
      errorCode: 'unsupported_query',
    })

    const failed = compareBatchSnapshot(BATCH, [
      run('run-1', 'failed', { current: null }),
      run('run-2', 'failed', { current: null }),
    ])
    expect(failed.status).toBe('failed')
  })

  it('keeps cancelled separate from failed and complete', () => {
    const snapshot = compareBatchSnapshot(BATCH, [
      run('run-1', 'cancelled', { current: null }),
      run('run-2', 'cancelled', { current: null }),
    ])

    expect(snapshot.status).toBe('cancelled')
    expect(snapshot.cancelled).toBe(2)
  })

  it('treats interrupted terminal runs without results as failed outcomes', () => {
    const snapshot = compareBatchSnapshot(BATCH, [
      run('run-1', 'interrupted', { current: null }),
      run('run-2', 'failed', { current: null }),
    ])

    expect(snapshot.status).toBe('failed')
    expect(snapshot.completed).toBe(2)
    expect(snapshot.queryOutcomes.map(({ status }) => status)).toEqual([
      'failed',
      'failed',
    ])
  })
})

describe('compare batch persistence', () => {
  it('keeps the active result separate from target history', () => {
    localStorage.setItem(
      'rdst_capacity_compare_batches_v3',
      JSON.stringify([
        { ...BATCH, id: 'older', createdAt: '2026-07-22T10:00:00.000Z' },
        BATCH,
      ])
    )

    expect(latestCompareBatch('demo')).toBeNull()
    expect(listCompareBatches('demo').map((batch) => batch.id)).toEqual([
      'compare-1',
      'older',
    ])

    selectCompareBatch(BATCH)
    expect(latestCompareBatch('demo')?.id).toBe('compare-1')
    clearActiveCompareBatch('demo')
    expect(latestCompareBatch('demo')).toBeNull()

    selectCompareBatch(BATCH)
    forgetCompareBatch('compare-1')
    expect(latestCompareBatch('demo')).toBeNull()
    expect(listCompareBatches('demo').map((batch) => batch.id)).toEqual([
      'older',
    ])
  })

  it('settles results so history survives background-run cleanup', () => {
    localStorage.setItem(
      'rdst_capacity_compare_batches_v3',
      JSON.stringify([BATCH])
    )
    const snapshot = compareBatchSnapshot(BATCH, [
      run('run-1', 'done', { compareResult: RESULT, current: 100 }),
      run('run-2', 'failed', { current: null }),
    ])

    const settled = settleCompareBatch(BATCH, snapshot)
    const restored = listCompareBatches('demo')[0]

    expect(settled.outcome?.status).toBe('partial')
    expect(restored.results).toHaveLength(1)
    expect(restored.queries[1].outcome).toMatchObject({ status: 'failed' })
    const restoredSnapshot = compareBatchSnapshot(restored, [])
    expect(restoredSnapshot.status).toBe('partial')
    expect(restoredSnapshot.queryOutcomes[1]).toMatchObject({
      cacheId: 'two',
      status: 'failed',
    })
  })
})
