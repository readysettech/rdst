import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BenchmarkProgress, useElapsedSeconds } from './BenchmarkProgress'

describe('BenchmarkProgress', () => {
  afterEach(cleanup)

  it('measures the wait against the container it is given', () => {
    render(
      <BenchmarkProgress
        value={1}
        max={3}
        ariaLabel="Cache preparation"
        label="1 of 3 queries cached"
        timing="12s elapsed"
      />
    )

    const bar = screen.getByRole('progressbar', { name: 'Cache preparation' })
    expect(bar.getAttribute('aria-valuenow')).toBe('1')
    expect(bar.getAttribute('aria-valuemax')).toBe('3')
    // The track fills its container rather than stopping at a fixed 240px,
    // so the fill reads against a full-width reference. [E-07]
    expect(bar.className).toContain('w-full')
    expect(bar.className).not.toContain('w-60')
    // One third of three queries, not one percent of a hundred.
    const indicator = bar.firstElementChild as HTMLElement
    expect(indicator.style.transform).toBe('translateX(-66.66666666666667%)')
  })

  it('leaves the value out while the end is not countable', () => {
    render(
      <BenchmarkProgress
        ariaLabel="Comparison progress"
        label="Preparing the temporary Readyset sandbox"
        detail="This can take a few minutes"
      />
    )

    const bar = screen.getByRole('progressbar', { name: 'Comparison progress' })
    expect(bar.getAttribute('aria-valuenow')).toBeNull()
    // The indeterminate track animates only where motion is welcome, and its
    // meaning survives without the animation.
    const indicator = bar.firstElementChild as HTMLElement
    expect(indicator.className).toContain('motion-safe:animate-pulse')
    expect(indicator.style.transform).toBe('')
    expect(screen.getByText('This can take a few minutes')).toBeTruthy()
  })

  it('drops the timing readout when the surface has none to give', () => {
    render(
      <BenchmarkProgress value={40} ariaLabel="Progress" label="Measuring" />
    )

    expect(screen.getByText('Measuring')).toBeTruthy()
    expect(
      screen
        .getByRole('progressbar', { name: 'Progress' })
        .getAttribute('aria-valuemax')
    ).toBe('100')
  })
})

describe('useElapsedSeconds', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('counts from the moment the wait starts and resets when it ends', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ active }: { active: boolean }) => useElapsedSeconds(active),
      { initialProps: { active: true } }
    )

    expect(result.current).toBe(0)
    act(() => {
      vi.advanceTimersByTime(3_000)
    })
    expect(result.current).toBeGreaterThanOrEqual(3)

    rerender({ active: false })
    expect(result.current).toBe(0)
  })
})
