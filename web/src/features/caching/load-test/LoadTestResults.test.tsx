import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BenchmarkRequest } from '../../../lib/api'
import type { LoadTestProgress } from '../../../lib/backgroundRuns'
import { LoadTestResults } from './LoadTestResults'
import { deriveLoadTestResultModel } from './loadTestModel'

const request: BenchmarkRequest = {
  queries: ['Q1'],
  target: 'demo',
  mode: 'interval',
  interval_ms: 100,
  concurrency: 1,
  duration_seconds: 30,
}

afterEach(cleanup)

function renderResult({
  status,
  progress,
  error,
  targetLocked = false,
}: {
  status: 'cancelled' | 'partial' | 'done' | 'failed'
  progress?: LoadTestProgress
  error?: string
  targetLocked?: boolean
}) {
  const state = status === 'failed' ? 'error' : 'complete'
  const model = deriveLoadTestResultModel({
    state,
    status,
    stage: status,
    progress,
    request,
    fallbackDurationSeconds: 30,
    fallbackIntervalMs: 100,
  })

  render(
    <LoadTestResults
      model={model}
      progress={progress}
      timeline={[]}
      request={request}
      runMessage={undefined}
      error={error}
      targetLocked={targetLocked}
      onStop={vi.fn()}
      onAdjust={vi.fn()}
      onRunAgain={vi.fn()}
    />
  )
}

describe('LoadTestResults terminal states', () => {
  it('shows cancellation as a neutral terminal state', () => {
    renderResult({ status: 'cancelled' })

    expect(screen.getByText('Load test cancelled')).toBeTruthy()
    expect(screen.getByText('Cancelled')).toBeTruthy()
    expect(screen.queryByText('Load test failed')).toBeNull()
  })

  it('says why a run cannot be repeated instead of only greying it out', () => {
    renderResult({ status: 'done', targetLocked: true })

    const runAgain = screen.getByRole('button', { name: 'Run again' })
    expect(runAgain.hasAttribute('disabled')).toBe(true)
    expect(
      screen.getByText('Unlock this database to run the test again.')
    ).toBeTruthy()
  })

  it('keeps partial completion distinct from failure', () => {
    renderResult({
      status: 'partial',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 10,
        total_successes: 8,
        total_failures: 2,
        qps: 4,
        queries: [],
      },
    })

    expect(screen.getByText('Completed with errors')).toBeTruthy()
    expect(screen.getByText(/8 requests completed; 2 failed/i)).toBeTruthy()
  })

  it('shows an explicit no-measurements outcome', () => {
    renderResult({ status: 'done' })

    expect(screen.getByText('No measurements completed')).toBeTruthy()
    expect(screen.getByText('No measurements')).toBeTruthy()
  })

  it('keeps the run error detail visible', () => {
    renderResult({
      status: 'failed',
      error: 'Database connection closed unexpectedly.',
    })

    expect(screen.getByText('Load test failed')).toBeTruthy()
    expect(
      screen.getByText('Database connection closed unexpectedly.')
    ).toBeTruthy()
  })

  it('leads a failed run with the reason, not a grid of zeros', () => {
    renderResult({
      status: 'failed',
      error: 'Database connection closed unexpectedly.',
    })

    // "Error rate 0.0% (0 failed)" and the pacing explainer describe a run that
    // never happened; only the reason is true of it.
    expect(screen.queryByText('Error rate')).toBeNull()
    expect(screen.queryByText('This run is intentionally paced')).toBeNull()
    expect(
      screen.getAllByText('Database connection closed unexpectedly.')
    ).toHaveLength(1)
  })
})

describe('LoadTestResults skipped queries', () => {
  it('stays hidden for a payload that predates skip reporting', () => {
    renderResult({
      status: 'partial',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 10,
        total_successes: 8,
        total_failures: 2,
        qps: 4,
        queries: [],
      },
    })

    expect(screen.queryByText(/skipped/i)).toBeNull()
  })

  it('surfaces a per-query reason once the backend reports one', () => {
    renderResult({
      status: 'partial',
      progress: {
        type: 'complete',
        elapsed_seconds: 30,
        total_executions: 10,
        total_successes: 8,
        total_failures: 0,
        qps: 4,
        queries: [],
        skipped_queries: [
          {
            query_hash: 'abc12345',
            query_name: 'Slow orders lookup',
            reason: 'No stored value for parameter $1',
          },
        ],
      } as unknown as LoadTestProgress,
    })

    expect(screen.getByText('1 query skipped')).toBeTruthy()
    expect(screen.getByText('Slow orders lookup')).toBeTruthy()
    expect(screen.getByText('No stored value for parameter $1')).toBeTruthy()
  })
})

