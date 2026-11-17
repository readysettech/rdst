import { describe, expect, it, vi } from 'vitest'

import { clearRdstRendererStorage } from './local-data.js'

describe('clearRdstRendererStorage', () => {
  it('clears persisted origin data and the HTTP cache', async () => {
    const clearStorageData = vi.fn().mockResolvedValue(undefined)
    const clearCache = vi.fn().mockResolvedValue(undefined)

    await clearRdstRendererStorage({ clearStorageData, clearCache })

    expect(clearStorageData).toHaveBeenCalledOnce()
    expect(clearCache).toHaveBeenCalledOnce()
  })

  it('reports storage cleanup failure', async () => {
    const clearStorageData = vi.fn().mockRejectedValue(new Error('locked'))
    const clearCache = vi.fn().mockResolvedValue(undefined)

    await expect(
      clearRdstRendererStorage({ clearStorageData, clearCache })
    ).rejects.toThrow('locked')
    expect(clearCache).not.toHaveBeenCalled()
  })
})
