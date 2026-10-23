import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AskYourAgent } from './AskYourAgent'

const CLAUDE = {
  key: 'claude:local',
  id: 'claude',
  name: 'Claude',
  label: 'Claude Code',
  side: 'local',
}

function routeFetch(agents: object[], apps: string[]) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string) => {
      calls.push(url)
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      if (url.endsWith('/agents')) return json({ agents, apps })
      if (url.endsWith('/ask-prompt'))
        return json({ prompt: 'Ask about shop.', attempt: 'a1' })
      if (url.endsWith('/ask-open'))
        return json({ opened: true, resumed: true })
      return json({ connected: true })
    })
  )
  return calls
}

const writeText = vi.fn().mockResolvedValue(undefined)
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText },
  configurable: true,
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AskYourAgent', () => {
  it("looks up the user's agents only when they connect, then reopens the setup conversation", async () => {
    const calls = routeFetch([CLAUDE], ['claude'])
    renderWithClient(<AskYourAgent target="shop" />)
    expect(calls).toEqual([])
    fireEvent.click(screen.getByTestId('ask-connect'))
    expect(
      await screen.findByText(
        'Opened the Claude conversation that set up shop.'
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByTestId('ask-everywhere'))
    expect(
      await screen.findByText(/Claude Code is connected to RDST/)
    ).toBeTruthy()
    expect(calls.some((url) => url.endsWith('/agent-connect/connect'))).toBe(
      true
    )
  })

  it('adds RDST to the CLI without an app, and copies the prompt without either', async () => {
    routeFetch([CLAUDE], [])
    renderWithClient(<AskYourAgent target="shop" />)
    fireEvent.click(screen.getByTestId('ask-connect'))
    expect(
      await screen.findByText(/Claude Code is connected to RDST/)
    ).toBeTruthy()
    cleanup()
    routeFetch([], [])
    renderWithClient(<AskYourAgent target="shop" />)
    fireEvent.click(screen.getByTestId('ask-connect'))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('Ask about shop.')
    )
    expect(await screen.findByText(/it copied the prompt/)).toBeTruthy()
  })

  it('connects Codex the same way, by its own button', async () => {
    const calls = routeFetch([CLAUDE], ['codex'])
    renderWithClient(<AskYourAgent target="shop" />)
    fireEvent.click(screen.getByTestId('ask-connect-codex'))
    expect(
      await screen.findByText('Opened the Codex conversation that set up shop.')
    ).toBeTruthy()
    expect(calls.some((url) => url.endsWith('/ask-open'))).toBe(true)
  })
})
