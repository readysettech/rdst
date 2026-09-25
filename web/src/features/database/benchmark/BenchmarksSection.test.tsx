import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BackgroundRunState } from '@/lib/backgroundRuns'
import { BenchmarksSection } from './BenchmarksSection'
import { benchmarkTimeline, presetById, queryRequest } from './benchmarkFlow'
import type { QueryBenchmark } from './useQueryBenchmark'

afterEach(cleanup)

const NOW = Date.now() / 1000

function lane(p50: number, n: number) {
  return {
    executions: n,
    successes: n,
    failures: 0,
    p50_ms: p50,
    p95_ms: p50 * 2,
    avg_ms: p50,
    qps: n / 30,
  }
}

function saved(ts: number, readyset: boolean) {
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
      ...lane(12, 300),
      readyset: readyset ? lane(0.5, 300) : null,
    },
  }
}

function bench(
  parts: { run?: BackgroundRunState; history?: ReturnType<typeof saved>[] } = {}
) {
  const live = parts.run
    ? ['running', 'stopping'].includes(parts.run.status)
    : false
  return {
    run: parts.run,
    live,
    timeline: benchmarkTimeline({
      run: parts.run,
      history: parts.history ?? [],
    }),
    historyLoading: false,
    stop: vi.fn(),
  } as unknown as QueryBenchmark
}

describe('BenchmarksSection', () => {
  it('offers the first benchmark', () => {
    const onOpen = vi.fn()
    render(
      <BenchmarksSection bench={bench()} onOpen={onOpen} blockedReason={null} />
    )
    expect(screen.getByText('Not benchmarked yet')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Benchmark' }))
    expect(onOpen).toHaveBeenCalled()
  })

  it("says why a query can't be benchmarked", () => {
    render(
      <BenchmarksSection
        bench={bench()}
        onOpen={vi.fn()}
        blockedReason="RDST only runs read-only queries."
      />
    )
    expect(screen.getByText('RDST only runs read-only queries.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Benchmark' })).toBeNull()
  })

  it('shows the latest run with its Readyset speedup, then earlier runs', () => {
    render(
      <BenchmarksSection
        bench={bench({
          history: [saved(NOW - 60, true), saved(NOW - 7200, false)],
        })}
        onOpen={vi.fn()}
        blockedReason={null}
      />
    )
    expect(screen.getByTestId('benchmark-speedup').textContent).toMatch(
      /^24x faster/
    )
    const numbers = screen.getByTestId('benchmark-numbers')
    expect(within(numbers).getByText('Through Readyset')).toBeTruthy()
    const history = screen.getByTestId('benchmark-history')
    expect(within(history).getAllByRole('listitem')).toHaveLength(1)
    expect(within(history).getByText('Database only')).toBeTruthy()
  })

  it('shows a live run with a way to stop it', () => {
    const b = bench({
      run: {
        runId: 'r1',
        kind: 'load_test',
        target: 'shop',
        stage: 'queued',
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
          withReadyset: true,
        }),
      },
    })
    render(
      <BenchmarksSection bench={b} onOpen={vi.fn()} blockedReason={null} />
    )
    const progress = screen.getByTestId('benchmark-progress')
    expect(progress.dataset.phase).toBe('waiting')
    expect(within(progress).getByText('Starting Readyset')).toBeTruthy()
    fireEvent.click(within(progress).getByRole('button', { name: 'Stop' }))
    expect(b.stop).toHaveBeenCalled()
  })
})

it('shows each query in a merged Readyset-only run without source numbers', () => {
  const stats = (hash: string, p50: number) => ({
    query_hash: hash,
    query_name: hash,
    ...lane(p50, 300),
    min_ms: p50,
    max_ms: p50 * 3,
    p99_ms: p50 * 3,
    lanes: { readyset: lane(p50, 300) },
  })
  const b = bench({
    run: {
      runId: 'merged',
      kind: 'load_test',
      target: 'shop',
      stage: 'complete',
      status: 'done',
      message: '',
      lastSeq: 0,
      current: null,
      total: null,
      hasWarnings: false,
      loadRequest: {
        queries: ['first', 'second'],
        target: 'shop',
        lanes: ['readyset'],
      },
      loadResult: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 600,
        total_successes: 600,
        total_failures: 0,
        qps: 20,
        queries: [stats('first', 0.5), stats('second', 1.5)],
      },
    },
  })
  b.controller = { queries: [] } as unknown as QueryBenchmark['controller']
  render(<BenchmarksSection bench={b} onOpen={vi.fn()} blockedReason={null} />)
  expect(screen.getByRole('heading', { name: 'first' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'second' })).toBeTruthy()
  expect(screen.getAllByText('Through Readyset')).toHaveLength(2)
  expect(screen.queryByText('Your database')).toBeNull()
  expect(screen.queryByTestId('benchmark-speedup')).toBeNull()
})
