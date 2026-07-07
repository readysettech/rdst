import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createAgent,
  deleteAgent,
  fetchAgents,
  useAgentChat,
} from './useAgents'
import { __resetTargetSwitchLockForTests } from './targetSwitchLock'
import type { EventTranscriptItem, TranscriptItem } from '../types/agents'

// The chat message endpoint streams SSE; sessions are created via a JSON POST.
// A single fetch mock routes by URL + method.

function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function eventItems(transcript: TranscriptItem[]): EventTranscriptItem[] {
  return transcript.filter((i): i is EventTranscriptItem => i.kind === 'event')
}

// The hook mixes two fetch callers: the openapi-fetch client (session create /
// delete) passes a Request object, while the manual SSE reader passes a URL
// string. Normalize both to a URL string for routing in the mock.
function requestUrl(input: unknown): string {
  if (typeof input === 'string') return input
  if (input instanceof Request) return input.url
  if (input instanceof URL) return input.toString()
  return String(input)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('agent CRUD functions', () => {
  it('fetchAgents returns the list payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          agents: [{ name: 'a1', target: 'pgtest', max_rows: 1000 }],
          count: 1,
        }),
      ),
    )
    const res = await fetchAgents()
    expect(res.count).toBe(1)
    expect(res.agents[0].name).toBe('a1')
  })

  it('createAgent surfaces the 409 detail message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: "Agent 'a1' already exists" }, 409)),
    )
    await expect(
      createAgent({ name: 'a1', target: 'pgtest' }),
    ).rejects.toThrow(/already exists/)
  })

  it('createAgent surfaces a 422 object detail message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: { message: 'Unknown target: nope' } }, 422),
      ),
    )
    await expect(
      createAgent({ name: 'a1', target: 'nope' }),
    ).rejects.toThrow(/Unknown target/)
  })

  it('deleteAgent returns the write response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ success: true, name: 'a1' })),
    )
    const res = await deleteAgent('a1')
    expect(res.success).toBe(true)
    expect(res.name).toBe('a1')
  })
})

describe('useAgentChat', () => {
  it('streams a full event sequence into the transcript in order', async () => {
    __resetTargetSwitchLockForTests()
    const sse = [
      'event: status\ndata: {"type":"status","phase":"llm","message":"Thinking..."}\n\n',
      'event: thinking\ndata: {"type":"thinking","text":"I should count orders"}\n\n',
      'event: tool_call\ndata: {"type":"tool_call","name":"query_database","input":{"question":"count orders"}}\n\n',
      'event: tool_result\ndata: {"type":"tool_result","success":true,"content":"1 row","data":{"sql":"SELECT count(*) FROM orders","columns":["count"],"rows":[[42]],"row_count":1,"execution_time_ms":3.2,"truncated":false,"query_hash":"abcdef1234"}}\n\n',
      'event: response\ndata: {"type":"response","text":"There are 42 orders."}\n\n',
      'event: complete\ndata: {"type":"complete","success":true,"tool_result_count":1}\n\n',
    ].join('')

    const fetchMock = vi.fn((input: unknown) => {
      const url = requestUrl(input)
      if (url.includes('/chat/sessions/') && url.includes('/message')) {
        return Promise.resolve(sseResponse(sse))
      }
      return Promise.resolve(jsonResponse({ session_id: 's1', agent: 'a1' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAgentChat('a1'))

    await act(async () => {
      await result.current.send('How many orders are there?')
    })

    expect(result.current.state).toBe('idle')
    expect(result.current.sessionId).toBe('s1')
    expect(result.current.messageCount).toBe(1)

    const items = result.current.transcript
    expect(items[0]).toMatchObject({ kind: 'user', text: 'How many orders are there?' })

    // Status events are ephemeral (live indicator), not transcript entries,
    // and the indicator clears once the stream finishes.
    const events = eventItems(items).map((i) => i.event.type)
    expect(events).toEqual([
      'thinking',
      'tool_call',
      'tool_result',
      'response',
      'complete',
    ])
    expect(result.current.statusMessage).toBeNull()
  })

  it('renders a stream error event and returns to idle', async () => {
    __resetTargetSwitchLockForTests()
    const sse =
      'event: status\ndata: {"type":"status","phase":"llm","message":"Thinking..."}\n\n' +
      'event: error\ndata: {"type":"error","message":"401 invalid api key"}\n\n'

    const fetchMock = vi.fn((input: unknown) => {
      const url = requestUrl(input)
      if (url.includes('/message')) return Promise.resolve(sseResponse(sse))
      return Promise.resolve(jsonResponse({ session_id: 's1', agent: 'a1' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAgentChat('a1'))

    await act(async () => {
      await result.current.send('hi')
    })

    // A stream error event is non-fatal to the session: state returns to idle
    // and the live "Thinking..." indicator is cleared.
    expect(result.current.state).toBe('idle')
    expect(result.current.statusMessage).toBeNull()
    const events = eventItems(result.current.transcript)
    const errorEvent = events.find((i) => i.event.type === 'error')
    expect(errorEvent?.event).toMatchObject({ type: 'error', message: '401 invalid api key' })
  })

  it('transparently recreates the session and retries once on a 404', async () => {
    __resetTargetSwitchLockForTests()
    const sse =
      'event: response\ndata: {"type":"response","text":"ok"}\n\n' +
      'event: complete\ndata: {"type":"complete","success":true}\n\n'

    let sessionCreates = 0
    let messageCalls = 0
    const fetchMock = vi.fn((input: unknown) => {
      const url = requestUrl(input)
      if (url.includes('/message')) {
        messageCalls += 1
        // First message hits an expired session (404); second succeeds.
        if (messageCalls === 1) return Promise.resolve(new Response(null, { status: 404 }))
        return Promise.resolve(sseResponse(sse))
      }
      sessionCreates += 1
      return Promise.resolve(jsonResponse({ session_id: `s${sessionCreates}`, agent: 'a1' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAgentChat('a1'))

    await act(async () => {
      await result.current.send('hi')
    })

    expect(sessionCreates).toBe(2)
    expect(messageCalls).toBe(2)
    expect(result.current.state).toBe('idle')
    expect(result.current.sessionId).toBe('s2')
    const events = eventItems(result.current.transcript).map((i) => i.event.type)
    expect(events).toEqual(['response', 'complete'])
  })

  it('surfaces a non-ok session-create failure as a transcript error', async () => {
    __resetTargetSwitchLockForTests()
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse({ detail: { code: 'TARGET_PASSWORD_REQUIRED' } }, 423)),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAgentChat('a1'))

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.state).toBe('error')
    const events = eventItems(result.current.transcript)
    expect(events.some((i) => i.event.type === 'error')).toBe(true)
  })
})
