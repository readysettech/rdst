import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React, { type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useConfigure } from './useConfigure'

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
  vi.clearAllMocks()
})

describe('useConfigure unknown events', () => {
  it('ignores unknown events without setting error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const queryClient = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      React.createElement(QueryClientProvider, { client: queryClient }, children)

    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      result.current.testConnection('prod')
      await Promise.resolve()
    })

    expect(result.current.error).toBeNull()
    expect(result.current.state).not.toBe('error')
  })

  it('invalidates status query after removing a target', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            targets: [
              {
                name: 'staging',
                engine: 'postgresql',
                has_password: true,
                is_default: true,
              },
            ],
            default_target: 'staging',
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }
        )
      )
    vi.stubGlobal('fetch', fetchMock)

    const queryClient = new QueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) =>
      React.createElement(QueryClientProvider, { client: queryClient }, children)

    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      await result.current.removeTarget('prod')
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/configure/targets/prod',
      expect.objectContaining({ method: 'DELETE' })
    )
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] })
  })
})
