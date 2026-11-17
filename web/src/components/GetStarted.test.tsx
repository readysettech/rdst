import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { markTourDone } from '../features/onboarding/onboardingState'
import { claimFirstRunPopup, GetStartedStrip } from './GetStarted'

afterEach(cleanup)

describe('GetStartedStrip', () => {
  it('names the step, its place among the two, and the one after it', () => {
    render(<GetStartedStrip step="add-database" />)
    expect(
      screen.getByRole('progressbar', { name: 'Getting started' })
    ).toBeTruthy()
    expect(screen.getByText(/Step 1 of 2/).textContent).toBe(
      'Step 1 of 2 · Connect database'
    )
    expect(screen.getByText('Next: Turn on monitoring')).toBeTruthy()
  })

  it('has no next step on the last one, turning on monitoring', () => {
    render(<GetStartedStrip step="monitor" />)
    expect(screen.getByText(/Step 2 of 2/).textContent).toBe(
      'Step 2 of 2 · Turn on monitoring'
    )
    expect(screen.queryByText(/^Next:/)).toBeNull()
  })
})

describe('claimFirstRunPopup', () => {
  afterEach(() => {
    sessionStorage.clear()
    localStorage.clear()
  })

  it('opens the add-database prompt once a session, never over another dialog', () => {
    markTourDone()
    const other = document.createElement('div')
    other.setAttribute('role', 'dialog')
    document.body.append(other)
    expect(claimFirstRunPopup('add-database')).toBe(false)
    other.remove()
    expect(claimFirstRunPopup('add-database')).toBe(true)
    expect(claimFirstRunPopup('add-database')).toBe(false)
  })
})
