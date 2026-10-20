import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({ useNavigate: () => vi.fn() })
})

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: { version: '0.9.4', targets: [] } }),
}))

vi.mock('../components/FeedbackChat', () => ({
  FeedbackChat: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div role="dialog">feedback</div> : null,
}))

import {
  onOpenTour,
  saveTourStep,
} from '../features/onboarding/onboardingState'
import { HelpPage } from './-help-page'

afterEach(cleanup)

describe('Help', () => {
  it('opens four doors, lists the shortcuts and names the version', () => {
    render(<HelpPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Help' })).toBeTruthy()
    expect(
      screen.getByRole('link', { name: /Docs/ }).getAttribute('href')
    ).toBe('https://readyset.io/docs/readyset-ai/rdst/desktop')
    expect(
      screen.getByRole('button', { name: /Keyboard shortcuts/ })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /Ask or give feedback/ })
    ).toBeTruthy()
    expect(
      screen.getByRole('link', { name: /Join Discord/ }).getAttribute('target')
    ).toBe('_blank')
    expect(
      screen.getByRole('heading', { level: 2, name: 'Keyboard shortcuts' })
    ).toBeTruthy()
    expect(screen.getByText('Go to Inbox')).toBeTruthy()
    expect(screen.getByText('Shortcuts')).toBeTruthy()
    expect(screen.getByText('RDST 0.9.4')).toBeTruthy()
    const solids = screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-surface-primary-solid'))
    expect(solids).toHaveLength(0)
  })

  it('opens the feedback dialog from its door', () => {
    render(<HelpPage />)
    fireEvent.click(
      screen.getByRole('button', { name: /Ask or give feedback/ })
    )
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('opens the tour, or resumes it where it was closed', () => {
    const listener = vi.fn()
    const stop = onOpenTour(listener)
    render(<HelpPage />)
    fireEvent.click(screen.getByRole('button', { name: /Take the tour/ }))
    expect(listener).toHaveBeenLastCalledWith(0)
    cleanup()
    saveTourStep(1)
    render(<HelpPage />)
    fireEvent.click(screen.getByRole('button', { name: /Resume the tour/ }))
    expect(listener).toHaveBeenLastCalledWith(1)
    stop()
  })
})
