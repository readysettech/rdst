import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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

describe('useAsk unknown events', () => {
  it('ignores unknown events and does not set error state', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const { result } = renderHook(() => useAsk())

    await act(async () => {
      await result.current.ask({ question: 'hello', target: 'prod' })
    })

    expect(result.current.error).toBeUndefined()
    expect(result.current.state).not.toBe('error')
  })

  it('keeps the target switch lock active during clarification and clears it on reset', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
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
