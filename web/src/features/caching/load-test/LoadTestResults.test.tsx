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
}: {
  status: 'cancelled' | 'partial' | 'done' | 'failed'
  progress?: LoadTestProgress
  error?: string
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
      targetLocked={false}
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
})
