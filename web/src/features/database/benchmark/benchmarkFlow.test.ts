import { describe, expect, it } from 'vitest'
import type { BackgroundRunState, LoadTestProgress } from '@/lib/backgroundRuns'
import {
  benchmarkTimeline,
  latestQueryRun,
  maxRuns,
  PRESETS,
  presetById,
  presetForRequest,
  type QueryBenchmarkRun,
  queryRequest,
  readysetReadiness,
  runPhase,
  runShape,
  speedupLabel,
  summaryFromCompare,
  summaryFromRun,
} from './benchmarkFlow'

function stats(p50: number, n: number, lanes?: Record<string, unknown>) {
  return {
    query_name: 'q',
    query_hash: 'abc',
    executions: n,
    successes: n,
    failures: 0,
    min_ms: p50,
    avg_ms: p50,
    p50_ms: p50,
    p95_ms: p50 * 2,
    p99_ms: p50 * 3,
    max_ms: p50 * 4,
    ...(lanes ? { lanes } : {}),
  }
}

function run(overrides: Partial<BackgroundRunState> = {}): BackgroundRunState {
  return {
    runId: 'r1',
    kind: 'load_test',
    target: 'shop',
    stage: 'running',
    status: 'running',
    message: '',
    lastSeq: 0,
    current: null,
    total: null,
    hasWarnings: false,
    loadRequest: queryRequest({
      identifier: 'abc',
      target: 'shop',
      preset: presetById('quick'),
      withReadyset: false,
    }),
    ...overrides,
  }
}

function progress(
  elapsed: number,
  query: ReturnType<typeof stats>,
  extra: Record<string, unknown> = {}
): LoadTestProgress {
  return {
    type: 'complete',
    elapsed_seconds: elapsed,
    total_executions: query.executions,
    total_successes: query.successes,
    total_failures: 0,
    qps: query.successes / elapsed,
    queries: [query],
    ...extra,
  } as LoadTestProgress
}

function saved(
  ts: number,
  p50: number,
  executions: number,
  readyset: number | null = null
): QueryBenchmarkRun {
  const lane = (value: number) => ({
    executions,
    successes: executions,
    failures: 0,
    p50_ms: value,
    p95_ms: value * 2,
    avg_ms: value,
    qps: executions / 30,
  })
  return {
    id: ts,
    ts,
    outcome: 'done',
    mode: 'interval',
    lanes: readyset ? ['origin', 'readyset'] : ['origin'],
    duration_seconds: 30,
    concurrency: 1,
    interval_ms: 100,
    elapsed_seconds: 30,
    readyset_setup: null,
    query: {
      hash: 'abc',
      name: 'q',
      ...lane(p50),
      readyset: readyset ? lane(readyset) : null,
    },
  }
}

describe('presets', () => {
  it('offers a quick paced run and a standard back-to-back run', () => {
    expect(PRESETS.map((preset) => preset.id)).toEqual(['quick', 'standard'])
    expect(maxRuns(presetById('quick'))).toBe(300)
    expect(maxRuns(presetById('standard'))).toBeNull()
  })

  it('builds one query request, with the Readyset lane when asked', () => {
    expect(
      queryRequest({
        identifier: 'abc',
        sql: 'SELECT 1',
        target: 'shop',
        preset: presetById('standard'),
        withReadyset: true,
      })
    ).toEqual({
      queries: [{ identifier: 'abc', sql: 'SELECT 1' }],
      target: 'shop',
      mode: 'concurrency',
      interval_ms: 0,
      concurrency: 2,
      duration_seconds: 120,
      lanes: ['origin', 'readyset'],
    })
    expect(
      queryRequest({
        identifier: 'abc',
        target: 'shop',
        preset: presetById('quick'),
        withReadyset: false,
      }).lanes
    ).toEqual(['origin'])
  })

  it('reopens on the preset a past run used', () => {
    expect(
      presetForRequest({ mode: 'concurrency', duration_seconds: 120 })
    ).toBe('standard')
    expect(presetForRequest({ mode: 'interval', duration_seconds: 45 })).toBe(
      'quick'
    )
    expect(presetForRequest(undefined)).toBe('quick')
  })
})

describe('Readyset readiness', () => {
  it('is off without Docker, ready when the sandbox serves this database', () => {
    expect(
      readysetReadiness(
        { docker_installed: true, docker_running: false },
        'shop'
      ).state
    ).toBe('unavailable')
    expect(
      readysetReadiness(
        { docker_running: true, phase: 'ready', current_target: 'shop' },
        'shop'
      ).state
    ).toBe('ready')
    expect(
      readysetReadiness(
        { docker_running: true, phase: 'ready', current_target: 'other' },
        'shop'
      ).state
    ).toBe('setup')
    expect(readysetReadiness(undefined, 'shop').state).toBe('unknown')
  })
})

