import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Sidebar } from './Sidebar'

// Radix ScrollArea (the nav's Scrollable) needs ResizeObserver; jsdom has none.
vi.stubGlobal(
  'ResizeObserver',
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
)

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: { pathname: '/' } }),
  Link: ({
    to,
    children,
    className,
    onClick,
  }: {
    to: string
    children?: ReactNode
    className?: string
    onClick?: () => void
  }) => (
    <a href={to} className={className} onClick={onClick}>
      {children}
    </a>
  ),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: null }),
}))

vi.mock('../components/TargetDropdown', () => ({
  TargetDropdown: () => <button type="button">target</button>,
}))

vi.mock('../components/ReportDialog', () => ({
  ReportDialog: () => null,
}))

vi.mock('../components/TrialBalanceBadge', () => ({
  TrialBalanceBadge: () => null,
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo', setTarget: vi.fn() }),
}))

vi.mock('../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({ data: undefined }),
}))

/** Wrapper with an external trigger so focus-return has a real target. */
function Harness({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  return (
    <div>
      <button type="button" data-testid="hamburger">
        menu
      </button>
      <Sidebar mobileOpen={open} onMobileClose={onClose} />
    </div>
  )
}

describe('Sidebar mobile drawer a11y (T19 · USE-077/USE-090)', () => {
  afterEach(cleanup)

  it('closes on Escape while open', () => {
    const onClose = vi.fn()
    render(<Sidebar mobileOpen onMobileClose={onClose} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not listen for Escape while closed', () => {
    const onClose = vi.fn()
    render(<Sidebar mobileOpen={false} onMobileClose={onClose} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('moves focus to the first nav item on open and returns it to the trigger on close', () => {
    const onClose = vi.fn()
    const { rerender } = render(<Harness open={false} onClose={onClose} />)
    screen.getByTestId('hamburger').focus()

    rerender(<Harness open onClose={onClose} />)
    const active = document.activeElement as HTMLElement
    expect(active.tagName).toBe('A')
    expect(active.getAttribute('href')).toBe('/')

    rerender(<Harness open={false} onClose={onClose} />)
    expect(document.activeElement).toBe(screen.getByTestId('hamburger'))
  })

  it('traps Tab within scrim + drawer while open (background not tabbable)', () => {
    render(<Sidebar mobileOpen onMobileClose={vi.fn()} />)
    // Last focusable inside the drawer set: the footer "Give Feedback" button.
    const feedback = screen.getByRole('button', { name: /Give Feedback/ })
    feedback.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    // Wraps to the first element of the trap set — the scrim.
    expect(
      (document.activeElement as HTMLElement).getAttribute('aria-label')
    ).toBe('Close navigation')

    // Shift+Tab from the scrim wraps back to the last element.
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(feedback)
  })
})
