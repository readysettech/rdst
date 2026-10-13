import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TargetConnectivityNotice } from './TargetConnectivityNotice'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

afterEach(cleanup)

describe('TargetConnectivityNotice', () => {
  it('names the database it is checking', () => {
    render(
      <TargetConnectivityNotice
        target="demo"
        failure={null}
        isChecking
        onRetry={vi.fn()}
      />
    )
    expect(screen.getByText('Checking demo')).toBeTruthy()
  })

  it('says database when no name is known', () => {
    render(
      <TargetConnectivityNotice failure={null} isChecking onRetry={vi.fn()} />
    )
    expect(screen.getByText('Checking the database')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).not.toMatch(/target/i)
  })
})
