import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React, { type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Mock the openapi-fetch client. In jsdom+Node, openapi-fetch's internal
// `new Request(..., { signal })` throws on the AbortSignal brand check
// (jsdom's AbortController.signal is not recognized by Node's undici Request),
// which prevents any REST hook test from reaching fetch. Short-circuit the
// transport layer so we can test hook behavior directly.
vi.mock('./client', () => {
  const api = {
    GET: vi.fn(),
    POST: vi.fn(),
    PUT: vi.fn(),
    DELETE: vi.fn(),
  }
  return { api }
})

// Import after the mock so the hook picks up the mocked client.
import { useConfigure } from './useConfigure'
import { api } from './client'

function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
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
    vi.mocked(api.DELETE).mockResolvedValueOnce({
      data: { success: true },
      response: jsonResponse({ success: true }),
    } as any)
    vi.mocked(api.GET).mockResolvedValueOnce({
      data: {
        targets: [
          {
            name: 'staging',
            engine: 'postgresql',
            has_password: true,
            is_default: true,
          },
        ],
        default_target: 'staging',
      },
      response: jsonResponse({}),
    } as any)

    const queryClient = new QueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) =>
      React.createElement(QueryClientProvider, { client: queryClient }, children)

    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      await result.current.removeTarget('prod')
    })

    expect(api.DELETE).toHaveBeenCalledWith(
      '/api/configure/targets/{name}',
      expect.objectContaining({ params: { path: { name: 'prod' } } })
    )
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] })
  })

  it('loads full target details for edit without dropping hidden fields', async () => {
    vi.mocked(api.GET).mockResolvedValueOnce({
      data: {
        target_name: 'prod',
        engine: 'postgresql',
        host: 'db.example.com',
        port: 5432,
        database: 'app',
        user: 'admin',
        password_env: 'PROD_DB_PASSWORD',
        tls: true,
        read_only: true,
        has_password: true,
        is_default: true,
      },
      response: jsonResponse({}),
    } as any)

    const queryClient = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      React.createElement(QueryClientProvider, { client: queryClient }, children)

    const { result } = renderHook(() => useConfigure(), { wrapper })

    let target: unknown = null
    await act(async () => {
      target = await result.current.getTarget('prod')
    })

    expect(api.GET).toHaveBeenCalledWith(
      '/api/configure/targets/{name}',
      expect.objectContaining({ params: { path: { name: 'prod' } } })
    )
    expect(target).toEqual({
      name: 'prod',
      engine: 'postgresql',
      host: 'db.example.com',
      port: 5432,
      database: 'app',
      user: 'admin',
      password_env: 'PROD_DB_PASSWORD',
      tls: true,
      read_only: true,
      has_password: true,
      is_default: true,
    })
  })
})
