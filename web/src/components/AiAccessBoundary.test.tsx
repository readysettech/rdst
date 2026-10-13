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
  AiProviderGate: ({ gate, title }: { gate: AiGate; title: string }) => (
    <div>
      <div>Setup {gate.status}</div>
      <div data-testid="gate-title">{title}</div>
    </div>
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
  ])('opens local database monitoring for $status', (gate) => {
    state.path = '/database'
    state.gate = gate
    render(
      <AiAccessBoundary>
        <div>Local database readings</div>
      </AiAccessBoundary>
    )
    expect(screen.getByText('Local database readings')).toBeTruthy()
    expect(screen.queryByText(`Setup ${gate.status}`)).toBeNull()
  })

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

it.each([
  '/inbox',
  '/configure',
])('allows local operations on %s without AI access', (path) => {
  state.path = path
  state.gate = { status: 'blocked', reason: 'missing' }
  render(
    <AiAccessBoundary>
      <div>Local operation</div>
    </AiAccessBoundary>
  )
  expect(screen.getByText('Local operation')).toBeTruthy()
})

it.each([
  '/',
  '/onboarding',
])('keeps the fresh-install chooser on %s', (path) => {
  state.path = path
  state.gate = { status: 'blocked', reason: 'missing' }
  render(
    <AiAccessBoundary>
      <div>Application</div>
    </AiAccessBoundary>
  )
  expect(screen.getByText('Setup blocked')).toBeTruthy()
  expect(screen.queryByText('Application')).toBeNull()
})

it.each([
  ['/ask', 'Ask'],
  ['/agent-activity', 'Activity'],
  ['/platform', 'Readyset Platform'],
  ['/help', 'Help'],
])('stands in for %s under its own bar title, inside the frame', (path, title) => {
  state.path = path
  state.gate = { status: 'blocked', reason: 'missing' }
  render(
    <AiAccessBoundary frame={(gate) => <main data-testid="shell">{gate}</main>}>
      <div>Application</div>
    </AiAccessBoundary>
  )
  expect(screen.getByTestId('shell').textContent).toContain('Setup blocked')
  expect(screen.getByTestId('gate-title').textContent).toBe(title)
})
