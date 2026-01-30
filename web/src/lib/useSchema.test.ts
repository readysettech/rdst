import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useSchema } from './useSchema'
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

describe('useSchema unknown events', () => {
  it('ignores unknown annotate events and does not set error', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const { result } = renderHook(() => useSchema())

    await act(async () => {
      await result.current.annotateWithLLM('prod')
    })

    expect(result.current.error).toBeNull()
  })

  it('activates the target switch lock while loading schema requests', async () => {
    __resetTargetSwitchLockForTests()

    let resolveFetch: (response: Response) => void = () => {}
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })

    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise))

    const { result } = renderHook(() => {
      const schema = useSchema()
      const lock = useTargetSwitchLockState()
      return { schema, lock }
    })

    let checkPromise = Promise.resolve<unknown>(null)
    act(() => {
      checkPromise = result.current.schema.checkStatus('prod')
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(true)
      expect(result.current.lock.message).toBe(
        'Target switching is disabled while Schema operations are in progress.'
      )
    })

    resolveFetch(
      new Response(
        JSON.stringify({
          target: 'prod',
          exists: true,
          source: 'semantic_layer',
        }),
        { status: 200 }
      )
    )

    await act(async () => {
      await checkPromise
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(false)
      expect(result.current.lock.message).toBe('')
    })
  })
})
