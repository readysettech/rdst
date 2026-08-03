import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createTestQueryClient,
  jsonResponse,
  queryClientWrapper,
  sseResponse,
} from '@/test-utils'

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

vi.mock('./api', () => ({
  setEnvSecret: vi.fn(),
}))

// Import after the mock so the hook picks up the mocked client.
import { useConfigure } from './useConfigure'
import { api } from './client'
import { setEnvSecret } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('useConfigure unknown events', () => {
  it('stores a new target password in the same add action', async () => {
    vi.mocked(api.POST).mockResolvedValueOnce({
      data: { success: true, target_name: 'prod' },
      response: jsonResponse({ success: true }),
    } as any)
    vi.mocked(setEnvSecret).mockResolvedValueOnce({
      success: true,
      name: 'RDST_PROD_PASSWORD',
      persisted: true,
      session_only: false,
      message: null,
    })
    vi.mocked(api.GET).mockResolvedValueOnce({
      data: { targets: [], default_target: null },
      response: jsonResponse({}),
    } as any)

    const wrapper = queryClientWrapper()
    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      await result.current.addTarget({
        name: 'prod',
        engine: 'postgresql',
        host: 'db.example.com',
        port: 5432,
        database: 'app',
        user: 'admin',
        password_env: 'RDST_PROD_PASSWORD',
        password: 'secret',
      })
    })

    expect(api.POST).toHaveBeenCalledWith(
      '/api/configure/targets',
      expect.objectContaining({
        body: expect.objectContaining({
          target: expect.objectContaining({ password: 'secret' }),
        }),
      })
    )
    expect(setEnvSecret).not.toHaveBeenCalled()
  })

  it('ignores unknown events without setting error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse('event: unknown\ndata: {"message":"x"}\n\n')
      )
    )

    const wrapper = queryClientWrapper()

    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      await result.current.testConnection('prod')
    })

    expect(result.current.error).toBeNull()
    expect(result.current.state).not.toBe('error')
  })

  it('keeps an in-flight connection test alive across a target list refresh', async () => {
    let testSignal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        testSignal = init?.signal ?? undefined
        return Promise.resolve(sseResponse('event: status\ndata: {}\n\n'))
      })
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: { targets: [], default_target: null },
      response: jsonResponse({}),
    } as any)

    const wrapper = queryClientWrapper()

    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      void result.current.testConnection('prod')
      await result.current.listTargets()
    })

    // Each action owns its own AbortController: refreshing the list must not
    // cancel the test the user just started.
    expect(testSignal?.aborted).toBe(false)
  })

  it('cancels in-flight connection tests and target adds', () => {
    let testSignal: AbortSignal | undefined
    let addSignal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        testSignal = init?.signal ?? undefined
        return new Promise<Response>(() => undefined)
      })
    )
    vi.mocked(api.POST).mockImplementationOnce((_path, options) => {
      addSignal = (options as { signal?: AbortSignal } | undefined)?.signal
      return new Promise(() => undefined)
    })

    const { result } = renderHook(() => useConfigure(), {
      wrapper: queryClientWrapper(),
    })

    act(() => {
      void result.current.testConnection('prod')
      void result.current.addTarget({
        name: 'prod',
        engine: 'postgresql',
        host: 'db.example.com',
        port: 5432,
        database: 'app',
        user: 'admin',
      })
      result.current.cancel()
    })

    expect(testSignal?.aborted).toBe(true)
    expect(addSignal?.aborted).toBe(true)
    expect(result.current.loading).toBe(false)
    expect(result.current.state).toBe('idle')
  })

  it('aborts in-flight work when the hook unmounts', () => {
    let listSignal: AbortSignal | undefined
    vi.mocked(api.GET).mockImplementationOnce((_path, options) => {
      listSignal = (options as { signal?: AbortSignal } | undefined)?.signal
      return new Promise(() => undefined)
    })

    const { result, unmount } = renderHook(() => useConfigure(), {
      wrapper: queryClientWrapper(),
    })
    act(() => {
      void result.current.listTargets()
    })
    unmount()

    expect(listSignal?.aborted).toBe(true)
  })

  it('sends unsaved SSH form values in the connection-test payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        'event: connection_test\ndata: {"target_name":"draft","status":"success"}\n\n'
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = queryClientWrapper()
    const { result } = renderHook(() => useConfigure(), { wrapper })

    await act(async () => {
      await result.current.testConnection('draft', {
        name: 'draft',
        engine: 'mysql',
        host: 'db.internal',
        port: 3306,
        database: 'app',
        user: 'app',
        password: 'unsaved-secret',
        password_env: 'RDST_DRAFT_PASSWORD',
        ssh: {
          host: 'jump.example.com',
          port: 2222,
          user: 'ec2-user',
          key_path: '~/.ssh/jump.pem',
        },
      })
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/configure/targets/draft/test')
    expect(JSON.parse(init.body)).toEqual({
      target: expect.objectContaining({
        host: 'db.internal',
        password: 'unsaved-secret',
        ssh: {
          host: 'jump.example.com',
          port: 2222,
          user: 'ec2-user',
          key_path: '~/.ssh/jump.pem',
        },
      }),
    })
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

    const queryClient = createTestQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const wrapper = queryClientWrapper(queryClient)

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

    const wrapper = queryClientWrapper()

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
      tls_verify: false,
      tls_ca: undefined,
      read_only: true,
      ssh: undefined,
      has_password: true,
      is_default: true,
    })
  })
})
