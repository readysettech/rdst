import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetAuditSessionForTests,
  beginAuditSession,
} from '../lib/auditSession'
import * as backgroundRuns from '../lib/backgroundRuns'
import { BackgroundRuns } from './BackgroundRuns'

const { navigate, setTarget } = vi.hoisted(() => ({
  navigate: vi.fn(),
  setTarget: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ setTarget }),
}))

vi.mock('../lib/backgroundRuns', () => ({
  useBackgroundRuns: vi.fn(),
  getBackgroundRuns: vi.fn(() => []),
  reattachBackgroundRuns: vi.fn(),
  acknowledgeBackgroundRun: vi.fn(),
  dismissBackgroundRun: vi.fn(),
  cancelBackgroundRun: vi.fn(),
  isAuditKind: (kind: string) => kind === 'audit' || kind === 'audit_capture',
  isHealthCheckKind: (kind: string) =>
    kind === 'audit' || kind === 'audit_capture' || kind === 'fleet_audit',
}))

vi.mock('../lib/trialQueries', () => ({
  invalidateTrialRelatedQueries: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({}),
}))

vi.mock('./TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="trial-dialog" /> : null,
}))

const useRuns = vi.mocked(backgroundRuns.useBackgroundRuns)

function openJobs() {
  fireEvent.click(screen.getByTestId('jobs-trigger'))
}

function run(
  partial: Partial<backgroundRuns.BackgroundRunState>
): backgroundRuns.BackgroundRunState {
  return {
    runId: 'schema_annotation_imdb_x',
    kind: 'schema_annotation',
    target: 'imdb',
    stage: 'annotate',
    status: 'running',
    message: 'Annotating title',
    lastSeq: 2,
    current: 3,
    total: 25,
    hasWarnings: false,
    ...partial,
  }
}

