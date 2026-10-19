import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { selectGlobalTarget } from '../../features/database/targetSelection'
import { addFlowFor, endAddFlow, newAddAttempt } from '../../lib/addFlow'
import { trackEvent } from '../../lib/analytics'
import { useConfigure } from '../../lib/useConfigure'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { AddDatabaseDialog } from './AddDatabaseDialog'
import { AgentConnect } from './AgentConnect'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))
vi.mock('../../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))
vi.mock('../../lib/backgroundRuns', () => ({ startBootstrapRun: vi.fn() }))
vi.mock('../../features/database/targetSelection', () => ({
  selectGlobalTarget: vi.fn(),
}))
vi.mock('../../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  trackEvent: vi.fn(),
}))

/** The progress stream, driven by the test. */
class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string
  listeners = new Map<string, (event: MessageEvent<string>) => void>()
  onerror: (() => void) | null = null
  closed = false
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener(
    name: string,
    listener: (event: MessageEvent<string>) => void
  ) {
    this.listeners.set(name, listener)
  }
  close() {
    this.closed = true
  }
  emit(payload: object) {
    act(() => {
      this.listeners.get('message')?.({
        data: JSON.stringify(payload),
      } as MessageEvent<string>)
    })
  }
}

const PROMPT = 'RDST is a local database monitoring app from Readyset.'
const writeText = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  FakeEventSource.last = null
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            prompt: PROMPT,
            issued_at: 1700000000,
          }),
          { headers: { 'content-type': 'application/json' } }
        )
    )
  )
  writeText.mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  vi.mocked(useSystemStatus).mockReturnValue({
    data: { targets: [] },
  } as never)
  vi.mocked(useConfigure).mockReturnValue({} as never)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function renderAgent() {
  const onAddByHand = vi.fn()
  renderWithClient(<AgentConnect onAddByHand={onAddByHand} />)
  expect(await screen.findByText(PROMPT)).toBeTruthy()
  await waitFor(() => expect(FakeEventSource.last).not.toBeNull())
  return { onAddByHand, stream: FakeEventSource.last as FakeEventSource }
}

const status = () => screen.getByTestId('agent-status')

