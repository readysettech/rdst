import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AskYourAgent, serverAgentCommands } from './AskYourAgent'

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

  describe('in server mode', () => {
    let meta: HTMLMetaElement
    beforeEach(() => {
      meta = document.createElement('meta')
      meta.name = 'rdst-runtime'
      meta.content = 'container'
      document.head.appendChild(meta)
    })
    afterEach(() => meta.remove())

    function routeServer(initial: object) {
      const calls: { url: string; method: string }[] = []
      let status = initial
      vi.stubGlobal(
        'fetch',
        vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
          const method = init?.method ?? 'GET'
          calls.push({ url, method })
          const json = (body: object) =>
            new Response(JSON.stringify(body), {
              headers: { 'content-type': 'application/json' },
            })
          if (url.endsWith('/api/server/agent-key')) {
            if (method === 'POST') {
              status = {
                exists: true,
                created_at: 1795200000,
                last_used_at: null,
              }
              return json({ key: 'rdst_ak_secret', ...status })
            }
            if (method === 'DELETE') {
              status = { exists: false, created_at: null, last_used_at: null }
              return json({ revoked: true, ...status })
            }
            return json(status)
          }
          if (url.endsWith('/ask-prompt'))
            return json({
              prompt:
                'Ask about shop with -H "Authorization: Bearer RDST_AGENT_KEY_HERE".',
              attempt: 'a1',
            })
          return json({})
        })
      )
      return calls
    }
    const NO_KEY = { exists: false, created_at: null, last_used_at: null }

    it('makes an agent key and copies commands that carry it', async () => {
      const calls = routeServer(NO_KEY)
      writeText.mockClear()
      renderWithClient(<AskYourAgent target="shop" />)
      expect(screen.queryByText(/local connection/)).toBeNull()
      expect(screen.queryByTestId('ask-connect')).toBeNull()
      const create = await screen.findByTestId('ask-create-key')
      await waitFor(() => expect(create.hasAttribute('disabled')).toBe(false))
      fireEvent.click(create)
      fireEvent.click(await screen.findByTestId('ask-connect'))
      await waitFor(() =>
        expect(writeText).toHaveBeenCalledWith(
          serverAgentCommands(window.location.origin, 'rdst_ak_secret').claude
        )
      )
      expect(screen.getByTestId('ask-key-note').textContent).toMatch(
        /Shown once/
      )
      // Nothing looks for local agents or apps on a server.
      expect(calls.some(({ url }) => url.endsWith('/agents'))).toBe(false)
      fireEvent.click(screen.getByTestId('ask-copy'))
      await waitFor(() =>
        expect(writeText).toHaveBeenLastCalledWith(
          'Ask about shop with -H "Authorization: Bearer rdst_ak_secret".'
        )
      )
    })

    it('shows the command to select when the browser blocks the clipboard', async () => {
      routeServer(NO_KEY)
      writeText.mockRejectedValueOnce(new Error('NotAllowedError'))
      renderWithClient(<AskYourAgent target="shop" />)
      const create = await screen.findByTestId('ask-create-key')
      await waitFor(() => expect(create.hasAttribute('disabled')).toBe(false))
      fireEvent.click(create)
      const shown = await screen.findByTestId('ask-command')
      expect(shown.textContent).toContain(
        serverAgentCommands(window.location.origin, 'rdst_ak_secret').claude
      )
      fireEvent.click(screen.getByTestId('ask-connect'))
      expect(
        await screen.findByText(/Select the command below and copy it/)
      ).toBeTruthy()
    })

    it('shows an existing key by date, and replaces or revokes it', async () => {
      const calls = routeServer({
        exists: true,
        created_at: 1795200000,
        last_used_at: 1795203600,
      })
      renderWithClient(<AskYourAgent target="shop" />)
      expect((await screen.findByTestId('ask-key-note')).textContent).toMatch(
        /Agent key created .+, last used .+\./
      )
      expect(screen.getByTestId('ask-create-key').textContent).toMatch(
        /Replace key/
      )
      fireEvent.click(screen.getByTestId('ask-revoke-key'))
      expect(
        await screen.findByText(
          'Key revoked. Agents that used it no longer have access.'
        )
      ).toBeTruthy()
      expect(
        calls.some(
          ({ url, method }) =>
            url.endsWith('/api/server/agent-key') && method === 'DELETE'
        )
      ).toBe(true)
      expect(screen.queryByTestId('ask-revoke-key')).toBeNull()
    })

    it('builds the Claude and Codex commands from the public address and the key', () => {
      const { claude, codex } = serverAgentCommands(
        'https://rdst.example.com',
        'rdst_ak_k'
      )
      expect(claude).toBe(
        'claude mcp add --transport http rdst https://rdst.example.com/mcp --header "Authorization: Bearer rdst_ak_k"'
      )
      expect(codex).toBe(
        'export RDST_AGENT_KEY=rdst_ak_k\ncodex mcp add rdst --url https://rdst.example.com/mcp --bearer-token-env-var RDST_AGENT_KEY'
      )
    })
  })
})
