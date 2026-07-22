import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BootstrapChip } from './BootstrapChip'
import * as bootstrapRun from '../lib/bootstrapRun'

vi.mock('../lib/bootstrapRun', () => ({
  useBootstrapRunState: vi.fn(),
  reattachBootstrapRun: vi.fn(),
  dismissBootstrapRun: vi.fn(),
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

const useState = vi.mocked(bootstrapRun.useBootstrapRunState)

function runState(partial: Partial<bootstrapRun.BootstrapRunState>) {
  return {
    runId: 'bootstrap_imdb_x',
    target: 'imdb',
    stage: '',
    status: 'running' as const,
    message: '',
    lastSeq: 1,
    ...partial,
  }
}

describe('BootstrapChip', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders nothing when idle and reattaches on mount', () => {
    useState.mockReturnValue(runState({ runId: null, status: 'idle' }))
    const { container } = render(<BootstrapChip />)
    expect(container.firstChild).toBeNull()
    expect(bootstrapRun.reattachBootstrapRun).toHaveBeenCalledTimes(1)
  })

  it('shows target and progress message while running', () => {
    useState.mockReturnValue(
      runState({ stage: 'profile', message: 'Profiling columns' })
    )
    render(<BootstrapChip />)
    expect(screen.getByText('Setting up imdb')).toBeTruthy()
    expect(screen.getByText('Profiling columns')).toBeTruthy()
  })

  it('needs_key state opens the trial dialog on click', () => {
    useState.mockReturnValue(runState({ status: 'needs_key' }))
    render(<BootstrapChip />)
    expect(screen.queryByTestId('trial-dialog')).toBeNull()

    fireEvent.click(screen.getByTestId('bootstrap-chip'))

    expect(screen.getByTestId('trial-dialog')).toBeTruthy()
  })

  it('done state dismisses on click', () => {
    useState.mockReturnValue(runState({ status: 'done' }))
    render(<BootstrapChip />)
    expect(screen.getByText('imdb is ready')).toBeTruthy()

    fireEvent.click(screen.getByTestId('bootstrap-chip'))

    expect(bootstrapRun.dismissBootstrapRun).toHaveBeenCalledTimes(1)
  })

  it('failure state shows the run message', () => {
    useState.mockReturnValue(
      runState({ status: 'failed', message: 'auth failed' })
    )
    render(<BootstrapChip />)
    expect(screen.getByText('auth failed')).toBeTruthy()
  })
})
