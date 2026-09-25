import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReportRun } from './useReportRun'
import { useRunCompletion } from './useRunCompletion'

function finishedRun(dismiss: () => void): ReportRun {
  return {
    status: 'done',
    reportId: 7,
    view: null,
    error: null,
    changedAt: 0,
    active: false,
    start: async () => {},
    cancel: async () => {},
    dismiss,
  }
}

describe('useRunCompletion', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('replaces a watched run on time while the page re-renders every second', () => {
    vi.useFakeTimers()
    const dismiss = vi.fn()
    const run = finishedRun(dismiss)
    const { rerender } = renderHook(
      ({ onOpen }: { onOpen: (id: number) => void }) =>
        useRunCompletion('watched-db', run, {
          watching: true,
          onOpen,
          holdMs: 1600,
        }),
      { initialProps: { onOpen: () => {} } }
    )
    // A page clock re-renders with a new inline callback each second.
    for (let second = 0; second < 3; second += 1) {
      act(() => vi.advanceTimersByTime(1000))
      rerender({ onOpen: () => {} })
    }
    expect(dismiss).toHaveBeenCalled()
  })
})