describe('BackgroundRuns', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => {
    cleanup()
    __resetAuditSessionForTests()
  })

  it('renders nothing when idle and probes stored runs on mount', () => {
    useRuns.mockReturnValue([])
    const { container } = render(<BackgroundRuns />)
    expect(container.firstChild).toBeNull()
    expect(backgroundRuns.reattachBackgroundRuns).toHaveBeenCalledOnce()
  })

  it('shows annotation table progress and bootstrap progress together', () => {
    useRuns.mockReturnValue([
      run({}),
      run({
        runId: 'bootstrap_sales_y',
        kind: 'bootstrap',
        target: 'sales',
        message: 'Profiling columns',
        current: null,
        total: null,
      }),
    ])

    render(<BackgroundRuns />)
    expect(screen.getByTestId('running-job-spinner')).toBeTruthy()
    expect(screen.queryByTestId('running-job-indicator')).toBeNull()
    expect(screen.getByTestId('jobs-trigger').textContent).toContain(
      'Setting up sales'
    )
    expect(screen.getByTestId('jobs-trigger').textContent).toContain(
      'Profiling columns'
    )
    openJobs()

    expect(screen.getByText('Annotating imdb')).toBeTruthy()
    expect(screen.getByText('3 of 25 tables')).toBeTruthy()
    expect(screen.getAllByText('Setting up sales')).toHaveLength(2)
    expect(screen.getAllByText('Profiling columns')).toHaveLength(2)
  })

  it('keeps a reconnecting job active and cancellable', () => {
    useRuns.mockReturnValue([
      run({
        status: 'reconnecting',
        message: 'Reconnecting to background job...',
      }),
    ])

    render(<BackgroundRuns />)

    expect(screen.getByTestId('running-job-spinner')).toBeTruthy()
    expect(screen.getByTestId('jobs-trigger').textContent).toContain(
      'Reconnecting to background job...'
    )
    openJobs()
    expect(screen.getByText('1 job running')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Cancel Annotating imdb'))
    expect(backgroundRuns.cancelBackgroundRun).toHaveBeenCalledWith(
      'schema_annotation_imdb_x'
    )
  })

  it('distinguishes queued jobs from the job that is actually running', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'load_test_imdb_queued',
        kind: 'load_test',
        stage: 'queued',
        message: 'Queued for measurement...',
      }),
      run({
        runId: 'cache_test_imdb_queued',
        kind: 'speed_test',
        stage: 'queued',
        message: 'Queued for the Readyset sandbox...',
      }),
      run({
        runId: 'cache_test_imdb_active',
        kind: 'speed_test',
        stage: 'waiting_for_readyset',
        message: 'Waiting for Readyset to accept SQL',
      }),
    ])

    render(<BackgroundRuns />)
    openJobs()

    expect(screen.getByText('1 running · 2 queued')).toBeTruthy()
    expect(screen.queryByText('3 jobs running')).toBeNull()
  })

  it('opens the key dialog for a parked run', () => {
    useRuns.mockReturnValue([run({ status: 'needs_key' })])
    render(<BackgroundRuns />)

    const trigger = screen.getByTestId('jobs-trigger')
    expect(trigger.textContent).toContain(
      'Add an Anthropic key or start a free trial.'
    )
    expect(trigger.className).toContain('bg-surface-warning-soft/20')
    expect(screen.getByTestId('job-warning-icon')).toBeTruthy()
    openJobs()

    const instructions = screen.getAllByText(
      'Add an Anthropic key or start a free trial.'
    )
    expect(instructions).toHaveLength(2)
    expect(instructions[1]?.className).not.toContain('truncate')
    expect(screen.getByText('1 job needs attention')).toBeTruthy()

    fireEvent.click(
      screen.getByTestId('background-run-schema_annotation_imdb_x')
    )

    expect(screen.getByTestId('trial-dialog')).toBeTruthy()
  })

  it('prioritizes warnings over newer running jobs', () => {
    useRuns.mockReturnValue([
      run({ status: 'needs_key' }),
      run({
        runId: 'bootstrap_sales_y',
        kind: 'bootstrap',
        target: 'sales',
      }),
    ])

    render(<BackgroundRuns />)

    const trigger = screen.getByTestId('jobs-trigger')
    expect(trigger.textContent).toContain('Annotating imdb')
    expect(trigger.textContent).not.toContain('Setting up sales')
    expect(screen.queryByTestId('running-job-spinner')).toBeNull()
  })

  it('prioritizes errors over warnings and running jobs', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'cache_test_imdb_failed',
        kind: 'cache_test',
        status: 'failed',
        queryLabel: 'Top customers',
        message: 'Connection failed. Check the database credentials.',
      }),
      run({
        runId: 'annotation_sales_key',
        target: 'sales',
        status: 'needs_key',
      }),
      run({ runId: 'bootstrap_store', kind: 'bootstrap', target: 'store' }),
    ])

    render(<BackgroundRuns />)

    const trigger = screen.getByTestId('jobs-trigger')
    expect(trigger.textContent).toContain('Testing Top customers')
    expect(trigger.textContent).toContain(
      'Connection failed. Check the database credentials.'
    )
    expect(trigger.className).toContain('bg-surface-negative-soft/20')
    expect(screen.getByTestId('job-error-icon')).toBeTruthy()
    expect(screen.queryByTestId('running-job-spinner')).toBeNull()

    openJobs()
    const errorDetails = screen.getAllByText(
      'Connection failed. Check the database credentials.'
    )
    expect(errorDetails).toHaveLength(2)
    expect(errorDetails[1]?.className).not.toContain('truncate')
    expect(screen.getByText('1 job has failed')).toBeTruthy()
  })

  it('cancels running and dismisses terminal runs', () => {
    useRuns.mockReturnValue([
      run({}),
      run({ runId: 'bootstrap_imdb_done', kind: 'bootstrap', status: 'done' }),
    ])
    render(<BackgroundRuns />)
    openJobs()

    fireEvent.click(screen.getByLabelText('Cancel Annotating imdb'))
    fireEvent.click(screen.getByTitle('Dismiss job'))

    expect(backgroundRuns.cancelBackgroundRun).toHaveBeenCalledWith(
      'schema_annotation_imdb_x'
    )
    expect(backgroundRuns.dismissBackgroundRun).toHaveBeenCalledWith(
      'bootstrap_imdb_done'
    )
  })

  it('shows the no-op completion message for an already annotated schema', () => {
    useRuns.mockReturnValue([
      run({
        status: 'done',
        message: 'Schema is already fully annotated',
      }),
    ])

    render(<BackgroundRuns />)
    expect(screen.queryByTestId('running-job-spinner')).toBeNull()
    openJobs()

    expect(
      screen.getAllByText('Schema is already fully annotated')
    ).toHaveLength(2)
  })

  it('opens and acknowledges a completed cache test from its query result', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'cache_test_imdb_done',
        kind: 'cache_test',
        status: 'done',
        queryHash: 'abc123',
        queryLabel: 'Top customers',
        message: 'Performance test complete',
      }),
    ])

    render(<BackgroundRuns />)
    openJobs()
    fireEvent.click(screen.getByTitle('View results'))

    expect(navigate).toHaveBeenCalledWith({
      to: '/query-registry',
      search: { hash: 'abc123', run: 'cache_test_imdb_done' },
    })
    expect(setTarget).toHaveBeenCalledWith('imdb')
    expect(backgroundRuns.acknowledgeBackgroundRun).toHaveBeenCalledWith(
      'cache_test_imdb_done'
    )
    expect(backgroundRuns.dismissBackgroundRun).not.toHaveBeenCalled()
  })

  it('opens and acknowledges a completed benchmark from the jobs list', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'load_test_imdb_done',
        kind: 'load_test',
        status: 'done',
        message: 'Benchmark complete',
      }),
    ])

    render(<BackgroundRuns />)
    openJobs()
    fireEvent.click(screen.getByTitle('View results'))

    expect(navigate).toHaveBeenCalledWith({
      to: '/benchmark',
      search: { run: 'load_test_imdb_done' },
    })
    expect(setTarget).toHaveBeenCalledWith('imdb')
    expect(backgroundRuns.acknowledgeBackgroundRun).toHaveBeenCalledWith(
      'load_test_imdb_done'
    )
  })

  it('dismisses a terminal job without opening it', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'cache_test_imdb_failed',
        kind: 'cache_test',
        status: 'failed',
        queryHash: 'abc123',
        queryLabel: 'Top customers',
        message: 'Connection failed',
      }),
    ])

    render(<BackgroundRuns />)
    openJobs()
    fireEvent.click(screen.getByLabelText('Dismiss Testing Top customers'))

    expect(backgroundRuns.dismissBackgroundRun).toHaveBeenCalledWith(
      'cache_test_imdb_failed'
    )
    expect(navigate).not.toHaveBeenCalled()
  })

  it('opens a finished health check on its saved run', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'audit_imdb_x',
        kind: 'audit',
        status: 'done',
        stage: 'storage',
        message: 'Health check complete',
        snapshotId: 'audit_imdb_20260727_000000',
      }),
    ])

    render(<BackgroundRuns />)
    expect(screen.getByTestId('jobs-trigger').textContent).toContain(
      'Health check on imdb'
    )
    openJobs()
    fireEvent.click(screen.getByTitle('View results'))

    expect(navigate).toHaveBeenCalledWith({
      to: '/audit/runs/$runId',
      params: { runId: 'audit_imdb_20260727_000000' },
    })
    expect(backgroundRuns.acknowledgeBackgroundRun).toHaveBeenCalledWith(
      'audit_imdb_x'
    )
  })

  it('shows one card for a whole fleet health check and opens its snapshot', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'fleet_audit_prod_x',
        kind: 'fleet_audit',
        target: 'prod',
        status: 'partial',
        stage: 'storage',
        message: 'Fleet health check complete',
        hasWarnings: true,
        snapshotId: 'fleet_20260727_000000',
      }),
    ])

    render(<BackgroundRuns />)
    expect(screen.getByTestId('jobs-trigger').textContent).toContain(
      'Fleet health check'
    )
    openJobs()
    // One run means one card: a fleet audit never decomposes into subtasks.
    expect(screen.getByLabelText('Background jobs').textContent).toContain(
      '1 total'
    )
    expect(screen.getByText('Partially complete')).toBeTruthy()
    fireEvent.click(screen.getByTitle('View results'))

    expect(navigate).toHaveBeenCalledWith({
      to: '/audit/runs/$runId',
      params: { runId: 'fleet_20260727_000000' },
    })
    // The fleet scope is not a target the sidebar could switch to.
    expect(setTarget).not.toHaveBeenCalled()
  })

  it('falls back to the audit page for a capture with no saved run', () => {
    useRuns.mockReturnValue([
      run({
        runId: 'audit_capture_imdb_x',
        kind: 'audit_capture',
        status: 'failed',
        stage: 'capture',
        message: 'Connection lost',
        snapshotId: undefined,
      }),
    ])

    render(<BackgroundRuns />)
    openJobs()
    expect(screen.getAllByText('Capturing imdb')).toHaveLength(2)
    fireEvent.click(screen.getByTitle('View results'))

    expect(navigate).toHaveBeenCalledWith({ to: '/audit' })
  })

  it('lists a running health check alongside its banner session', () => {
    beginAuditSession({
      kind: 'snapshot',
      targetLabel: 'imdb',
      targetNames: ['imdb'],
      durationSeconds: 60,
      startedAt: Date.now(),
      phase: 'capture',
      statusMessage: 'Capturing queries...',
      cancel: vi.fn(),
    })
    useRuns.mockReturnValue([
      run({
        runId: 'audit_imdb_running',
        kind: 'audit',
        status: 'running',
        stage: 'capture',
        message: 'Capturing queries',
        current: null,
        total: null,
      }),
    ])

    render(<BackgroundRuns />)

    // The banner is an extra indicator, not a replacement: the job keeps its
    // card and counts towards the running total like every other kind.
    expect(screen.getByTestId('running-job-spinner')).toBeTruthy()
    openJobs()
    expect(screen.getByTestId('background-run-audit_imdb_running')).toBeTruthy()
    expect(screen.getByText('1 job running')).toBeTruthy()
  })

  it('shows partial annotation completion as a warning', () => {
    useRuns.mockReturnValue([
      run({
        status: 'partial',
        message: 'Annotated 23 tables; 1 failed',
      }),
    ])

    render(<BackgroundRuns />)
    openJobs()

    expect(screen.getByText('Partially complete')).toBeTruthy()
    expect(screen.getAllByText('Annotated 23 tables; 1 failed')).toHaveLength(2)
  })
})
