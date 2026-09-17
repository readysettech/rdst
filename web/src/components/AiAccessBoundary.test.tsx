import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AiGate } from '../lib/useAiGate'
import { AiAccessBoundary } from './AiAccessBoundary'

const state = vi.hoisted(() => ({
  path: '/',
  gate: { status: 'checking' } as AiGate,
}))
vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => state.path,
}))
vi.mock('../lib/useAiGate', () => ({ useAiGate: () => state.gate }))
vi.mock('./AiProviderGate', () => ({
  AiProviderGate: ({ gate }: { gate: AiGate }) => (
    <div>Setup {gate.status}</div>
  ),
}))
afterEach(() => {
  cleanup()
  state.path = '/'
})

describe('AI startup boundary', () => {
  it.each<AiGate>([
    { status: 'checking' },
    { status: 'blocked', reason: 'missing' },
    { status: 'blocked', reason: 'invalid' },
    { status: 'blocked', reason: 'exhausted' },
    { status: 'error', message: 'Backend unavailable' },
  ])('keeps the application hidden for $status', (gate) => {
    state.gate = gate
    render(
      <AiAccessBoundary>
        <div>Application</div>
      </AiAccessBoundary>
    )
    expect(screen.queryByText('Application')).toBeNull()
    expect(screen.getByText(`Setup ${gate.status}`)).toBeTruthy()
  })

  it.each<AiGate>([
    { status: 'ready' },
    { status: 'unverified' },
  ])('opens the application for $status', (gate) => {
    state.gate = gate
    render(
      <AiAccessBoundary>
        <div>Application</div>
      </AiAccessBoundary>
    )
    expect(screen.getByText('Application')).toBeTruthy()
  })

  it('allows the account callback to complete before credentials exist', () => {
    state.path = '/account-login'
    state.gate = { status: 'blocked', reason: 'missing' }
    render(
      <AiAccessBoundary>
        <div>Account callback</div>
      </AiAccessBoundary>
    )
    expect(screen.getByText('Account callback')).toBeTruthy()
  })

  it('enters the app after provider setup succeeds', () => {
    state.gate = { status: 'blocked', reason: 'missing' }
    const { rerender } = render(
      <AiAccessBoundary>
        <div>Application</div>
      </AiAccessBoundary>
    )
    state.gate = { status: 'ready' }
    rerender(
      <AiAccessBoundary>
        <div>Application</div>
      </AiAccessBoundary>
    )
    expect(screen.getByText('Application')).toBeTruthy()
    expect(screen.queryByText('Setup blocked')).toBeNull()
  })
})