describe('runs of one query', () => {
  it('finds this query on this database, preferring a live run', () => {
    const done = run({ runId: 'a', status: 'done' })
    const live = run({ runId: 'b' })
    const other = run({
      runId: 'c',
      loadRequest: { ...run().loadRequest!, queries: ['abc', 'def'] },
    })
    const elsewhere = run({ runId: 'd', target: 'other' })
    expect(
      latestQueryRun([done, live, other, elsewhere], 'shop', ['abc'])?.runId
    ).toBe('b')
    expect(latestQueryRun([done, other], 'shop', ['abc'])?.runId).toBe('c')
    expect(latestQueryRun([other, elsewhere], 'shop', ['abc'])?.runId).toBe('c')
  })

  it('names the phase a live run is in', () => {
    expect(runPhase(run({ stage: 'queued' }))).toBe('waiting')
    expect(runPhase(run({ stage: 'preparing' }))).toBe('readyset')
    expect(runPhase(run({ loadResult: progress(5, stats(10, 40)) }))).toBe(
      'measuring'
    )
    expect(runPhase(run({ status: 'done' }))).toBe('done')
  })
})

describe('results', () => {
  it('reads latency, throughput and the Readyset speedup from a run', () => {
    const summary = summaryFromRun(
      run({
        status: 'done',
        loadResult: progress(
          30,
          stats(12, 300, {
            origin: stats(12, 300),
            readyset: stats(0.5, 600),
          })
        ),
      }),
      1000
    )
    expect(summary.database).toMatchObject({ p50: 12, p95: 24, qps: 10 })
    expect(summary.readyset).toMatchObject({ p50: 0.5, qps: 20 })
    expect(summary.speedup).toBe(24)
    expect(summary.readysetNote).toBeNull()
  })

  it("says why Readyset didn't measure when it was asked for", () => {
    const summary = summaryFromRun(
      run({
        status: 'done',
        loadRequest: {
          ...run().loadRequest!,
          lanes: ['origin', 'readyset'],
        } as never,
        loadResult: progress(30, stats(12, 300), {
          readyset_setup: {
            status: 'unavailable',
            detail: 'Docker is not running.',
          },
        }),
      })
    )
    expect(summary.readyset).toBeNull()
    expect(summary.readysetNote).toBe('Docker is not running.')
  })

  it('keeps an old Compare readable', () => {
    const summary = summaryFromCompare({
      ts: 50,
      verdict: 'improved',
      speedup: 8,
      origin_ms: 16,
      readyset_ms: 2,
    })
    expect(summary).toMatchObject({ source: 'compare', speedup: 8 })
    expect(runShape(summary)).toBe('Compared against Readyset')
  })

  it('words the speedup', () => {
    expect(speedupLabel(24)).toBe('24x faster')
    expect(speedupLabel(1.44)).toBe('1.4x faster')
    expect(speedupLabel(1.02)).toBe('about the same')
    expect(speedupLabel(0.5)).toBe('2.0x slower')
    expect(speedupLabel(null)).toBeNull()
  })
})

describe('timeline', () => {
  it('leads with a live run, then saved runs and an older Compare', () => {
    const timeline = benchmarkTimeline({
      run: run({ loadResult: progress(5, stats(10, 40)) }),
      history: [saved(300, 11, 280), saved(200, 12, 290)],
      lastCompare: { ts: 100, verdict: 'improved', speedup: 3 },
      nowSeconds: 400,
    })
    expect(timeline.latest?.source).toBe('run')
    expect(timeline.past.map((item) => item.source)).toEqual([
      'history',
      'history',
      'compare',
    ])
  })

  it('lets the saved copy stand in for a finished run', () => {
    const timeline = benchmarkTimeline({
      run: run({ status: 'done', loadResult: progress(30, stats(11, 280)) }),
      history: [saved(300, 11, 280)],
      nowSeconds: 400,
    })
    expect(timeline.latest?.source).toBe('history')
    expect(timeline.past).toEqual([])
  })

  it("shows a finished run the backend hasn't saved", () => {
    const timeline = benchmarkTimeline({
      run: run({ status: 'done', loadResult: progress(30, stats(9, 250)) }),
      history: [saved(300, 11, 280)],
      nowSeconds: 400,
    })
    expect(timeline.latest?.source).toBe('run')
    expect(timeline.past).toHaveLength(1)
  })

  it('is empty with nothing run', () => {
    expect(benchmarkTimeline({ history: [] })).toEqual({
      latest: null,
      past: [],
    })
  })
})

it('labels Readyset-only measurements without a source speedup', () => {
  const summary = summaryFromRun(
    run({
      status: 'done',
      loadRequest: { ...run().loadRequest!, lanes: ['readyset'] },
      loadResult: progress(30, stats(0.5, 300, { readyset: stats(0.5, 300) })),
    })
  )
  expect(summary.database).toBeNull()
  expect(summary.readyset).toMatchObject({ p50: 0.5, qps: 10 })
  expect(summary.speedup).toBeNull()
})
