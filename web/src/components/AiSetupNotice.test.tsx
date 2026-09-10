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
  it('asks for a provider only once the probe says one is missing', () => {
    const { rerender } = render(<AiSetupNotice feature="Ask" />)
    expect(screen.queryByRole('alert')).toBeNull()

    mocks.gate.current = { status: 'checking' }
    rerender(<AiSetupNotice feature="Ask" />)
    expect(screen.queryByRole('alert')).toBeNull()

    mocks.gate.current = { status: 'blocked', reason: 'missing' }
    rerender(<AiSetupNotice feature="Ask" />)
    expect(screen.getByText('Connect an AI provider to use this')).toBeTruthy()
    expect(screen.getByText(/^Ask sends your question/)).toBeTruthy()
    expect(
      screen.getByText('Everything else in RDST works without it.')
    ).toBeTruthy()
  })

  it('names the reason a configured provider stopped working', () => {
    mocks.gate.current = { status: 'blocked', reason: 'invalid' }
    render(<AiSetupNotice feature="Query analysis" />)
    expect(screen.getByText(/Anthropic rejected the saved key/)).toBeTruthy()

    cleanup()
    mocks.gate.current = { status: 'blocked', reason: 'exhausted' }
    render(<AiSetupNotice feature="Query analysis" />)
    expect(screen.getByText(/Your included AI is used up/)).toBeTruthy()
  })

  it('routes the fix to the AI settings panel', () => {
    mocks.gate.current = { status: 'blocked', reason: 'missing' }
    render(<AiSetupNotice feature="Ask" />)
    screen.getByRole('button', { name: 'Set up AI' }).click()
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'ai' },
    })
  })
})
