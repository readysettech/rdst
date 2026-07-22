import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  __resetTargetSwitchLockForTests,
  useTargetSwitchLockState,
} from './targetSwitchLock'
import { useSchema } from './useSchema'

describe('useSchema', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
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

  it('preserves the last good status when an overlapping load aborts a refresh', async () => {
    __resetTargetSwitchLockForTests()
    let request = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, options?: RequestInit) => {
        request += 1
        if (request === 1) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                target: 'prod',
                exists: true,
                tables: 1,
                columns: 2,
                relationships: 0,
                terminology: 0,
                updated_at: null,
              }),
              { status: 200 }
            )
          )
        }
        if (request === 2) {
          return new Promise<Response>((_resolve, reject) => {
            options?.signal?.addEventListener('abort', () => {
              const error = new Error('aborted')
              error.name = 'AbortError'
              reject(error)
            })
          })
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              target: 'prod',
              tables: [],
              terminology: [],
              extensions: [],
              custom_types: [],
              metrics: [],
            }),
            { status: 200 }
          )
        )
      })
    )
    const { result } = renderHook(() => useSchema())

    await act(async () => {
      await result.current.checkStatus('prod')
    })

    let refresh = Promise.resolve<unknown>(null)
    let load = Promise.resolve<unknown>(null)
    act(() => {
      refresh = result.current.checkStatus('prod')
      load = result.current.loadSchema('prod')
    })
    await act(async () => {
      await Promise.all([refresh, load])
    })

    expect(result.current.status?.exists).toBe(true)
    expect(result.current.schema?.target).toBe('prod')
  })

  it('stays loading when a superseded request settles before its replacement', async () => {
    __resetTargetSwitchLockForTests()
    let secondResolve: (response: Response) => void = () => {}
    let request = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, options?: RequestInit) => {
        request += 1
        if (request === 1) {
          return new Promise<Response>((_resolve, reject) => {
            options?.signal?.addEventListener('abort', () => {
              const error = new Error('aborted')
              error.name = 'AbortError'
              reject(error)
            })
          })
        }
        return new Promise<Response>((resolve) => {
          secondResolve = resolve
        })
      })
    )

    const { result } = renderHook(() => useSchema())
    let first = Promise.resolve<unknown>(null)
    let second = Promise.resolve<unknown>(null)
    act(() => {
      first = result.current.checkStatus('old')
      second = result.current.checkStatus('new')
    })

    await act(async () => {
      await first
    })
    expect(result.current.loading).toBe(true)

    secondResolve(
      new Response(
        JSON.stringify({
          target: 'new',
          exists: true,
          tables: 1,
          columns: 2,
          relationships: 0,
          terminology: 0,
          updated_at: null,
        }),
        { status: 200 }
      )
    )
    await act(async () => {
      await second
    })
    expect(result.current.loading).toBe(false)
  })
})
