import { afterEach, describe, expect, it } from 'vitest'
import { isDesktopRuntime, thisComputer } from './desktop'

function setDesktopPlatform(platform: string) {
  window.rdstDesktop = { isDesktop: true, platform }
}

afterEach(() => {
  delete window.rdstDesktop
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
