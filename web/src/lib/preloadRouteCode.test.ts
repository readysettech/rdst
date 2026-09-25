import { describe, expect, it, vi } from 'vitest'
import { preloadRouteCode } from './preloadRouteCode'

describe('preloadRouteCode', () => {
  it('loads every route chunk and swallows failures', async () => {
    const loaded: string[] = []
    const loadRouteChunk = vi.fn((route: string) => {
      loaded.push(route)
      return route === '/broken'
        ? Promise.reject(new Error('chunk missing'))
        : Promise.resolve()
    })
    preloadRouteCode({
      routesById: {
        __root__: '__root__',
        '/ask': '/ask',
        '/broken': '/broken',
      },
      loadRouteChunk,
    })
    await Promise.resolve()
    expect(loaded).toEqual(['__root__', '/ask', '/broken'])
  })
})