describe('AgentConnect', () => {
  it('shows the prompt as a short card and copies it', async () => {
    await renderAgent()
    expect(screen.getByText('Prompt for your agent')).toBeTruthy()
    expect(
      screen.getByText(
        /Paste this into Claude Code \(or any coding agent\) open in the project whose database you want RDST to watch/
      )
    ).toBeTruthy()
    expect(
      screen.getByText("It has RDST's local address.")
    ).toBeTruthy()
    expect(screen.queryByText(/minute|expire|once/i)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show full prompt' }))
    expect(
      screen.getByRole('button', { name: 'Hide full prompt' })
    ).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-copy-prompt'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PROMPT))
    expect(await screen.findByText('Copied')).toBeTruthy()
    expect(trackEvent).toHaveBeenCalledWith('agent_prompt_shown', {
      attempt_id: null,
    })
  })

  it('names the attempt by a hash of its nonce, never the nonce', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        async () =>
          new Response(
            JSON.stringify({
              prompt: PROMPT,
              attempt: '0123abcd0123abcd',
              issued_at: 1700000000,
            }),
            { headers: { 'content-type': 'application/json' } }
          )
      )
    )
    await renderAgent()
    await waitFor(() =>
      expect(trackEvent).toHaveBeenCalledWith('agent_prompt_shown', {
        attempt_id: expect.stringMatching(/^[0-9a-f]{16}$/),
      })
    )
    expect(JSON.stringify(vi.mocked(trackEvent).mock.calls)).not.toContain(
      '0123abcd0123abcd'
    )
  })

  it('follows the agent until it adds the database, then opens its turn-on dialog', async () => {
    const { stream } = await renderAgent()
    expect(stream.url).toBe('/api/agent-connect/events?since=1700000000')
    expect(status().getAttribute('data-state')).toBe('waiting')
    expect(screen.getByText('Waiting for your agent')).toBeTruthy()

    stream.emit({ step: 'connected' })
    expect(status().getAttribute('data-state')).toBe('connected')
    expect(screen.getByText('Your agent connected')).toBeTruthy()
    expect(screen.getByText('Your agent connected.')).toBeTruthy()

    stream.emit({ step: 'database_added', target: 'shop' })
    expect(status().getAttribute('data-state')).toBe('added')
    expect(screen.getByText('Added shop')).toBeTruthy()
    await waitFor(
      () =>
        expect(navigate).toHaveBeenCalledWith({
          to: '/database',
          search: { target: 'shop', panel: 'enable' },
        }),
      { timeout: 3000 }
    )
    expect(selectGlobalTarget).toHaveBeenCalledWith('shop')
    expect(addFlowFor('shop')).toMatchObject({
      via: 'agent',
      agentSince: 1700000000,
    })
    endAddFlow('shop')
  })

  it("opens the database's page without the dialog once monitoring is already on", async () => {
    const { stream } = await renderAgent()
    stream.emit({ step: 'monitoring_enabled', target: 'shop' })
    await waitFor(
      () =>
        expect(navigate).toHaveBeenCalledWith({
          to: '/database',
          search: { target: 'shop' },
        }),
      { timeout: 3000 }
    )
    endAddFlow('shop')
  })

  it("shows the agent's error with a way to connect manually", async () => {
    const { stream, onAddByHand } = await renderAgent()
    stream.emit({ step: 'connected' })
    stream.emit({ step: 'error', message: "Couldn't reach the database." })
    expect(status().getAttribute('data-state')).toBe('error')
    expect(await screen.findByText("Couldn't reach the database.")).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-connect-manually'))
    expect(onAddByHand).toHaveBeenCalled()
  })

  it('reports where the attempt stopped when the dialog closes', async () => {
    const attempt = newAddAttempt()
    renderWithClient(<AgentConnect onAddByHand={vi.fn()} attempt={attempt} />)
    expect(await screen.findByText(PROMPT)).toBeTruthy()
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull())
    FakeEventSource.last?.emit({ step: 'connected' })
    cleanup()
    expect(trackEvent).toHaveBeenCalledWith('database_add_abandoned', {
      last_stage: 'paired',
      via: 'agent',
      seconds_open: expect.any(Number),
    })
  })

  it('never dead-ends when RDST has no prompt to give', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue(
        new Response(
          JSON.stringify({ prompt: PROMPT, issued_at: 1700000000 }),
          {
            headers: { 'content-type': 'application/json' },
          }
        )
      )
    vi.stubGlobal('fetch', fetchMock)
    const onAddByHand = vi.fn()
    renderWithClient(<AgentConnect onAddByHand={onAddByHand} />)
    expect(
      await screen.findByText("Couldn't get a prompt from RDST")
    ).toBeTruthy()
    expect(screen.getByText(/RDST didn't answer/)).toBeTruthy()
    expect(screen.queryByText(/Failed to fetch/)).toBeNull()

    fireEvent.click(screen.getByTestId('agent-connect-url-instead'))
    expect(onAddByHand).toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(PROMPT)).toBeTruthy()
  })

  it('names a server without agent connect', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('{}', { status: 404 }))
    )
    renderWithClient(<AgentConnect onAddByHand={vi.fn()} />)
    expect(
      await screen.findByText(/can't connect through an agent yet/)
    ).toBeTruthy()
  })

  it('leaves for the form at any stage', async () => {
    const onAddByHand = vi.fn()
    const { unmount } = renderWithClient(
      <AgentConnect onAddByHand={onAddByHand} />
    )
    expect(await screen.findByText(PROMPT)).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-connect-manually'))
    expect(onAddByHand).toHaveBeenCalled()
    unmount()
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
  })

  it('links how data is handled, and nothing more', async () => {
    await renderAgent()
    expect(
      screen.getByRole('link', { name: 'How your data is handled' })
    ).toBeTruthy()
    expect(screen.queryByText(/keyring/i)).toBeNull()
  })
})

describe('AddDatabaseDialog agent path', () => {
  it('opens from the form and goes back to it', async () => {
    renderWithClient(<AddDatabaseDialog request="form" onClose={vi.fn()} />)
    fireEvent.click(screen.getByTestId('agent-connect-option'))
    expect(
      await screen.findByRole('heading', {
        name: 'Connect with your coding agent',
      })
    ).toBeTruthy()
    expect(trackEvent).toHaveBeenCalledWith('database_add_path_selected', {
      via: 'agent',
    })
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(await screen.findByLabelText('Connection string')).toBeTruthy()
  })

  it('opens straight on the agent path from ?add=agent', async () => {
    renderWithClient(<AddDatabaseDialog request="agent" onClose={vi.fn()} />)
    expect(await screen.findByText(PROMPT)).toBeTruthy()
  })
})
