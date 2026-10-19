import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AGENT_EVENTS_URL,
  advanceAgentProgress,
  currentAgentStep,
  fetchAgentPrompt,
  NO_PROGRESS,
  parseAgentEvent,
  recheckAgentTarget,
  subscribeAgentConnect,
} from './agentConnect'

afterEach(() => vi.unstubAllGlobals())

describe('parseAgentEvent', () => {
  it('reads the step, target and message out of an unnamed frame', () => {
    expect(
      parseAgentEvent('{"step":"database_added","target":"shop","ts":1}')
    ).toEqual({ step: 'database_added', target: 'shop', message: null, ts: 1 })
  })

  it('takes the step from the event name when the payload leaves it out', () => {
    expect(parseAgentEvent('{"target":"shop"}', 'tested')).toEqual({
      step: 'tested',
      target: 'shop',
      message: null,
    })
  })

  it('reads an awaiting_user frame', () => {
    expect(
      parseAgentEvent(
        '{"step":"awaiting_user","target":"shop","message":"Click it","ts":5}'
      )
    ).toEqual({
      step: 'awaiting_user',
      target: 'shop',
      message: 'Click it',
      ts: 5,
    })
  })

  it('reads what a needs_setup frame says to change', () => {
    expect(
      parseAgentEvent(
        JSON.stringify({
          step: 'needs_setup',
          target: 'shop',
          check: 'query_stats',
          instructions: ['Load pg_stat_statements', 7],
        })
      )
    ).toEqual({
      step: 'needs_setup',
      target: 'shop',
      message: null,
      check: 'query_stats',
      instructions: ['Load pg_stat_statements'],
    })
  })

  it('ignores keep-alives and unknown steps', () => {
    expect(parseAgentEvent('')).toBeNull()
    expect(parseAgentEvent('{"step":"dropped_tables"}')).toBeNull()
    expect(parseAgentEvent('"ping"')).toBeNull()
  })
})

describe('advanceAgentProgress', () => {
  it('ticks the step and every one before it', () => {
    const next = advanceAgentProgress(NO_PROGRESS, {
      step: 'tested',
      target: 'shop',
    })
    expect([...next.done]).toEqual(['connected', 'database_added', 'tested'])
    expect(next.target).toBe('shop')
    expect(currentAgentStep(next)).toBe('monitoring_enabled')
  })

  it('keeps the target through an error and clears the error on the next step', () => {
    const added = advanceAgentProgress(NO_PROGRESS, {
      step: 'database_added',
      target: 'shop',
    })
    const failed = advanceAgentProgress(added, {
      step: 'error',
      message: 'pg_stat_statements is off',
    })
    expect(failed.error).toBe('pg_stat_statements is off')
    expect(failed.target).toBe('shop')
    expect(currentAgentStep(failed)).toBe('tested')
    const tested = advanceAgentProgress(failed, { step: 'tested' })
    expect(tested.error).toBeNull()
  })

  it('holds the test on missing setup and counts each verdict', () => {
    const blocked = advanceAgentProgress(NO_PROGRESS, {
      step: 'needs_setup',
      target: 'shop',
      check: 'query_stats',
      instructions: ['Load pg_stat_statements'],
    })
    expect([...blocked.done]).toEqual(['connected', 'database_added'])
    expect(blocked.setup).toEqual({
      check: 'query_stats',
      instructions: ['Load pg_stat_statements'],
    })
    expect(currentAgentStep(blocked)).toBe('tested')
    const again = advanceAgentProgress(blocked, {
      step: 'needs_setup',
      target: 'shop',
    })
    expect(again.verdicts).toBe(2)
    const passed = advanceAgentProgress(again, { step: 'tested' })
    expect(passed.setup).toBeNull()
    expect(passed.verdicts).toBe(3)
    expect(currentAgentStep(passed)).toBe('monitoring_enabled')
  })

  it("waits on the user's click once the agent asks to turn monitoring on", () => {
    const waiting = advanceAgentProgress(NO_PROGRESS, {
      step: 'awaiting_user',
      target: 'shop',
    })
    expect([...waiting.done]).toEqual(['connected', 'database_added', 'tested'])
    expect(waiting.awaitingUser).toBe(true)
    expect(currentAgentStep(waiting)).toBe('monitoring_enabled')
    const done = advanceAgentProgress(waiting, {
      step: 'monitoring_enabled',
      target: 'shop',
    })
    expect(done.awaitingUser).toBe(false)
    expect(currentAgentStep(done)).toBeNull()
  })

  it('is done once monitoring is on', () => {
    const done = advanceAgentProgress(NO_PROGRESS, {
      step: 'monitoring_enabled',
      target: 'shop',
    })
    expect(currentAgentStep(done)).toBeNull()
  })
})

describe('fetchAgentPrompt', () => {
  it('posts for a prompt and reads its attempt', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          prompt: 'Connect my database',
          attempt: '0123abcd0123abcd',
          issued_at: 1700000000,
        }),
        { headers: { 'content-type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    await expect(fetchAgentPrompt()).resolves.toEqual({
      prompt: 'Connect my database',
      issuedAt: 1700000000,
      attempt: '0123abcd0123abcd',
    })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/agent-connect/prompt')
    expect(init.method).toBe('POST')
  })

  it("says RDST didn't answer instead of a bare network error", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    )
    await expect(fetchAgentPrompt()).rejects.toThrow("RDST didn't answer.")
  })

  it("surfaces the server's reason when it can't make one", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Not from this computer.' }), {
          status: 403,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
    await expect(fetchAgentPrompt()).rejects.toThrow('Not from this computer.')
  })
})

describe('subscribeAgentConnect', () => {
  it('replays from the prompt and hands each step on', () => {
    const listeners = new Map<string, (event: MessageEvent<string>) => void>()
    const close = vi.fn()
    const createSource = vi.fn(() => ({
      addEventListener: (
        name: string,
        listener: (event: MessageEvent<string>) => void
      ) => listeners.set(name, listener),
      onerror: null,
      close,
    }))
    const onEvent = vi.fn()
    const stop = subscribeAgentConnect(onEvent, {
      since: 1700000000,
      createSource,
    })
    expect(createSource).toHaveBeenCalledWith(
      `${AGENT_EVENTS_URL}?since=1700000000`
    )
    listeners.get('message')?.({
      data: '{"step":"connected"}',
    } as MessageEvent<string>)
    expect(onEvent).toHaveBeenCalledWith({
      step: 'connected',
      target: null,
      message: null,
    })
    stop()
    expect(close).toHaveBeenCalled()
  })
})

describe('recheckAgentTarget', () => {
  it('asks RDST to test the database again', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    await recheckAgentTarget('shop')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-connect/recheck',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ target: 'shop' }),
      })
    )
  })

  it('says why when RDST refuses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Unknown database' }), {
          status: 404,
        })
      )
    )
    await expect(recheckAgentTarget('shop')).rejects.toThrow('Unknown database')
  })
})