const COMPARATIVE_QUERY = {
  query_hash: 'q1',
  query_name: 'Orders lookup',
  executions: 10,
  successes: 10,
  failures: 0,
  avg_ms: 10,
  min_ms: 1,
  max_ms: 20,
  p50_ms: 9,
  p95_ms: 15,
  p99_ms: 20,
  lanes: {
    origin: {
      successes: 10,
      failures: 0,
      avg_ms: 10,
      p95_ms: 15,
      p99_ms: 20,
    },
    readyset: {
      successes: 10,
      failures: 0,
      avg_ms: 2,
      p95_ms: 3,
      p99_ms: 4,
    },
  },
}

describe('LoadTestResults comparative lanes', () => {
  it('renders an origin vs Readyset comparison when every query carries lane stats', () => {
    renderResult({
      status: 'done',
      progress: {
        type: 'complete',
        elapsed_seconds: 10,
        total_executions: 10,
        total_successes: 10,
        total_failures: 0,
        qps: 1,
        queries: [COMPARATIVE_QUERY],
      } as unknown as LoadTestProgress,
    })

    expect(screen.getByText(/faster with Readyset/)).toBeTruthy()
    expect(screen.getAllByText('Origin').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Readyset').length).toBeGreaterThan(0)
    expect(screen.queryByText('Query')).toBeNull() // no table header in the comparative layout
  })

  it('renders the plain per-query table when no query carries lane stats', () => {
    renderResult({
      status: 'done',
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
            query_name: 'Orders lookup',
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
      } as unknown as LoadTestProgress,
    })

    expect(screen.getByText('Query')).toBeTruthy()
    expect(screen.queryByText(/faster with Readyset/)).toBeNull()
    expect(screen.queryByText('Origin')).toBeNull()
  })

  it('shows a neutral note and keeps the origin-only view when Readyset was unavailable', () => {
    renderResult({
      status: 'done',
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
      } as unknown as LoadTestProgress,
    })

    expect(
      screen.getByText('Readyset was unavailable for this run')
    ).toBeTruthy()
    expect(
      screen.getByText('Readyset could not be reached for this run.')
    ).toBeTruthy()
    expect(screen.queryByText(/faster with Readyset/)).toBeNull()
  })
})

describe('LoadTestResults cache preparation', () => {
  function renderRunning(progress: LoadTestProgress, stage: string) {
    const model = deriveLoadTestResultModel({
      state: 'running',
      status: 'running',
      stage,
      progress,
      request,
      fallbackDurationSeconds: 30,
      fallbackIntervalMs: 100,
    })

    render(
      <LoadTestResults
        model={model}
        progress={progress}
        timeline={[]}
        request={request}
        runMessage={undefined}
        error={undefined}
        targetLocked={false}
        onStop={vi.fn()}
        onAdjust={vi.fn()}
        onRunAgain={vi.fn()}
      />
    )
  }

  const measuring: LoadTestProgress = {
    type: 'progress',
    elapsed_seconds: 2,
    total_executions: 20,
    total_successes: 20,
    total_failures: 0,
    qps: 10,
    queries: [],
  }

  it('names the wait while Readyset caches the workload', () => {
    renderRunning(
      {
        ...measuring,
        elapsed_seconds: 0,
        total_executions: 0,
        total_successes: 0,
        qps: 0,
        phase: 'preparing',
        prepared_count: 1,
        prepare_total: 3,
      } as unknown as LoadTestProgress,
      'preparing'
    )

    expect(screen.getByText(/Preparing Readyset caches/)).toBeTruthy()
    // The counts the run already reports drive a real bar, so the wait shows
    // how far along it is rather than a static glyph. [D-18]
    const bar = screen.getByRole('progressbar', { name: 'Cache preparation' })
    expect(bar.getAttribute('aria-valuenow')).toBe('1')
    expect(bar.getAttribute('aria-valuemax')).toBe('3')
    expect(screen.getByText('1 of 3 queries cached')).toBeTruthy()
    expect(screen.getByText('0s elapsed')).toBeTruthy()
    expect(screen.getByText(/takes a few minutes/)).toBeTruthy()
    // The wait is cancellable, and the action names the phase it stops.
    expect(
      screen.getByRole('button', { name: 'Cancel preparation' })
    ).toBeTruthy()
    // The measurement has not started, so no metric claims to be one.
    expect(screen.queryByText('Paced QPS')).toBeNull()
    // The heading already says what is happening; the body does not repeat it.
    expect(screen.queryByText('No action is required')).toBeNull()
  })

  it('measures against the run duration once the workload is warm', () => {
    renderRunning({ ...measuring, elapsed_seconds: 12 }, 'running')

    expect(screen.getByText('Measuring')).toBeTruthy()
    expect(screen.getByText('12s of 30s · ~18s left')).toBeTruthy()
    expect(
      screen.getByRole('progressbar', { name: 'Load test progress' })
    ).toBeTruthy()
  })

  it('renders the measuring view for a tick without the phase', () => {
    renderRunning(measuring, 'running')

    expect(screen.queryByText(/Preparing Readyset caches/)).toBeNull()
    expect(screen.getByText('Paced QPS')).toBeTruthy()
  })
})
