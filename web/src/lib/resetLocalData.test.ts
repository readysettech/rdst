import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({ api: { POST: vi.fn() } }))
vi.mock('./desktop', () => ({
  clearBrowserStorage: vi.fn(),
  clearDesktopStorage: vi.fn(),
}))

import { resetLocalData } from './api'
import { api } from './client'
import { clearBrowserStorage, clearDesktopStorage } from './desktop'

const post = vi.mocked(api.POST)
const clearBrowser = vi.mocked(clearBrowserStorage)
const clearStorage = vi.mocked(clearDesktopStorage)

describe('resetLocalData', () => {
  beforeEach(() => {
    post.mockReset()
    clearBrowser.mockReset()
    clearStorage.mockReset()
  })

  it('clears the app storage after the backend deletes its files', async () => {
    const order: string[] = []
    clearBrowser.mockImplementation(() => {
      order.push('browser')
    })
    clearStorage.mockImplementation(async () => {
      order.push('desktop')
    })
    post.mockImplementation(async () => {
      order.push('backend')
      return {
        data: { success: true, removed_path: 'C:\\Users\\me\\.rdst' },
        error: undefined,
        response: new Response('{}', { status: 200 }),
      }
    })

    await resetLocalData()

    expect(order).toEqual(['backend', 'browser', 'desktop'])
  })

  it('surfaces a desktop storage failure after the backend reset', async () => {
    post.mockResolvedValue({
      data: { success: true, removed_path: '/home/me/.rdst' },
      error: undefined,
      response: new Response('{}', { status: 200 }),
    })
    clearStorage.mockRejectedValue(new Error('Desktop storage is locked'))

    await expect(resetLocalData()).rejects.toThrow('Desktop storage is locked')
  })

  it('surfaces an incomplete backend deletion', async () => {
    clearBrowser.mockReturnValue(undefined)
    clearStorage.mockResolvedValue(undefined)
    post.mockResolvedValue({
      data: undefined,
      error: undefined,
      response: new Response('{}', { status: 500 }),
    })

    await expect(resetLocalData()).rejects.toThrow(
      'Failed to remove local data: 500'
    )
    expect(clearBrowser).not.toHaveBeenCalled()
    expect(clearStorage).not.toHaveBeenCalled()
  })
})
