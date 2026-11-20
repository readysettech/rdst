import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../lib/analytics', () => ({ trackEvent: vi.fn() }))
vi.mock('../lib/feedbackNudge', () => ({
  nudgeDue: () => true,
  SESSION_DELAY_MS: 1000,
  silenceNudge: vi.fn(),
  snoozeNudge: vi.fn(),
}))
vi.mock('./FeedbackChat', () => ({ FeedbackChat: () => null }))

import { FeedbackNudge } from './FeedbackNudge'

describe('FeedbackNudge', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('waits while a modal is open, then shows', () => {
    const modal = document.createElement('div')
    modal.setAttribute('aria-modal', 'true')
    document.body.appendChild(modal)
    render(<FeedbackNudge />)
    act(() => vi.advanceTimersByTime(1000))
    expect(screen.queryByText(/better for you/i)).toBeNull()
    modal.remove()
    act(() => vi.advanceTimersByTime(30_000))
    expect(screen.getByText(/better for you/i)).toBeTruthy()
  })
})
