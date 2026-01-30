import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useReadyset } from './useReadyset'
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

describe('useReadyset unknown events', () => {
  it('ignores unknown events and keeps non-error state', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const { result } = renderHook(() => useReadyset())

    await act(async () => {
      await result.current.setupContainers('prod')
    })

    expect(result.current.error).toBeUndefined()
    expect(result.current.state).not.toBe('error')
  })

  it('activates the target switch lock while readyset operation is running', async () => {
    __resetTargetSwitchLockForTests()

    let resolveFetch: (response: Response) => void = () => {}
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })

    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise))

    const { result } = renderHook(() => {
      const readyset = useReadyset()
      const lock = useTargetSwitchLockState()
      return { readyset, lock }
    })

    let setupPromise = Promise.resolve()
    act(() => {
      setupPromise = result.current.readyset.setupContainers('prod')
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(true)
      expect(result.current.lock.message).toBe(
        'Target switching is disabled while Readyset operations are in progress.'
      )
    })

    resolveFetch(sseResponse('event: complete\ndata: {"success":true}\n\n'))

    await act(async () => {
      await setupPromise
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(false)
      expect(result.current.lock.message).toBe('')
    })
  })
})
