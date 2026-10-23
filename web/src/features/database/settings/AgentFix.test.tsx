import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AgentFix } from './AgentFix'

const AGENT = {
  key: 'claude:local',
  id: 'claude',
  side: 'local',
  label: 'Claude Code',
  name: 'Claude',
}

const RUN = {
  id: 'fix1',
  agent: AGENT,
  kind: 'setup',
  state: 'running',
  message: null,
  steps: 1,
  progress: [],
  elapsed: 3,
  candidates: [],
  activity: [
    { kind: 'say', text: 'Creating the pg_stat_statements extension.' },
  ],
  target: 'shop',
  waiting: false,
  monitoring: false,
  resume_command: null,
  continue_in: null,
}

function routeFetch(agents: object[], apps: string[] = []) {
  const calls: [string, RequestInit | undefined][] = []
  let polls = 0
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      calls.push([url, init])
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      if (url.endsWith('/agents')) return json({ agents, apps })
      if (url.endsWith('/fix-prompt'))
        return json({ prompt: 'Get shop ready.', attempt: 'n1' })
      if (url.endsWith('/open-app')) return json({ opened: true })
      if (url.endsWith('/agent-connect/setup')) return json(RUN)
      polls += 1
      return json(
        polls < 2
          ? RUN
          : { ...RUN, state: 'done', message: 'shop is ready.\n\nDetails.' }
      )
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('AgentFix', () => {
  it("without an agent RDST can run, hands the database's fix prompt to the user's own", async () => {
    const calls = routeFetch([], ['claude'])
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    renderWithClient(<AgentFix target="shop" onFinished={vi.fn()} />)
    expect(
      await screen.findByText('Have your coding agent fix this')
    ).toBeTruthy()
    expect(screen.queryByTestId('agent-fix-start')).toBeNull()
    fireEvent.click(screen.getByTestId('agent-fix-copy'))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('Get shop ready.')
    )
    expect(await screen.findByText('Prompt copied')).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-fix-in-claude'))
    await waitFor(() =>
      expect(calls.some(([url]) => url.endsWith('/open-app'))).toBe(true)
    )
    const opened = calls.find(([url]) => url.endsWith('/open-app'))
    expect(JSON.parse(String(opened?.[1]?.body))).toEqual({
      app: 'claude',
      attempt: 'n1',
      target: 'shop',
    })
  })

  it('has the agent fix the database, shows what it does, then checks again', async () => {
    const calls = routeFetch([AGENT])
    const onFinished = vi.fn()
    renderWithClient(<AgentFix target="shop" onFinished={onFinished} />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Fix with Claude' })
    )
    expect(await screen.findByText('Claude is getting shop ready')).toBeTruthy()
    expect(
      screen.getByText('Creating the pg_stat_statements extension.')
    ).toBeTruthy()
    const started = calls.find(([url]) => url.endsWith('/agent-connect/setup'))
    expect(JSON.parse(String(started?.[1]?.body))).toEqual({
      agent: 'claude:local',
      targets: ['shop'],
    })
    await waitFor(() => expect(onFinished).toHaveBeenCalled(), {
      timeout: 5000,
    })
    expect(await screen.findByText('shop is ready.')).toBeTruthy()
  })

  it('offers only Continue in the app when the conversation is already there', async () => {
    cleanup()
    const calls: string[] = []
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        calls.push(url)
        const json = (body: object) =>
          new Response(JSON.stringify(body), {
            headers: { 'content-type': 'application/json' },
          })
        if (url.endsWith('/agents'))
          return json({ agents: [AGENT], apps: ['claude'] })
        if (url.endsWith('/conversation/shop'))
          return json({ agent: 'claude:local', in_app: 'claude' })
        return json({ opened: true, resumed: true })
      })
    )
    renderWithClient(
      <AgentFix
        target="shop"
        onFinished={() => {}}
        problem="password authentication failed"
      />
    )
    expect(
      (await screen.findByTestId('agent-fix-in-app')).textContent
    ).toContain("shop's conversation is in the Claude app")
    expect(screen.queryByTestId('agent-fix-start')).toBeNull()
    fireEvent.click(screen.getByTestId('agent-fix-continue-in-app'))
    await waitFor(() =>
      expect(calls.some((url) => url.endsWith('/ask-open'))).toBe(true)
    )
    expect(writeText).toHaveBeenCalledWith(
      "RDST still can't connect to shop: password authentication failed"
    )
  })
})
