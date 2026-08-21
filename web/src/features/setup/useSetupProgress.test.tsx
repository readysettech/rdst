import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { trackEvent } from '../../lib/analytics'
import type { SetupProgress } from './setupModel'
import {
  __resetSetupProgressEventsForTests,
  useSetupStepCompletionEvents,
} from './useSetupProgress'

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

const tracked = vi.mocked(trackEvent)

function progress(overrides: Partial<SetupProgress> = {}): SetupProgress {
  return {
    target: 'orders',
    connected: false,
    schema_built: false,
    queries_found: false,
    analyzed: false,
    compared: false,
    ...overrides,
  }
}

beforeEach(() => {
  tracked.mockClear()
  __resetSetupProgressEventsForTests()
})

describe('useSetupStepCompletionEvents', () => {
  it('treats the first reading as a baseline', () => {
    renderHook(() =>
      useSetupStepCompletionEvents(progress({ connected: true }))
    )

    expect(tracked).not.toHaveBeenCalled()
  })

  it('emits once when a step is observed turning complete', () => {
    const { rerender } = renderHook(
      ({ value }: { value: SetupProgress }) =>
        useSetupStepCompletionEvents(value),
      { initialProps: { value: progress({ connected: true }) } }
    )

    rerender({ value: progress({ connected: true, schema_built: true }) })

    expect(tracked).toHaveBeenCalledTimes(1)
    expect(tracked).toHaveBeenCalledWith('setup_step_completed', {
      step: 'build-schema',
    })

    // Flipping back and forth (a target switch) must not inflate the funnel.
    rerender({ value: progress({ connected: true }) })
    rerender({ value: progress({ connected: true, schema_built: true }) })
    expect(tracked).toHaveBeenCalledTimes(1)
  })

  it('ignores readings whose signals are unknown', () => {
    const { rerender } = renderHook(
      ({ value }: { value: SetupProgress }) =>
        useSetupStepCompletionEvents(value),
      { initialProps: { value: progress({ connected: true }) } }
    )

    rerender({
      value: progress({ connected: true, analyzed: true, error: 'no config' }),
    })

    expect(tracked).not.toHaveBeenCalled()
  })
})
