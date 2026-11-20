import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { CodingAgentsGroup } from './CodingAgentsGroup'

const command =
  'claude mcp add --transport http rdst "http://127.0.0.1:8787/mcp"'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            url: 'http://127.0.0.1:8787',
            mcp_url: 'http://127.0.0.1:8787/mcp',
            command,
          }),
          { headers: { 'content-type': 'application/json' } }
        )
    )
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('CodingAgentsGroup', () => {
  it('points a server at the database page, where agents get a key', () => {
    const meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    try {
      renderWithClient(<CodingAgentsGroup />)
      expect(screen.getByText(/Connect Claude or Codex to RDST/)).toBeTruthy()
      expect(
        screen.queryByRole('button', { name: 'Copy Claude Code command' })
      ).toBeNull()
      expect(vi.mocked(fetch)).not.toHaveBeenCalled()
    } finally {
      meta.remove()
    }
  })

  it('shows the MCP URL and the Claude Code command, with no token', async () => {
    const { container } = renderWithClient(<CodingAgentsGroup />)
    expect(await screen.findByText('http://127.0.0.1:8787/mcp')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Copy Claude Code command' })
    ).toBeTruthy()
    expect(screen.getByText(/Other agents use the same URL\./)).toBeTruthy()
    expect(container.textContent).not.toMatch(/token|Authorization|Bearer/i)
    expect(screen.queryByRole('button', { name: /reset/i })).toBeNull()
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      '/api/agent-connect/mcp-config'
    )
  })
})
