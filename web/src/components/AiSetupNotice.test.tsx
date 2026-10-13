import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AiGate } from '../lib/useAiGate'
import { AiSetupNotice } from './AiSetupNotice'

const mocks = vi.hoisted(() => ({
  gate: { current: { status: 'ready' } as AiGate },
  navigate: vi.fn(),
}))

vi.mock('../lib/useAiGate', () => ({ useAiGate: () => mocks.gate.current }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

afterEach(() => {
  cleanup()
  mocks.gate.current = { status: 'ready' }
  mocks.navigate.mockClear()
})

describe('AiSetupNotice', () => {
  it('says AI checks are paused only once the probe says a provider is missing', () => {
    const { rerender } = render(<AiSetupNotice />)
    expect(screen.queryByRole('alert')).toBeNull()

    mocks.gate.current = { status: 'checking' }
    rerender(<AiSetupNotice />)
    expect(screen.queryByRole('alert')).toBeNull()

    mocks.gate.current = { status: 'blocked', reason: 'missing' }
    rerender(<AiSetupNotice />)
    expect(screen.getByRole('alert').textContent).toContain(
      'AI checks paused. Sign in or add a key in Settings.'
    )
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  it('reads the same line whatever stopped the provider', () => {
    mocks.gate.current = { status: 'blocked', reason: 'invalid' }
    render(<AiSetupNotice />)
    expect(
      screen.getByText('AI checks paused. Sign in or add a key in Settings.')
    ).toBeTruthy()
  })

  it('routes the fix to the AI settings panel', () => {
    mocks.gate.current = { status: 'blocked', reason: 'missing' }
    render(<AiSetupNotice />)
    screen.getByRole('button', { name: 'Set up AI' }).click()
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'ai' },
    })
  })
})
