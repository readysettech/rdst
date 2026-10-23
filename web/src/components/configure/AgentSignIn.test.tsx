import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const signInAgent = vi.fn()
const fetchDiscoveryOptions = vi.fn()
vi.mock('../../lib/agentDiscovery', () => ({
  signInAgent: (...args: unknown[]) => signInAgent(...args),
  fetchDiscoveryOptions: (...args: unknown[]) => fetchDiscoveryOptions(...args),
}))
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

import { AgentSignIn } from './AgentSignIn'

const CLAUDE = {
  key: 'claude:local',
  id: 'claude' as const,
  side: 'local' as const,
  label: 'Claude Code',
  name: 'Claude',
  signed_in: false,
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  signInAgent.mockReset()
  fetchDiscoveryOptions.mockReset()
})

describe('AgentSignIn', () => {
  it('opens the sign-in, then carries on once the agent is signed in', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    signInAgent.mockResolvedValue({ opened: true })
    fetchDiscoveryOptions
      .mockResolvedValueOnce({ agents: [CLAUDE], apps: [] })
      .mockResolvedValue({ agents: [{ ...CLAUDE, signed_in: true }], apps: [] })
    const onSignedIn = vi.fn()
    render(<AgentSignIn agent={CLAUDE} onSignedIn={onSignedIn} />)
    expect(
      screen.getByText(/Claude Code is signed out on this computer/)
    ).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-sign-in-open'))
    await waitFor(() =>
      expect(signInAgent).toHaveBeenCalledWith('claude:local')
    )
    expect(
      await screen.findByText(/Finish signing in to Claude Code/)
    ).toBeTruthy()
    await vi.advanceTimersByTimeAsync(4000)
    expect(onSignedIn).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(4000)
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1))
    expect(fetchDiscoveryOptions).toHaveBeenCalledWith(true)
  })

  it('offers the desktop app, which has a sign-in of its own', async () => {
    const open = vi.fn(async () => undefined)
    render(
      <AgentSignIn
        agent={CLAUDE}
        onSignedIn={vi.fn()}
        inApp={{ name: 'Claude', open }}
      />
    )
    expect(screen.getByText(/or carry on in the Claude app/)).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-sign-in-app'))
    await waitFor(() => expect(open).toHaveBeenCalledTimes(1))
    expect(signInAgent).not.toHaveBeenCalled()
  })

  it('says why when the sign-in window can not open', async () => {
    signInAgent.mockRejectedValue(
      new Error("RDST couldn't find a terminal to open.")
    )
    render(<AgentSignIn agent={CLAUDE} onSignedIn={vi.fn()} />)
    fireEvent.click(screen.getByTestId('agent-sign-in-open'))
    expect(
      await screen.findByText("RDST couldn't find a terminal to open.")
    ).toBeTruthy()
  })
})
