import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { GetStartedStrip } from './GetStarted'

afterEach(cleanup)

describe('GetStartedStrip', () => {
  it('names the step, its place among the three, and the one after it', () => {
    render(<GetStartedStrip step="sign-in" />)
    expect(
      screen.getByRole('progressbar', { name: 'Getting started' })
    ).toBeTruthy()
    expect(screen.getByText(/Step 1 of 3/).textContent).toBe(
      'Step 1 of 3 · Sign in'
    )
    expect(screen.getByText('Next: Connect database')).toBeTruthy()
  })

  it('has no next step on the last one', () => {
    render(<GetStartedStrip step="monitor" />)
    expect(screen.getByText(/Step 3 of 3/).textContent).toBe(
      'Step 3 of 3 · Turn on monitoring'
    )
    expect(screen.queryByText(/^Next:/)).toBeNull()
  })
})
