import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBenchmark } from '../lib/sse'
import { useQueryRegistry } from '../lib/useQueryRegistry'
import { useTargetConnectivityGate } from '../lib/useTargetConnectivityGate'
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
  useQueries: ({ queries }: { queries: unknown[] }) =>
    queries.map(() => ({
      data: undefined,
      isLoading: false,
      isFetching: false,
      error: null,
    })),
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

vi.mock('../lib/useTargetConnectivityGate', () => ({
  useTargetConnectivityGate: vi.fn(),
}))

vi.mock('../components', () => ({
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
  TargetConnectivityNotice: () => <div>Database unreachable</div>,
}))

afterEach(cleanup)

function setup(
  lockActive: boolean,
  runState: 'idle' | 'queued' = 'idle',
  reachable = true
) {
  const start = vi.fn()
  const ensureReachable = vi.fn().mockResolvedValue(reachable)
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
    refetch: vi.fn(),
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
    markReviewedMutation: { mutate: vi.fn(), isPending: false } as any,
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
    start,
    stop: vi.fn(),
    state: runState === 'queued' ? 'running' : 'idle',
    stage: runState,
    message:
      runState === 'queued'
        ? 'Waiting for an isolated measurement slot...'
        : undefined,
    progress: undefined,
    timeline: [],
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
    status: runState === 'queued' ? 'running' : undefined,
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
  vi.mocked(useTargetConnectivityGate).mockReturnValue({
    failure: reachable
      ? null
      : { target: 'prod', message: 'connection refused' },
    isChecking: false,
    ensureReachable,
    reset: vi.fn(),
  })

  render(<BenchmarkPage />)

  if (runState === 'idle') {
    fireEvent.click(screen.getAllByText('Q1')[0])
  }

  return { start, ensureReachable }
}

describe('BenchmarkPage password lock', () => {
  it('allows start when unlocked and query is selected', () => {
    setup(false)
    const startButton = screen.getAllByRole('button', {
      name: /Run load test/i,
    })[0]
    expect((startButton as HTMLButtonElement).disabled).toBe(false)
  })

  it('keeps start disabled when target is password-locked', () => {
    setup(true)
    const startButton = screen.getAllByRole('button', {
      name: /Run load test/i,
    })[0]
    expect((startButton as HTMLButtonElement).disabled).toBe(true)
  })

  it('explains why a queued load test has not started', () => {
    setup(false, 'queued')

    expect(screen.getByText('Waiting for an isolated test slot')).toBeTruthy()
    expect(
      screen.getByText(/another performance measurement is active/i)
    ).toBeTruthy()
    expect(
      screen.getByText('Waiting for an isolated measurement slot...')
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /Cancel queued test/ })
    ).toBeTruthy()
    expect(screen.queryByText('Running...')).toBeNull()
    expect(screen.queryByText('Total Executions')).toBeNull()
  })

  it('keeps the existing paced request as the default', async () => {
    const { start } = setup(false)

    fireEvent.click(screen.getByRole('button', { name: /Run load test/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Run load test$/i }))

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: 'interval',
          interval_ms: 100,
          concurrency: 1,
          duration_seconds: 30,
        })
      )
    )
  })

  it('offers a bounded capacity profile without changing the default', async () => {
    const { start } = setup(false)

    fireEvent.click(screen.getByText('Capacity test'))
    fireEvent.click(screen.getByRole('button', { name: /Run load test/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Run load test$/i }))

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: 'concurrency',
          interval_ms: 0,
          concurrency: 2,
          duration_seconds: 30,
        })
      )
    )
  })

  it('does not start when the database preflight fails', async () => {
    const { start, ensureReachable } = setup(false, 'idle', false)

    fireEvent.click(screen.getByRole('button', { name: /Run load test/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Run load test$/i }))

    await waitFor(() => expect(ensureReachable).toHaveBeenCalledOnce())
    expect(start).not.toHaveBeenCalled()
  })
})
