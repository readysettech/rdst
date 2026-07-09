import { afterEach, describe, expect, it, vi } from 'vitest'

import { createGlassFallbackLogger, loadLiquidGlass } from './liquid-glass.js'

describe('loadLiquidGlass', () => {
  it('loads electron-liquid-glass on darwin and unwraps the default export', async () => {
    const result = await loadLiquidGlass({
      platform: 'darwin',
      importModule: async () => ({
        default: {
          addView() {
            return 7
          },
        },
      }),
    })

    expect(result.failureReason).toBeNull()
    expect(result.source).toBe('package')
    expect(
      result.module?.addView?.(Buffer.alloc(0), {
        cornerRadius: 0,
        tintColor: '#00000000',
        opaque: false,
      })
    ).toBe(7)
  })

  it('returns a clear failure reason when the package cannot be loaded', async () => {
    const result = await loadLiquidGlass({
      platform: 'darwin',
      importModule: async () => {
        throw new Error('missing package')
      },
    })

    expect(result.module).toBeNull()
    expect(result.source).toBe('package')
    expect(result.failureReason).toContain('missing package')
  })

  it('does not attempt to import electron-liquid-glass off darwin', async () => {
    const importModule = vi.fn()

    const result = await loadLiquidGlass({
      platform: 'linux',
      importModule,
    })

    expect(result).toEqual({
      module: null,
      failureReason: null,
      source: null,
    })
    expect(importModule).not.toHaveBeenCalled()
  })
})

describe('createGlassFallbackLogger', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('logs only once on darwin', () => {
    const warn = vi.fn()
    const logGlassFallback = createGlassFallbackLogger('darwin', warn)

    logGlassFallback('first reason')
    logGlassFallback('second reason')

    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalledWith(
      '[liquid-glass] Falling back to Electron vibrancy: first reason'
    )
  })

  it('does not log off darwin', () => {
    const warn = vi.fn()
    const logGlassFallback = createGlassFallbackLogger('linux', warn)

    logGlassFallback('reason')

    expect(warn).not.toHaveBeenCalled()
  })
})
