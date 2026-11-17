import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearBrowserStorage,
  clearDesktopStorage,
  isDesktopRuntime,
  thisComputer,
} from './desktop'

function setDesktopPlatform(platform: string) {
  window.rdstDesktop = { isDesktop: true, platform }
}

afterEach(() => {
  delete window.rdstDesktop
})

describe('clearBrowserStorage', () => {
  it('clears local and session storage', () => {
    localStorage.setItem('rdst.selectedTarget', 'prod')
    sessionStorage.setItem('rdst.recentPath', '/tmp/key')

    clearBrowserStorage()

    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})

describe('clearDesktopStorage', () => {
  it('delegates cleanup to the desktop bridge', async () => {
    const clearStorage = vi.fn().mockResolvedValue(undefined)
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'win32',
      data: { clearStorage },
    }

    await clearDesktopStorage()

    expect(clearStorage).toHaveBeenCalledOnce()
  })

  it('is a no-op in the browser', () => {
    expect(clearDesktopStorage()).toBeUndefined()
  })
})

describe('isDesktopRuntime', () => {
  it('uses the preload bridge as the runtime signal', () => {
    expect(isDesktopRuntime()).toBe(false)

    setDesktopPlatform('win32')

    expect(isDesktopRuntime()).toBe(true)
  })
})

describe('thisComputer', () => {
  it('says this Mac only in the macOS app', () => {
    expect(thisComputer()).toBe('this computer')
    setDesktopPlatform('win32')
    expect(thisComputer()).toBe('this computer')
    setDesktopPlatform('darwin')
    expect(thisComputer()).toBe('this Mac')
  })
})
