import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { HandRaiser } from './HandRaiser'

const seen = vi.fn()
const mark = vi.fn()

// Control the once-ever store directly so the tests never depend on a real
// localStorage (unreliable under vitest; rdst-e7s.23).
vi.mock('../lib/handRaiser', () => ({
  HAND_RAISER_URL: 'https://example.test/contact',
  isHandRaiserSeen: (...args: unknown[]) => seen(...args),
  markHandRaiserSeen: (...args: unknown[]) => mark(...args),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('HandRaiser', () => {
  it('renders nothing once the signal has already been seen', () => {
    seen.mockReturnValue(true)
    const { container } = render(<HandRaiser signal="cache_created" message="hi" />)
    expect(container.textContent).toBe('')
    expect(mark).not.toHaveBeenCalled()
  })

  it('shows the invitation, routes the link, and marks the signal seen once', () => {
    seen.mockReturnValue(false)
    render(
      <HandRaiser signal="cache_created" message="Running this beyond one instance?" />,
    )
    expect(screen.getByText(/Running this beyond one instance/)).toBeTruthy()
    const link = screen.getByRole('link', { name: /Talk to an engineer/ })
    expect(link.getAttribute('href')).toBe('https://example.test/contact')
    expect(mark).toHaveBeenCalledWith('cache_created')
  })

  it("hides immediately when 'Don't show again' is clicked", () => {
    seen.mockReturnValue(false)
    render(<HandRaiser signal="retention_30d" message="size me up" showDismiss />)
    fireEvent.click(screen.getByRole('button', { name: /Don't show again/ }))
    expect(screen.queryByText('size me up')).toBeNull()
  })

  it('omits the dismiss control unless showDismiss is set', () => {
    seen.mockReturnValue(false)
    render(<HandRaiser signal="fleet_audit" message="fleet audit" />)
    expect(screen.queryByRole('button', { name: /Don't show again/ })).toBeNull()
  })
})
