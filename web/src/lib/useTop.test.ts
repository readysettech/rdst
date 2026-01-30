import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useTop } from './useTop'
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

describe('useTop unknown events', () => {
  it('ignores unknown events without setting error', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const { result } = renderHook(() => useTop())

    await act(async () => {
      result.current.startRealtime('prod')
      await Promise.resolve()
    })

    expect(result.current.error).toBeNull()
    expect(result.current.state).not.toBe('error')
  })

  it('activates the target switch lock while top streaming is active', async () => {
    __resetTargetSwitchLockForTests()

    let resolveFetch: (response: Response) => void = () => {}
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })

    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise))

    const { result } = renderHook(() => {
      const top = useTop()
      const lock = useTargetSwitchLockState()
      return { top, lock }
    })

    act(() => {
      result.current.top.startRealtime('prod')
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(true)
      expect(result.current.lock.message).toBe(
        'Target switching is disabled while Top Queries is in progress.'
      )
    })

    resolveFetch(sseResponse('event: complete\ndata: {"queries":[],"newly_saved":0}\n\n'))
    await act(async () => {
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(false)
      expect(result.current.lock.message).toBe('')
    })
  })
})
