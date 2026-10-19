import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearTourStep,
  onOpenTour,
  openTour,
  savedTourStep,
  saveTourStep,
} from './onboardingState'
import { TourEntry } from './TourEntry'

afterEach(cleanup)

describe('tour state', () => {
  it('opens at a step, or from the start', () => {
    const listener = vi.fn()
    const stop = onOpenTour(listener)
    openTour(2)
    openTour()
    stop()
    openTour(1)
    expect(listener.mock.calls).toEqual([[2], [0]])
  })

  it('remembers where a closed tour left off until it is cleared', () => {
    expect(savedTourStep()).toBeNull()
    saveTourStep(2)
    expect(savedTourStep()).toBe(2)
    clearTourStep()
    expect(savedTourStep()).toBeNull()
  })
})

describe('TourEntry', () => {
  it('offers the tour from the start when nothing is saved', () => {
    const listener = vi.fn()
    const stop = onOpenTour(listener)
    render(<TourEntry />)
    expect(screen.queryByTestId('tour-resume')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'See how it works' }))
    expect(listener).toHaveBeenCalledWith(0)
    stop()
  })

  it('offers to resume at the saved step as soon as it is saved', () => {
    const listener = vi.fn()
    const stop = onOpenTour(listener)
    render(<TourEntry />)
    act(() => saveTourStep(2))
    fireEvent.click(
      screen.getByRole('button', { name: 'Resume the tour at step 3' })
    )
    expect(listener).toHaveBeenCalledWith(2)
    fireEvent.click(screen.getByRole('button', { name: 'Start it over' }))
    expect(listener).toHaveBeenLastCalledWith(0)
    act(() => clearTourStep())
    expect(screen.queryByTestId('tour-resume')).toBeNull()
    stop()
  })
})
