import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBenchmark } from '../lib/sse'
import { useQueryRegistry } from '../lib/useQueryRegistry'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import { BenchmarkPage } from './-benchmark-page'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
  // autoCodeSplitting rewrites the route's `component` to a lazyRouteComponent
  // call; the tests render BenchmarkPage directly, so this just needs to exist.
  lazyRouteComponent: (loader: unknown) => loader,
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useQuery: () => ({
    data: undefined,
    isLoading: false,
    isFetching: false,
    error: null,
  }),
  useMutation: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    data: undefined,
    error: null,
    reset: vi.fn(),
  }),
}))

vi.mock('../lib/useQueryRegistry', () => ({
  useQueryRegistry: vi.fn(),
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'prod' }),
}))

vi.mock('../lib/sse', () => ({
  useBenchmark: vi.fn(),
}))

vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: vi.fn(),
}))

vi.mock('../components', () => ({
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}))

afterEach(cleanup)

function setup(lockActive: boolean, runState: 'idle' | 'queued' = 'idle') {
  vi.mocked(useQueryRegistry).mockReturnValue({
    listError: null,
    queries: [
      {
        sql: 'select 1',
        hash: 'abc12345',
        tag: 'Q1',
        last_analyzed: '2025-01-01',
        target: 'prod',
        frequency: 1,
        source: 'web',
        avg_duration_ms: 0,
        max_duration_ms: 0,
        most_recent_params: {},
        observation_count: 0,
      },
    ],
    isLoading: false,
    addQuery: vi.fn(),
    addMutation: {
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
      isIdle: true,
      isSuccess: false,
      data: undefined,
      error: null,
      reset: vi.fn(),
      status: 'idle',
      variables: undefined,
      failureCount: 0,
      failureReason: null,
      submittedAt: 0,
      context: undefined,
      isPaused: false,
    } as any,
    removeQuery: vi.fn(),
    updateTag: vi.fn(),
    updateSqlMutation: { mutate: vi.fn(), isPending: false } as any,
    importMutation: {
      mutate: vi.fn(),
      isPending: false,
      data: undefined,
      reset: vi.fn(),
    } as any,
    isFetching: false,
    total: 1,
    limit: 50,
    offset: 0,
    setLimit: vi.fn(),
    setOffset: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    resetPagination: vi.fn(),
  })

  vi.mocked(useBenchmark).mockReturnValue({
    start: vi.fn(),
    stop: vi.fn(),
    state: runState === 'queued' ? 'running' : 'idle',
    stage: runState,
    message:
      runState === 'queued'
        ? 'Waiting for an isolated measurement slot...'
        : undefined,
    progress: undefined,
    request:
      runState === 'queued'
        ? {
            queries: ['Q1'],
            target: 'prod',
            mode: 'interval',
            interval_ms: 100,
            concurrency: 1,
            duration_seconds: 30,
          }
        : undefined,
    error: undefined,
    reset: vi.fn(),
  })

  vi.mocked(useTargetPasswordLock).mockReturnValue({
    isResolved: true,
    isLocked: lockActive,
    targetName: 'prod',
    message: "Target 'prod' is locked.",
    missingTargetRequirements: [],
    keyringAvailable: true,
  })

  render(<BenchmarkPage />)

  if (runState === 'idle') {
    fireEvent.click(screen.getAllByText('Q1')[0])
  }
}

describe('BenchmarkPage password lock', () => {
  it('allows start when unlocked and query is selected', () => {
    setup(false)
    const startButton = screen.getAllByRole('button', {
      name: /Start Benchmark/i,
    })[0]
    expect((startButton as HTMLButtonElement).disabled).toBe(false)
  })

  it('keeps start disabled when target is password-locked', () => {
    setup(true)
    const startButton = screen.getAllByRole('button', {
      name: /Start Benchmark/i,
    })[0]
    expect((startButton as HTMLButtonElement).disabled).toBe(true)
  })

  it('explains why a queued benchmark has not started', () => {
    setup(false, 'queued')

    expect(
      screen.getByText('Waiting for another performance test')
    ).toBeTruthy()
    expect(
      screen.getByText(/runs performance measurements one at a time/i)
    ).toBeTruthy()
    expect(screen.getByText(/30s test timer begins only after/i)).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /Cancel queued test/ })
    ).toBeTruthy()
    expect(screen.queryByText('Running...')).toBeNull()
    expect(screen.queryByText('Total Executions')).toBeNull()
  })
})
