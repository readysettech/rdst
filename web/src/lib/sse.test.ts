import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useAnalyze } from './sse'
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

describe('useAnalyze unknown events', () => {
  it('ignores unknown events and does not enter error state', async () => {
    __resetTargetSwitchLockForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const { result } = renderHook(() => useAnalyze())

    await act(async () => {
      await result.current.analyze({ query: 'SELECT 1', target: 'prod' })
    })

    expect(result.current.error).toBeUndefined()
    expect(result.current.state).not.toBe('error')
  })

  it('activates the target switch lock while analysis is in progress', async () => {
    __resetTargetSwitchLockForTests()

    let resolveFetch: (response: Response) => void = () => {}
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })

    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise))

    const { result } = renderHook(() => {
      const analyze = useAnalyze()
      const lock = useTargetSwitchLockState()
      return { analyze, lock }
    })

    let analyzePromise = Promise.resolve()
    act(() => {
      analyzePromise = result.current.analyze.analyze({ query: 'SELECT 1', target: 'prod' })
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(true)
      expect(result.current.lock.message).toBe(
        'Target switching is disabled while Analyze is in progress.'
      )
    })

    resolveFetch(
      sseResponse(
        'event: complete\ndata: {"success":true,"analysis_id":"a1","query_hash":"h1","explain_results":{},"llm_analysis":{}}\n\n'
      )
    )

    await act(async () => {
      await analyzePromise
    })

    await waitFor(() => {
      expect(result.current.lock.isLocked).toBe(false)
      expect(result.current.lock.message).toBe('')
    })
  })
})
