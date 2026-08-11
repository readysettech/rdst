import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAsk } from './ask'
import {
  __resetTargetSwitchLockForTests,
  useTargetSwitchLockState,
} from './targetSwitchLock'

function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetTargetSwitchLockForTests()
})

describe('useAsk stream lifecycle', () => {
  it('ignores unknown events when a valid terminal result follows', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(
            [
              'event: unknown\r\ndata: {"message":"x"}\r\n\r\n',
              'event: result\r\n',
              'data: {"type":"result",\r\n',
              'data: "success":true,"sql":"SELECT 1","columns":["one"],"rows":[[1]],"row_count":1,"execution_time_ms":1,"llm_calls":0,"total_tokens":0,"query_hash":"h1","query_tag":"one"}\r\n\r\n',
            ].join('')
          )
        )
    )

    const { result } = renderHook(() => useAsk())

    await act(async () => {
      await result.current.ask({ question: 'hello', target: 'prod' })
    })

    expect(result.current.error).toBeUndefined()
    expect(result.current.state).toBe('complete')
    expect(result.current.result?.sql).toBe('SELECT 1')
  })

  it('reports an incomplete stream instead of leaving Ask spinning', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(
            'event: status\ndata: {"type":"status","phase":"schema","message":"Inspecting schema"}\n\n'
          )
        )
    )

    const { result } = renderHook(() => useAsk())

    await act(async () => {
      await result.current.ask({ question: 'hello', target: 'prod' })
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error?.code).toBe('ASK_STREAM_INCOMPLETE')
    expect(result.current.error?.target).toBe('prod')
  })

  it('cancels an active request without converting the abort into an error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              const abortError = new Error('Aborted')
              abortError.name = 'AbortError'
              reject(abortError)
            })
          })
      )
    )

    const { result } = renderHook(() => useAsk())
    let request: Promise<void> | undefined

    act(() => {
      request = result.current.ask({ question: 'hello', target: 'prod' })
    })
    await waitFor(() => expect(result.current.state).toBe('loading'))

    act(() => {
      result.current.cancel()
    })
    await act(async () => {
      await request
    })

    expect(result.current.state).toBe('cancelled')
    expect(result.current.error).toBeUndefined()
  })

  it('keeps the target switch lock active during clarification and clears it on reset', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(
            'event: clarification_needed\ndata: {"session_id":"s1","interpretations":[],"questions":[]}\n\n'
          )
        )
    )

    const { result } = renderHook(() => {
      const ask = useAsk()
      const lock = useTargetSwitchLockState()
      return { ask, lock }
    })

    await act(async () => {
      await result.current.ask.ask({ question: 'hello', target: 'prod' })
    })

    expect(result.current.ask.state).toBe('clarification_needed')
    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(true)
      expect(result.current.lock.message).toBe(
        'Target switching is disabled while Ask is in progress.'
      )
    })

    act(() => {
      result.current.ask.reset()
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(false)
      expect(result.current.lock.message).toBe('')
    })
  })
})
