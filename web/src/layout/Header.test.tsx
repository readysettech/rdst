import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Header } from './Header'

const locationMock = vi.hoisted(() => ({
  pathname: '/',
  search: {} as Record<string, unknown>,
}))

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: locationMock }),
  Link: ({
    to,
    children,
    className,
  }: {
    to: string
    children?: ReactNode
    className?: string
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}))

vi.mock('../components/WindowControls', () => ({
  WindowControls: () => null,
}))

beforeEach(() => {
  locationMock.pathname = '/'
  locationMock.search = {}
})

describe('Header mobile hamburger (T19 drawer a11y)', () => {
  afterEach(cleanup)

  it('exposes the drawer state via aria-expanded and points at the drawer via aria-controls', () => {
    const { rerender } = render(<Header mobileNavOpen={false} />)
    const btn = screen.getByRole('button', { name: 'Open navigation' })
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    expect(btn.getAttribute('aria-controls')).toBe('app-sidebar')

    rerender(<Header mobileNavOpen />)
    expect(
      screen
        .getByRole('button', { name: 'Open navigation' })
        .getAttribute('aria-expanded')
    ).toBe('true')
  })

  it('fires onMenuClick when the hamburger is pressed', () => {
    const onMenuClick = vi.fn()
    render(<Header onMenuClick={onMenuClick} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open navigation' }))
    expect(onMenuClick).toHaveBeenCalledTimes(1)
  })
})

describe('Header breadcrumb names the active Benchmarks tab (B5)', () => {
  afterEach(cleanup)

  it('defaults to Compare when no view is selected', () => {
    locationMock.pathname = '/cache'
    locationMock.search = {}
    render(<Header />)
    expect(screen.getAllByText('Benchmarks - Compare').length).toBeGreaterThan(
      0
    )
  })

  it('names Load test when that tab is active', () => {
    locationMock.pathname = '/cache'
    locationMock.search = { view: 'load-test' }
    render(<Header />)
    expect(
      screen.getAllByText('Benchmarks - Load test').length
    ).toBeGreaterThan(0)
  })

  it('leaves other routes alone', () => {
    locationMock.pathname = '/ask'
    render(<Header />)
    expect(screen.getAllByText('Ask').length).toBeGreaterThan(0)
  })
})
